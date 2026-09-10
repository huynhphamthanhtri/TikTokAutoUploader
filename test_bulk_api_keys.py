import unittest
from unittest.mock import MagicMock, patch

from youtube_monitor.core import (
    parse_api_keys_text,
    add_api_keys_bulk_to_pool,
    api_key_manager,
)
from youtube_monitor.key_manager import ApiKeyManager


class TestBulkApiKeys(unittest.TestCase):
    def test_parse_api_keys_text_various_formats(self):
        sample = """
        AIzaSyA1234567890abcdef
        AIzaSyB1234567890abcdef, 'AIzaSyC1234567890abcdef'; "AIzaSyD1234567890abcdef"
        [AIzaSyE1234567890abcdef]
        AIzaSyA1234567890abcdef  # duplicate
        """
        keys = parse_api_keys_text(sample)
        self.assertEqual(
            keys,
            [
                "AIzaSyA1234567890abcdef",
                "AIzaSyB1234567890abcdef",
                "AIzaSyC1234567890abcdef",
                "AIzaSyD1234567890abcdef",
                "AIzaSyE1234567890abcdef",
            ],
        )

    def test_parse_api_keys_text_empty(self):
        self.assertEqual(parse_api_keys_text(""), [])
        self.assertEqual(parse_api_keys_text("   \n\n\t  "), [])
        self.assertEqual(parse_api_keys_text(None), [])

    def test_parse_api_keys_from_list(self):
        items = ["KEY_1, KEY_2", "KEY_3", "KEY_1"]
        self.assertEqual(parse_api_keys_text(items), ["KEY_1", "KEY_2", "KEY_3"])

    def test_add_bulk_fast_mode(self):
        fake_cfg = {"api_keys": ["KEY_EXISTING"], "api_keys_pool": [{"key": "KEY_EXISTING"}]}
        test_mgr = ApiKeyManager(["KEY_EXISTING"])

        with patch("youtube_monitor.core.get_config", return_value=fake_cfg), \
             patch("youtube_monitor.core._save_config") as mock_save, \
             patch("youtube_monitor.core.api_key_manager", test_mgr):

            input_text = "KEY_EXISTING\nKEY_NEW_1\nKEY_NEW_2\nKEY_NEW_1"
            res = add_api_keys_bulk_to_pool(input_text, test_before_add=False)

            self.assertEqual(res["total_input"], 3)  # after deduplicating input: KEY_EXISTING, KEY_NEW_1, KEY_NEW_2
            self.assertEqual(res["added"], ["KEY_NEW_1", "KEY_NEW_2"])
            self.assertEqual(res["duplicate"], ["KEY_EXISTING"])
            self.assertEqual(res["invalid"], [])

            mock_save.assert_called_once()
            self.assertIn("KEY_NEW_1", fake_cfg["api_keys"])
            self.assertIn("KEY_NEW_2", fake_cfg["api_keys"])

    def test_add_bulk_verified_mode_with_invalid_keys(self):
        fake_cfg = {"api_keys": [], "api_keys_pool": []}
        test_mgr = ApiKeyManager()

        def mock_test(key):
            if key == "KEY_VALID":
                return True, "OK"
            return False, "Key không hợp lệ"

        progress_calls = []

        def on_progress(idx, total, key, ok, msg):
            progress_calls.append((idx, total, key, ok, msg))

        with patch("youtube_monitor.core.get_config", return_value=fake_cfg), \
             patch("youtube_monitor.core._save_config"), \
             patch("youtube_monitor.core.api_key_manager", test_mgr), \
             patch.object(test_mgr, "test_key", side_effect=mock_test):

            raw = "KEY_VALID\nKEY_BAD"
            res = add_api_keys_bulk_to_pool(raw, test_before_add=True, progress_cb=on_progress)

            self.assertEqual(res["added"], ["KEY_VALID"])
            self.assertEqual(len(res["invalid"]), 1)
            self.assertEqual(res["invalid"][0]["key"], "KEY_BAD")
            self.assertEqual(len(progress_calls), 2)
            self.assertTrue(progress_calls[0][3])
            self.assertFalse(progress_calls[1][3])

    def test_concurrent_tick_does_not_wipe_keys(self):
        fake_cfg = {"api_keys": [], "api_keys_pool": []}
        test_mgr = ApiKeyManager()

        with patch("youtube_monitor.core.get_config", return_value=fake_cfg), \
             patch("youtube_monitor.core._save_config"), \
             patch("youtube_monitor.core.api_key_manager", test_mgr):

            from youtube_monitor.core import get_api_keys_pool_status
            initial_status = get_api_keys_pool_status()
            self.assertEqual(initial_status, [])

            res = add_api_keys_bulk_to_pool("KEY_CONCURRENT_1\nKEY_CONCURRENT_2", test_before_add=False)
            self.assertEqual(res["added"], ["KEY_CONCURRENT_1", "KEY_CONCURRENT_2"])

            final_status = get_api_keys_pool_status()
            final_keys = [item["key"] for item in final_status]
            self.assertIn("KEY_CONCURRENT_1", final_keys)
            self.assertIn("KEY_CONCURRENT_2", final_keys)

    def test_test_all_api_keys_in_pool_summary(self):
        from youtube_monitor.core import test_all_api_keys_in_pool

        fake_cfg = {"api_keys": ["KEY_ACT", "KEY_ERR"], "api_keys_pool": [{"key": "KEY_ACT"}, {"key": "KEY_ERR"}]}
        test_mgr = ApiKeyManager(["KEY_ACT", "KEY_ERR"])

        def mock_test(key):
            if key == "KEY_ACT":
                return True, "Hợp lệ"
            return False, "Không hợp lệ"

        progress_items = []

        def on_progress(cur, tot, k, status, msg):
            progress_items.append((cur, tot, k, status, msg))

        with patch("youtube_monitor.core.get_config", return_value=fake_cfg), \
             patch("youtube_monitor.core._save_config") as mock_save, \
             patch("youtube_monitor.core.api_key_manager", test_mgr), \
             patch.object(test_mgr, "test_key", side_effect=mock_test):

            summary = test_all_api_keys_in_pool(progress_cb=on_progress)
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["active"], 1)
            self.assertEqual(summary["invalid"] + summary["error"], 1)
            self.assertEqual(len(progress_items), 2)
            mock_save.assert_called_once()


