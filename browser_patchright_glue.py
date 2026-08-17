"""Sync facade tying main.py to the Patchright runtime.

The app keeps storing a token in the legacy ``driver`` slots so the queue,
lifecycle and UI logic keep working unchanged. All browser work runs on the
runtime thread; callers wait synchronously on the returned futures.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from browser_maintenance import create_owned_root
from core_helpers import _extract_ip_address, parse_cookie, parse_proxy_string
from tiktok_account_discovery import build_discovery_operation, is_readonly_allowed
from patchright_browser import (
    BrowserServiceClosedError,
    BrowserSessionConfig,
    CookieImportReport,
    PatchrightBrowser,
    ProfileInUseError,
    SessionMode,
    StaleSessionError,
)
from patchright_cookie_codec import (
    TIKTOK_AUTH_COOKIES,
    has_primary_tiktok_auth_cookie,
    patchright_cookies_to_selenium,
    selenium_cookies_to_patchright,
)
from patchright_profile_migration import (
    create_patchright_profile,
    migration_status,
    profile_owner_id,
    set_profile_owner,
)
from profile_config_engine import (
    generate_stealth_profile_config,
    generate_orbita_profile_config,
)

try:
    from patchright_upload import SELECTORS as _UPLOAD_SELECTORS
    from patchright_upload import upload_tiktok
except ImportError:  # pragma: no cover - only needed at upload time
    _UPLOAD_SELECTORS = None
    upload_tiktok = None


class SessionSetupError(RuntimeError):
    """Browser session could not be opened or configured."""


class ProfileBusyError(SessionSetupError):
    """Profile directory is still owned by another live session."""


class ProxyVerificationError(RuntimeError):
    """Browser exit IP could not be verified."""

    def __init__(self, reason, current_ip=None, expected_ip=None):
        super().__init__(reason)
        self.reason = reason
        self.current_ip = current_ip
        self.expected_ip = expected_ip


class LoginRequiredError(RuntimeError):
    """TikTok redirected the freshly opened session to its login page."""


UPLOAD_TOTAL_TIMEOUT = 15 * 60  # page_ready + editor + confirmation margin
SESSION_OPEN_TIMEOUT = 90
OP_DEFAULT_TIMEOUT = 60

PROXY_IP_SERVICES = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
)

_service = None
_service_lock = threading.Lock()


class SessionToken:
    """Opaque session handle stored in the legacy driver slots."""

    __slots__ = (
        "profile_name",
        "handle",
        "mode",
        "profile_path",
        "_closed",
        "_close_lock",
        "generation",
        "cancellation_event",
    )

    def __init__(self, profile_name, handle, mode, profile_path, generation=0):
        self.profile_name = profile_name
        self.handle = handle
        self.mode = mode
        self.profile_path = profile_path
        self.generation = generation
        self._closed = False
        self._close_lock = threading.Lock()
        self.cancellation_event = threading.Event()

    def is_alive(self):
        return not self._closed

    def mark_closed(self):
        self._closed = True

    def set_cancelled(self):
        self.cancellation_event.set()

    def quit(self, timeout=10.0):
        """Lifecycle-compatible close: close the session and mark the token dead
        only once the registry confirms the session was released. Idempotent and
        thread-safe: concurrent callers share one close, and a close that timed
        out is not reported as a successful release."""
        with self._close_lock:
            if self._closed:
                return True
            closed = close_session(self.handle, timeout=timeout)
            if closed:
                self.mark_closed()
            return closed


def browser_service():
    global _service
    with _service_lock:
        if _service is None or _service.status().closed:
            _service = PatchrightBrowser()
        return _service


def shutdown_browser_service(timeout=10.0):
    global _service
    with _service_lock:
        service = _service
        _service = None
    if service is not None:
        service.shutdown(timeout=timeout)


def _resolve_profile(config):
    legacy = str(config.get("chrome_profile", "")).strip()
    if not legacy:
        raise SessionSetupError("chrome_profile chưa được cấu hình cho profile này")
    return Path(legacy)


def ensure_patchright_profile(config):
    """Create (or resume) the owned Profile-Patchright sibling of the legacy profile."""
    legacy_dir = _resolve_profile(config)
    if legacy_dir.name.casefold() != "profile":
        raise ValueError("Legacy profile directory must be named Profile")
    target = legacy_dir.with_name("Profile-Patchright")
    account_id = str(config.get("account_uuid", "") or "").strip() or None
    if target.exists():
        status = migration_status(target)
        if Path(status["legacy_profile"]).resolve() != legacy_dir.resolve():
            raise ValueError("Owned profile belongs to a different legacy profile")
        try:
            owner = profile_owner_id(target)
        except Exception:
            owner = None
        if owner and account_id and owner != account_id:
            raise ValueError(
                "Browser profile thuộc tài khoản khác; không được tái sử dụng"
            )
        if account_id and owner is None:
            try:
                set_profile_owner(target, account_id)
            except Exception:
                pass
    else:
        if not legacy_dir.is_dir():
            create_owned_root(legacy_dir)
        target = create_patchright_profile(str(legacy_dir), str(legacy_dir.parent), account_id=account_id)
    profile_path = str(target)
    config["browser_profile_path"] = profile_path
    return profile_path


def active_profile_path(config):
    """The data dir currently in use: Patchright sibling when available, else legacy."""
    if config.get("browser_profile_path"):
        return str(config["browser_profile_path"])
    return str(config.get("chrome_profile", ""))


def _app_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundled_browser_dir(app_base=None):
    root = Path(app_base or _app_base_dir())
    root_dir = root / "Browser"
    if root_dir.exists():
        return root_dir
    internal_dir = root / "_internal" / "Browser"
    if internal_dir.exists():
        return internal_dir
    return root_dir


def _find_system_chrome_executable():
    candidates = []
    try:
        import winreg

        for root_key in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(
                    root_key,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                ) as key:
                    value, _ = winreg.QueryValueEx(key, None)
                    if value:
                        candidates.append(value)
            except OSError:
                pass
    except Exception:
        pass
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(
                os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            )
    for path in candidates:
        if path and _is_valid_executable(path):
            return str(path)
    return None


def _is_valid_executable(path):
    try:
        return bool(path) and Path(path).is_file() and Path(path).stat().st_size > 0
    except OSError:
        return False


def resolve_browser_executable(app_base=None):
    """Return a Chromium-compatible executable path for Patchright.

    Strict preference order: bundled ``Browser/chrome-win64/chrome.exe``, then system Google Chrome.
    Orbita binaries have been completely removed in favor of standard chrome-win64 + native stealth engine.
    Returns ``None`` when no usable browser is found so callers can fail with a
    clear Vietnamese message instead of relying on Patchright's default
    ``.local-browsers`` lookup."""
    browser_dir = _bundled_browser_dir(app_base)
    candidates = [
        browser_dir / "chrome-win64" / "chrome.exe",
        browser_dir / "chrome.exe",
        browser_dir / "chrome" / "chrome.exe",
    ]

    # If in standard environment without explicit app_base, check all potential workspace roots
    if app_base is None:
        extra_bases = [
            _app_base_dir(),
            Path.cwd(),
            Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None,
            Path(__file__).resolve().parent,
        ]
        for base in extra_bases:
            if base and base.exists() and base != browser_dir.parent:
                candidates.extend([
                    base / "Browser" / "chrome-win64" / "chrome.exe",
                    base / "_internal" / "Browser" / "chrome-win64" / "chrome.exe",
                ])

    seen = set()
    for path in candidates:
        if path is not None:
            p_str = str(path.resolve()) if hasattr(path, "resolve") and path.exists() else str(path)
            if p_str not in seen:
                seen.add(p_str)
                if _is_valid_executable(path):
                    return p_str
    return _find_system_chrome_executable()


def build_session_config(config, mode=SessionMode.AUTOMATION, headed=None, profile_name=None):
    """Map app config to a patchright native session configuration."""
    profile_path = config.get("browser_profile_path")
    if not profile_path:
        raise SessionSetupError("browser_profile_path chưa được khởi tạo")
    if headed is None:
        headed = not config.get("headless", True)

    kwargs = {"profile_path": profile_path, "mode": mode, "headed": headed}

    executable = config.get("browser_executable") or resolve_browser_executable()
    if not executable:
        raise SessionSetupError(
            "Không tìm thấy browser. Hãy tải tài nguyên Browser lần đầu "
            "(nút 'Tải tài nguyên') hoặc cài Google Chrome rồi thử lại."
        )
    kwargs["executable_path"] = executable

    proxy = None
    if config.get("use_proxy", False):
        proxy_data = parse_proxy_string(config.get("proxy_string", ""))
        if not proxy_data:
            raise SessionSetupError("Proxy sai định dạng; từ chối mở browser")
        host = str(proxy_data.get("ip", "")).strip()
        port = str(proxy_data.get("port", "")).strip()
        if not host or any(character.isspace() for character in host) or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise SessionSetupError("Proxy sai định dạng; từ chối mở browser")
        proxy_type = str(config.get("proxy_type", "http") or "http").strip().lower()
        if proxy_type not in ("http", "socks5"):
            raise SessionSetupError("Proxy type chỉ hỗ trợ http hoặc socks5")
        scheme = "socks5" if proxy_type == "socks5" else "http"
        native = {
            "server": "{}://{}:{}".format(scheme, host, port)
        }
        if proxy_data.get("user"):
            native["username"] = proxy_data["user"]
        if proxy_data.get("pass"):
            native["password"] = proxy_data["pass"]
        proxy = native
    if proxy is not None:
        kwargs["proxy"] = proxy

    fingerprint = config.get("fingerprint") or {}
    user_agent = fingerprint.get("user_agent") or config.get("user_agent")
    if user_agent:
        kwargs["user_agent"] = user_agent
    timezone_id = fingerprint.get("timezone")
    if timezone_id:
        kwargs["timezone_id"] = timezone_id
    lang = fingerprint.get("lang")
    if isinstance(lang, str) and len(lang) <= 64:
        kwargs["locale"] = lang
    geolocation = fingerprint.get("geolocation")
    if isinstance(geolocation, dict):
        try:
            latitude = float(geolocation["latitude"])
            longitude = float(geolocation["longitude"])
            accuracy = float(geolocation.get("accuracy", 50))
        except (KeyError, TypeError, ValueError):
            pass
        else:
            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                kwargs["geolocation"] = {
                    "latitude": latitude,
                    "longitude": longitude,
                    "accuracy": accuracy,
                }
                kwargs["permissions"] = ("geolocation",)

    # Generate native stealth anti-detect config dictionary (in-memory)
    account_uuid = str(
        config.get("account_uuid")
        or config.get("profile_name")
        or (Path(profile_path).name if profile_path else "")
    )
    proxy_info = None
    if proxy is not None:
        proxy_info = dict(proxy)
    geoip_info = {}
    if isinstance(fingerprint, dict):
        geoip_info = {
            "timezone": fingerprint.get("timezone"),
            "ip": fingerprint.get("ip"),
        }
        geo = fingerprint.get("geolocation")
        if isinstance(geo, dict):
            geoip_info["latitude"] = geo.get("latitude")
            geoip_info["longitude"] = geo.get("longitude")

    resolved_profile_name = str(
        profile_name
        or config.get("profile_name")
        or config.get("name")
        or account_uuid
    )
    stealth_cfg = generate_stealth_profile_config(
        account_uuid=account_uuid,
        proxy_info=proxy_info,
        geoip_info=geoip_info,
        user_agent=config.get("user_agent"),
        profile_name=resolved_profile_name,
    )
    clean_profile_volatile_caches(profile_path)

    # Ultra-optimized arguments for low RAM, anti-freeze & high concurrency multi-profile TikTok automation
    kwargs["args"] = (
        "--no-first-run",
        "--log-level=3",
        "--disable-session-crashed-bubble",
        "--disable-backgrounding-occluded-windows",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-domain-reliability",
        "--disable-sync",
        "--disable-breakpad",
        "--disable-client-side-phishing-detection",
        "--disable-default-apps",
        "--disable-hang-monitor",
        "--disable-popup-blocking",
        "--disable-prompt-on-repost",
        "--metrics-recording-only",
        "--password-store=basic",
        "--use-mock-keychain",
        "--renderer-process-limit=2",
        "--disable-site-isolation-trials",
        "--disable-dev-shm-usage",
        "--disable-component-extensions-with-background-pages",
        "--disk-cache-size=33554432",
        "--media-cache-size=67108864",
        "--aggressive-cache-discard",
        "--js-flags=--max-old-space-size=256 --expose-gc",
        "--enable-features=MemoryReducer,PurgeAndSuspend,ResourceLoadScheduler",
        "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints,InterestFeedContentSuggestions,CalculateNativeWinOcclusion",
    )
    kwargs["account_uuid"] = account_uuid
    kwargs["profile_name"] = resolved_profile_name
    kwargs["stealth_config"] = stealth_cfg
    return BrowserSessionConfig(**kwargs)


def clean_profile_volatile_caches(profile_path):
    """Safely purge volatile rendering and network caches from profile dir.
    
    Preserves cookies, local storage, indexedDB, sessions, and credentials.
    Only purges GPU cache, shader cache, code cache, and media cache to minimize RAM and disk bloat.
    """
    if not profile_path:
        return
    p = Path(profile_path)
    if not p.exists():
        return
    volatile_subdirs = [
        p / "Default" / "Cache",
        p / "Default" / "Code Cache",
        p / "Default" / "GPUCache",
        p / "GrShaderCache",
        p / "ShaderCache",
        p / "Default" / "Media Cache",
    ]
    for target in volatile_subdirs:
        try:
            if target.exists() and target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
        except Exception:
            pass


def _canonical_profile(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _validate_session_handle(handle, session_config):
    """Verify a freshly opened SessionHandle before publishing a token.

    A malformed handle would make every later operation fail and leak the
    session in the registry, so close the raw handle immediately when the
    result is structurally invalid."""
    errors = []
    session_id = getattr(handle, "session_id", None)
    if not isinstance(session_id, str) or not session_id:
        errors.append("thiếu session_id hợp lệ")
    generation = getattr(handle, "generation", None)
    if not isinstance(generation, int):
        errors.append("thiếu generation hợp lệ")
    if getattr(handle, "mode", None) != SessionMode.AUTOMATION:
        errors.append("mode không đúng AUTOMATION")
    if _canonical_profile(getattr(handle, "profile_path", "")) != _canonical_profile(
        session_config.profile_path
    ):
        errors.append("profile_path không khớp với session")
    if errors:
        try:
            close_session(handle, timeout=10)
        except Exception:
            pass
        raise SessionSetupError(
            "Session trả về không hợp lệ: {}".format("; ".join(errors))
        )


def open_session(config, profile_name, timeout=SESSION_OPEN_TIMEOUT):
    service = browser_service()
    session_config = build_session_config(config, mode=SessionMode.AUTOMATION, profile_name=profile_name)
    try:
        future = service.open_session(session_config)
        result = future.result(timeout=timeout)
    except ProfileInUseError as error:
        raise ProfileBusyError(str(error)) from error
    except BrowserServiceClosedError as error:
        raise SessionSetupError("Browser service đã đóng") from error
    except FutureTimeoutError:
        future.cancel()
        raise SessionSetupError(
            "Mở browser quá {} giây; tài nguyên máy có thể đang quá tải".format(timeout)
        ) from None
    _validate_session_handle(result.handle, session_config)
    return SessionToken(
        profile_name=profile_name,
        handle=result.handle,
        mode=SessionMode.AUTOMATION,
        profile_path=session_config.profile_path,
    )


def import_cookies_report(token, selenium_cookies, timeout=OP_DEFAULT_TIMEOUT):
    """Import cookies and return the acceptance report without raising.

    Only the cookies the context actually kept are counted; TikTok session
    cookies are verified against ``www.tiktok.com`` coverage."""
    converted = selenium_cookies_to_patchright(selenium_cookies or [])
    if not converted:
        return CookieImportReport(
            requested=0,
            accepted=0,
            auth_requested=0,
            auth_accepted=0,
            missing_auth_names=(),
        )
    service = browser_service()
    future = service.import_cookies(token.handle, converted)
    result = future.result(timeout=timeout)
    return result.value


def import_cookies(token, selenium_cookies, timeout=OP_DEFAULT_TIMEOUT):
    """Import cookies; raises when TikTok session cookies are not accepted.

    Returns the number of cookies actually kept by the context, not the number
    of cookies that were merely sent to ``add_cookies``."""
    report = import_cookies_report(token, selenium_cookies, timeout=timeout)
    if report.requested > 0 and report.accepted == 0:
        raise SessionSetupError(
            "Không cookie nào được Chromium chấp nhận; cookie có thể sai domain hoặc hết hạn"
        )
    if report.auth_requested > 0 and report.auth_accepted == 0:
        raise SessionSetupError(
            "Cookie đăng nhập TikTok không được chấp nhận (thiếu: {})".format(
                ", ".join(report.missing_auth_names) or "không rõ"
            )
        )
    return report.accepted


def authenticate_session(
    token,
    config,
    profile_name,
    upload_url,
    allow_cookie_fallback=True,
    status_callback=None,
    timeout=30,
):
    """Verify the persistent profile session first, then saved cookie once.

    Priority: an already-logged-in persistent profile is reused as-is; the
    saved ``cookie_str`` is imported only when the profile session is not
    authenticated, and only one fallback attempt is made.

    Returns the auth source: ``profile_session`` or ``cookie_fallback``.
    Raises :class:`LoginRequiredError` when neither path authenticates.
    """
    status = status_callback or (lambda message: None)
    navigate(token, upload_url)
    login_state = wait_page_login_state(token, timeout=timeout)
    if login_state == "authenticated":
        return "profile_session"
    if not allow_cookie_fallback:
        raise LoginRequiredError(
            "Profile chưa đăng nhập; hãy dùng 'Mở Chrome' để đăng nhập thủ công"
            if login_state == "login_required"
            else "Không xác minh được trạng thái đăng nhập trên profile"
        )
    cookies = parse_cookie(config.get("cookie_str", ""))
    if not cookies:
        raise LoginRequiredError(
            "Profile chưa đăng nhập và không có cookie dự phòng; hãy dùng 'Mở Chrome'"
        )
    status(f"[{profile_name}] Session profile chưa hợp lệ, thử cookie dự phòng...")
    import_cookies(token, cookies)
    navigate(token, upload_url)
    login_state = wait_page_login_state(token, timeout=timeout)
    if login_state == "authenticated":
        return "cookie_fallback"
    raise LoginRequiredError(
        "Cookie dự phòng cũng bị TikTok từ chối; hãy dùng 'Mở Chrome' để đăng nhập thủ công"
    )


def _run(handle, operation, timeout=OP_DEFAULT_TIMEOUT, convert_timeout=True):
    service = browser_service()
    future = service.run(handle, operation)
    try:
        result = future.result(timeout=timeout)
    except FutureTimeoutError:
        if not convert_timeout:
            raise
        try:
            service.cancel_session(handle).result(timeout=10)
        except Exception:
            pass
        raise SessionSetupError("Thao tác browser vượt quá {} giây".format(timeout)) from None
    return result.value


def run_operation(token, operation, timeout=OP_DEFAULT_TIMEOUT):
    try:
        return _run(token.handle, operation, timeout=timeout)
    except StaleSessionError:
        token.mark_closed()
        raise SessionSetupError("Phiên browser đã bị đóng") from None


def navigate(token, url, timeout=OP_DEFAULT_TIMEOUT):
    async def _goto(page):
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        return page.url

    return run_operation(token, _goto, timeout=timeout)


def _is_login_url(url):
    return bool(
        re.search(r"tiktok\.com/(?:[^?#]+/)?login(?:[/?#]|$)", str(url or "").lower())
    )


async def _any_visible(page, selector):
    locator = page.locator(selector)
    for index in range(await locator.count()):
        if await locator.nth(index).is_visible():
            return True
    return False


def page_login_state(token, timeout=OP_DEFAULT_TIMEOUT):
    """Inspect the live page for TikTok's login form.

    Returns ``authenticated``, ``login_required`` or ``indeterminate``.
    ``indeterminate`` is fail-closed: callers must not publish the driver as
    ready, because neither a login form nor an authenticated Studio shell was
    visible."""
    login_selectors = _UPLOAD_SELECTORS["login"]
    shell_selectors = (
        _UPLOAD_SELECTORS["file_input"],
        _UPLOAD_SELECTORS["upload_surface"],
    )

    async def _inspect(page):
        current_url = str(getattr(page, "url", "") or "").lower()
        if _is_login_url(current_url):
            return "login_required"
        if await _any_visible(page, login_selectors):
            return "login_required"
        for shell_selector in shell_selectors:
            if await _any_visible(page, shell_selector):
                return "authenticated"
        return "indeterminate"

    return run_operation(token, _inspect, timeout=timeout)


def wait_page_login_state(token, timeout=30.0, poll=1.0, run_timeout=OP_DEFAULT_TIMEOUT):
    """Poll until TikTok login state is definitive.

    Returns ``authenticated`` or ``login_required`` once either is observed,
    or ``indeterminate`` if no decision was reached within ``timeout``."""
    deadline = time.monotonic() + timeout
    last = "indeterminate"
    while True:
        last = page_login_state(token, timeout=run_timeout)
        if last != "indeterminate":
            return last
        if time.monotonic() >= deadline:
            return last
        time.sleep(poll)


def wait_upload_page_ready(token, timeout=30.0, poll=1.0, run_timeout=OP_DEFAULT_TIMEOUT):
    """Poll until TikTok's upload surface (file input / upload card) is visible.

    Returns True when the upload page is ready to accept the next video, False
    when it is not ready within ``timeout``. Session errors are reported as
    False (never raised) so an already-confirmed post is not downgraded by a
    failed "return to upload" step."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            state = page_login_state(token, timeout=run_timeout)
        except (SessionSetupError, StaleSessionError):
            return False
        if state == "authenticated":
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def navigate_upload_ready(token, url, timeout=OP_DEFAULT_TIMEOUT, ready_timeout=30.0, poll=1.0):
    """Navigate to the upload URL and wait until its surface is ready.

    Returns True when the page is upload-ready. Raises SessionSetupError only
    when the session itself is stale; a live session that simply has not shown
    the upload surface is reported as False."""
    navigate(token, url, timeout=timeout)
    return wait_upload_page_ready(token, timeout=ready_timeout, poll=poll, run_timeout=timeout)


