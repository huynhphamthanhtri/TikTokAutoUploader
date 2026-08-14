import unittest
from pathlib import Path


class ProxyConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.source = (root / "main.py").read_text(encoding="utf-8")
        cls.glue_source = (root / "browser_patchright_glue.py").read_text(encoding="utf-8")

    def test_proxy_uses_patchright_native_configuration_only(self):
        combined_source = self.source + self.glue_source
        self.assertNotIn("--gologing_proxy_server_", combined_source)
        self.assertNotIn("--gologin", combined_source)
        self.assertNotIn("--load-extension=", combined_source)
        self.assertNotIn("orbita-proxy-auth", combined_source)

        start = self.glue_source.index("def build_session_config")
        end = self.glue_source.index("def open_session", start)
        setup_source = self.glue_source[start:end]
        self.assertIn('"server": "{}://{}:{}".format(scheme, host, port)', setup_source)
        self.assertIn('scheme = "socks5" if proxy_type == "socks5" else "http"', setup_source)
        self.assertIn('native["username"] = proxy_data["user"]', setup_source)
        self.assertIn('native["password"] = proxy_data["pass"]', setup_source)
        self.assertIn('kwargs["proxy"] = proxy', setup_source)

    def test_each_new_browser_is_verified_without_process_cache(self):
        self.assertNotIn("PROXY_OK_CACHE", self.source)
        self.assertIn("Đang check IP trên browser mới", self.source)
        self.assertEqual(self.source.count("browser_glue.verify_exit_ip("), 3)

        cookie_flow = self.source[
            self.source.index("def _capture_tiktok_cookies_worker"):
            self.source.index("def get_tiktok_cookies")
        ]
        automation_flow = self.source[
            self.source.index("def ensure_driver"):
            self.source.index("def upload_video")
        ]
        manual_flow = self.source[
            self.source.index("def open_browser"):
            self.source.index("def _wait_and_close_driver")
        ]
        for flow in (cookie_flow, automation_flow, manual_flow):
            self.assertIn("open_session", flow)
            self.assertIn("verify_exit_ip", flow)

    def test_unknown_browser_ip_is_not_labeled_as_concrete_mismatch(self):
        self.assertIn("Proxy Verification Indeterminate", self.source)
        self.assertIn("proxy='Không xác minh được'", self.source)
        self.assertIn("else f\"Proxy sai IP: {current_ip}\"", self.source)
        self.assertIn("proxy='Không xác minh được' if not current_ip else 'Sai IP'", self.source)

    def test_invalid_proxy_configuration_fails_closed(self):
        self.assertGreaterEqual(
            self.source.count("Proxy sai định dạng; từ chối mở browser trực tiếp"),
            3,
        )
        self.assertIn("raise SessionSetupError(\"Proxy sai định dạng; từ chối mở browser\")", self.glue_source)
        self.assertNotIn('proxy_data = None\n        if config.get("use_proxy", False)', self.glue_source)

    def test_rotating_exit_is_distinguished_from_direct_ip(self):
        self.assertEqual(self.source.count("current_ip != direct_ip"), 3)
        self.assertIn("Proxy exit IP thay đổi", self.source)


if __name__ == "__main__":
    unittest.main()
