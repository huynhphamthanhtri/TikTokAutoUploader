import http.cookiejar
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from youtube_monitor.core import (
    AUTO_YOUTUBE_COOKIES_PATH,
    FAILURE_AUTH_REQUIRED,
    FAILURE_HTTP_403,
    _build_attempt_plan,
    _resolve_cookies_file,
    register_cookie_update_listener,
    unregister_cookie_update_listener,
    _notify_cookie_updated,
)
from youtube_monitor.cookie_exporter import (
    _format_netscape_row,
    export_youtube_cookies_cdp,
)


class TestCookieFormatCompliance(unittest.TestCase):
    def test_python_cookiejar_assertion_strict_compliance(self):
        now = int(time.time())
        # Domain with initial dot
        row_dot = _format_netscape_row(
            {"domain": ".youtube.com", "name": "SID", "value": "val_1", "path": "/", "secure": True, "expires": now + 3600},
            now,
        )
        self.assertIsNotNone(row_dot)
        self.assertTrue(row_dot.startswith(".youtube.com\tTRUE\t"))

        # Domain without initial dot
        row_nodot = _format_netscape_row(
            {"domain": "accounts.google.com", "name": "SSID", "value": "val_2", "path": "/", "secure": False, "expires": now + 3600},
            now,
        )
        self.assertIsNotNone(row_nodot)
        self.assertTrue(row_nodot.startswith("accounts.google.com\tFALSE\t"))

        # Parse with standard library MozillaCookieJar
        with tempfile.TemporaryDirectory() as td:
            cookie_file = Path(td) / "test_netscape.txt"
            content = f"# Netscape HTTP Cookie File\n\n{row_dot}\n{row_nodot}\n"
            cookie_file.write_text(content, encoding="utf-8")

            jar = http.cookiejar.MozillaCookieJar(str(cookie_file))
            # Must not raise AssertionError
            jar.load(ignore_discard=True, ignore_expires=True)
            names = {c.name for c in jar}
            self.assertIn("SID", names)
            self.assertIn("SSID", names)


class TestCookieResolutionAndPrecedence(unittest.TestCase):
    def test_configured_path_has_highest_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            custom_cookie = Path(td) / "custom.txt"
            custom_cookie.write_text("# Netscape\n", encoding="utf-8")
            auto_cookie = Path(td) / "auto.txt"
            auto_cookie.write_text("# Netscape\n", encoding="utf-8")

            with patch("youtube_monitor.core.get_config", return_value={"cookies_file": str(custom_cookie)}):
                with patch("youtube_monitor.core.AUTO_YOUTUBE_COOKIES_PATH", auto_cookie):
                    resolved = _resolve_cookies_file()
                    self.assertEqual(resolved, str(custom_cookie))

    def test_auto_cookies_path_fallback_when_configured_empty(self):
        with tempfile.TemporaryDirectory() as td:
            auto_cookie = Path(td) / "youtube_cookies.txt"
            auto_cookie.write_text("# Netscape\n", encoding="utf-8")

            with patch("youtube_monitor.core.get_config", return_value={"cookies_file": ""}):
                with patch("youtube_monitor.core.AUTO_YOUTUBE_COOKIES_PATH", auto_cookie):
                    resolved = _resolve_cookies_file()
                    self.assertEqual(resolved, str(auto_cookie))


class TestCookieFirstAttemptPlan(unittest.TestCase):
    def test_cookie_first_when_cookies_available(self):
        cfg = {"youtube_cookie_policy": "primary", "youtube_proxy_fallback": False}
        empty_pool = MagicMock()
        empty_pool.pick_accounts_for_download.return_value = []
        with patch("youtube_monitor.core.get_config", return_value=cfg), \
             patch("youtube_monitor.core.get_cookie_pool_manager", return_value=empty_pool):
            with patch("youtube_monitor.core._resolve_cookies_file", return_value="C:/path/cookies.txt"):
                attempts = _build_attempt_plan("test_profile")
                self.assertEqual(attempts[0].name, "direct-cookies")
                self.assertTrue(attempts[0].use_cookies)
                self.assertEqual(attempts[0].route, "direct")
                # Fallbacks remain present
                names = [a.name for a in attempts]
                self.assertIn("direct-primary", names)
                self.assertIn("direct-alt-format", names)

    def test_anonymous_first_when_policy_fallback(self):
        cfg = {"youtube_cookie_policy": "fallback", "youtube_proxy_fallback": False}
        with patch("youtube_monitor.core.get_config", return_value=cfg):
            with patch("youtube_monitor.core._resolve_cookies_file", return_value="C:/path/cookies.txt"):
                attempts = _build_attempt_plan("test_profile")
                self.assertEqual(attempts[0].name, "direct-primary")
                self.assertFalse(attempts[0].use_cookies)
                self.assertTrue(any(a.name == "direct-cookies" for a in attempts))

    def test_no_cookies_when_policy_never(self):
        cfg = {"youtube_cookie_policy": "never", "youtube_proxy_fallback": False}
        with patch("youtube_monitor.core.get_config", return_value=cfg):
            with patch("youtube_monitor.core._resolve_cookies_file", return_value="C:/path/cookies.txt"):
                attempts = _build_attempt_plan("test_profile")
                self.assertFalse(any(a.use_cookies for a in attempts))


class TestCookieUpdateListener(unittest.TestCase):
    def test_listener_registration_and_notification(self):
        called_with = []
        def listener(path):
            called_with.append(path)

        register_cookie_update_listener(listener)
        try:
            _notify_cookie_updated("C:/test/path.txt")
            self.assertEqual(called_with, ["C:/test/path.txt"])
        finally:
            unregister_cookie_update_listener(listener)

        _notify_cookie_updated("C:/another.txt")
        self.assertEqual(len(called_with), 1)


class TestPostDownloadResilience(unittest.TestCase):
    def test_temp_mp4_renaming_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            staging = Path(td) / "staging"
            staging.mkdir()
            temp_file = staging / "video.temp.mp4"
            temp_file.write_bytes(b"0" * 2048)

            target_mp4 = temp_file.with_name("video.mp4")
            self.assertFalse(target_mp4.exists())

            # Simulate recovery logic
            temp_candidates = list(staging.rglob("*.temp.mp4"))
            for tf in temp_candidates:
                tgt = tf.with_name(tf.name.replace(".temp.mp4", ".mp4"))
                tf.rename(tgt)

            self.assertTrue(target_mp4.exists())
            self.assertEqual(target_mp4.stat().st_size, 2048)

    def test_webm_container_detection(self):
        with tempfile.TemporaryDirectory() as td:
            staging = Path(td) / "staging"
            staging.mkdir()
            webm_file = staging / "video.webm"
            webm_file.write_bytes(b"0" * 4096)

            # Scanner searches for multiple containers
            found_path = None
            for ext in ("*.mp4", "*.webm", "*.mkv", "*.m4v"):
                candidates = [p for p in staging.rglob(ext) if not p.name.endswith(".temp.mp4") and p.stat().st_size > 1024]
                if candidates:
                    found_path = str(candidates[0])
                    break

            self.assertEqual(found_path, str(webm_file))


if __name__ == "__main__":
    unittest.main()
