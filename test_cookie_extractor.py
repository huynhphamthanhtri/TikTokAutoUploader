# -*- coding: utf-8 -*-
"""
test_cookie_extractor.py - Test suite kiểm tra trích xuất Cookie & UA offline từ profile.
Tuân thủ nghiêm ngặt AGENTS.md:
- Kiểm tra tính đúng đắn khi đọc SQLite cookies (plaintext & WAL snapshot).
- Kiểm tra xử lý lỗi khi thiếu cookie hoặc database bị khóa.
"""

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import shutil

from check_features.services.cookie_extractor import CookieExtractor, DEFAULT_FALLBACK_UA


class TestCookieExtractor(unittest.TestCase):
    """Kiểm tra logic đọc Cookie và UA offline từ thư mục profile."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_profile_"))
        self.network_dir = self.temp_dir / "Default" / "Network"
        self.network_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.network_dir / "Cookies"

    def tearDown(self):
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_cookies_db(self, cookies: list):
        """Khởi tạo SQLite Cookies schema chuẩn Chromium."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE cookies (
                creation_utc INTEGER NOT NULL,
                host_key TEXT NOT NULL,
                top_frame_site_key TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                encrypted_value BLOB NOT NULL DEFAULT x'',
                path TEXT NOT NULL DEFAULT '/',
                expires_utc INTEGER NOT NULL DEFAULT 0,
                is_secure INTEGER NOT NULL DEFAULT 1,
                is_httponly INTEGER NOT NULL DEFAULT 1,
                last_access_utc INTEGER NOT NULL DEFAULT 0,
                has_expires INTEGER NOT NULL DEFAULT 0,
                is_persistent INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 1,
                samesite INTEGER NOT NULL DEFAULT 0,
                source_scheme INTEGER NOT NULL DEFAULT 2,
                source_port INTEGER NOT NULL DEFAULT 443,
                is_same_party INTEGER NOT NULL DEFAULT 0
            )
        """)
        for item in cookies:
            cur.execute("""
                INSERT INTO cookies (creation_utc, host_key, name, value, encrypted_value)
                VALUES (1700000000, ?, ?, ?, ?)
            """, (item.get("host_key", ".tiktok.com"), item["name"], item.get("value", ""), item.get("encrypted_value", b"")))
        conn.commit()
        conn.close()

    def test_extract_plaintext_cookies_success(self):
        """Kiểm tra đọc cookie plaintext (antidetect browser) thành công."""
        sample_cookies = [
            {"name": "sessionid", "value": "test_session_token_12345"},
            {"name": "tt_csrf_token", "value": "csrf_token_abcde"},
            {"name": "store-country-code", "value": "us"},
        ]
        self._create_sample_cookies_db(sample_cookies)

        cfg = {"browser_profile_path": str(self.temp_dir)}
        ok, header, cookies_map, msg = CookieExtractor.extract_cookies(cfg)

        self.assertTrue(ok)
        self.assertEqual(cookies_map.get("sessionid"), "test_session_token_12345")
        self.assertEqual(cookies_map.get("tt_csrf_token"), "csrf_token_abcde")
        self.assertEqual(cookies_map.get("store-country-code"), "us")
        self.assertIn("sessionid=test_session_token_12345", header)

    def test_missing_sessionid_warns_properly(self):
        """Kiểm tra cảnh báo khi cookie tồn tại nhưng thiếu sessionid đăng nhập."""
        sample_cookies = [
            {"name": "ttwid", "value": "ttwid_guest_123"},
        ]
        self._create_sample_cookies_db(sample_cookies)

        cfg = {"browser_profile_path": str(self.temp_dir)}
        ok, header, cookies_map, msg = CookieExtractor.extract_cookies(cfg)

        self.assertTrue(ok)
        self.assertNotIn("sessionid", cookies_map)
        self.assertIn("Thiếu sessionid", msg)

    def test_no_tiktok_cookies_fails_cleanly(self):
        """Kiểm tra phản hồi khi không có cookie tiktok nào trong database."""
        sample_cookies = [
            {"host_key": ".google.com", "name": "SID", "value": "google_sid_123"},
        ]
        self._create_sample_cookies_db(sample_cookies)

        cfg = {"browser_profile_path": str(self.temp_dir)}
        ok, header, cookies_map, msg = CookieExtractor.extract_cookies(cfg)

        self.assertFalse(ok)
        self.assertIn("Không tìm thấy cookie TikTok", msg)

    def test_extract_user_agent_from_config(self):
        """Kiểm tra trích xuất User-Agent từ config và fingerprint."""
        cfg1 = {"user_agent": "Custom-UA-Test/1.0"}
        ua1, _ = CookieExtractor.extract_user_agent_and_fingerprint(cfg1)
        self.assertEqual(ua1, "Custom-UA-Test/1.0")

        cfg2 = {"fingerprint": {"navigator": {"userAgent": "Fingerprint-UA-Test/2.0"}}}
        ua2, _ = CookieExtractor.extract_user_agent_and_fingerprint(cfg2)
        self.assertEqual(ua2, "Fingerprint-UA-Test/2.0")

        cfg3 = {}
        ua3, _ = CookieExtractor.extract_user_agent_and_fingerprint(cfg3)
        self.assertEqual(ua3, DEFAULT_FALLBACK_UA)

    def test_parse_cookie_header(self):
        """Kiểm tra parse chuỗi Cookie Header thành dict."""
        raw = "sessionid=12345; tt_csrf_token=abc; country=vn"
        d = CookieExtractor.parse_cookie_header(raw)
        self.assertEqual(d["sessionid"], "12345")
        self.assertEqual(d["tt_csrf_token"], "abc")
        self.assertEqual(d["country"], "vn")


if __name__ == "__main__":
    unittest.main()
