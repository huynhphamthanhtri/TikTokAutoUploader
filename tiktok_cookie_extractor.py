"""
tiktok_cookie_extractor.py - Trích xuất và giải mã cookie TikTok từ Chrome Profile và Config.

Hỗ trợ chiến lược Hybrid:
1. Đọc Live Cookie trực tiếp từ file SQLite `Default/Network/Cookies` của Profile.
   - Sao chép tạm ra temp file để không bị lỗi xung đột khi Chrome đang chạy.
   - Giải mã DPAPI + AES-GCM (v10) cho Windows.
2. Fallback về chuỗi `cookie_str` trong cấu hình profile nếu profile chưa mở browser.
3. Xuất ra định dạng dict và chuỗi Cookie header chuẩn cho HTTP requests.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Thử import AESGCM từ cryptography
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    AESGCM_AVAILABLE = True
except ImportError:
    AESGCM = None
    AESGCM_AVAILABLE = False


def _dpapi_decrypt(encrypted_data: bytes) -> Optional[bytes]:
    """Giải mã dữ liệu bằng Windows DPAPI qua ctypes (không cần pywin32)."""
    import ctypes
    import ctypes.wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    p_data_in = DATA_BLOB(len(encrypted_data), ctypes.create_string_buffer(encrypted_data, len(encrypted_data)))
    p_data_out = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    if crypt32.CryptUnprotectData(
        ctypes.byref(p_data_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(p_data_out),
    ):
        raw = ctypes.string_at(p_data_out.pbData, p_data_out.cbData)
        ctypes.windll.kernel32.LocalFree(p_data_out.pbData)
        return raw
    return None


def get_master_key(local_state_path: Path) -> Optional[bytes]:
    """Lấy và giải mã Master Key từ file Local State của Chromium."""
    if not local_state_path.exists():
        return None
    try:
        content = json.loads(local_state_path.read_text(encoding="utf-8"))
        os_crypt = content.get("os_crypt", {})
        encrypted_key_b64 = os_crypt.get("encrypted_key")
        if not encrypted_key_b64:
            return None
        encrypted_key = base64.b64decode(encrypted_key_b64)
        if encrypted_key.startswith(b"DPAPI"):
            encrypted_key = encrypted_key[5:]
        return _dpapi_decrypt(encrypted_key)
    except Exception as e:
        logger.debug(f"Không thể đọc master key từ {local_state_path}: {e}")
        return None


def decrypt_cookie_value(encrypted_val: bytes, master_key: Optional[bytes]) -> str:
    """Giải mã giá trị cookie mã hóa v10 / AES-GCM."""
    if not encrypted_val:
        return ""
    if not encrypted_val.startswith(b"v10") and not encrypted_val.startswith(b"v11"):
        try:
            return encrypted_val.decode("utf-8", errors="ignore")
        except Exception:
            return ""

    if not master_key or not AESGCM_AVAILABLE:
        return ""

    try:
        nonce = encrypted_val[3:15]
        ciphertext_with_tag = encrypted_val[15:]
        aesgcm = AESGCM(master_key)
        decrypted = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
        return decrypted.decode("utf-8", errors="ignore")
    except Exception:
        return ""


def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    """Parse chuỗi cookie (hỗ trợ cả JSON Array và chuỗi HTTP header `k1=v1; k2=v2`)."""
    cookies: Dict[str, str] = {}
    if not cookie_str:
        return cookies

    clean_str = cookie_str.strip()

    # 1. Thử parse dạng JSON Array: [{"name": "...", "value": "..."}, ...]
    if clean_str.startswith("[") and clean_str.endswith("]"):
        try:
            items = json.loads(clean_str)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        k = str(item.get("name") or "").strip()
                        v = str(item.get("value") or "").strip()
                        if k and not any(ch in k for ch in '[]{}":;, \t\r\n'):
                            cookies[k] = v
                return cookies
        except Exception:
            pass

    # 2. Parse dạng chuỗi `k1=v1; k2=v2`
    for part in clean_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, val = part.split("=", 1)
        k = key.strip()
        v = val.strip()
        # Loại bỏ các key rác chứa ký tự JSON hoặc không hợp lệ
        if k and not any(ch in k for ch in '[]{}":;, \t\r\n'):
            cookies[k] = v

    return cookies


def sanitize_latin1(s: str) -> str:
    """Loại bỏ các ký tự ngoài bảng mã latin-1 (ord > 255) để tránh lỗi HTTP header encoding."""
    if not s:
        return ""
    return str(s).encode("latin-1", "ignore").decode("latin-1")


def cookies_to_header_str(cookies: Dict[str, str]) -> str:
    """Chuyển dict cookie thành chuỗi Cookie header HTTP hợp lệ và sạch sẽ."""
    parts = []
    for k, v in cookies.items():
        if not k or not v:
            continue
        clean_k = sanitize_latin1(str(k))
        clean_v = sanitize_latin1(str(v))
        if not clean_k or not clean_v:
            continue
        # Bỏ qua các key rác
        if any(ch in clean_k for ch in '[]{}":;, \t\r\n'):
            continue
        parts.append(f"{clean_k}={clean_v}")
    return "; ".join(parts)


def extract_cookies_from_profile_sqlite(profile_dir: Path) -> Dict[str, str]:
    """Trích xuất cookies của TikTok từ file SQLite của profile."""
    cookies_result: Dict[str, str] = {}
    if not profile_dir.is_dir():
        return cookies_result

    # Tìm file Cookies
    possible_cookie_paths = [
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
        profile_dir / "Cookies",
    ]
    cookie_db_path = None
    for p in possible_cookie_paths:
        if p.exists() and p.stat().st_size > 0:
            cookie_db_path = p
            break

    if not cookie_db_path:
        return cookies_result

    # Tìm file Local State
    possible_local_state = [
        profile_dir / "Local State",
        profile_dir.parent / "Local State",
    ]
    master_key = None
    for lp in possible_local_state:
        master_key = get_master_key(lp)
        if master_key:
            break

    # Copy ra temp file để tránh database is locked
    temp_dir = tempfile.gettempdir()
    temp_db_path = Path(temp_dir) / f"tk_cookie_{os.getpid()}_{id(cookie_db_path)}.db"
    try:
        shutil.copy2(cookie_db_path, temp_db_path)
        conn = sqlite3.connect(f"file:{temp_db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        
        rows = cur.execute(
            "SELECT name, value, encrypted_value, host_key FROM cookies "
            "WHERE host_key LIKE '%.tiktok%' OR host_key LIKE '%tiktok.com%'"
        ).fetchall()

        for name, plain_val, enc_val, host_key in rows:
            val = plain_val or ""
            if not val and enc_val:
                val = decrypt_cookie_value(enc_val, master_key)
            if val:
                cookies_result[name] = val

        conn.close()
    except Exception as e:
        logger.debug(f"Lỗi đọc SQLite cookies từ {cookie_db_path}: {e}")
    finally:
        if temp_db_path.exists():
            try:
                temp_db_path.unlink()
            except Exception:
                pass

    return cookies_result


def get_profile_tiktok_cookies(profile_config: Dict[str, Any], workspace_dir: Optional[Path] = None) -> Dict[str, str]:
    """Hàm giao diện chính trích xuất Cookie của profile theo chiến lược Hybrid.
    
    1. Ưu tiên đọc SQLite Live Cookie từ `browser_profile_path`.
    2. Fallback sang `cookie_str` trong cấu hình.
    """
    cookies: Dict[str, str] = {}
    profile_path_str = str(profile_config.get("browser_profile_path") or "").strip()
    chrome_profile_str = str(profile_config.get("chrome_profile") or "").strip()
    profile_name = str(profile_config.get("profile_name") or "").strip()

    candidate_dirs: List[Path] = []
    if profile_path_str:
        candidate_dirs.append(Path(profile_path_str))
    if chrome_profile_str:
        candidate_dirs.append(Path(chrome_profile_str))
    if workspace_dir and profile_name:
        candidate_dirs.append(workspace_dir / "Auto_Data" / profile_name / "Profile-Patchright")
        candidate_dirs.append(workspace_dir / "Auto_Data" / profile_name / "Profile")
        candidate_dirs.append(workspace_dir / "profiles" / profile_name)
        candidate_dirs.append(workspace_dir / "profile" / profile_name)

    for pdir in candidate_dirs:
        if pdir.exists() and pdir.is_dir():
            extracted = extract_cookies_from_profile_sqlite(pdir)
            if extracted:
                cookies.update(extracted)
                if extracted.get("sessionid"):
                    break

    # Fallback cấu hình
    config_cookie_str = str(profile_config.get("cookie_str") or profile_config.get("cookie") or "").strip()
    if config_cookie_str:
        cfg_cookies = parse_cookie_string(config_cookie_str)
        # Giữ lại cookie lấy từ SQLite (nếu có), bổ sung những gì thiếu từ config
        for k, v in cfg_cookies.items():
            if k not in cookies:
                cookies[k] = v

    return cookies


def extract_user_id_from_cookies(cookies: Dict[str, str], raw_header: str = "") -> Optional[str]:
    r"""Trích xuất User ID từ cookies hoặc header theo regex `UserId:(\d+)~`."""
    # Tìm trong raw_header hoặc cookie values
    combined = raw_header + " " + " ".join(f"{k}={v}" for k, v in cookies.items())
    m = re.search(r"UserId:(\d+)~", combined)
    if m:
        return m.group(1)

    for key in ("uid", "my_uid", "user_id"):
        if cookies.get(key) and cookies[key].isdigit():
            return cookies[key]

    return None


def check_profile_api_readiness(
    profile_config: Dict[str, Any],
    workspace_dir: Optional[Path] = None,
) -> Tuple[bool, str, Dict[str, str]]:
    """Kiểm tra Mức 1 (Offline Check): Xác minh tính sẵn sàng của profile cho Direct API Upload.
    
    Tiêu chuẩn kiểm tra hoàn toàn cục bộ (Zero Network / Zero Spam Footprint):
    1. Trích xuất cookie từ file SQLite của profile và cấu hình profile (< 15ms).
    2. Kiểm tra sự tồn tại của cookie 'sessionid' (hoặc 'sessionid_ss').
    3. Trả về (is_ready, message, cookies).
    """
    try:
        cookies = get_profile_tiktok_cookies(profile_config, workspace_dir)
        if not cookies:
            return False, "Chưa tìm thấy cookie nào trong profile hoặc cấu hình", {}

        sessionid = cookies.get("sessionid") or cookies.get("sessionid_ss")
        if not sessionid:
            return False, "Thiếu cookie 'sessionid' (Profile chưa đăng nhập TikTok)", cookies

        user_id = extract_user_id_from_cookies(cookies)
        uid_desc = f" (User: {user_id})" if user_id else ""
        return True, f"Sẵn sàng (Đã xác nhận sessionid hợp lệ{uid_desc})", cookies
    except Exception as e:
        return False, f"Lỗi đọc cookie nội bộ: {e}", {}

