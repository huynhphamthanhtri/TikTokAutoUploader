import unittest
from pathlib import Path


class ResetBrowserUIFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.main_source = (root / "main.py").read_text(encoding="utf-8")
        cls.ui_source = (root / "app_ui.py").read_text(encoding="utf-8")

    def test_reset_browser_handler_exists(self):
        self.assertIn("'clean_browser': clean_browser", self.main_source)

    def test_toolbar_has_reset_browser_button(self):
        self.assertIn('("Reset Browser"', self.ui_source)
        self.assertIn("handlers['clean_browser']", self.ui_source)

    def test_context_menu_uses_reset_browser_label(self):
        self.assertIn('label="Reset Browser"', self.ui_source)

    def test_maintenance_dialog_has_three_named_modes(self):
        dialog = self.main_source[
            self.main_source.index("def _choose_browser_maintenance_mode"):
            self.main_source.index("def _reset_full_with_quarantine")
        ]
        self.assertIn("'Dọn cache'", dialog)
        self.assertIn("'Đăng xuất và login lại'", dialog)
        self.assertIn("'Tạo môi trường login mới'", dialog)

    def test_maintenance_dialog_offers_restore_when_quarantine_exists(self):
        dialog = self.main_source[
            self.main_source.index("def _choose_browser_maintenance_mode"):
            self.main_source.index("def _reset_full_with_quarantine")
        ]
        self.assertIn("latest_quarantine", dialog)
        self.assertIn("'Khôi phục browser trước đó'", dialog)

    def test_full_reset_uses_quarantine(self):
        worker = self.main_source[
            self.main_source.index("def _clean_browser_worker"):
            self.main_source.index("def _restore_browser_profile_worker")
        ]
        self.assertIn("_reset_full_with_quarantine", worker)

    def test_quarantine_reset_wires_clean_profile(self):
        reset_flow = self.main_source[
            self.main_source.index("def _reset_full_with_quarantine"):
            self.main_source.index("def _clean_browser_worker")
        ]
        self.assertIn("quarantine_profile", reset_flow)
        self.assertIn("create_patchright_profile", reset_flow)
        self.assertIn("invalidate_session_auth", reset_flow)
        self.assertIn("'manual_login_pending'] = True", reset_flow)
        self.assertIn("'session_auth_state'] = 'expired'", reset_flow)

    def test_restore_worker_wires_restore_quarantine(self):
        restore_flow = self.main_source[
            self.main_source.index("def _restore_browser_profile_worker"):
            self.main_source.index("def clean_browser():")
        ]
        self.assertIn("restore_quarantine", restore_flow)
        self.assertIn("restore_target", restore_flow)
        self.assertIn("migration_status", restore_flow)

    def test_edit_profile_evaluates_proxy_change(self):
        edit_flow = self.main_source[
            self.main_source.index("def edit_profile("):
            self.main_source.index("def rename_profile()")
        ]
        self.assertIn("_evaluate_proxy_environment_change", edit_flow)
        self.assertIn("apply_proxy_environment_warning", edit_flow)

    def test_startup_cleanup_quarantines(self):
        self.assertIn("_cleanup_expired_quarantines", self.main_source)
        self.assertIn("cleanup_quarantines", self.main_source)


if __name__ == "__main__":
    unittest.main()