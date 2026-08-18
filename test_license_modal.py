"""
test_license_modal.py - Unit tests for LicenseModal and silent background authentication.
"""

import unittest
from unittest.mock import MagicMock, patch
import customtkinter as ctk

from ui_dialogs import LicenseModal
import main


class TestLicenseModal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.LICENSE_OK = True
        cls.root = main.root

    def test_license_modal_prefills_existing_key(self):
        mock_check = MagicMock()
        modal = LicenseModal(
            parent=self.root,
            check_func=mock_check,
            on_success=MagicMock(),
            initial_key="USER-1234-ABCD",
            is_first_run=False,
        )
        try:
            self.assertEqual(modal.key_var.get(), "USER-1234-ABCD")
            self.assertEqual(modal.btn_close.cget("text"), "Đóng")
        finally:
            modal.destroy()

    def test_license_modal_successful_activation(self):
        mock_check = MagicMock(return_value=(True, {"status": "ACTIVE", "expiry": "2026-12-31"}, "License hợp lệ."))
        on_success = MagicMock()

        modal = LicenseModal(
            parent=self.root,
            check_func=mock_check,
            on_success=on_success,
            initial_key="USER-VALID-KEY",
            is_first_run=True,
        )
        try:
            modal._do_activate()
            mock_check.assert_called_once_with("USER-VALID-KEY")
            on_success.assert_called_once_with("USER-VALID-KEY", {"status": "ACTIVE", "expiry": "2026-12-31"})
            self.assertIn("thành công", modal.msg_var.get())
        finally:
            modal.destroy()

    def test_license_modal_failed_activation(self):
        mock_check = MagicMock(return_value=(False, {}, "License đã hết hạn."))
        on_success = MagicMock()

        modal = LicenseModal(
            parent=self.root,
            check_func=mock_check,
            on_success=on_success,
            initial_key="USER-EXPIRED-KEY",
            is_first_run=True,
        )
        try:
            modal._do_activate()
            mock_check.assert_called_once_with("USER-EXPIRED-KEY")
            on_success.assert_not_called()
            self.assertIn("License đã hết hạn", modal.msg_var.get())
        finally:
            modal.destroy()

    def test_silent_auto_boot_when_cached_key_valid(self):
        with patch("main._load_license_cache", return_value={"key": "USER-SAVED-KEY"}), \
             patch("main.check_license_online_or_cache", return_value=(True, {"status": "ACTIVE"}, "OK")), \
             patch("main._license_dialog") as mock_dialog:
            
            # Reset license state
            main.LICENSE_OK = False
            main.LICENSE_KEY = None
            
            main.require_license_then_boot()
            self.assertTrue(main.LICENSE_OK)
            self.assertEqual(main.LICENSE_KEY, "USER-SAVED-KEY")
            # Ensure dialog was NOT opened
            mock_dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
