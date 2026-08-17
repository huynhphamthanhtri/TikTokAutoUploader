"""
youtube_monitor/ngrok_helper.py - Helper module to locate, verify and install ngrok binary.
"""

import io
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path
from typing import Callable, Optional, Tuple

import requests
from pyngrok import conf as ngconf
from pyngrok import ngrok


_ngrok_install_lock = threading.Lock()
OFFICIAL_NGROK_CDN_WIN64 = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


NGROK_APP_EXE = _app_root() / "ngrok.exe"


def _bundled_ngrok() -> Optional[Path]:
    p = Path(_app_root() / "_internal" / "ngrok.exe")
    return p if p.exists() else None


def _pyngrok_cache_ngrok() -> Optional[Path]:
    try:
        default_path = ngconf.get_default().ngrok_path
        if default_path and Path(default_path).exists():
            return Path(default_path)
    except Exception:
        pass

    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "pyngrok" / "bin" / "ngrok.exe",
        Path.home() / ".ngrok2" / "ngrok.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def find_ngrok() -> Optional[Path]:
    """Find ngrok.exe across App root, _internal, pyngrok cache, and System PATH."""
    bundled = _bundled_ngrok()
    if bundled:
        return bundled
    if NGROK_APP_EXE.exists() and NGROK_APP_EXE.stat().st_size > 0:
        return NGROK_APP_EXE
    cache = _pyngrok_cache_ngrok()
    if cache and cache.stat().st_size > 0:
        return cache
    which = shutil.which("ngrok")
    if which and Path(which).exists():
        return Path(which)
    return None


def ngrok_source() -> str:
    """Returns a readable description of where ngrok.exe is located."""
    if _bundled_ngrok():
        return "Bundled"
    if NGROK_APP_EXE.exists() and NGROK_APP_EXE.stat().st_size > 0:
        return "App dir"
    cache = _pyngrok_cache_ngrok()
    if cache and cache.stat().st_size > 0:
        return "pyngrok cache"
    p = shutil.which("ngrok")
    if p:
        return "System PATH"
    return ""


def check_ngrok() -> Tuple[bool, str, str]:
    """Returns (is_available, status_message, source)."""
    p = find_ngrok()
    if p:
        src = ngrok_source()
        return True, f"Ngrok sẵn sàng ({src})", src
    return False, "Chưa cài đặt ngrok", ""


def get_ngrok_bin_path() -> Optional[str]:
    p = find_ngrok()
    return str(p) if p else None


def ensure_ngrok(
    progress_callback: Optional[Callable[[str, float], None]] = None,
    target_exe: Optional[Path] = None,
) -> Tuple[bool, str]:
    """
    Downloads and installs ngrok.exe if missing.
    Uses pyngrok.install_ngrok() with direct Equinox CDN stream fallback.
    """
    dest = target_exe or NGROK_APP_EXE
    if find_ngrok():
        return True, f"Ngrok đã sẵn sàng tại {find_ngrok()}"

    with _ngrok_install_lock:
        if find_ngrok():
            return True, f"Ngrok đã sẵn sàng tại {find_ngrok()}"

        if progress_callback:
            progress_callback("Đang tải ngrok từ CDN chính thức...", 0.1)

        # Method 1: pyngrok.install_ngrok
        try:
            cfg = ngconf.PyngrokConfig(ngrok_path=str(dest))
            ngrok.install_ngrok(pyngrok_config=cfg)
            if dest.exists() and dest.stat().st_size > 1024 * 1024:
                if progress_callback:
                    progress_callback("Đã cài đặt ngrok thành công.", 1.0)
                return True, f"Đã cài đặt ngrok tại {dest}"
        except Exception:
            pass

        # Method 2: Direct Equinox CDN download
        try:
            if progress_callback:
                progress_callback("Đang tải ngrok từ Equinox CDN...", 0.3)
            resp = requests.get(OFFICIAL_NGROK_CDN_WIN64, stream=True, timeout=30)
            if resp.status_code == 200:
                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                buf = io.BytesIO()
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        buf.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total > 0:
                            progress_callback(
                                f"Đang tải ngrok ({downloaded / (1024 * 1024):.1f} MB)...",
                                0.3 + 0.6 * (downloaded / total),
                            )

                buf.seek(0)
                if progress_callback:
                    progress_callback("Đang giải nén ngrok.exe...", 0.95)

                with zipfile.ZipFile(buf, "r") as zf:
                    for member in zf.infolist():
                        if member.filename.lower().endswith("ngrok.exe") or member.filename.lower() == "ngrok":
                            with zf.open(member) as zf_file, open(dest, "wb") as out_file:
                                shutil.copyfileobj(zf_file, out_file)
                            break

                if dest.exists() and dest.stat().st_size > 1024 * 1024:
                    if progress_callback:
                        progress_callback("Đã cài đặt ngrok thành công.", 1.0)
                    return True, f"Đã tải và cài đặt ngrok tại {dest}"
        except Exception as e:
            return False, f"Không thể tải ngrok từ CDN: {e}"

        return False, "Không thể tải hoặc xác minh ngrok.exe"
