"""CDP cookie extraction service for YouTube persistent browser profiles.

Exports authenticated YouTube and Google cookies from a persistent Chromium
profile directly into Python-compliant 7-column Netscape format, bypassing
Windows 11 App-Bound encryption and SQLite database locks.
"""

import math
import os
import shutil
import time
from pathlib import Path
from typing import Optional, Tuple


AUTH_COOKIE_NAMES = frozenset({
    "login_info",
    "sid",
    "hsid",
    "ssid",
    "apisid",
    "sapisid",
    "__secure-1psid",
    "__secure-3psid",
    "__secure-1papisid",
    "__secure-3papisid",
})

YOUTUBE_TARGET_DOMAINS = (
    ".youtube.com",
    "youtube.com",
    ".google.com",
    "google.com",
)


def _resolve_chrome_executable() -> Optional[str]:
    """Find installed Google Chrome or compatible Chromium executable."""
    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    try:
        import browser_patchright_glue as browser_glue
        return browser_glue.resolve_browser_executable(profile_name="YouTube Login")
    except Exception:
        return None


def _format_netscape_row(c: dict, now: int) -> Optional[str]:
    """Format one cookie dict to Netscape 7-column row.
    
    CRITICAL: Python's http.cookiejar.MozillaCookieJar requires:
        assert domain_specified == initial_dot
    where domain_specified is (column_2 == 'TRUE') and initial_dot is domain.startswith('.').
    """
    domain = str(c.get("domain") or "").strip()
    name = str(c.get("name") or "").strip()
    value = str(c.get("value") or "")
    if not domain or not name:
        return None

    # Strict field validation: reject embedded delimiters that would corrupt TSV
    for field_val in (domain, name, value):
        if "\t" in field_val or "\n" in field_val or "\r" in field_val:
            return None

    path = str(c.get("path") or "/").strip()
    if not path.startswith("/") or "\t" in path or "\n" in path or "\r" in path:
        path = "/"

    # Python http.cookiejar strict assertion compliance
    has_initial_dot = domain.startswith(".")
    include_subdomains = "TRUE" if has_initial_dot else "FALSE"
    secure = "TRUE" if c.get("secure") else "FALSE"

    raw_exp = c.get("expires")
    if raw_exp is None or raw_exp == -1:
        expires = 0
    else:
        try:
            f_exp = float(raw_exp)
            if not math.isfinite(f_exp) or f_exp < 0:
                expires = 0
            else:
                expires = int(f_exp)
        except (ValueError, TypeError, OverflowError):
            expires = 0

    return f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}"


def export_youtube_cookies_cdp(
    profile_dir: str | Path,
    output_path: str | Path,
    executable_path: Optional[str] = None,
    timeout_seconds: float = 30.0,
    wait_settle_seconds: float = 2.5,
    max_lock_retries: int = 4,
) -> Tuple[bool, str, int]:
    """Export YouTube cookies from a persistent profile via headless Playwright/Patchright.

    Returns:
        (success: bool, message: str, cookie_count: int)
    """
    profile_dir = Path(profile_dir).resolve()
    output_path = Path(output_path).resolve()

    if not profile_dir.exists():
        return False, f"Thư mục profile không tồn tại: {profile_dir}", 0

    chrome_exe = executable_path or _resolve_chrome_executable()
    if not chrome_exe or not Path(chrome_exe).is_file():
        return False, "Không tìm thấy Chrome/browser executable để trích xuất cookie.", 0

    # Import Patchright (preferred) or Playwright
    sync_playwright_fn = None
    try:
        from patchright.sync_api import sync_playwright
        sync_playwright_fn = sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
            sync_playwright_fn = sync_playwright
        except ImportError:
            return False, "Cần cài đặt patchright hoặc playwright để trích xuất cookie qua CDP.", 0

    backoffs = [0.5, 1.0, 1.5, 2.0]
    last_exc = None

    for attempt in range(max_lock_retries):
        try:
            with sync_playwright_fn() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    executable_path=chrome_exe,
                    headless=True,
                    viewport={"width": 1280, "height": 720},
                    args=[
                        "--profile-directory=Default",
                        "--no-first-run",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=int(timeout_seconds * 1000))
                    if wait_settle_seconds > 0:
                        time.sleep(wait_settle_seconds)
                    raw_cookies = context.cookies()
                finally:
                    context.close()

            # Filter relevant cookies for YouTube and Google authentication
            now = int(time.time())
            lines = [
                "# Netscape HTTP Cookie File",
                "# http://curl.haxx.se/rfc/cookie_spec.html",
                "# This file was generated by Automation Engine",
                "",
            ]

            valid_count = 0
            has_auth = False

            for c in raw_cookies:
                domain = str(c.get("domain") or "").lower()
                if not any(td in domain for td in ("youtube.com", "google.com")):
                    continue
                row = _format_netscape_row(c, now)
                if row:
                    lines.append(row)
                    valid_count += 1
                    name = str(c.get("name") or "").lower()
                    if name in AUTH_COOKIE_NAMES:
                        has_auth = True

            if valid_count == 0:
                return False, "Không tìm thấy cookie YouTube/Google nào trong profile.", 0

            # Atomic write to temporary file first (unique per process and timestamp)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = output_path.with_suffix(f".tmp_{os.getpid()}_{int(time.time()*1000)}")
            tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            # Validate before publishing
            from youtube_monitor.core import validate_youtube_cookie_file
            is_valid, validation_msg = validate_youtube_cookie_file(str(tmp_path))
            if not is_valid:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return False, f"File cookie trích xuất không hợp lệ: {validation_msg}", 0

            # Publish atomically
            os.replace(str(tmp_path), str(output_path))
            auth_note = " (Đã đăng nhập tài khoản)" if has_auth else " (Chưa có token đăng nhập)"
            return True, f"Trích xuất thành công {valid_count} cookie YouTube/Google{auth_note}.", valid_count

        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            if "target closed" in err_str or "process" in err_str or "lock" in err_str or "ebusy" in err_str:
                if attempt < max_lock_retries - 1:
                    time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
                    continue
            break

    return False, f"Lỗi bóc tách cookie từ profile: {last_exc}", 0
