import http.cookiejar
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from youtube_monitor.cookie_exporter import (
    AUTH_COOKIE_NAMES,
    _format_netscape_row,
    export_youtube_cookies_cdp,
)


class TestCookieExporter(unittest.TestCase):
    def test_format_netscape_row_strict_python_assertion(self):
        now = int(time.time())
        # Case 1: domain starts with dot -> column 2 MUST be TRUE
        c1 = {"domain": ".youtube.com", "name": "SID", "value": "val123", "path": "/", "secure": True, "expires": now + 3600}
        row1 = _format_netscape_row(c1, now)
        self.assertIsNotNone(row1)
        parts1 = row1.split("\t")
        self.assertEqual(parts1[0], ".youtube.com")
        self.assertEqual(parts1[1], "TRUE")

        # Case 2: domain does NOT start with dot -> column 2 MUST be FALSE
        c2 = {"domain": "accounts.google.com", "name": "SSID", "value": "val456", "path": "/", "secure": False, "expires": now + 3600}
        row2 = _format_netscape_row(c2, now)
        self.assertIsNotNone(row2)
        parts2 = row2.split("\t")
        self.assertEqual(parts2[0], "accounts.google.com")
        self.assertEqual(parts2[1], "FALSE")

        # Roundtrip parse with standard http.cookiejar.MozillaCookieJar
        with tempfile.TemporaryDirectory() as td:
            cookie_path = Path(td) / "test_cookies.txt"
            content = f"# Netscape HTTP Cookie File\n\n{row1}\n{row2}\n"
            cookie_path.write_text(content, encoding="utf-8")

            jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
            # Must NOT raise AssertionError (assert domain_specified == initial_dot)
            jar.load(ignore_discard=True, ignore_expires=True)
            loaded_names = {c.name for c in jar}
            self.assertIn("SID", loaded_names)
            self.assertIn("SSID", loaded_names)

    def test_format_netscape_row_sanitizes_bad_characters(self):
        now = int(time.time())
        # Newline or tab in name should be rejected
        c_bad_name = {"domain": ".youtube.com", "name": "BAD\tNAME", "value": "val"}
        self.assertIsNone(_format_netscape_row(c_bad_name, now))

        # Newline in domain rejected
        c_bad_dom = {"domain": ".you\ntube.com", "name": "OK", "value": "val"}
        self.assertIsNone(_format_netscape_row(c_bad_dom, now))

        # Newlines/tabs in value should be rejected to prevent corrupting credentials
        c_dirty_val = {"domain": ".youtube.com", "name": "CLEAN", "value": "val\nwith\ttabs"}
        row = _format_netscape_row(c_dirty_val, now)
        self.assertIsNone(row)

    def test_export_cdp_atomic_success(self):
        with tempfile.TemporaryDirectory() as td:
            profile_dir = Path(td) / "mock_profile"
            profile_dir.mkdir()
            out_file = Path(td) / "exported_cookies.txt"
            fake_chrome = Path(td) / "chrome.exe"
            fake_chrome.touch()

            mock_cookies = [
                {"domain": ".youtube.com", "name": "SID", "value": "auth_sid", "path": "/", "secure": True, "expires": time.time() + 86400},
                {"domain": ".youtube.com", "name": "LOGIN_INFO", "value": "auth_login", "path": "/", "secure": True, "expires": time.time() + 86400},
                {"domain": ".example.com", "name": "OTHER", "value": "ignored", "path": "/", "secure": False, "expires": time.time() + 86400},
            ]

            mock_context = MagicMock()
            mock_context.cookies.return_value = mock_cookies
            mock_page = MagicMock()
            mock_context.pages = [mock_page]

            mock_playwright = MagicMock()
            mock_playwright.chromium.launch_persistent_context.return_value = mock_context
            mock_pw_cm = MagicMock()
            mock_pw_cm.__enter__.return_value = mock_playwright

            with patch("patchright.sync_api.sync_playwright", return_value=mock_pw_cm):
                ok, msg, count = export_youtube_cookies_cdp(
                    profile_dir=profile_dir,
                    output_path=out_file,
                    executable_path=str(fake_chrome),
                    wait_settle_seconds=0,
                )

                self.assertTrue(ok, msg)
                self.assertEqual(count, 2)  # Only 2 youtube cookies
                self.assertTrue(out_file.exists())
                content = out_file.read_text(encoding="utf-8")
                self.assertIn("SID", content)
                self.assertIn("LOGIN_INFO", content)
                self.assertNotIn("OTHER", content)

    def test_export_cdp_fails_when_profile_missing(self):
        ok, msg, count = export_youtube_cookies_cdp("non_existent_folder_xyz", "out.txt")
        self.assertFalse(ok)
        self.assertIn("không tồn tại", msg)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