def page_url(token, timeout=OP_DEFAULT_TIMEOUT):
    async def _url(page):
        return page.url

    return run_operation(token, _url, timeout=timeout)


def page_evaluate(token, script, timeout=OP_DEFAULT_TIMEOUT):
    async def _evaluate(page):
        return await page.evaluate(script)

    return run_operation(token, _evaluate, timeout=timeout)


def verify_exit_ip(token, expected_ip, timeout_per_service=6, diagnostics=None):
    """Fetch an IP reporter inside the browser; returns (is_match, ip, details)."""
    expected = _extract_ip_address(expected_ip) or str(expected_ip).strip()
    details = diagnostics if diagnostics is not None else []
    async def _verify(page):
        loop = asyncio.get_event_loop()
        for url in PROXY_IP_SERVICES:
            started_at = time.perf_counter()
            last_error = None
            last_body = ""
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=10_000)
            except Exception as error:
                last_error = error
            deadline = loop.time() + timeout_per_service
            while loop.time() < deadline:
                try:
                    last_body = await page.evaluate(
                        "document.body ? document.body.innerText : ''"
                    )
                    current_ip = _extract_ip_address(str(last_body))
                    if current_ip:
                        return current_ip == expected, current_ip
                except Exception as error:
                    last_error = error
                await asyncio.sleep(0.2)
            details.append({
                "url": url,
                "elapsed": round(time.perf_counter() - started_at, 2),
                "body": (last_body or "")[:120],
                "error": None if last_error is None else type(last_error).__name__,
            })
        return False, None
    try:
        total_timeout = len(PROXY_IP_SERVICES) * (10 + timeout_per_service) + 5
        return run_operation(token, _verify, timeout=total_timeout)
    except Exception:
        return False, None


