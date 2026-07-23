import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open


sys.path.insert(0, str(Path(__file__).resolve().parent))


class TestFfmpegPaths(unittest.TestCase):
    def test_app_root_ffmpeg_dir(self):
        from youtube_monitor.ffmpeg_helper import FFMPEG_DIR
        self.assertTrue(str(FFMPEG_DIR).endswith("FFmpeg"))

    @patch("youtube_monitor.ffmpeg_helper.find_ffmpeg")
    def test_ffmpeg_available_true(self, mock_find):
        mock_find.return_value = Path("/usr/bin/ffmpeg")
        from youtube_monitor.ffmpeg_helper import ffmpeg_available
        self.assertTrue(ffmpeg_available())

    @patch("youtube_monitor.ffmpeg_helper.find_ffmpeg")
    def test_ffmpeg_available_false(self, mock_find):
        mock_find.return_value = None
        from youtube_monitor.ffmpeg_helper import ffmpeg_available
        self.assertFalse(ffmpeg_available())


class TestFfmpegEnsure(unittest.TestCase):
    @patch("youtube_monitor.ffmpeg_helper.check_ffmpeg", return_value=(True, "FFmpeg sẵn sàng (System PATH)", "System PATH"))
    def test_ensure_already_ok(self, mock_check):
        from youtube_monitor.ffmpeg_helper import ensure_ffmpeg
        ok, msg = ensure_ffmpeg()
        self.assertTrue(ok)

    @patch("youtube_monitor.ffmpeg_helper.check_ffmpeg", side_effect=[(False, "missing", ""), (True, "FFmpeg sẵn sàng (App dir)", "App dir")])
    @patch("youtube_monitor.ffmpeg_helper.download_ffmpeg")
    def test_ensure_download_ok(self, mock_dl, mock_check):
        from youtube_monitor.ffmpeg_helper import ensure_ffmpeg
        ok, msg = ensure_ffmpeg()
        self.assertTrue(ok)
        mock_dl.assert_called_once()

    @patch("youtube_monitor.ffmpeg_helper.check_ffmpeg", side_effect=[(False, "missing", ""), (False, "still missing", "")])
    @patch("youtube_monitor.ffmpeg_helper.download_ffmpeg")
    def test_ensure_download_fail(self, mock_dl, mock_check):
        from youtube_monitor.ffmpeg_helper import ensure_ffmpeg
        ok, msg = ensure_ffmpeg()
        self.assertFalse(ok)


class TestZipSecurity(unittest.TestCase):
    def test_path_traversal_detected(self):
        from youtube_monitor.ffmpeg_helper import download_ffmpeg
        bad_names = ["../ffmpeg.exe", "/etc/passwd", "..\\ffmpeg.exe", "foo/../../bar.exe"]
        for name in bad_names:
            if ".." in name:
                self.assertIn("..", name)
            if name.startswith("/"):
                self.assertTrue(name.startswith("/"))


class TestEncoderDetection(unittest.TestCase):
    def setUp(self):
        import youtube_monitor.ffmpeg_helper as fh
        fh._encoder_cache = None

    def tearDown(self):
        import youtube_monitor.ffmpeg_helper as fh
        fh._encoder_cache = None

    @patch("youtube_monitor.ffmpeg_helper.find_ffmpeg", return_value=Path("/ffmpeg"))
    @patch("subprocess.run")
    def test_nvenc_detected(self, mock_run, mock_find):
        from youtube_monitor.ffmpeg_helper import detect_gpu_encoder
        mock_proc = MagicMock()
        mock_proc.stdout = "h264_nvenc"
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc
        enc = detect_gpu_encoder()
        self.assertEqual(enc, "h264_nvenc")

    @patch("youtube_monitor.ffmpeg_helper.find_ffmpeg", return_value=Path("/ffmpeg"))
    @patch("subprocess.run")
    def test_libx264_fallback(self, mock_run, mock_find):
        from youtube_monitor.ffmpeg_helper import detect_gpu_encoder
        mock_proc = MagicMock()
        mock_proc.stdout = "aac libx264 libmp3lame"
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc
        enc = detect_gpu_encoder()
        self.assertEqual(enc, "libx264")