class TestBulkApiKeyModal(unittest.TestCase):
    def setUp(self):
        import customtkinter as ctk
        try:
            self.root = ctk.CTk()
            self.root.withdraw()
        except Exception:
            self.root = None

    def tearDown(self):
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass

    def test_modal_widgets_and_count_preview(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

        from youtube_monitor.polling_ui import BulkApiKeyModal

        modal = BulkApiKeyModal(self.root)
        try:
            self.assertIsNotNone(modal.text_input)
            self.assertIsNotNone(modal.count_lbl)
            self.assertIsNotNone(modal.test_chk)
            self.assertIsNotNone(modal.btn_cancel)
            self.assertIsNotNone(modal.btn_import)

            # Insert sample keys
            modal.text_input.insert("1.0", "KEY_AAA, KEY_BBB\nKEY_CCC")
            detected = modal._update_count()
            self.assertEqual(detected, ["KEY_AAA", "KEY_BBB", "KEY_CCC"])
            self.assertIn("3", modal.count_lbl.cget("text"))

            # Empty text validation
            modal.text_input.delete("1.0", "end")
            modal._start_import()
            self.assertIn("Vui lòng nhập", modal.status_lbl.cget("text"))
        finally:
            modal.destroy()

    def test_unmasked_keys_and_test_all_button(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

        from youtube_monitor.polling_ui import ApiPollingView
        import customtkinter as ctk

        sample_status = [
            {"key": "AIzaSyFULL_UNMASKED_KEY_12345", "status": "ACTIVE"}
        ]
        with patch("youtube_monitor.core.get_api_keys_pool_status", return_value=sample_status), \
             patch("youtube_monitor.core.get_config", return_value={"predictive_polling": {"poll_interval_seconds": 1.0, "window_before_minutes": 10.0, "window_after_minutes": 10.0, "stop_after_detection": True}}):
            view = ApiPollingView(self.root)
            try:
                self.assertTrue(hasattr(view, "btn_test_all"))
                self.assertIn("Kiểm tra toàn bộ", view.btn_test_all.cget("text"))

                view._refresh_keys()
                # Check children in keys_frame
                rows = view.keys_frame.winfo_children()
                self.assertEqual(len(rows), 1)
                labels = [w for w in rows[0].winfo_children() if isinstance(w, ctk.CTkLabel)]
                buttons = [w for w in rows[0].winfo_children() if isinstance(w, ctk.CTkButton)]

                self.assertTrue(any("AIzaSyFULL_UNMASKED_KEY_12345" in lbl.cget("text") for lbl in labels))
                # Individual "Kiểm tra" button is removed, only "Xóa" is present
                self.assertEqual(len(buttons), 1)
                self.assertEqual(buttons[0].cget("text"), "Xóa")
            finally:
                view.destroy()


if __name__ == "__main__":
    unittest.main()


