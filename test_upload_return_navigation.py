import unittest
from pathlib import Path


class UploadReturnNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.main_source = (root / "main.py").read_text(encoding="utf-8")
        cls.glue_source = (root / "browser_patchright_glue.py").read_text(encoding="utf-8")
        cls.upload_source = (root / "patchright_upload.py").read_text(encoding="utf-8")
        cls.login_source = (root / "login_environment_runner.py").read_text(encoding="utf-8")

    def test_upload_url_is_standardized_with_tab_video(self):
        self.assertIn(
            "TIKTOK_UPLOAD_URL = \"https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video\"",
            self.main_source,
        )
        self.assertIn(
            "UPLOAD_URL = \"https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video\"",
            self.upload_source,
        )
        self.assertIn(
            "STUDIO_UPLOAD_URL = \"https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video\"",
            self.login_source,
        )

    def test_glue_exposes_navigate_upload_ready_helpers(self):
        self.assertIn("def navigate_upload_ready", self.glue_source)
        self.assertIn("def wait_upload_page_ready", self.glue_source)
        ready_flow = self.glue_source[self.glue_source.index("def wait_upload_page_ready"):]
        self.assertIn("page_login_state(token, timeout=run_timeout)", ready_flow)
        self.assertIn('state == "authenticated"', ready_flow)
        self.assertIn("return False", ready_flow)

    def test_upload_video_returns_to_upload_page_after_posted(self):
        posted_flow = self.main_source[
            self.main_source.index("if result.outcome == 'posted':"):
            self.main_source.index("if result.outcome == 'prepared':")
        ]
        self.assertIn("_return_to_upload_page(profile_name)", posted_flow)
        self.assertIn("open_only_when_video", posted_flow)

    def test_return_to_upload_page_uses_glue_helper_and_never_downgrades_success(self):
        start = self.main_source.index("def _return_to_upload_page")
        block = self.main_source[start:self.main_source.index("def process_video_queue_thread", start)]
        self.assertIn("browser_glue.navigate_upload_ready(token, TIKTOK_UPLOAD_URL)", block)
        self.assertIn("browser='Sẵn sàng'", block)
        self.assertIn("detach_automation()", block)
        self.assertNotIn("upload_video(profile_name", block)
        self.assertNotIn("return False\n\n\n            if result.outcome == 'posted':", self.main_source)

    def test_open_only_mode_closes_browser_without_return_navigation(self):
        queue_flow = self.main_source[
            self.main_source.index("def process_video_queue_thread"):
            self.main_source.index("# =========================\n# UI Helpers & Log")
        ]
        self.assertIn("close_profile_browser(profile_name)", queue_flow)
        self.assertIn("open_only", queue_flow)
        self.assertNotIn("_return_to_upload_page", queue_flow)

    def test_return_after_post_does_not_change_success_result(self):
        start = self.main_source.index("def _return_to_upload_page")
        block = self.main_source[start:self.main_source.index("def process_video_queue_thread", start)]
        self.assertIn("ready = False", block)
        self.assertIn("if ready:", block)
        self.assertIn("return True", block)
        self.assertIn("return False", block)

    def test_pre_post_dry_run_does_not_increment_upload_count(self):
        prepared_flow = self.main_source[
            self.main_source.index("if result.outcome == 'prepared':"):
            self.main_source.index("no_retry = result.post_dispatched", self.main_source.index("if result.outcome == 'prepared':"))
        ]
        self.assertIn("return 'prepared'", prepared_flow)
        queue_flow = self.main_source[
            self.main_source.index("def process_video_queue_thread"):
            self.main_source.index("# =========================\n# UI Helpers & Log")
        ]
        self.assertIn("if ok == 'prepared':", queue_flow)
        prepared_branch = queue_flow[
            queue_flow.index("if ok == 'prepared':"):
            queue_flow.index("elif ok:", queue_flow.index("if ok == 'prepared':"))
        ]
        self.assertNotIn("uploads_today_count += 1", prepared_branch)
        harness = self.main_source[self.main_source.index("def _run_single_upload_test_from_env"):]
        self.assertIn("UPLOAD_TEST_STOP_BEFORE_POST", harness)
        self.assertIn("target_path.name.startswith('UPLOAD_TEST_')", harness)
        self.assertIn("target_path.unlink()", harness)


if __name__ == "__main__":
    unittest.main()
