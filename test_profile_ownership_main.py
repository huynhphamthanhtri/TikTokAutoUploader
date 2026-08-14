import unittest
from pathlib import Path


class ProfileOwnershipMainFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.source = (root / "main.py").read_text(encoding="utf-8")
        cls.glue_source = (root / "browser_patchright_glue.py").read_text(encoding="utf-8")

    def test_conflict_gate_helpers_exist(self):
        for helper in ("_profile_conflicts", "_profile_conflict_blocked_names", "_blocked_by_profile_conflict", "_profile_conflict_message"):
            self.assertIn("def " + helper, self.source)

    def test_conflict_gate_wired_into_start(self):
        start_flow = self.source[
            self.source.index("def start_profile"):
            self.source.index("def _stop_profile_driver")
        ]
        self.assertIn("_blocked_by_profile_conflict", start_flow)

    def test_conflict_gate_wired_into_open_browser(self):
        manual_flow = self.source[
            self.source.index("def open_browser()"):
            self.source.index("def _capture_after_manual_close")
        ]
        self.assertIn("_blocked_by_profile_conflict", manual_flow)

    def test_conflict_gate_wired_into_get_cookies(self):
        cookie_flow = self.source[
            self.source.index("def get_tiktok_cookies"):
            self.source.index("def _choose_browser_maintenance_mode")
        ]
        self.assertIn("_blocked_by_profile_conflict", cookie_flow)

    def test_account_uuid_assigned_when_adding_profile(self):
        add_flow = self.source[
            self.source.index("def add_profile"):
            self.source.index("def batch_add_profiles")
        ]
        self.assertIn("ensure_account_uuid", add_flow)

    def test_session_proxy_key_delegates_to_ownership(self):
        self.assertIn("_session_proxy_key", self.source)
        self.assertIn("ownership_session_proxy_key", self.source)

    def test_edit_profile_invalidates_session_on_save(self):
        edit_flow = self.source[
            self.source.index("def edit_profile("):
            self.source.index("def rename_profile()")
        ]
        self.assertIn("invalidate_session_auth", edit_flow)
        self.assertIn("_blocked_by_profile_conflict", edit_flow)

    def test_edit_profile_keeps_account_uuid(self):
        edit_flow = self.source[
            self.source.index("def edit_profile("):
            self.source.index("def rename_profile()")
        ]
        self.assertNotIn("account_uuid = ", edit_flow)
        self.assertIn("ensure_account_uuid(cfg)", edit_flow)

    def test_socks5_supported_in_build_session_config(self):
        setup_source = self.glue_source[
            self.glue_source.index("def build_session_config"):
            self.glue_source.index("def open_session")
        ]
        self.assertIn('proxy_type not in ("http", "socks5")', setup_source)
        self.assertIn('scheme = "socks5" if proxy_type == "socks5" else "http"', setup_source)

    def test_ensure_patchright_profile_binds_owner(self):
        glue_source = self.glue_source[
            self.glue_source.index("def ensure_patchright_profile"):
            self.glue_source.index("def open_session")
        ]
        self.assertIn("account_id", glue_source)
        self.assertIn("profile_owner_id", glue_source)


class UIRedesignMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.source = (root / "main.py").read_text(encoding="utf-8")

    def test_detail_dialog_uses_cards_and_two_columns(self):
        detail_flow = self.source[
            self.source.index("def view_profile_details("):
            self.source.index("def open_browser()")
        ]
        self.assertIn("_ui_card", detail_flow)
        self.assertIn("grid_columnconfigure", detail_flow)
        self.assertIn("_ui_footer", detail_flow)
        self.assertIn("_ui_badge", detail_flow)
        self.assertIn("account_uuid", detail_flow)

    def test_detail_dialog_shows_session_state_badge(self):
        detail_flow = self.source[
            self.source.index("def view_profile_details("):
            self.source.index("def open_browser()")
        ]
        self.assertIn("session_auth_state", detail_flow)
        self.assertIn("Session đã lưu", detail_flow)
        self.assertIn("Cần đăng nhập", detail_flow)

    def test_detail_dialog_masks_secrets_with_reveal(self):
        detail_flow = self.source[
            self.source.index("def _detail_cell("):
            self.source.index("def open_browser()")
        ]
        self.assertIn("show='*'", detail_flow)
        self.assertIn("sensitive=True", detail_flow)

    def test_edit_dialog_uses_cards_and_sections(self):
        edit_flow = self.source[
            self.source.index("def edit_profile("):
            self.source.index("def rename_profile()")
        ]
        self.assertIn("_ui_card", edit_flow)
        self.assertIn("'Tài khoản'", edit_flow)
        self.assertIn("'Bảo mật'", edit_flow)
        self.assertIn("'Proxy & vận hành'", edit_flow)
        self.assertIn("_ui_footer", edit_flow)

    def test_edit_dialog_validates_input(self):
        edit_flow = self.source[
            self.source.index("def edit_profile("):
            self.source.index("def rename_profile()")
        ]
        self.assertIn("Kiểm tra dữ liệu", edit_flow)
        self.assertIn("Limit/Ngày phải là số nguyên", edit_flow)
        self.assertIn("Proxy sai định dạng", edit_flow)

    def test_edit_dialog_tracks_dirty_state(self):
        edit_flow = self.source[
            self.source.index("def edit_profile("):
            self.source.index("def rename_profile()")
        ]
        self.assertIn("_is_dirty", edit_flow)
        self.assertIn("Bỏ thay đổi?", edit_flow)
        self.assertIn("WM_DELETE_WINDOW", edit_flow)

    def test_edit_dialog_proxy_type_selectable(self):
        edit_flow = self.source[
            self.source.index("def edit_profile("):
            self.source.index("def rename_profile()")
        ]
        self.assertIn("socks5", edit_flow)
        self.assertIn("proxy_type", edit_flow)


if __name__ == "__main__":
    unittest.main()