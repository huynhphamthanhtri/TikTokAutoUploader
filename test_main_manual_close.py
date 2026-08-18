import unittest
from pathlib import Path


class ManualCloseMainFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.source = (root / "main.py").read_text(encoding="utf-8")
        cls.glue_source = (root / "browser_patchright_glue.py").read_text(encoding="utf-8")

    def test_open_browser_always_quits_token_in_finally(self):
        manual_flow = self.source[
            self.source.index("def open_browser()"):
            self.source.index("def _wait_and_close_driver")
        ]
        self.assertIn("closed_ok = token.quit()", manual_flow)
        self.assertIn("lc.release_manual(token)", manual_flow)
        self.assertIn("profiles.get(nm, {})['session_busy'] = False", manual_flow)
        self.assertIn("closed_ok", manual_flow)

    def test_open_browser_does_not_report_success_before_cleanup_confirmed(self):
        manual_flow = self.source[
            self.source.index("def open_browser()"):
            self.source.index("def _wait_and_close_driver")
        ]
        self.assertIn("if closed_ok:", manual_flow)
        self.assertIn("browser='Đóng lỗi'", manual_flow)

    def test_ensure_driver_does_not_retry_profile_busy(self):
        automation_flow = self.source[
            self.source.index("def ensure_driver"):
            self.source.index("def upload_video")
        ]
        self.assertIn("isinstance(error, ProfileBusyError)", automation_flow)
        self.assertIn("không retry", automation_flow)

    def test_ensure_driver_logs_first_attempt_error_before_retry(self):
        automation_flow = self.source[
            self.source.index("def ensure_driver"):
            self.source.index("def upload_video")
        ]
        self.assertIn("Lỗi mở Patchright lần {attempt + 1}", automation_flow)
        self.assertIn("Thử mở Patchright lại lần {attempt + 2}", automation_flow)

    def test_watch_manual_close_closes_session_via_quit(self):
        start = self.glue_source.index("def watch_manual_close")
        glue_source = self.glue_source[start:]
        self.assertIn("token.quit()", glue_source)

    def test_quit_marks_closed_only_after_confirmed_release(self):
        glue_source = self.glue_source[
            self.glue_source.index("def quit(self, timeout=10.0)"):
            self.glue_source.index("def browser_service")
        ]
        self.assertIn("closed = close_session(self.handle, timeout=timeout)", glue_source)
        self.assertIn("if closed:", glue_source)
        self.assertIn("self.mark_closed()", glue_source)

    def test_open_session_maps_profile_in_use_to_profile_busy(self):
        glue_source = self.glue_source[
            self.glue_source.index("def open_session"):
            self.glue_source.index("def import_cookies")
        ]
        self.assertIn("ProfileInUseError", glue_source)
        self.assertIn("ProfileBusyError", glue_source)

    def test_upload_timeout_closes_session_not_just_cancels(self):
        glue_source = self.glue_source[
            self.glue_source.index("def run_upload"):
            self.glue_source.index("def watch_manual_close")
        ]
        self.assertIn("token.quit()", glue_source)
        self.assertIn("cancel_session(token.handle, timeout=10)", glue_source)

    def test_open_browser_injects_cookies_when_available(self):
        manual_flow = self.source[
            self.source.index("def open_browser()"):
            self.source.index("def _capture_after_manual_close")
        ]
        self.assertIn("import_cookies_report", manual_flow)
        self.assertIn("parse_cookie", manual_flow)

    def test_open_browser_captures_session_and_inspects_account_after_close(self):
        after_close_flow = self.source[
            self.source.index("def _capture_after_manual_close"):
            self.source.index("def _wait_and_close_driver")
        ]
        self.assertIn("_capture_tiktok_cookies_worker", after_close_flow)
        self.assertIn("_inspect_tiktok_account_worker", after_close_flow)

    def test_capture_worker_does_not_inject_cookies(self):
        capture_flow = self.source[
            self.source.index("def _capture_tiktok_cookies_worker"):
            self.source.index("def get_tiktok_cookies")
        ]
        self.assertNotIn("import_cookies", capture_flow)

    def test_ensure_driver_uses_profile_first_authenticate(self):
        automation_flow = self.source[
            self.source.index("def ensure_driver"):
            self.source.index("def upload_video")
        ]
        self.assertIn("authenticate_session", automation_flow)
        self.assertNotIn("import_cookies", automation_flow)

    def test_capture_worker_saves_session_metadata(self):
        capture_flow = self.source[
            self.source.index("def _capture_tiktok_cookies_worker"):
            self.source.index("def get_tiktok_cookies")
        ]
        self.assertIn("_save_session_auth_metadata", capture_flow)
        self.assertIn("_mark_session_failure", capture_flow)

    def test_session_metadata_helpers_exist(self):
        main_source = self.source
        for helper in ("_session_proxy_key", "_save_session_auth_metadata", "_mark_session_failure", "_wait_profile_lock_release"):
            self.assertIn("def " + helper, main_source)

    def test_verified_session_clears_manual_login_pending(self):
        meta_flow = self.source[
            self.source.index("def _save_session_auth_metadata"):
            self.source.index("def _mark_session_failure")
        ]
        self.assertIn("state == 'verified'", meta_flow)
        self.assertIn("'manual_login_pending'] = False", meta_flow)


if __name__ == "__main__":
    unittest.main()