"""
test_filter_chips_contract.py - Unit tests for Profile Table Quick Filter Chips, Search, & Summary Synchronization.
"""

import unittest
from unittest.mock import MagicMock, patch

from ui_components import calculate_summary_counts


class TestFilterChipsContract(unittest.TestCase):
    def setUp(self):
        self.sample_profiles = {
            "Profile 1": {
                "running": True,
                "config": {
                    "project_name": "Dự án Alpha",
                    "tiktok_id": "@alpha_user",
                    "proxy_string": "1.2.3.4:8080",
                    "region": "US",
                    "cookie_str": "sessionid=abc12345;",
                    "session_auth_state": "verified",
                },
                "status": "Đang chạy",
            },
            "Profile 2": {
                "running": False,
                "config": {
                    "project_name": "Dự án Beta",
                    "tiktok_id": "@beta_user",
                    "proxy_string": "5.6.7.8:8080",
                    "region": "DE",
                    "cookie_str": "sessionid=expired;",
                    "session_auth_state": "expired",
                },
                "status": "Cookie Die",
            },
            "Profile 3": {
                "running": False,
                "config": {
                    "project_name": "Dự án Alpha",
                    "tiktok_id": "@gamma_user",
                    "proxy_string": "",
                    "region": "GB",
                    "cookie_str": "",
                    "session_auth_state": "",
                },
                "status": "Chưa nạp cookie",
            },
        }

    def test_calculate_summary_counts_all(self):
        counts = calculate_summary_counts(self.sample_profiles, active_project="Tất cả", filter_text="")
        self.assertEqual(counts["total"], 3)
        self.assertEqual(counts["running"], 1)
        self.assertEqual(counts["cookie_live"], 1)
        self.assertEqual(counts["errors"], 1)

    def test_calculate_summary_counts_by_project(self):
        counts = calculate_summary_counts(self.sample_profiles, active_project="Dự án Alpha", filter_text="")
        self.assertEqual(counts["total"], 2)
        self.assertEqual(counts["running"], 1)
        self.assertEqual(counts["cookie_live"], 1)

    def test_calculate_summary_counts_by_search_region(self):
        # Searching region "DE" should find Profile 2
        counts = calculate_summary_counts(self.sample_profiles, active_project="Tất cả", filter_text="DE")
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["errors"], 1)

    def test_filter_chips_criteria(self):
        """Test the pure logic of NO_COOKIE, COOKIE_LIVE, and COOKIE_DIE."""
        p1 = self.sample_profiles["Profile 1"]["config"]
        p2 = self.sample_profiles["Profile 2"]["config"]
        p3 = self.sample_profiles["Profile 3"]["config"]

        # Profile 3 has empty cookie
        self.assertTrue((not p3.get("cookie_str")) or (p3.get("cookie_str") in ("[]", "{}", "null")))

        # Profile 1 has valid cookie and verified auth
        cookie_raw_1 = str(p1.get("cookie_str", "")).strip()
        is_no_cookie_1 = (not cookie_raw_1) or (cookie_raw_1 in ("[]", "{}", "null"))
        self.assertFalse(is_no_cookie_1)
        is_live_1 = not is_no_cookie_1 and (p1.get("session_auth_state") in ("live", "verified"))
        self.assertTrue(is_live_1)

        # Profile 2 has expired cookie
        cookie_raw_2 = str(p2.get("cookie_str", "")).strip()
        is_no_cookie_2 = (not cookie_raw_2) or (cookie_raw_2 in ("[]", "{}", "null"))
        self.assertFalse(is_no_cookie_2)
        is_die_2 = not is_no_cookie_2 and (p2.get("session_auth_state") in ("expired", "invalid", "dead"))
        self.assertTrue(is_die_2)

    def test_update_profile_list_live_filtering(self):
        """Test that update_profile_list executes without exceptions on all chips."""
        import customtkinter as ctk
        import main

        root = ctk.CTk()
        root.withdraw()
        try:
            main.load_configs()
            chips = ['ALL', 'COOKIE_LIVE', 'COOKIE_DIE', 'NO_COOKIE', 'KYC_OK', 'TAX_OK', 'TKTBM', 'RUNNING']
            for chip in chips:
                main.active_filter_chip_var.set(chip)
                main.update_profile_list()
                items = main.tree.get_children('')
                self.assertIsInstance(items, tuple)
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
