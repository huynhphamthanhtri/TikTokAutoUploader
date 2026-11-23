import unittest
from unittest.mock import MagicMock, patch

import main


class TestBatchProxyIntegration(unittest.TestCase):
    def setUp(self):
        self.mock_profiles = {
            "prof_1": {
                "running": False,
                "driver": None,
                "manual_driver": None,
                "config": {
                    "use_proxy": True,
                    "proxy_type": "http",
                    "proxy_string": "1.1.1.1:8080:user:pass",
                    "region": "US",
                    "fingerprint": {"geo_country_code": "US"},
                },
                "ui": {"proxy": "Chưa kiểm tra"},
            },
            "prof_2": {
                "running": False,
                "driver": None,
                "manual_driver": None,
                "config": {
                    "use_proxy": False,
                    "proxy_type": "http",
                    "proxy_string": "",
                    "region": "",
                    "fingerprint": {},
                },
                "ui": {"proxy": "Tắt"},
            },
            "prof_running": {
                "running": True,
                "driver": MagicMock(),
                "manual_driver": None,
                "config": {
                    "use_proxy": True,
                    "proxy_type": "http",
                    "proxy_string": "2.2.2.2:8080",
                    "region": "VN",
                    "fingerprint": {},
                },
                "ui": {"proxy": "Chưa kiểm tra"},
            },
        }

    @patch("main._selected_profile_names")
    @patch("main.messagebox.showinfo")
    def test_batch_set_proxy_empty_selection(self, mock_info, mock_selected):
        mock_selected.return_value = []
        with patch.object(main, "_license_guard", return_value=True):
            main.batch_set_proxy()
            mock_info.assert_called_once()

    @patch("main._selected_profile_names")
    @patch("main.messagebox.showwarning")
    def test_batch_set_proxy_all_running(self, mock_warning, mock_selected):
        mock_selected.return_value = ["prof_running"]
        with patch.object(main, "_license_guard", return_value=True), \
             patch.object(main, "profiles", self.mock_profiles):
            main.batch_set_proxy()
            mock_warning.assert_called_once()
            self.assertIn("đang chạy", mock_warning.call_args[0][1])

    @patch("main._selected_profile_names")
    @patch("main.BatchSetProxyModal", create=True)
    def test_batch_set_proxy_save_contract(self, mock_modal_cls, mock_selected):
        mock_selected.return_value = ["prof_1", "prof_2"]
        saved_on_save_cb = None

        def capture_init(*args, **kwargs):
            nonlocal saved_on_save_cb
            saved_on_save_cb = kwargs.get("on_save")
            return MagicMock()

        with patch("ui_dialogs.BatchSetProxyModal", side_effect=capture_init), \
             patch.object(main, "_license_guard", return_value=True), \
             patch.object(main, "profiles", self.mock_profiles), \
             patch.object(main, "save_configs") as mock_save, \
             patch.object(main, "update_profile_list") as mock_update, \
             patch.object(main, "request_profile_refresh"):

            main.batch_set_proxy()
            self.assertIsNotNone(saved_on_save_cb)

            # Simulate saving mapping from modal
            mapping = {
                "prof_1": {
                    "use_proxy": True,
                    "proxy_type": "socks5",
                    "proxy_string": "9.9.9.9:1080",
                    "region": "JP",
                    "status_badge": "🟢 Live [JP]",
                },
                "prof_2": {
                    "use_proxy": False,
                    "proxy_type": "http",
                    "proxy_string": "",
                },
            }
            res = saved_on_save_cb(mapping)
            self.assertTrue(res)

            # Assert updated configs
            self.assertEqual(self.mock_profiles["prof_1"]["config"]["proxy_type"], "socks5")
            self.assertEqual(self.mock_profiles["prof_1"]["config"]["proxy_string"], "9.9.9.9:1080")
            self.assertEqual(self.mock_profiles["prof_1"]["config"]["region"], "JP")
            self.assertEqual(self.mock_profiles["prof_1"]["ui"]["proxy"], "🟢 Live [JP]")

            self.assertFalse(self.mock_profiles["prof_2"]["config"]["use_proxy"])
            self.assertEqual(self.mock_profiles["prof_2"]["ui"]["proxy"], "Tắt")

            mock_save.assert_called_once()
            mock_update.assert_called_once()

    @patch("main._selected_profile_names")
    @patch("main.messagebox.showinfo")
    def test_batch_check_selected_proxies_no_proxy_enabled(self, mock_info, mock_selected):
        mock_selected.return_value = ["prof_2"]
        with patch.object(main, "_license_guard", return_value=True), \
             patch.object(main, "profiles", self.mock_profiles), \
             patch.object(main, "update_profile_list"):
            main.batch_check_selected_proxies()
            mock_info.assert_called_once()
            self.assertEqual(self.mock_profiles["prof_2"]["ui"]["proxy"], "Tắt")

    @patch("main._selected_profile_names")
    def test_batch_check_selected_proxies_live_and_die(self, mock_selected):
        mock_selected.return_value = ["prof_1"]
        mock_probe_results = {
            "1.1.1.1:8080:user:pass": {
                "ok": True,
                "exit_ip": "1.1.1.1",
                "latency_ms": 110,
                "geo": {"country_code": "SG", "country": "Singapore"},
                "error": "",
            }
        }

        with patch.object(main, "_license_guard", return_value=True), \
             patch.object(main, "profiles", self.mock_profiles), \
             patch("proxy_diagnostics.batch_probe_proxies", return_value=mock_probe_results), \
             patch.object(main, "save_configs") as mock_save, \
             patch.object(main, "update_profile_list") as mock_update, \
             patch.object(main, "request_profile_refresh"), \
             patch.object(main, "update_status"), \
             patch.object(main.root, "after", side_effect=lambda delay, func: func()):

            main.batch_check_selected_proxies()

            self.assertIn("Live", self.mock_profiles["prof_1"]["ui"]["proxy"])
            self.assertEqual(self.mock_profiles["prof_1"]["config"]["region"], "SG")
            self.assertEqual(
                self.mock_profiles["prof_1"]["config"]["fingerprint"]["geo_country_code"], "SG"
            )
            mock_save.assert_called_once()
            mock_update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
