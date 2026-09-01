"""
test_tls_client_engine.py - Unit test suite for TLS Network Engine (curl_cffi & fallback).
"""

import unittest
from unittest.mock import MagicMock, patch

from tls_client_engine import (
    CURL_CFFI_AVAILABLE,
    DEFAULT_IMPERSONATE,
    TLSSessionWrapper,
    create_tls_session,
    format_proxy_url,
)


class TestTLSClientEngine(unittest.TestCase):
    """Kiểm thử tính đúng đắn và độ bền vững của TLS Client Engine."""

    def test_format_proxy_url_none(self):
        self.assertIsNone(format_proxy_url(None))
        self.assertIsNone(format_proxy_url({}))
        self.assertIsNone(format_proxy_url({"use_proxy": False, "proxy_string": "1.2.3.4:8080"}))

    def test_format_proxy_url_http_no_auth(self):
        cfg = {"use_proxy": True, "proxy_string": "123.45.67.89:8080", "proxy_type": "http"}
        url = format_proxy_url(cfg)
        self.assertEqual(url, "http://123.45.67.89:8080")

    def test_format_proxy_url_socks5_with_auth(self):
        cfg = {"use_proxy": True, "proxy_string": "user1:pass123@123.45.67.89:1080", "proxy_type": "socks5"}
        url = format_proxy_url(cfg)
        self.assertEqual(url, "socks5://user1:pass123@123.45.67.89:1080")

    def test_create_tls_session_default(self):
        session = create_tls_session(impersonate=DEFAULT_IMPERSONATE)
        self.assertIsInstance(session, TLSSessionWrapper)
        self.assertEqual(session.impersonate, "chrome124")
        if CURL_CFFI_AVAILABLE:
            self.assertTrue(session.using_curl_cffi)
        session.close()

    def test_create_tls_session_with_proxy(self):
        cfg = {"use_proxy": True, "proxy_string": "proxy_user:proxy_pass@1.1.1.1:8080", "proxy_type": "http"}
        session = create_tls_session(impersonate="chrome120", proxy_cfg=cfg)
        self.assertEqual(session.proxy_url, "http://proxy_user:proxy_pass@1.1.1.1:8080")
        session.close()

    def test_graceful_fallback_when_curl_fails(self):
        """Kiểm tra nếu curl_cffi văng exception thì tự động retry an toàn bằng requests."""
        session = create_tls_session(impersonate="chrome124", enable_fallback=True)
        # Giả lập lỗi ở _session.request
        with patch.object(session._session, "request", side_effect=Exception("Curl simulated error")):
            with patch("requests.Session.request") as mock_fallback_request:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_fallback_request.return_value = mock_resp

                resp = session.get("https://httpbin.org/get")
                self.assertEqual(resp.status_code, 200)
                mock_fallback_request.assert_called_once()
        session.close()


if __name__ == "__main__":
    unittest.main()
