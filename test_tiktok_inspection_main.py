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

    def test_inspection_worker_uses_readonly_fetch(self):
        start = self.main_source.index("def _inspect_tiktok_account_worker")
        block = self.main_source[start:self.main_source.index("class InspectionDialog", start)]
        self.assertIn("browser_glue.inspect_tiktok_account", block)
        self.assertIn("wait_page_login_state", block)
        self.assertIn("SEED_ENDPOINTS", block)

    def test_inspection_worker_owns_and_quits_session(self):
        start = self.main_source.index("def _inspect_tiktok_account_worker")
        block = self.main_source[start:self.main_source.index("class InspectionDialog", start)]
        self.assertIn("token = browser_glue.open_session(cfg, name)", block)
        self.assertIn("owned = True", block)
        self.assertIn("if owned and token is not None:", block)
        self.assertIn("token.quit()", block)

    def test_inspection_snapshot_persisted(self):
        start = self.main_source.index("def _persist_inspection_snapshot")
        block = self.main_source[start:self.main_source.index("def _inspect_tiktok_account_worker", start)]
        self.assertIn("cfg['tiktok_inspection'] = snapshot", block)
        self.assertIn("'schema_version': 1", block)
        self.assertIn("save_configs()", block)

    def test_inspection_uses_operation_state(self):
        self.assertIn("OperationState.INSPECTING_ACCOUNT.value", self.main_source)
        self.assertIn('INSPECTING_ACCOUNT = "INSPECTING_ACCOUNT"', self.runtime_source)

    def test_dialog_masks_payout_identifier(self):
        start = self.main_source.index("class InspectionDialog")
        block = self.main_source[start:self.main_source.index("def inspect_selected_tiktok_account", start)]
        self.assertIn("masked_identifier", block)
        self.assertIn("Không có dữ liệu xác minh", block)
        self.assertIn("mask_detail(warning)", block)


if __name__ == "__main__":
    unittest.main()
