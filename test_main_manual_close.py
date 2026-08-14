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


if __name__ == "__main__":
    unittest.main()