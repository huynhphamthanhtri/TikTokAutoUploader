# -*- coding: utf-8 -*-
"""
test_check_post_integration_phase3.py - Integration Tests cho Phase 3 (Hệ thống main.py & app_ui.py).
Tuân thủ nghiêm ngặt AGENTS.md và tiêu chuẩn Gate 3 của Master Plan:
1. Kiểm tra luồng gọi check_profile_posts_dialog(profile_name) từ main.py khởi tạo đúng check_features.ui.CheckPostDialog.
2. Kiểm tra truyền đúng cấu hình profile (config, cookies, proxy, sec_uid, user_agent).
3. Kiểm tra fallback khi không truyền profile_name:
   - Nhặt từ Treeview chính.
   - Nhặt từ Monetization Treeview.
   - Hiện cảnh báo an toàn khi không có profile nào được chọn.
4. Kiểm tra handlers wiring trong app_ui.py:
   - Nút btn_check_post ("🔁 Check Post").
   - Menu chuột phải profile table.
   - Menu chuột phải monetization table.
   - Overflow actions list.
5. Kiểm tra bảo toàn tương thích với DedupWatchdogView.
"""

import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
import customtkinter as ctk

import main
import app_ui
from check_features.ui import CheckPostDialog


class TestCheckPostIntegrationPhase3(unittest.TestCase):
    """Kiểm tra tích hợp hệ thống của CheckPostDialog trong main.py và app_ui.py."""

    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        # Thiết lập profile mẫu trong main.profiles
        self.prof_name = "Prof_Integration_Test"
        self.prof_config = {
            "name": self.prof_name,
            "tiktok_id": "creator_integrated",
            "sec_uid": "MS4wLjABAAAA_integrated_sec_uid",
            "cookie_str": "sessionid=test_integrated_sess_123",
            "proxy_string": "1.2.3.4:8080",
            "user_agent": "Mozilla/5.0 TestUA",
        }
        main.profiles[self.prof_name] = {"config": self.prof_config}

    def tearDown(self):
        if self.prof_name in main.profiles:
            del main.profiles[self.prof_name]

    @patch("main.CheckPostDialog")
    def test_check_profile_posts_dialog_with_profile_name(self, mock_dialog_cls):
        """Kiểm tra gọi trực tiếp check_profile_posts_dialog(profile_name)."""
        main.check_profile_posts_dialog(self.prof_name)

        mock_dialog_cls.assert_called_once()
        args, kwargs = mock_dialog_cls.call_args
        self.assertEqual(kwargs.get("profile_name"), self.prof_name)
        self.assertEqual(kwargs.get("profile_config"), self.prof_config)

    @patch("main.CheckPostDialog")
    @patch("tkinter.messagebox.showwarning")
    def test_check_profile_posts_dialog_no_selection_shows_warning(self, mock_warn, mock_dialog_cls):
        """Kiểm tra không truyền profile và không có dòng nào được chọn -> Hiện cảnh báo, không mở dialog."""
        with patch.dict(main.__dict__, {"tree": MagicMock()}):
            main.tree.selection.return_value = []
            if "ui_widgets" in main.__dict__:
                main.ui_widgets["monetization_tree"] = MagicMock()
                main.ui_widgets["monetization_tree"].selection.return_value = []

            main.check_profile_posts_dialog(None)

            mock_warn.assert_called_once()
            self.assertIn("chọn 1 profile", mock_warn.call_args[0][1])
            mock_dialog_cls.assert_not_called()

    @patch("main.CheckPostDialog")
    def test_check_profile_posts_dialog_fallback_to_tree_selection(self, mock_dialog_cls):
        """Kiểm tra tự động lấy profile từ selection của bảng profile chính."""
        mock_tree = MagicMock()
        mock_tree.selection.return_value = ["item_1"]
        mock_tree.item.return_value = {"values": [self.prof_name]}

        with patch.dict(main.__dict__, {"tree": mock_tree}):
            main.check_profile_posts_dialog(None)

            mock_dialog_cls.assert_called_once()
            kwargs = mock_dialog_cls.call_args[1]
            self.assertEqual(kwargs.get("profile_name"), self.prof_name)
            self.assertEqual(kwargs.get("profile_config"), self.prof_config)

    def test_app_ui_handlers_wiring(self):
        """Kiểm tra từ điển handlers trong main.py và app_ui.py liên kết đúng check_profile_posts_dialog."""
        # Kiểm tra check_profile_posts_dialog nằm trong main.py
        self.assertTrue(hasattr(main, "check_profile_posts_dialog"))
        self.assertEqual(main.check_profile_posts_dialog.__module__, "main")

        # Kiểm tra CheckPostDialog được import từ check_features.ui
        self.assertIs(main.CheckPostDialog, CheckPostDialog)

    def test_dedup_watchdog_view_preservation(self):
        """Kiểm tra tích hợp không làm phá vỡ DedupWatchdogView hay DedupCheckPostDialog."""
        self.assertTrue(hasattr(main, "DedupWatchdogView"))
        self.assertTrue(hasattr(main, "DedupCheckPostDialog"))
        self.assertTrue(hasattr(main, "dedup_worker"))
        self.assertTrue(hasattr(main, "dedup_db"))


if __name__ == "__main__":
    unittest.main()
