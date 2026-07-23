import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from updater import (
    DEFAULT_RELEASE_NOTES_VI,
    GitHubReleaseUpdater,
    normalize_release_notes,
    run_background_check,
)


class UpdaterTests(unittest.TestCase):
    def test_normalize_release_notes_converts_markdown_to_plain_text(self):
        notes = "## Điểm mới\n- **Nhanh hơn**\n- [Xem thêm](https://example.com)\n```py\nsecret()\n```"
        normalized = normalize_release_notes(notes)
        self.assertIn("Điểm mới", normalized)
        self.assertIn("• Nhanh hơn", normalized)
        self.assertIn("• Xem thêm", normalized)
        self.assertNotIn("secret", normalized)
        self.assertNotIn("https://", normalized)

    def test_empty_release_notes_use_vietnamese_fallback(self):
        self.assertEqual(normalize_release_notes(""), DEFAULT_RELEASE_NOTES_VI)

    def test_check_update_returns_release_metadata(self):
        release = {
            "tag_name": "v1.0.10",
            "name": "Phiên bản 1.0.10",
            "body": "## Cải thiện\n- Ổn định hơn",
            "html_url": "https://github.com/owner/repo/releases/tag/v1.0.10",
            "published_at": "2026-07-19T00:00:00Z",
            "assets": [{
                "name": "TikTokAutoUploader-v1.0.10.zip",
                "browser_download_url": "https://example.com/app.zip",
                "size": 123,
            }],
        }
        updater = GitHubReleaseUpdater(Path.cwd(), "owner", "repo")
        with patch.object(updater, "get_latest_release", return_value=(release, None)):
            result = updater.check_update()
        self.assertTrue(result["has_update"])
        self.assertEqual(result["latest_version"], "1.0.10")
        self.assertEqual(result["release_name"], "Phiên bản 1.0.10")
        self.assertIn("• Ổn định hơn", result["release_notes"])
        self.assertEqual(result["release_url"], release["html_url"])

    def test_background_check_schedules_ui_callback_and_saves_check_time(self):
        result = {"has_update": True, "latest_version": "1.0.5", "error": None}
        scheduled = []
        on_update = Mock()
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch("updater.load_updater_config", return_value={"auto_check": True, "skip_version": ""}), \
                patch("updater.update_updater_config") as update_config, \
                patch("updater.GitHubReleaseUpdater.check_update", return_value=result):
            thread = run_background_check(
                "owner", "repo", "", temp_dir,
                on_update=on_update,
                on_error=Mock(),
                on_current=Mock(),
                schedule=lambda callback: scheduled.append(callback),
            )
            thread.join(timeout=2)
        self.assertEqual(len(scheduled), 1)
        scheduled[0]()
        on_update.assert_called_once_with(result)
        self.assertGreater(update_config.call_args.kwargs["last_check_epoch"], 0)

    def test_background_check_honors_auto_check_and_skip_version(self):
        with patch("updater.load_updater_config", return_value={"auto_check": False}):
            self.assertIsNone(run_background_check("o", "r", "", ".", Mock(), Mock(), Mock()))

        on_update = Mock()
        result = {"has_update": True, "latest_version": "1.0.5", "error": None}
        with patch("updater.load_updater_config", return_value={"auto_check": True, "skip_version": "1.0.5"}), \
                patch("updater.update_updater_config"), \
                patch("updater.GitHubReleaseUpdater.check_update", return_value=result):
            thread = run_background_check("o", "r", "", ".", on_update, Mock(), Mock())
            thread.join(timeout=2)
        on_update.assert_not_called()

    def test_error_result_is_not_suppressed_by_empty_skip_version(self):
        scheduled = []
        on_error = Mock()
        result = {"has_update": False, "error": "GitHub unavailable"}
        with patch("updater.load_updater_config", return_value={"auto_check": True, "skip_version": ""}), \
                patch("updater.update_updater_config"), \
                patch("updater.GitHubReleaseUpdater.check_update", return_value=result):
            thread = run_background_check(
                "o", "r", "", ".", Mock(), on_error, Mock(),
                schedule=lambda callback: scheduled.append(callback),
            )
            thread.join(timeout=2)
        self.assertEqual(len(scheduled), 1)
        scheduled[0]()
        on_error.assert_called_once_with("GitHub unavailable")

    def test_invalid_release_tag_returns_error(self):
        updater = GitHubReleaseUpdater(Path.cwd(), "owner", "repo")
        release = {"tag_name": "not-a-version", "assets": []}
        with patch.object(updater, "get_latest_release", return_value=(release, None)):
            result = updater.check_update()
        self.assertFalse(result["has_update"])
        self.assertIn("Tag release không hợp lệ", result["error"])

    def test_update_script_contains_rollback_path(self):
        with tempfile.TemporaryDirectory() as app_dir, tempfile.TemporaryDirectory() as new_dir:
            updater = GitHubReleaseUpdater(app_dir, "owner", "repo")
            script = updater.write_update_script(Path(new_dir))
            content = script.read_text(encoding="utf-8")
        self.assertIn(":rollback", content)
        self.assertIn("_internal_backup", content)
        self.assertIn("update_error.txt", content)


if __name__ == "__main__":
    unittest.main()
