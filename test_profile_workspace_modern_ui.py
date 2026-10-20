# -*- coding: utf-8 -*-
"""
Unit tests for Profiles Workspace Modern UI improvements (Phases 1, 2, 3):
- Dynamic Filter Chip Counts & Search integration
- Treeview displaycolumns Presets preserving values[0] == profile_name contract
- get_profile_drawer_info data extraction & formatting
- NEEDS_ATTENTION semantic filter logic
"""

import unittest
from unittest.mock import MagicMock

from profile_table_engine import (
    calculate_filter_chip_counts,
    build_row_model,
)
from app_ui import TABLE_PRESETS


class TestProfileWorkspaceModernUI(unittest.TestCase):

    def setUp(self):
        self.sample_profiles = {
            "Profile_1_Healthy": {
                "running": True,
                "config": {
                    "account_uuid": "uuid-1",
                    "tiktok_id": "clean_creator",
                    "cookie_str": "valid_session_cookie=123",
                    "session_auth_state": "live",
                    "proxy_string": "1.2.3.4:8080",
                    "fingerprint": {"geo_country_code": "US"},
                    "folder_path": "H:/Videos/Chan1",
                },
                "ui": {
                    "status": "Đang chạy",
                    "login": "Live",
                    "last_error": "",
                },
            },
            "Profile_2_Needs_Attention_Cookie_Die": {
                "running": False,
                "config": {
                    "account_uuid": "uuid-2",
                    "tiktok_id": "expired_creator",
                    "cookie_str": "old_session_cookie=456",
                    "session_auth_state": "expired",
                    "proxy_string": "5.6.7.8:8080",
                    "fingerprint": {"geo_country_code": "VN"},
                    "folder_path": "H:/Videos/Chan2",
                },
                "ui": {
                    "status": "Đã dừng",
                    "login": "die",
                    "last_error": "Cookie hết hạn",
                },
            },
            "Profile_3_Needs_Attention_TKTBM": {
                "running": False,
                "config": {
                    "account_uuid": "uuid-3",
                    "tiktok_id": "locked_creator",
                    "cookie_str": "some_cookie",
                    "session_auth_state": "live",
                    "tiktok_inspection": {"state": "TKTBM_RESTRICTED"},
                    "folder_path": "H:/Videos/Chan3",
                },
                "ui": {
                    "status": "Đã dừng",
                    "login": "Live",
                    "last_error": "",
                },
            },
            "Profile_4_No_Cookie": {
                "running": False,
                "config": {
                    "account_uuid": "uuid-4",
                    "tiktok_id": "",
                    "cookie_str": "",
                    "session_auth_state": "",
                    "folder_path": "",
                },
                "ui": {
                    "status": "Đã dừng",
                    "login": "",
                    "last_error": "",
                },
            },
            "Profile_5_Has_Error_Only": {
                "running": False,
                "config": {
                    "account_uuid": "uuid-5",
                    "tiktok_id": "error_creator",
                    "cookie_str": "live_cookie_sample",
                    "session_auth_state": "live",
                    "folder_path": "H:/Videos/Chan5",
                },
                "ui": {
                    "status": "Lỗi",
                    "login": "Live",
                    "last_error": "FFmpeg encode failed: exit code 1",
                },
            },
        }

        self.sample_monetization_cache = {
            "Profile_1_Healthy": {
                "status": "SUCCESS",
                "payout_status": "PAYOUT_READY",
                "kyc_status": "APPROVED",
                "tax_status": "TAX_VERIFIED",
                "analytics": {"views_30d": 1250000, "follower_count": 85000},
            },
            "Profile_2_Needs_Attention_Cookie_Die": {
                "status": "COOKIE_EXPIRED",
                "payout_status": "Cookie Die",
            },
            "Profile_3_Needs_Attention_TKTBM": {
                "crp_status": "TKTBM",
            },
            "Profile_4_No_Cookie": {},
            "Profile_5_Has_Error_Only": {
                "status": "SUCCESS",
            },
        }

    def test_calculate_filter_chip_counts_all(self):
        """Kiểm tra số đếm động trên các chip khi không có từ khóa tìm kiếm."""
        counts = calculate_filter_chip_counts(
            list(self.sample_profiles.keys()),
            self.sample_profiles,
            self.sample_monetization_cache,
            search_keyword="",
        )

        self.assertEqual(counts["ALL"], 5)
        self.assertEqual(counts["COOKIE_LIVE"], 3)  # Profile 1, 3, 5
        self.assertEqual(counts["COOKIE_DIE"], 1)   # Profile 2
        self.assertEqual(counts["NO_COOKIE"], 1)    # Profile 4
        self.assertEqual(counts["KYC_OK"], 1)       # Profile 1
        self.assertEqual(counts["TAX_OK"], 1)       # Profile 1
        self.assertEqual(counts["TKTBM"], 1)        # Profile 3
        self.assertEqual(counts["RUNNING"], 1)      # Profile 1
        # Needs attention: Profile 2 (cookie die), Profile 3 (tktbm), Profile 5 (has last_error)
        self.assertEqual(counts["NEEDS_ATTENTION"], 3)

    def test_calculate_filter_chip_counts_with_keyword_search(self):
        """Kiểm tra số đếm động khi kết hợp từ khóa tìm kiếm (Search Box)."""
        # Tìm kiếm theo "Chan2" -> chỉ Profile 2 khớp
        counts_chan2 = calculate_filter_chip_counts(
            list(self.sample_profiles.keys()),
            self.sample_profiles,
            self.sample_monetization_cache,
            search_keyword="Chan2",
        )
        self.assertEqual(counts_chan2["ALL"], 1)
        self.assertEqual(counts_chan2["COOKIE_DIE"], 1)
        self.assertEqual(counts_chan2["NEEDS_ATTENTION"], 1)
        self.assertEqual(counts_chan2["COOKIE_LIVE"], 0)

        # Tìm kiếm theo keyword không tồn tại
        counts_none = calculate_filter_chip_counts(
            list(self.sample_profiles.keys()),
            self.sample_profiles,
            self.sample_monetization_cache,
            search_keyword="NonExistentKeyword_XYZ",
        )
        self.assertEqual(counts_none["ALL"], 0)
        self.assertEqual(counts_none["NEEDS_ATTENTION"], 0)

    def test_displaycolumns_contract_values0_preserved(self):
        """
        Bảo toàn Hợp Đồng Bất Biến (values[0] == profile_name):
        Kiểm tra rằng các Preset displaycolumns chỉ là cấu hình hiển thị,
        không làm thay đổi thứ tự hay vị trí các giá trị trong values tuple.
        """
        # 1. Preset Vận hành mặc định phải có đúng 6 cột cốt lõi
        default_preset = TABLE_PRESETS["Vận hành (Mặc định)"]
        self.assertEqual(len(default_preset), 6)
        self.assertEqual(
            default_preset,
            ('name', 'tiktok', 'cookie_st', 'activity', 'proxy_region', 'upload')
        )

        # 2. Toàn bộ 4 presets đều phải bắt đầu bằng cột 'name'
        for preset_name, cols in TABLE_PRESETS.items():
            self.assertTrue(len(cols) >= 6, f"Preset {preset_name} quá ít cột")
            self.assertEqual(cols[0], 'name', f"Preset {preset_name} phải có 'name' ở cột đầu")

        # 3. Model row_model sinh ra từ engine phải giữ values[0] là profile_name
        model = build_row_model(
            "Profile_1_Healthy",
            self.sample_profiles["Profile_1_Healthy"],
            self.sample_monetization_cache,
        )
        self.assertEqual(model["name"], "Profile_1_Healthy")
        self.assertEqual(model["values"][0], "Profile_1_Healthy")

    def test_get_profile_drawer_info_data_contract(self):
        """Kiểm tra cấu trúc và tính đúng đắn dữ liệu trả về từ get_profile_drawer_info."""
        # Import hàm get_profile_drawer_info từ main
        import main
        orig_profiles = main.profiles
        orig_mono = main.monetization_cache
        try:
            main.profiles = self.sample_profiles
            main.monetization_cache = self.sample_monetization_cache

            # Profile 1: Healthy & Running
            info1 = main.get_profile_drawer_info("Profile_1_Healthy")
            self.assertEqual(info1["name"], "Profile_1_Healthy")
            self.assertEqual(info1["tiktok_id"], "@clean_creator")
            self.assertEqual(info1["folder_path"], "H:/Videos/Chan1")
            self.assertIn("Live", info1["cookie_badge"])
            self.assertIn("Đang chạy", info1["activity_badge"])
            self.assertIn("Đang bật", info1["mono_badge"])
            self.assertIn("[US]", info1["proxy_badge"])

            # Profile 2: Cookie Die with Last Error
            info2 = main.get_profile_drawer_info("Profile_2_Needs_Attention_Cookie_Die")
            self.assertEqual(info2["name"], "Profile_2_Needs_Attention_Cookie_Die")
            self.assertIn("Die", info2["cookie_badge"])
            self.assertEqual(info2["last_error"], "Cookie hết hạn")

            # Profile không tồn tại -> dict rỗng an toàn
            info_none = main.get_profile_drawer_info("NonExistentProfile")
            self.assertEqual(info_none, {})
        finally:
            main.profiles = orig_profiles
            main.monetization_cache = orig_mono

    def test_needs_attention_semantic_filtering(self):
        """Kiểm tra logic nghiệp vụ lọc NEEDS_ATTENTION."""
        # Giả lập vòng lặp lọc như trong update_profile_list
        selected_for_attention = []
        for name, prof in self.sample_profiles.items():
            ui = prof.get("ui", {})
            cfg = prof.get("config", {})
            mono = self.sample_monetization_cache.get(name, {})
            auth_state = str(cfg.get("session_auth_state", "")).lower()
            is_cookie_die = auth_state == "expired" or mono.get("status") == "COOKIE_EXPIRED"
            is_tktbm = mono.get("crp_status") == "TKTBM"
            has_error = bool(ui.get("last_error"))

            if is_cookie_die or is_tktbm or has_error:
                selected_for_attention.append(name)

        self.assertIn("Profile_2_Needs_Attention_Cookie_Die", selected_for_attention)
        self.assertIn("Profile_3_Needs_Attention_TKTBM", selected_for_attention)
        self.assertIn("Profile_5_Has_Error_Only", selected_for_attention)
        self.assertNotIn("Profile_1_Healthy", selected_for_attention)
        self.assertNotIn("Profile_4_No_Cookie", selected_for_attention)


if __name__ == "__main__":
    unittest.main()