def export_cookies(token, timeout=OP_DEFAULT_TIMEOUT):
    """Return selenium-style cookie dicts captured from the live session."""
    service = browser_service()
    future = service.export_cookies(token.handle)
    export = future.result(timeout=timeout)
    cookies = json.loads(export.cookies_json)
    return patchright_cookies_to_selenium(cookies)


def cancel_session(handle, timeout=10):
    service = browser_service()
    try:
        future = service.cancel_session(handle)
        future.result(timeout=timeout)
    except (BrowserServiceClosedError, StaleSessionError, FutureTimeoutError):
        pass


def close_session(handle, timeout=10):
    """Close a session. Returns True when the session is confirmed released
    (closed, already stale, or the service shut down) and False when the close
    could not be confirmed within the timeout."""
    service = browser_service()
    try:
        future = service.close_session(handle)
        future.result(timeout=timeout)
    except (BrowserServiceClosedError, StaleSessionError):
        return True
    except FutureTimeoutError:
        return False
    except Exception:
        return False
    return True


def run_upload(token, video_path, status_callback=None, timeout=UPLOAD_TOTAL_TIMEOUT, stop_before_post=False):
    """Run the standalone uploader to completion on the runtime thread."""
    if upload_tiktok is None:  # pragma: no cover
        raise SessionSetupError("patchright_upload không khả dụng")
    log = status_callback or (lambda message: None)

    def _log_status(message):
        try:
            log(str(message))
        except Exception:
            pass

    async def _upload(page):
        return await upload_tiktok(
            page,
            video_path,
            status_callback=_log_status,
            cancellation_event=token.cancellation_event,
            stop_before_post=stop_before_post,
        )

    try:
        value = _run(token.handle, _upload, timeout=timeout, convert_timeout=False)
    except StaleSessionError:
        token.mark_closed()
        raise SessionSetupError("Phiên browser bị mất khi đang đăng") from None
    except FutureTimeoutError:
        cancel_session(token.handle, timeout=10)
        token.quit()
        raise SessionSetupError("Đăng video vượt quá {} giây".format(timeout)) from None
    return value


