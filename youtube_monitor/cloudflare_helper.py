"""
youtube_monitor/cloudflare_helper.py - Module quản lý binary cloudflared.exe:
Định vị, kiểm tra tính toàn vẹn và tự động tải ngầm từ GitHub Releases của Cloudflare.
"""

import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

import requests

# Pinned stable release chống lỗi gateway/transient 504 của GitHub latest tag (Codex Redteam Finding #1)
CLOUDFLARE_PINNED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/download/2026.10.0/cloudflared-windows-amd64.exe"
)
CLOUDFLARE_LATEST_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
)
MIN_CLOUDFLARED_SIZE = 15 * 1024 * 1024  # Tối thiểu 15MB cho cloudflared.exe
_install_lock = threading.Lock()


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_default_bin_path() -> Path:
    return _app_root() / "bin" / "cloudflared.exe"


def is_valid_executable(path: Path) -> bool:
    """Kiểm tra file thực thi hợp lệ (tồn tại, dung lượng đủ và có PE header 'MZ')."""
    try:
        if not path.is_file() or path.stat().st_size < MIN_CLOUDFLARED_SIZE:
            return False
        with open(path, "rb") as f:
            header = f.read(2)
            if sys.platform == "win32" and header != b"MZ":
                return False
        return True
    except Exception:
        return False


def find_cloudflared() -> Optional[Path]:
    """Tìm binary cloudflared.exe theo thứ tự ưu tiên: bin/, _internal/, app_root, PATH."""
    candidates = [
        get_default_bin_path(),
        _app_root() / "_internal" / "cloudflared.exe",
        _app_root() / "cloudflared.exe",
    ]
    for c in candidates:
        if is_valid_executable(c):
            return c

    which_path = shutil.which("cloudflared") or shutil.which("cloudflared.exe")
    if which_path:
        wp = Path(which_path)
        if is_valid_executable(wp):
            return wp

    return None


def get_cloudflared_bin_path() -> Optional[str]:
    p = find_cloudflared()
    return str(p) if p else None


def check_cloudflared() -> tuple[bool, str, Optional[str]]:
    p = find_cloudflared()
    if p:
        return True, f"Tìm thấy cloudflared.exe tại {p}", str(p)
    return False, "Chưa cài đặt cloudflared.exe", None


def ensure_cloudflared(
    status_callback: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> Optional[Path]:
    """Đảm bảo cloudflared.exe sẵn sàng. Nếu chưa có, tự động tải ngầm về bin/cloudflared.exe."""
    def _emit(msg: str, pct: float = 0.0) -> None:
        if status_callback:
            try:
                status_callback(msg)
            except Exception:
                pass
        if progress_callback:
            try:
                progress_callback(msg, max(0.0, min(1.0, float(pct))))
            except Exception:
                pass

    existing = find_cloudflared()
    if existing:
        _emit("Đã sẵn sàng cloudflared.exe.", 1.0)
        return existing

    with _install_lock:
        existing = find_cloudflared()
        if existing:
            _emit("Đã sẵn sàng cloudflared.exe.", 1.0)
            return existing

        target = get_default_bin_path()
        target.parent.mkdir(parents=True, exist_ok=True)

        _emit("[Cloudflare] Đang tải binary cloudflared.exe lần đầu từ Cloudflare...", 0.05)

        urls_to_try = [CLOUDFLARE_PINNED_URL, CLOUDFLARE_LATEST_URL]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VibeAutoUpload/2.0"}

        for url in urls_to_try:
            tmp_path = None
            try:
                with requests.get(url, stream=True, headers=headers, timeout=90, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    total_bytes = int(resp.headers.get("content-length", 0))
                    downloaded = 0

                    with tempfile.NamedTemporaryFile(dir=str(target.parent), delete=False, suffix=".tmp") as tmp_file:
                        tmp_path = Path(tmp_file.name)
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if not chunk:
                                continue
                            tmp_file.write(chunk)
                            downloaded += len(chunk)
                            if total_bytes > 0:
                                pct_float = downloaded / total_bytes
                                pct_int = int(pct_float * 100)
                                _emit(
                                    f"[Cloudflare] Đang tải cloudflared.exe: {pct_int}% ({downloaded // (1024*1024)}MB/{total_bytes // (1024*1024)}MB)",
                                    pct_float,
                                )

                if not is_valid_executable(tmp_path):
                    raise ValueError(f"File tải về không hợp lệ (size={tmp_path.stat().st_size} bytes)")

                os.replace(tmp_path, target)
                tmp_path = None

                _emit(f"[Cloudflare] Đã cài đặt cloudflared.exe thành công ({target.stat().st_size // (1024*1024)}MB).", 1.0)

                return target
            except Exception as e:
                _emit(f"[Cloudflare] Lỗi tải từ {url}: {e}. Thử mirror tiếp theo...", 0.05)
            finally:
                if tmp_path and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass

        if status_callback:
            status_callback("[Cloudflare] Thất bại khi tải binary từ tất cả mirror.")
        return None
