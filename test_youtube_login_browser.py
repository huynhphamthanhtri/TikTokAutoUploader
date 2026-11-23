import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from unittest.mock import MagicMock

from youtube_monitor.login_browser import YOUTUBE_LOGIN_URL, YouTubeLoginBrowser


class TestYouTubeLoginBrowser(unittest.TestCase):
    def test_opens_persistent_profile_without_shell_or_python(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome.exe"
            executable.touch()
            process = MagicMock()
            process.poll.return_value = None
            factory = MagicMock(return_value=process)
            profile = Path(directory) / "youtube-profile"
            browser = YouTubeLoginBrowser(profile, lambda: str(executable), factory)

            ok, _message = browser.open()

            self.assertTrue(ok)
            args = factory.call_args.args[0]
            self.assertEqual(args[0], str(executable))
            self.assertIn(f"--user-data-dir={profile}", args)
            self.assertIn(YOUTUBE_LOGIN_URL, args)
            self.assertNotIn("main.py", " ".join(args))
            self.assertFalse(factory.call_args.kwargs["shell"])

    def test_second_click_does_not_open_duplicate_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome.exe"
            executable.touch()
            process = MagicMock()
            process.poll.return_value = None
            factory = MagicMock(return_value=process)
            browser = YouTubeLoginBrowser(Path(directory) / "profile", lambda: str(executable), factory)
            self.assertTrue(browser.open()[0])
            self.assertTrue(browser.open()[0])
            self.assertEqual(factory.call_count, 1)

    def test_missing_browser_fails_without_process(self):
        factory = MagicMock()
        browser = YouTubeLoginBrowser("missing-profile", lambda: "missing-browser.exe", factory)
        ok, message = browser.open()
        self.assertFalse(ok)
        self.assertIn("Không tìm thấy", message)
        factory.assert_not_called()

    def test_immediate_browser_exit_is_reported_as_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome.exe"
            executable.touch()
            process = MagicMock()
            process.poll.return_value = 7
            browser = YouTubeLoginBrowser(
                Path(directory) / "profile", lambda: str(executable), MagicMock(return_value=process)
            )
            ok, message = browser.open()
            self.assertFalse(ok)
            self.assertIn("mã 7", message)

    def test_default_resolver_prefers_installed_chrome(self):
        with tempfile.TemporaryDirectory() as directory:
            chrome = Path(directory) / "Google/Chrome/Application/chrome.exe"
            chrome.parent.mkdir(parents=True)
            chrome.touch()
            with patch.dict("os.environ", {"ProgramFiles": directory}, clear=False):
                self.assertEqual(YouTubeLoginBrowser._default_resolver(), str(chrome))

    def test_on_close_callback_fires_when_process_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome.exe"
            executable.touch()
            process = MagicMock()
            process.poll.return_value = None
            factory = MagicMock(return_value=process)
            browser = YouTubeLoginBrowser(Path(directory) / "profile", lambda: str(executable), factory)

            callback_fired = False
            def callback():
                nonlocal callback_fired
                callback_fired = True

            ok, _ = browser.open(on_close=callback)
            self.assertTrue(ok)
            # Simulate process termination
            process.wait.return_value = 0
            # Wait a moment for watcher thread to finish
            time_limit = time.time() + 2.0
            while not callback_fired and time.time() < time_limit:
                time.sleep(0.05)
            self.assertTrue(callback_fired)

    def test_stale_generation_does_not_fire_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome.exe"
            executable.touch()
            proc1 = MagicMock()
            proc1.poll.return_value = None
            # proc1 wait blocks until we unblock it
            proc1_wait_event = threading.Event()
            def proc1_wait():
                proc1_wait_event.wait(timeout=5)
                return 0
            proc1.wait = proc1_wait

            proc2 = MagicMock()
            proc2.poll.return_value = None

            factory = MagicMock(side_effect=[proc1, proc2])
            browser = YouTubeLoginBrowser(Path(directory) / "profile", lambda: str(executable), factory)

            cb1_fired = False
            def cb1():
                nonlocal cb1_fired
                cb1_fired = True

            browser.open(on_close=cb1)
            # Now simulate manual generation bump (reopen)
            with browser._lock:
                browser._generation_id += 1  # gen 2 now
            # Now proc1 finishes
            proc1_wait_event.set()
            time.sleep(0.1)
            # cb1 should NOT have fired because gen was bumped
            self.assertFalse(cb1_fired)



if __name__ == "__main__":
    unittest.main()