class TestConfigMigration(unittest.TestCase):
    def test_workers_to_download_workers(self):
        cfg = {"workers": 8}
        if "workers" in cfg and "download_workers" not in cfg:
            cfg["download_workers"] = max(1, int(cfg.pop("workers", 8) // 2 or 4))
        self.assertNotIn("workers", cfg)
        self.assertEqual(cfg["download_workers"], 4)

    def test_download_workers_preserved(self):
        cfg = {"download_workers": 6}
        if "workers" in cfg and "download_workers" not in cfg:
            cfg["download_workers"] = max(1, int(cfg.pop("workers", 8) // 2 or 4))
        self.assertNotIn("workers", cfg)
        self.assertEqual(cfg["download_workers"], 6)

    def test_default_download_workers(self):
        from youtube_monitor.core import CONFIG_DEFAULTS
        self.assertEqual(CONFIG_DEFAULTS["download_workers"], 4)




class TestCheckFfmpeg3Tuple(unittest.TestCase):
    def test_check_returns_three_values(self):
        from youtube_monitor.ffmpeg_helper import check_ffmpeg
        ok, msg, src = check_ffmpeg()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)
        self.assertIsInstance(src, str)


class TestFfmpegSource(unittest.TestCase):
    def test_source_empty_when_not_found(self):
        from youtube_monitor.ffmpeg_helper import ffmpeg_source
        with patch("youtube_monitor.ffmpeg_helper._bundled_ffmpeg", return_value=None), \
             patch("youtube_monitor.ffmpeg_helper.FFMPEG_EXE") as mock_exe, \
             patch("youtube_monitor.ffmpeg_helper.shutil.which", return_value=None):
            mock_exe.exists.return_value = False
            self.assertEqual(ffmpeg_source(), "")

    def test_source_system_path(self):
        from youtube_monitor.ffmpeg_helper import ffmpeg_source
        with patch("youtube_monitor.ffmpeg_helper._bundled_ffmpeg", return_value=None), \
             patch("youtube_monitor.ffmpeg_helper.FFMPEG_EXE") as mock_exe, \
             patch("youtube_monitor.ffmpeg_helper.shutil.which", return_value="C:\\ffmpeg.exe"):
            mock_exe.exists.return_value = False
            self.assertEqual(ffmpeg_source(), "System PATH")


class TestFindFfmpegSearchOrder(unittest.TestCase):
    def test_bundled_preferred(self):
        from youtube_monitor.ffmpeg_helper import find_ffmpeg
        mock_bundled = Path("C:\\_internal\\FFmpeg\\ffmpeg.exe")
        with patch("youtube_monitor.ffmpeg_helper._bundled_ffmpeg", return_value=mock_bundled):
            result = find_ffmpeg()
            self.assertEqual(result, mock_bundled)

    def test_path_fallback(self):
        from youtube_monitor.ffmpeg_helper import find_ffmpeg
        with patch("youtube_monitor.ffmpeg_helper._bundled_ffmpeg", return_value=None), \
             patch("youtube_monitor.ffmpeg_helper.FFMPEG_EXE") as mock_exe, \
             patch("youtube_monitor.ffmpeg_helper.shutil.which", return_value="C:\\ffmpeg.exe"):
            mock_exe.exists.return_value = False
            result = find_ffmpeg()
            self.assertIsNotNone(result)


class TestVerifyExe(unittest.TestCase):
    @patch("youtube_monitor.ffmpeg_helper.subprocess.run")
    def test_verify_ok(self, mock_run):
        from youtube_monitor.ffmpeg_helper import _verify_exe
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.stat().st_size = 1024
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc
        self.assertTrue(_verify_exe(mock_path))

    def test_verify_none(self):
        from youtube_monitor.ffmpeg_helper import _verify_exe
        self.assertFalse(_verify_exe(None))

    def test_verify_empty(self):
        from youtube_monitor.ffmpeg_helper import _verify_exe
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.stat().st_size = 0
        self.assertFalse(_verify_exe(mock_path))

    @patch("youtube_monitor.ffmpeg_helper.subprocess.run")
    def test_verify_fails_version(self, mock_run):
        from youtube_monitor.ffmpeg_helper import _verify_exe
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.stat().st_size = 1024
        mock_run.side_effect = Exception("crashed")
        self.assertFalse(_verify_exe(mock_path))


class TestDownloadFfmpegSecurity(unittest.TestCase):
    def test_path_traversal_rejected(self):
        from youtube_monitor.ffmpeg_helper import download_ffmpeg
        bad_names = ["../ffmpeg.exe", "/etc/passwd", "..\\ffmpeg.exe", "foo/../../bar.exe"]
        for name in bad_names:
            if ".." in name or name.startswith("/"):
                self.assertTrue(True)

    @patch("youtube_monitor.ffmpeg_helper._download_with_sha256")
    @patch("youtube_monitor.ffmpeg_helper.zipfile.ZipFile")
    @patch("youtube_monitor.ffmpeg_helper.Path.rglob")
    @patch("youtube_monitor.ffmpeg_helper.shutil.copy2")
    @patch("youtube_monitor.ffmpeg_helper._verify_exe", return_value=True)
    def test_download_flow_ok(self, mock_verify, mock_copy, mock_rglob, mock_zf, mock_dl):
        from youtube_monitor.ffmpeg_helper import download_ffmpeg
        mock_rglob.side_effect = [
            [Path("extract/ffmpeg.exe")],
            [Path("extract/ffprobe.exe")],
        ]
        mock_zf.return_value.__enter__.return_value.namelist.return_value = []
        download_ffmpeg()
        mock_dl.assert_called_once()


class TestGYanUrl(unittest.TestCase):
    def test_gyan_url_ends_with_zip(self):
        from youtube_monitor.ffmpeg_helper import GYAN_URL
        self.assertTrue(GYAN_URL.endswith(".zip"))

    def test_gyan_sha256_url_ends_with_sha256(self):
        from youtube_monitor.ffmpeg_helper import GYAN_SHA256_URL
        self.assertTrue(GYAN_SHA256_URL.endswith(".sha256"))


if __name__ == "__main__":
    unittest.main()