def watch_manual_close(token, poll=0.5):
    """Block until the manual browser is closed by the user (daemon callers only)."""
    async def _watch(page):
        while True:
            try:
                if not page.context.pages:
                    break
            except Exception:
                break
            await asyncio.sleep(poll)
        return True

    try:
        _run(token.handle, _watch, timeout=None)
    except (StaleSessionError, asyncio.CancelledError):
        pass
    except Exception:
        pass
    finally:
        try:
            token.quit()
        except Exception:
            pass


def discover_tiktok_readonly_endpoints(token, pages=None, timeout=OP_DEFAULT_TIMEOUT):
    """Discover read-only Creator Center endpoints from a live session.

    Runs the discovery collector on the browser runtime thread. Returns a
    tiktok_account_discovery.DiscoveryResult. Never issues non-GET requests."""
    operation = build_discovery_operation(pages=pages or ())
    return run_operation(token, operation, timeout=timeout)


def inspect_tiktok_account(token, endpoint_paths=(), urls_by_path=None, timeout=OP_DEFAULT_TIMEOUT):
    """Fetch read-only Creator Center JSON endpoints via in-page fetch.

    Only GET requests on allowlisted paths are issued; every request is checked
    by the same read-only guard as the discovery collector. When ``urls_by_path``
    carries an observed full URL for a seed path it is replayed as-is (the real
    query context, e.g. the reward analytics params) instead of guessing a bare
    path. Returns a dict of ``{path: payload}`` for endpoints that returned
    JSON, plus a ``_errors`` list for endpoints that failed the guard or did not
    parse."""
    from tiktok_account_discovery import SEED_ENDPOINTS

    paths = list(endpoint_paths) if endpoint_paths else list(SEED_ENDPOINTS)
    urls_by_path = dict(urls_by_path or {})
    results = {}
    errors = []

    async def _fetch_all(page):
        for path in paths:
            url = urls_by_path.get(path) or ("https://www.tiktok.com" + path)
            if not is_readonly_allowed(url, "GET"):
                errors.append({"path": path, "reason": "not_readonly_allowed"})
                continue
            try:
                payload = await page.evaluate(
                    """async (url) => {
                        const res = await fetch(url, {
                            credentials: 'include',
                            headers: {'Accept': 'application/json'},
                        });
                        const contentType = res.headers.get('content-type') || '';
                        let body = null;
                        if (contentType.includes('json')) {
                            try { body = await res.json(); } catch (e) { body = null; }
                        }
                        return {status: res.status, contentType, body};
                    }""",
                    url,
                )
            except Exception as error:
                errors.append(
                    {"path": path, "reason": "fetch_failed", "error": type(error).__name__}
                )
                continue
            if payload and payload.get("status") == 200 and payload.get("body") is not None:
                from tiktok_account_inspection import to_plain

                results[path] = to_plain(payload.get("body"))
            else:
                errors.append(
                    {
                        "path": path,
                        "reason": "bad_response",
                        "status": (payload or {}).get("status"),
                    }
                )
        return {"results": results, "errors": errors}

    try:
        value = run_operation(token, _fetch_all, timeout=timeout)
    except SessionSetupError as error:
        return {"results": {}, "errors": [{"reason": "session_error", "error": str(error)}]}
    value = dict(value or {})
    value["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return value


def fetch_tiktok_capabilities(token, requests, timeout=OP_DEFAULT_TIMEOUT * 2):
    """Execute verified read-only capability contracts in the browser context."""
    from readonly_policy import evaluate_request
    from tiktok_endpoint_catalog import endpoint_policy
    from tiktok_account_inspection import to_plain
    from tiktok_account_discovery import extract_payload_keys

    prepared = []
    errors = []
    for request in requests or ():
        spec = endpoint_policy(request.endpoint_id)
        if spec is None:
            errors.append({"capability": request.capability, "endpoint_id": request.endpoint_id, "reason": "unknown_endpoint"})
            continue
        decision = evaluate_request(spec, request.url, request.method, request.body)
        if not decision.allowed:
            errors.append({"capability": request.capability, "endpoint_id": request.endpoint_id, "reason": decision.reason})
            continue
        prepared.append(
            {
                "capability": request.capability,
                "endpointId": request.endpoint_id,
                "method": request.method,
                "url": request.url,
                "body": dict(request.body or {}),
                "headers": dict(request.headers or {}),
                "maxBytes": int(spec.max_response_bytes),
            }
        )

    async def _fetch(page):
        return await page.evaluate(
            """async (requests) => {
                const out = [];
                for (const item of requests) {
                    try {
                        const headers = Object.assign({'Accept': 'application/json'}, item.headers || {});
                        const options = {method: item.method, credentials: 'include', headers};
                        if (item.method === 'POST') {
                            const contentType = String(headers['Content-Type'] || '').toLowerCase();
                            options.body = contentType.includes('x-www-form-urlencoded')
                                ? new URLSearchParams(item.body || {}).toString()
                                : JSON.stringify(item.body || {});
                        }
                        const response = await fetch(item.url, options);
                        const contentType = response.headers.get('content-type') || '';
                        const contentLength = Number(response.headers.get('content-length') || 0);
                        if (contentLength > item.maxBytes) {
                            out.push({capability: item.capability, endpointId: item.endpointId,
                                      status: response.status, contentType, error: 'response_too_large'});
                            continue;
                        }
                        const text = await response.text();
                        if (new TextEncoder().encode(text).length > item.maxBytes) {
                            out.push({capability: item.capability, endpointId: item.endpointId,
                                      status: response.status, contentType, error: 'response_too_large'});
                            continue;
                        }
                        let body = null;
                        try { body = JSON.parse(text); } catch (_) {}
                        out.push({capability: item.capability, endpointId: item.endpointId,
                                  status: response.status, contentType, body,
                                  error: body === null ? 'invalid_json' : ''});
                    } catch (error) {
                        out.push({capability: item.capability, endpointId: item.endpointId,
                                  status: 0, contentType: '', body: null,
                                  error: error && error.name ? error.name : 'fetch_failed'});
                    }
                }
                return out;
            }""",
            prepared,
        )

    if not prepared:
        return {"results": {}, "errors": errors}
    rows = run_operation(token, _fetch, timeout=timeout) or ()
    results = {}
    for raw in rows:
        row = dict(raw or {})
        capability = str(row.get("capability") or "")
        endpoint_id = str(row.get("endpointId") or "")
        status = int(row.get("status") or 0)
        error = str(row.get("error") or "")
        body = row.get("body")
        if status == 200 and not error and body is not None:
            plain_body = to_plain(body)
            results[capability] = {
                "endpoint_id": endpoint_id,
                "status": status,
                "content_type": str(row.get("contentType") or ""),
                "payload": plain_body,
                "payload_keys": list(extract_payload_keys(plain_body)),
            }
        else:
            errors.append(
                {
                    "capability": capability,
                    "endpoint_id": endpoint_id,
                    "status": status,
                    "content_type": str(row.get("contentType") or ""),
                    "reason": error or "bad_response",
                }
            )
    return {"results": results, "errors": errors}
