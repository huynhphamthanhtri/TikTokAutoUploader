import unittest
from pathlib import Path


class UIOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.main_source = (root / "main.py").read_text(encoding="utf-8")
        cls.ui_source = (root / "app_ui.py").read_text(encoding="utf-8")

    def test_toolbar_keeps_primary_buttons(self):
        self.assertIn("text=\"Thêm\"", self.ui_source)
        self.assertIn("handlers['add_profile']", self.ui_source)
        self.assertIn("text=\"Mở Chrome\"", self.ui_source)
        self.assertIn("handlers['open_browser']", self.ui_source)

    def test_secondary_actions_moved_to_overflow_menu(self):
        self.assertIn("overflow_actions", self.ui_source)
        self.assertIn("handlers['edit_profile']", self.ui_source)
        self.assertIn("handlers['rename_profile']", self.ui_source)
        self.assertIn("handlers['export_profiles']", self.ui_source)
        self.assertIn("handlers['batch_add_profiles']", self.ui_source)

    def test_overflow_menu_uses_popup_helper(self):
        self.assertIn("def _open_overflow_menu", self.ui_source)
        self.assertIn("menu.tk_popup", self.ui_source)
        self.assertIn("menu.grab_release", self.ui_source)

    def test_table_adds_tiktok_and_region_columns(self):
        self.assertIn("'tiktok'", self.ui_source)
        self.assertIn("'proxy_region'", self.ui_source)
        self.assertIn("text='TikTok ID'", self.ui_source)
        self.assertIn("text='Proxy / Vùng'", self.ui_source)

    def test_table_renames_status_to_health(self):
        self.assertIn("text='Trạng Thái'", self.ui_source)
        self.assertNotIn("text='Đăng nhập'", self.ui_source)
        self.assertNotIn("text='Trình duyệt'", self.ui_source)

    def test_technical_columns_hidden(self):
        self.assertIn("'cookie_st'", self.ui_source)
        self.assertIn("'monetization'", self.ui_source)

    def test_log_panel_shrunk(self):
        self.assertIn("weight=78", self.ui_source)
        self.assertIn("weight=22", self.ui_source)

    def test_update_profile_list_uses_health_tiktok_region(self):
        block = self.main_source[self.main_source.index("def update_profile_list"):]
        self.assertIn("tiktok_id = str(cfg.get('tiktok_id', '') or cfg.get('tiktok_account', '') or '').lstrip('@')", block)
        self.assertIn("region = _profile_region(cfg)", block)

    def test_health_summary_maps_row_tags(self):
        block = self.main_source[self.main_source.index("def _health_summary"):]
        self.assertIn("'tag_error'", block)
        self.assertIn("'tag_processing'", block)
        self.assertIn("return 'Đang chạy'", block)
        self.assertIn("return 'Đã dừng'", block)

    def test_profile_region_reads_fingerprint(self):
        block = self.main_source[self.main_source.index("def _profile_region"):]
        self.assertIn("geo_country_code", block)
        self.assertIn("geo_country", block)

    def test_status_bar_counts_running_from_profiles(self):
        block = self.main_source[self.main_source.index("def _refresh_status_bar"):]
        self.assertIn("profiles.get(name, {}).get('running')", block)

    def test_export_still_available_in_overflow_and_context(self):
        self.assertIn("handlers['export_profiles']", self.ui_source)
        self.assertIn("label=\"Export tài khoản\"", self.ui_source)

    def test_reset_browser_kept_in_overflow_menu(self):
        self.assertIn("(\"Reset Browser\"", self.ui_source)
        self.assertIn("handlers['clean_browser']", self.ui_source)


if __name__ == "__main__":
    unittest.main()