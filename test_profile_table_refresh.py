import unittest
from pathlib import Path


class ProfileTableRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.main_source = (root / "main.py").read_text(encoding="utf-8")
        cls.ui_source = (root / "app_ui.py").read_text(encoding="utf-8")

    def test_update_profile_list_does_not_rebuild_whole_table(self):
        start = self.main_source.index("def update_profile_list")
        block = self.main_source[start:self.main_source.index("def _thread_sequential_start", start)]
        self.assertNotIn("tree.delete(item)", block)
        self.assertNotIn("for item in tree.get_children(): tree.delete(item)", block)
        self.assertIn("if iid not in row_map:\n            tree.delete(iid)", block)
        self.assertIn("tree.item(iid, values=values)", block)
        self.assertIn("tree.insert('', 'end', iid=iid", block)

    def test_table_uses_stable_account_uuid_iid(self):
        block = self.main_source[self.main_source.index("def update_profile_list"):]
        self.assertIn("uuid = ensure_account_uuid(cfg)", block)

    def test_refresh_scheduler_coalesces(self):
        start = self.main_source.index("def request_profile_refresh")
        block = self.main_source[start:self.main_source.index("def _flush_profile_refresh", start)]
        self.assertIn("_profile_refresh_pending", block)
        self.assertIn("if _profile_refresh_pending:\n        return", block)

    def test_set_profile_ui_skips_when_unchanged(self):
        start = self.main_source.index("def _set_profile_ui")
        block = self.main_source[start:self.main_source.index("def _short_ui_text", start)]
        self.assertIn("changed = False", block)
        self.assertIn("if refresh and changed:", block)

    def test_save_configs_no_longer_refreshes_table(self):
        start = self.main_source.index("def save_configs():")
        end = self.main_source.index("def load_configs():", start)
        block = self.main_source[start:end]
        self.assertNotIn("update_profile_list()", block)

    def test_action_buttons_guards_exist(self):
        self.assertIn("def _update_action_buttons", self.main_source)
        self.assertIn("btn_check_cookie", self.main_source)
        self.assertIn("snapshot.can_start", self.main_source)
        self.assertIn("snapshot.can_stop", self.main_source)
        self.assertIn("snapshot.can_check_cookie", self.main_source)

    def test_cookie_live_check_wired(self):
        self.assertIn("def check_cookie_live", self.main_source)
        self.assertIn("def _check_profile_cookie_live", self.main_source)
        self.assertIn("class CookieCheckDialog", self.main_source)
        self.assertIn("'check_cookie_live': check_cookie_live", self.main_source)

    def test_status_columns_present_in_ui(self):
        self.assertIn("'activity'", self.ui_source)
        self.assertIn("'cookie_st'", self.ui_source)
        self.assertIn("text='Trạng Thái'", self.ui_source)
        self.assertIn("text='Cookie'", self.ui_source)
        self.assertIn("Check Cookie", self.ui_source)
        self.assertIn("handlers['check_cookie_live']", self.ui_source)

    def test_batch_start_uses_preflight_not_error_write(self):
        start = self.main_source.index("def _thread_sequential_start")
        block = self.main_source[start:self.main_source.index("def _set_buttons_state", start)]
        self.assertIn("batch_start_preflight", block)
        self.assertIn("Đã chạy sẵn", block)


if __name__ == "__main__":
    unittest.main()