# -*- coding: utf-8 -*-
"""
check_features.services.cookie_extractor - Trích xuất Cookie và User-Agent/Fingerprint offline.
Đọc trực tiếp từ file dữ liệu của browser profile (Default/Network/Cookies) KHÔNG CẦN BẬT TRÌNH DUYỆT.
Hỗ trợ cả cookie plaintext (antidetect) lẫn giải mã Chromium DPAPI + AES-256-GCM (Windows).
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional, Tuple
import uuid

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

logger = logging.getLogger("CookieExtractor")

DEFAULT_FALLBACK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def win32_crypt_unprotect_data(encrypted_bytes: bytes) -> Optional[bytes]:
    """Giải mã dữ liệu Windows DPAPI bằng CryptUnprotectData qua ctypes."""
    if not encrypted_bytes:
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        data_in = DATA_BLOB(len(encrypted_bytes), ctypes.cast(ctypes.create_string_buffer(encrypted_bytes), ctypes.POINTER(ctypes.c_byte)))
        data_out = DATA_BLOB()

        if crypt32.CryptUnprotectData(ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out)):
            size = int(data_out.cbData)
            buffer = (ctypes.c_byte * size)()
            ctypes.memmove(buffer, data_out.pbData, size)
            ctypes.windll.kernel32.LocalFree(data_out.pbData)
            return bytes(buffer)
    except Exception as e:
        logger.debug(f"win32_crypt_unprotect_data error: {e}")
    return None


class CookieExtractor:
    """Bộ trích xuất Cookie và Fingerprint offline từ thư mục profile trình duyệt."""

    @staticmethod
    def resolve_profile_dirs(profile_config: Dict[str, Any]) -> Tuple[Optional[Path], Optional[Path]]:
        """
        Xác định đường dẫn thư mục profile data và thư mục cha chứa Local State.
        Trả về (profile_dir, user_data_root).
        """
        candidates: List[Path] = []
        p_path = profile_config.get("browser_profile_path") or profile_config.get("chrome_profile")
        if p_path:
            candidates.append(Path(p_path))

        # Thử tìm theo cấu trúc TikTokManager nếu có
        profile_name = profile_config.get("name") or profile_config.get("tiktok_id") or ""
        if profile_name:
            ttm_path = Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "tiktokmanager" / "profiles" / profile_name
            candidates.append(ttm_path)

        for c in candidates:
            if c.exists() and c.is_dir():
                # Profile dir có thể là Default hoặc chính thư mục đó
                if (c / "Default").exists() and (c / "Default").is_dir():
                    return c / "Default", c
                return c, c.parent

        return None, None

    @staticmethod
    def locate_cookies_db(profile_dir: Path) -> Optional[Path]:
        """Tìm file SQLite Cookies trong thư mục profile."""
        search_paths = [
            profile_dir / "Network" / "Cookies",
            profile_dir / "Default" / "Network" / "Cookies",
            profile_dir / "Cookies",
        ]
        for p in search_paths:
            if p.exists() and p.is_file():
                return p
        return None

    @classmethod
    def get_aes_key_from_local_state(cls, user_data_root: Optional[Path]) -> Optional[bytes]:
        """Đọc và giải mã master key AES-256 từ file Local State."""
        if not user_data_root or not user_data_root.exists():
            return None

        local_state_paths = [
            user_data_root / "Local State",
            user_data_root.parent / "Local State",
        ]

        local_state_file = None
        for p in local_state_paths:
            if p.exists() and p.is_file():
                local_state_file = p
                break

        if not local_state_file:
            return None

        try:
            with open(local_state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            encrypted_key_b64 = state_data.get("os_crypt", {}).get("encrypted_key")
            if not encrypted_key_b64:
                return None

            encrypted_key = base64.b64decode(encrypted_key_b64)
            # Khóa mã hóa bắt đầu bằng tiền tố DPAPI (5 bytes: b'DPAPI')
            if encrypted_key.startswith(b"DPAPI"):
                raw_key = encrypted_key[5:]
                master_key = win32_crypt_unprotect_data(raw_key)
                return master_key
        except Exception as e:
            logger.debug(f"Không thể giải mã AES key từ {local_state_file}: {e}")

        return None

    @classmethod
    def decrypt_cookie_value(cls, encrypted_blob: bytes, aes_key: Optional[bytes]) -> str:
        """Giải mã một giá trị cookie nhị phân Chromium Windows."""
        if not encrypted_blob:
            return ""

        # Trường hợp Chrome 127+ App-Bound Encryption (tiền tố v20)
        if encrypted_blob.startswith(b"v20"):
            logger.warning("Phát hiện App-Bound Encryption (v20). Cần phân loại UNSUPPORTED_ENCRYPTION.")
            return ""

        # Trường hợp Chrome 80+ AES-256-GCM (tiền tố v10 hoặc v11)
        if encrypted_blob.startswith(b"v10") or encrypted_blob.startswith(b"v11"):
            if not aes_key or not AESGCM:
                return ""
            try:
                nonce = encrypted_blob[3:15]
                ciphertext = encrypted_blob[15:]
                aesgcm = AESGCM(aes_key)
                decrypted = aesgcm.decrypt(nonce, ciphertext, None)
                return decrypted.decode("utf-8", errors="replace")
            except Exception as e:
                logger.debug(f"Lỗi giải mã AESGCM cookie: {e}")
                return ""

        # Trường hợp Chrome cũ DPAPI trực tiếp
        try:
            decrypted = win32_crypt_unprotect_data(encrypted_blob)
            if decrypted:
                return decrypted.decode("utf-8", errors="replace")
        except Exception:
            pass

        return ""

    @classmethod
    def extract_cookies(cls, profile_config: Dict[str, Any]) -> Tuple[bool, str, Dict[str, str], str]:
        """
        Trích xuất cookie TikTok trực tiếp từ SQLite Cookies của profile mà KHÔNG mở trình duyệt.
        Trả về (success, cookie_header, cookies_map, error_message).
        """
        profile_dir, user_data_root = cls.resolve_profile_dirs(profile_config)
        if not profile_dir:
            # Fallback: kiểm tra xem trong profile_config đã có cookie_str sẵn chưa
            saved_cookie = profile_config.get("cookie_str") or profile_config.get("cookies") or ""
            if saved_cookie:
                cookies_map = cls.parse_cookie_header(str(saved_cookie))
                return True, str(saved_cookie), cookies_map, "Sử dụng cookie đã lưu trong cấu hình."
            return False, "", {}, "Không tìm thấy thư mục profile dữ liệu trình duyệt."

        cookies_db = cls.locate_cookies_db(profile_dir)
        if not cookies_db:
            # Fallback nếu đã có cookie lưu sẵn
            saved_cookie = profile_config.get("cookie_str") or profile_config.get("cookies") or ""
            if saved_cookie:
                cookies_map = cls.parse_cookie_header(str(saved_cookie))
                return True, str(saved_cookie), cookies_map, "Sử dụng cookie đã lưu trong cấu hình."
            return False, "", {}, f"Không tìm thấy file cơ sở dữ liệu Cookies tại {profile_dir}."

        # Đọc khóa giải mã AES từ Local State (nếu cần)
        aes_key = cls.get_aes_key_from_local_state(user_data_root)

        # Xử lý SQLite: Thử mở trực tiếp, nếu bị khóa (WAL), tạo bản sao an toàn vào %TEMP%
        temp_dir = None
        db_to_open = cookies_db
        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="cookie_read_"))
            temp_db = temp_dir / "Cookies"
            shutil.copy2(cookies_db, temp_db)

            # Sao chép cả WAL, SHM và journal nếu có
            wal_file = cookies_db.parent / (cookies_db.name + "-wal")
            shm_file = cookies_db.parent / (cookies_db.name + "-shm")
            journal_file = cookies_db.parent / (cookies_db.name + "-journal")
            if wal_file.exists():
                shutil.copy2(wal_file, temp_dir / wal_file.name)
            if shm_file.exists():
                shutil.copy2(shm_file, temp_dir / shm_file.name)
            if journal_file.exists():
                shutil.copy2(journal_file, temp_dir / journal_file.name)

            db_to_open = temp_db
        except Exception as e:
            logger.debug(f"Không thể tạo bản sao tạm, mở trực tiếp: {e}")
            db_to_open = cookies_db

        cookies_map: Dict[str, str] = {}
        has_v20_encryption = False

        try:
            # Mở kết nối SQLite read-only với timeout ngắn
            conn = sqlite3.connect(f"file:{db_to_open.resolve()}?mode=ro", uri=True, timeout=5.0)
            cur = conn.cursor()

            query = (
                "SELECT name, value, encrypted_value, host_key "
                "FROM cookies "
                "WHERE host_key LIKE '%.tiktok%'"
            )
            cur.execute(query)
            rows = cur.fetchall()
            conn.close()

            for name, val, enc_val, host_key in rows:
                final_val = ""
                # 1. Plaintext ưu tiên (Antidetect patch)
                if val:
                    final_val = val
                # 2. Giải mã nhị phân
                elif enc_val:
                    if isinstance(enc_val, bytes) and enc_val.startswith(b"v20"):
                        has_v20_encryption = True
                    final_val = cls.decrypt_cookie_value(enc_val, aes_key)

                if final_val:
                    cookies_map[name] = final_val

        except Exception as e:
            logger.warning(f"Lỗi truy vấn SQLite Cookies: {e}")
            return False, "", {}, f"Lỗi đọc cơ sở dữ liệu cookie: {e}"
        finally:
            if temp_dir and temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

        if not cookies_map:
            if has_v20_encryption:
                return False, "", {}, "UNSUPPORTED_ENCRYPTION: Profile sử dụng Chrome App-Bound Encryption (v20)."
            return False, "", {}, "Không tìm thấy cookie TikTok nào trong profile."

        # Xây dựng Cookie Header RFC
        parts = [f"{k}={v}" for k, v in cookies_map.items()]
        cookie_header = "; ".join(parts)

        # Kiểm tra sự hiện diện của sessionid
        if "sessionid" not in cookies_map:
            return True, cookie_header, cookies_map, "Đã đọc cookie TikTok (Cảnh báo: Thiếu sessionid đăng nhập)."

        return True, cookie_header, cookies_map, "Đã trích xuất cookie TikTok thành công."

    @staticmethod
    def parse_cookie_header(cookie_str: str) -> Dict[str, str]:
        """Chuyển chuỗi Cookie Header thành dictionary."""
        res: Dict[str, str] = {}
        if not cookie_str:
            return res
        for item in str(cookie_str).split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                if k.strip():
                    res[k.strip()] = v.strip()
        return res

    @staticmethod
    def extract_user_agent_and_fingerprint(profile_config: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Trích xuất User-Agent và Client-Hints từ cấu hình fingerprint của profile."""
        ua = profile_config.get("user_agent") or ""
        fp = profile_config.get("fingerprint") or {}

        if not ua and isinstance(fp, dict):
            nav = fp.get("navigator") or {}
            ua = nav.get("userAgent") or fp.get("userAgent") or ""

        # Thử đọc từ data.huynhthang nếu có
        profile_path = profile_config.get("browser_profile_path") or profile_config.get("chrome_profile")
        if not ua and profile_path:
            fp_file = Path(profile_path) / "data.huynhthang"
            if fp_file.exists():
                try:
                    with open(fp_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    ua = data.get("navigator", {}).get("userAgent") or data.get("userAgent") or ""
                    if not fp:
                        fp = data
                except Exception:
                    pass

        if not ua:
            ua = DEFAULT_FALLBACK_UA

        return ua, fp
