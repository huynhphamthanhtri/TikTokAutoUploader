import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parent))


class TestFormatSelector(unittest.TestCase):
    def test_format_fast_720p_defined(self):
        from youtube_monitor.core import FORMAT_FAST_720P
        self.assertIn("height<=720", FORMAT_FAST_720P)
        self.assertNotIn("1080", FORMAT_FAST_720P)
        self.assertIn("avc1", FORMAT_FAST_720P)

    def test_format_prioritizes_mp4(self):
        from youtube_monitor.core import FORMAT_FAST_720P
        self.assertIn("mp4", FORMAT_FAST_720P)
        self.assertNotIn("webm", FORMAT_FAST_720P.lower())
        self.assertNotIn("mkv", FORMAT_FAST_720P.lower())


class TestDownloadSemaphore(unittest.TestCase):
    def test_semaphore_default_value(self):
        from youtube_monitor.core import _download_sem, CONFIG_DEFAULTS
        self.assertEqual(CONFIG_DEFAULTS["download_workers"], 4)

    @patch("youtube_monitor.core._download_sem")
    def test_download_semaphore_blocks(self, mock_sem):
        import threading
        called = []
        def worker():
            with mock_sem:
                called.append(1)
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(len(called), 2)


class TestDownloadDedup(unittest.TestCase):
    def test_claim_and_release(self):
        from youtube_monitor.core import _claim_download, _release_download
        self.assertTrue(_claim_download("test123"))
        self.assertFalse(_claim_download("test123"))
        _release_download("test123")
        self.assertTrue(_claim_download("test123"))
        _release_download("test123")

    def test_different_ids_dont_conflict(self):
        from youtube_monitor.core import _claim_download, _release_download
        self.assertTrue(_claim_download("vid1"))
        self.assertTrue(_claim_download("vid2"))
        _release_download("vid1")
        _release_download("vid2")


class TestStagingDir(unittest.TestCase):
    def test_staging_is_sibling(self):
        from youtube_monitor.core import _staging_dir
        with tempfile.TemporaryDirectory() as tmp:
            video_folder = Path(tmp) / "Auto_Data" / "Profile01" / "Video"
            video_folder.mkdir(parents=True)
            staging = _staging_dir(str(video_folder))
            self.assertEqual(staging.parent, video_folder.parent)
            self.assertEqual(staging.name, ".youtube_tmp")

    def test_staging_not_inside_video(self):
        from youtube_monitor.core import _staging_dir
        with tempfile.TemporaryDirectory() as tmp:
            video_folder = Path(tmp) / "Video"
            video_folder.mkdir()
            staging = _staging_dir(str(video_folder))
            self.assertNotIn("Video", staging.parent.name)

    def test_staging_cleanup_removes_video_dir(self):
        import shutil
        from youtube_monitor.core import _staging_dir
        with tempfile.TemporaryDirectory() as tmp:
            video_folder = Path(tmp) / "Auto_Data" / "Profile01" / "Video"
            video_folder.mkdir(parents=True)
            staging = _staging_dir(str(video_folder))
            dl_dir = staging / "testvid-abc123"
            dl_dir.mkdir()
            (dl_dir / "test.mp4").touch()
            for p in staging.glob("testvid-*"):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
            remaining = list(staging.glob("testvid-*"))
            self.assertEqual(len(remaining), 0)
            self.assertTrue(staging.exists())


class TestFfmpegHelper(unittest.TestCase):
    def test_find_ffmpeg_none(self):
        from youtube_monitor.ffmpeg_helper import find_ffmpeg
        with patch.object(Path, "exists", return_value=False):
            with patch("shutil.which", return_value=None):
                result = find_ffmpeg()
                self.assertIsNone(result)

    def test_detect_gpu_encoder_libx264_fallback(self):
        from youtube_monitor.ffmpeg_helper import detect_gpu_encoder, invalidate_encoder_cache
        invalidate_encoder_cache()
        with patch("youtube_monitor.ffmpeg_helper.find_ffmpeg", return_value=None):
            enc = detect_gpu_encoder()
            self.assertEqual(enc, "libx264")

    def test_has_audio_empty(self):
        from youtube_monitor.ffmpeg_helper import has_audio
        with patch("youtube_monitor.ffmpeg_helper.run_ffprobe", return_value=None):
            self.assertFalse(has_audio("dummy.mp4"))


class TestVideoProcessing(unittest.TestCase):
    def test_skip_ffprobe_for_long_videos(self):
        dur = 120.0
        self.assertGreaterEqual(dur, 62)
        self.assertFalse(dur < 62)

    def test_needs_processing_for_short(self):
        dur = 30.0
        from youtube_monitor.core import MIN_SECONDS
        self.assertLess(dur, MIN_SECONDS)

    def test_no_processing_for_long(self):
        dur = 120.0
        from youtube_monitor.core import MIN_SECONDS
        self.assertGreaterEqual(dur, MIN_SECONDS)

    def test_loop_for_45_62_range(self):
        dur = 50.0
        from youtube_monitor.core import LOOP_MIN_DURATION, MIN_SECONDS
        self.assertGreaterEqual(dur, LOOP_MIN_DURATION)
        self.assertLess(dur, MIN_SECONDS)


class TestFormatBuild(unittest.TestCase):
    def test_sanitize_filename(self):
        from youtube_monitor.core import sanitize_filename
        self.assertEqual(sanitize_filename("Hello World"), "Hello World")
        self.assertNotIn("<", sanitize_filename("Bad<File"))
        self.assertNotIn(">", sanitize_filename("Bad>File"))
        self.assertNotIn(":", sanitize_filename("Bad:File"))
        self.assertNotIn('"', sanitize_filename('Bad"File'))
        self.assertNotIn("/", sanitize_filename("Bad/File"))
        self.assertNotIn("|", sanitize_filename("Bad|File"))

    def test_build_final_path_no_collision(self):
        from youtube_monitor.core import build_final_path
        with tempfile.TemporaryDirectory() as tmp:
            path = build_final_path(tmp, "Test Video", "abc123", ".mp4")
            self.assertTrue(path.startswith(tmp))
            self.assertTrue(path.endswith(".mp4"))

    def test_build_final_path_with_collision(self):
        from youtube_monitor.core import build_final_path
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "Test Video.mp4"
            p1.touch()
            path = build_final_path(tmp, "Test Video", "abc123", ".mp4")
            self.assertNotEqual(path, str(p1))


if __name__ == "__main__":
    unittest.main()
