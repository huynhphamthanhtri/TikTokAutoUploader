"""
test_ngrok_helper.py - Unit tests for youtube_monitor/ngrok_helper.py
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from youtube_monitor import ngrok_helper


class TestNgrokHelper(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_ngrok_"))

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch("youtube_monitor.ngrok_helper._bundled_ngrok")
    def test_find_ngrok_bundled_priority(self, mock_bundled):
        fake_bundled = self.tmp_dir / "bundled" / "ngrok.exe"
        fake_bundled.parent.mkdir(parents=True, exist_ok=True)
        fake_bundled.write_text("fake_ngrok")
        mock_bundled.return_value = fake_bundled

        found = ngrok_helper.find_ngrok()
        self.assertEqual(found, fake_bundled)
        self.assertEqual(ngrok_helper.ngrok_source(), "Bundled")

    @patch("youtube_monitor.ngrok_helper._bundled_ngrok", return_value=None)
    @patch("youtube_monitor.ngrok_helper.NGROK_APP_EXE")
    def test_find_ngrok_app_dir(self, mock_app_exe, mock_bundled):
        fake_app_exe = self.tmp_dir / "ngrok.exe"
        fake_app_exe.write_text("fake_ngrok")
        mock_app_exe.exists.return_value = True
        mock_app_exe.stat.return_value = MagicMock(st_size=100)
        mock_app_exe.__str__.return_value = str(fake_app_exe)

        # Mock find_ngrok
        with patch("youtube_monitor.ngrok_helper.NGROK_APP_EXE", fake_app_exe):
            found = ngrok_helper.find_ngrok()
            self.assertEqual(found, fake_app_exe)
            self.assertEqual(ngrok_helper.ngrok_source(), "App dir")

    def test_check_ngrok_status_when_missing(self):
        with patch("youtube_monitor.ngrok_helper.find_ngrok", return_value=None):
            ok, msg, src = ngrok_helper.check_ngrok()
            self.assertFalse(ok)
            self.assertIn("Chưa cài đặt", msg)
            self.assertEqual(src, "")

    def test_check_ngrok_status_when_available(self):
        fake_path = self.tmp_dir / "ngrok.exe"
        fake_path.write_text("binary")
        with patch("youtube_monitor.ngrok_helper.find_ngrok", return_value=fake_path), \
             patch("youtube_monitor.ngrok_helper.ngrok_source", return_value="App dir"):
            ok, msg, src = ngrok_helper.check_ngrok()
            self.assertTrue(ok)
            self.assertIn("Ngrok sẵn sàng", msg)
            self.assertEqual(src, "App dir")

    @patch("pyngrok.ngrok.install_ngrok")
    def test_ensure_ngrok_via_pyngrok(self, mock_install):
        target_exe = self.tmp_dir / "installed" / "ngrok.exe"
        target_exe.parent.mkdir(parents=True, exist_ok=True)

        def do_install(pyngrok_config=None):
            # Create valid file > 1MB
            target_exe.write_bytes(b"\x00" * (2 * 1024 * 1024))

        mock_install.side_effect = do_install

        with patch("youtube_monitor.ngrok_helper.find_ngrok", side_effect=[None, None, target_exe]):
            ok, msg = ngrok_helper.ensure_ngrok(target_exe=target_exe)
            self.assertTrue(ok)
            self.assertIn("Đã cài đặt ngrok", msg)
            self.assertTrue(target_exe.exists())


if __name__ == "__main__":
    unittest.main()
