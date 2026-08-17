import unittest
from pathlib import Path


class TiktokInspectionMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.main_source = (root / "main.py").read_text(encoding="utf-8")
        cls.ui_source = (root / "app_ui.py").read_text(encoding="utf-8")
        cls.runtime_source = (root / "profile_runtime_status.py").read_text(encoding="utf-8")

    def test_inspection_handler_wired(self):
        self.assertIn("def inspect_selected_tiktok_account", self.main_source)
        self.assertIn("def _inspect_tiktok_account_worker", self.main_source)
        self.assertIn("class InspectionDialog", self.main_source)
        self.assertIn("'inspect_tiktok_account': inspect_selected_tiktok_account", self.main_source)

    def test_inspection_context_menu_entry(self):
        self.assertIn(
            "ctx_menu.add_command(label=\"Kiểm tra thông tin TikTok\", command=handlers['inspect_tiktok_account'])",
            self.ui_source,
        )

    def test_inspection_runtime_guard_in_preflight(self):
        start = self.main_source.index("def _inspection_preflight_reason")
        block = self.main_source[start:self.main_source.index("def _persist_inspection_snapshot", start)]
        self.assertIn("_blocked_by_profile_conflict(name)", block)
        self.assertIn("snapshot.can_check_cookie", block)

    def test_inspection_worker_uses_fast_http(self):
        start = self.main_source.index("def _inspect_tiktok_account_worker")
        block = self.main_source[start:self.main_source.index("class InspectionDialog", start)]
        self.assertIn("TikTokMonetizationClient", block)
        self.assertIn("fetch_all_monetization_data", block)
        self.assertIn("cfg['tiktok_id'] = data['unique_id']", block)
        self.assertIn("monetization_cache[name] = data", block)

    def test_inspection_dialog_saas_features(self):
        start = self.main_source.index("class InspectionDialog")
        block = self.main_source[start:self.main_source.index("def inspect_selected_tiktok_account", start)]
        self.assertIn("ThreadPoolExecutor", block)
        self.assertIn("btn_copy_uid", block)
        self.assertIn("btn_copy_user", block)
        self.assertIn("btn_open_web", block)
        self.assertIn("concurrency_menu", block)


if __name__ == "__main__":
    unittest.main()
