"""
tiktok_signer_bridge.py - Cầu nối Python điều khiển tiktok_signer_worker.js

Tự động tìm kiếm binary Node.js:
1. PyInstaller bundled: sys._MEIPASS / patchright / driver / node.exe
2. Package patchright: patchright/driver/node.exe
3. System PATH: shutil.which('node')

Cung cấp hàm sign_tiktok_url() trả về (x_bogus, x_gnarly).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

logger = logging.getLogger(__name__)

_CURRENT_DIR = Path(__file__).resolve().parent


def get_worker_js_path() -> Path:
    if hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "tiktok_signer_worker.js"
        if p.is_file():
            return p
    return _CURRENT_DIR / "tiktok_signer_worker.js"


def find_node_executable() -> Optional[str]:
    """Tìm đường dẫn binary Node.js khả dụng trên máy."""
    # 1. PyInstaller bundled path
    if hasattr(sys, "_MEIPASS"):
        bundled_node = Path(sys._MEIPASS) / "patchright" / "driver" / "node.exe"
        if bundled_node.is_file():
            return str(bundled_node)

    # 2. Patchright internal driver node
    try:
        import patchright
        pr_node = Path(patchright.__file__).resolve().parent / "driver" / "node.exe"
        if pr_node.is_file():
            return str(pr_node)
    except Exception:
        pass

    # 3. System PATH
    system_node = shutil.which("node")
    if system_node:
        return system_node

    return None


def read_profile_fingerprint(profile_dir: Optional[Union[Path, str]]) -> Optional[Dict[str, Any]]:
    """Đọc file data.huynhthang hoặc data.orbita nếu có trong profile."""
    if not profile_dir:
        return None
    p = Path(profile_dir)
    if not p.is_dir():
        return None
    profile_dir = p
    for name in ("data.huynhthang", "data.orbita"):
        fp_path = profile_dir / name
        if fp_path.is_file():
            try:
                return json.loads(fp_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


def sign_tiktok_url(
    url: str,
    body: Optional[str] = None,
    profile_path: Optional[str | Path] = None,
    user_agent: Optional[str] = None,
    timeout: float = 10.0,
) -> Tuple[str, str]:
    """Ký URL với X-Bogus và X-Gnarly (hỗ trợ cả GET và POST có body).
    
    Trả về: (x_bogus, x_gnarly)
    Nếu không ký được, raise RuntimeError.
    """
    node_exe = find_node_executable()
    if not node_exe:
        raise RuntimeError("Không tìm thấy Node.js binary (cả trong patchright/driver và PATH hệ thống)")

    worker_js = get_worker_js_path()
    if not worker_js.is_file():
        raise RuntimeError(f"Không tìm thấy worker script tại: {worker_js}")

    pdir = Path(profile_path) if profile_path else None
    fingerprint = read_profile_fingerprint(pdir)

    payload = {
        "url": url,
        "body": body,
        "fingerprint": fingerprint,
        "user_agent": user_agent or "",
    }

    try:
        # Ẩn console window trên Windows nếu có
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        proc = subprocess.Popen(
            [node_exe, str(worker_js)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=creationflags,
        )

        stdout, stderr = proc.communicate(input=json.dumps(payload), timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"Worker process lỗi (code {proc.returncode}): {stderr.strip()}")

        data = json.loads(stdout.strip() or "{}")
        if not data.get("success"):
            raise RuntimeError(f"Lỗi ký MSSDK: {data.get('error', 'unknown error')}")

        x_bogus = str(data.get("x_bogus") or "")
        x_gnarly = str(data.get("x_gnarly") or "")
        return x_bogus, x_gnarly

    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError(f"Ký X-Bogus/X-Gnarly quá thời gian {timeout}s") from None
    except Exception as e:
        logger.error(f"Lỗi sign_tiktok_url: {e}")
        raise
