"""
test_cookie_check_enhancements.py - Unit tests for Fast HTTP cookie check, concurrency & UI contracts.
"""

import unittest
from unittest.mock import MagicMock, patch

from cookie_live_check import (
    CookieCheckState,
    CookieSource,
    CookieCheckMode,
    CookieCheckResult,
    check_cookie_fast_http,
    build_summary,
)


class TestCookieCheckEnhancements(unittest.TestCase):
    @patch("requests.Session.get")
    def test_fast_http_check_live_via_webcast(self, mock_get):
        """Webcast API returns status 200 and balance data -> LIVE."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 0,
            "data": {"balance": 100.0}
        }
        mock_get.return_value = mock_resp

        state, detail, auth_names = check_cookie_fast_http(
            "sessionid=valid_token; sid_tt=token2;",
            proxy_cfg={"use_proxy": False},
        )
        self.assertEqual(state, CookieCheckState.LIVE)
        self.assertIn("Live", detail)
        self.assertIn("sessionid", auth_names)
        self.assertIn("sid_tt", auth_names)

    @patch("requests.Session.get")
    def test_fast_http_check_dead_via_401(self, mock_get):
        """HTTP 401 response -> DEAD."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        state, detail, auth_names = check_cookie_fast_http(
            "sessionid=expired_token;",
            proxy_cfg=None,
        )
        self.assertEqual(state, CookieCheckState.DEAD)
        self.assertIn("401", detail)

    @patch("requests.Session.get")
    def test_fast_http_check_dead_via_status_20003(self, mock_get):
        """Webcast returns 200 but status_code is 20003 (not logged in) -> DEAD."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 20003,
            "status_message": "not logged in"
        }
        mock_get.return_value = mock_resp

        state, detail, auth_names = check_cookie_fast_http(
            "sessionid=expired_token;",
            proxy_cfg=None,
        )
        self.assertEqual(state, CookieCheckState.DEAD)
        self.assertIn("20003", detail)

    @patch("requests.Session.get")
    def test_fast_http_check_proxy_error(self, mock_get):
        """Proxy connection failure -> PROXY_ERROR."""
        import requests
        mock_get.side_effect = requests.exceptions.ProxyError("Cannot connect to proxy 1.2.3.4:8080")

        state, detail, auth_names = check_cookie_fast_http(
            "sessionid=valid_token;",
            proxy_cfg={"use_proxy": True, "proxy_string": "1.2.3.4:8080"},
        )
        self.assertEqual(state, CookieCheckState.PROXY_ERROR)
        self.assertIn("Proxy", detail)

    def test_fast_http_check_missing_cookie(self):
        """Empty or missing cookie string -> DEAD."""
        state, detail, auth_names = check_cookie_fast_http("", None)
        self.assertEqual(state, CookieCheckState.DEAD)
        self.assertIn("Không có chuỗi cookie", detail)

    def test_cookie_check_result_mode_field(self):
        """CookieCheckResult stores mode field properly."""
        res_fast = CookieCheckResult(
            account_uuid="uuid-1",
            profile_name="Profile 1",
            state=CookieCheckState.LIVE,
            mode=CookieCheckMode.HTTP_FAST,
        )
        self.assertEqual(res_fast.mode, CookieCheckMode.HTTP_FAST)

        res_full = CookieCheckResult(
            account_uuid="uuid-2",
            profile_name="Profile 2",
            state=CookieCheckState.LIVE,
            mode=CookieCheckMode.BROWSER_FULL,
        )
        self.assertEqual(res_full.mode, CookieCheckMode.BROWSER_FULL)

    def test_cookie_check_dialog_ui_contract(self):
        """CookieCheckDialog initializes properly with clean UI tokens."""
        import customtkinter as ctk
        import main

        root = ctk.CTk()
        root.withdraw()
        try:
            targets = [('AUTO 18', 'test-uuid-1'), ('AUTO 6', 'test-uuid-2')]
            dialog = main.CookieCheckDialog(targets)
            self.assertTrue(dialog.dialog.winfo_exists())
            self.assertEqual(dialog.mode_var.get(), "HTTP_FAST")
            self.assertEqual(dialog.workers_var.get(), "3 luồng")
            dialog.dialog.destroy()
        finally:
            try:
                for after_id in root.tk.eval('after info').split():
                    try:
                        root.after_cancel(after_id)
                    except Exception:
                        pass
                root.update_idletasks()
                root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
