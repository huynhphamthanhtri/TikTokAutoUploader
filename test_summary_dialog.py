# -*- coding: utf-8 -*-
"""
test_summary_dialog.py - Kiểm thử hộp thoại báo cáo CheckInfoSummaryDialog.
Tuân thủ nghiêm ngặt AGENTS.md:
- Kiểm tra khởi tạo UI đầy đủ và không ném ngoại lệ.
- Kiểm tra định dạng báo cáo copy clipboard và tính năng sanitize dữ liệu nhạy cảm.
- Kiểm tra callback kích hoạt chọn profile trên bảng.
- Kiểm tra trường hợp 100% thành công.
"""

import unittest
from unittest.mock import MagicMock
import customtkinter as ctk

from check_features.ui.summary_dialog import CheckInfoSummaryDialog, sanitize_secret_text


class TestCheckInfoSummaryDialog(unittest.TestCase):
    """Kiểm tra component UI CheckInfoSummaryDialog."""

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

    def test_sanitize_secret_text(self):
        """Kiểm tra làm sạch credentials proxy và sessionid."""
        raw_err = "Cannot connect to http://user123:secretPass99@108.165.83.187:7150 with sessionid=abcd1234efgh5678"
        cleaned = sanitize_secret_text(raw_err)
        self.assertNotIn("secretPass99", cleaned)
        self.assertNotIn("abcd1234efgh5678", cleaned)
        self.assertIn("://***:***@", cleaned)
        self.assertIn("sessionid=***", cleaned)

    def test_summary_dialog_with_failed_items(self):
        """Kiểm tra hiển thị dialog khi có nhiều loại lỗi khác nhau."""
        mock_summary = {
            "total": 28,
            "success": 25,
            "partial": 0,
            "not_logged_in": 1,
            "expired": 2,
            "proxy_error": 0,
            "error": 0,
            "failed_items": [
                {
                    "profile": "BKT TEAM T10 02",
                    "status": "NOT_LOGGED_IN",
                    "auth_state": "NOT_LOGGED_IN",
                    "status_badge": "⚪ Chưa đăng nhập",
                    "error": "Chưa đăng nhập TikTok (Thiếu cookie phiên sessionid).",
                    "suggested_action": "Mở profile và đăng nhập TikTok trên trình duyệt.",
                },
                {
                    "profile": "DE_16_5 27",
                    "status": "SESSION_INVALID",
                    "auth_state": "EXPIRED",
                    "status_badge": "🟠 Cần đăng nhập lại",
                    "error": "Phiên đăng nhập không còn hợp lệ trên máy chủ TikTok (Mã 20001: user not found).",
                    "suggested_action": "Đăng nhập lại tài khoản trên trình duyệt.",
                },
            ],
        }

        selected_profiles = []
        def on_select(names):
            selected_profiles.extend(names)

        dlg = CheckInfoSummaryDialog(self.root, summary=mock_summary, on_select_profiles=on_select)

        # 1. Kiểm tra tồn tại các nút hành động
        self.assertTrue(hasattr(dlg, "btn_copy"))
        self.assertTrue(hasattr(dlg, "btn_select_table"))

        # 2. Kiểm tra kích hoạt callback chọn profile trên bảng
        dlg._trigger_select_on_table()
        self.assertEqual(selected_profiles, ["BKT TEAM T10 02", "DE_16_5 27"])

        # 3. Kiểm tra logic copy clipboard
        dlg.clipboard_clear = MagicMock()
        dlg.clipboard_append = MagicMock()
        dlg._copy_error_report()
        dlg.clipboard_clear.assert_called_once()
        dlg.clipboard_append.assert_called_once()
        copied_text = dlg.clipboard_append.call_args[0][0]
        self.assertIn("BKT TEAM T10 02", copied_text)
        self.assertIn("DE_16_5 27", copied_text)
        self.assertIn("20001", copied_text)

        dlg.destroy()

    def test_summary_dialog_all_success(self):
        """Kiểm tra hiển thị dialog khi toàn bộ profile thành công."""
        mock_summary = {
            "total": 10,
            "success": 10,
            "partial": 0,
            "not_logged_in": 0,
            "expired": 0,
            "proxy_error": 0,
            "error": 0,
            "failed_items": [],
        }

        dlg = CheckInfoSummaryDialog(self.root, summary=mock_summary)
        self.assertFalse(hasattr(dlg, "btn_select_table"))
        dlg.destroy()


if __name__ == "__main__":
    unittest.main()
