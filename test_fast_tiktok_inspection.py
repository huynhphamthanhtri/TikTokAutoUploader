"""
test_fast_tiktok_inspection.py - Unit and Integration Tests for Fast HTTP TikTok Account & Monetization Inspection.
"""

import unittest
from unittest.mock import MagicMock, patch
import customtkinter as ctk
import main


class TestFastTikTokInspection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            for after_id in cls.root.tk.eval('after info').split():
                try:
                    cls.root.after_cancel(after_id)
                except Exception:
                    pass
            cls.root.update_idletasks()
            cls.root.destroy()
        except Exception:
            pass

    def test_inspect_worker_non_existent_profile(self):
        res = main._inspect_tiktok_account_worker("__non_existent_profile_xyz__")
        self.assertEqual(res["status"], "ERROR")
        self.assertIn("không tồn tại", res.get("error", "").lower())

    @patch("tiktok_monetization_client.TikTokMonetizationClient.fetch_all_monetization_data")
    def test_inspect_worker_success_and_sync(self, mock_fetch):
        mock_fetch.return_value = {
            "status": "SUCCESS",
            "profile_name": "Test Profile",
            "unique_id": "test_user_99",
            "tiktok_user_id": "7447972851385451523",
            "nickname": "Test Nickname",
            "follower_count": 12500,
            "heart_count": 54000,
            "video_count": 88,
            "balance": 150.50,
            "currency_symbol": "$",
            "crp_display": "🟢 KIẾM TIỀN",
            "kyc_status": "APPROVED",
            "tax_status": "TAX_VERIFIED",
            "payment_method": "Bank Transfer (***1234)",
            "checked_at": "2026-08-17 12:00:00",
        }

        # Setup sample profile in main.profiles
        main.profiles["Test Profile"] = {
            "running": False,
            "config": {
                "cookie_str": "sessionid=abc12345;",
                "tiktok_id": "",
                "tiktok_user_id": "",
                "session_auth_state": "unknown",
            }
        }

        try:
            res = main._inspect_tiktok_account_worker("Test Profile")
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["unique_id"], "test_user_99")
            self.assertEqual(res["tiktok_user_id"], "7447972851385451523")

            # Check auto-sync to profile config
            cfg = main.profiles["Test Profile"]["config"]
            self.assertEqual(cfg.get("tiktok_id"), "test_user_99")
            self.assertEqual(cfg.get("tiktok_user_id"), "7447972851385451523")
            self.assertEqual(cfg.get("session_auth_state"), "verified")

            # Check monetization cache updated
            self.assertIn("Test Profile", main.monetization_cache)
            self.assertEqual(main.monetization_cache["Test Profile"]["balance"], 150.50)
        finally:
            if "Test Profile" in main.profiles:
                del main.profiles["Test Profile"]
            if "Test Profile" in main.monetization_cache:
                del main.monetization_cache["Test Profile"]

    def test_inspection_dialog_creation(self):
        targets = ["AUTO 6", "AUTO 18"]
        dlg = main.InspectionDialog(targets)
        try:
            self.assertIsNotNone(dlg.dialog)
            self.assertIsNotNone(dlg.table)
            self.assertEqual(dlg.concurrency_var.get(), "2 luồng")
            self.assertEqual(len(dlg.table.get_children('')), 2)
        finally:
            dlg._on_close()


if __name__ == "__main__":
    unittest.main()
