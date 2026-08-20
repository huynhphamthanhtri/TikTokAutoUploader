"""yt-dlp updater for the YouTube Monitor.

Frozen-safe and non-destructive by design:
- In dev (non-frozen) mode, the bundled ``yt-dlp`` pip package can be upgraded on
  demand by the user (never automatically).
- In frozen builds the bundled library cannot be changed at runtime, so we only
  CHECK for a newer version and report that a rebuild is required.

This module never downloads anything by itself unless the user explicitly triggers
an update, and it never modifies application files in a frozen environment.
"""

import subprocess
import sys
from typing import Tuple


def get_ytdlp_version() -> str:
    try:
        from yt_dlp.version import __version__
        return str(__version__ or "")
    except Exception:
        try:
            from yt_dlp import version
            return str(getattr(version, "__version__", "") or "")
        except Exception:
            return "?"


def check_latest_ytdlp(timeout: int = 15) -> Tuple[str, str, bool, str]:
    """Return (current, latest, has_update, error)."""
    current = get_ytdlp_version()
    latest = ""
    has_update = False
    try:
        import requests
        resp = requests.get("https://pypi.org/pypi/yt-dlp/json", timeout=timeout)
        if resp.status_code != 200:
            return current, latest, has_update, f"PyPI HTTP {resp.status_code}"
        data = resp.json()
        latest = (data.get("info") or {}).get("version") or ""
        try:
            from packaging.version import Version
            if current and latest and Version(latest) > Version(current):
                has_update = True
        except Exception:
            has_update = bool(current and latest and current != latest)
        return current, latest, has_update, ""
    except Exception as e:
        return current, latest, has_update, str(e)


def update_ytdlp() -> Tuple[bool, str]:
    """Upgrade the bundled yt-dlp pip package.

    Refuses to run in frozen builds (a rebuild is required). Runs only when the
    user explicitly invokes this - never automatically.
    """
    if getattr(sys, "frozen", False):
        return False, (
            "Bản chạy đã đóng gói (frozen) không thể tự cập nhật yt-dlp. "
            "Hãy chờ bản build mới."
        )
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True, text=True, timeout=180,
        )
    except Exception as e:
        return False, f"Lỗi chạy pip: {e}"
    if proc.returncode == 0:
        return True, f"Đã cập nhật yt-dlp lên {get_ytdlp_version()}."
    tail = (proc.stderr or proc.stdout or "").strip()[-500:]
    return False, f"pip lỗi: {tail or 'không rõ'}"