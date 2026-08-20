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
    def test_short_rules_constants(self):
        from youtube_monitor.core import SHORT_SLOW_MIN_DURATION, SHORT_SLOW_MAX_DURATION, SHORT_TARGET_DURATION
        self.assertEqual(SHORT_SLOW_MIN_DURATION, 40.0)
        self.assertEqual(SHORT_SLOW_MAX_DURATION, 60.0)
        self.assertEqual(SHORT_TARGET_DURATION, 61.0)

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp")
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration", return_value=None)
    def test_under_40s_kept_unchanged_no_probe(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing
        result = apply_short_processing("v39.mp4", 39.9, True)
        self.assertEqual(result, ("v39.mp4", []))
        mock_probe.assert_not_called()
        mock_slow.assert_not_called()

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp", return_value=("slow48.mp4", ["slow48.mp4"]))
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration", return_value=None)
    def test_40s_slowed_to_61s(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing, SHORT_TARGET_DURATION
        result = apply_short_processing("v40.mp4", 40.0, True)
        mock_probe.assert_called_once_with("v40.mp4")
        mock_slow.assert_called_once_with("v40.mp4", SHORT_TARGET_DURATION)
        self.assertEqual(result, ("slow48.mp4", ["slow48.mp4"]))

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp", return_value=("slow59.mp4", ["slow59.mp4"]))
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration", return_value=None)
    def test_59_9s_slowed_to_61s(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing, SHORT_TARGET_DURATION
        result = apply_short_processing("v59.mp4", 59.9, True)
        mock_slow.assert_called_once_with("v59.mp4", SHORT_TARGET_DURATION)
        self.assertEqual(result, ("slow59.mp4", ["slow59.mp4"]))

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp", return_value=("slow60.mp4", ["slow60.mp4"]))
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration", return_value=None)
    def test_60s_slowed_to_61s(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing, SHORT_TARGET_DURATION
        result = apply_short_processing("v60.mp4", 60.0, True)
        mock_probe.assert_called_once_with("v60.mp4")
        mock_slow.assert_called_once_with("v60.mp4", SHORT_TARGET_DURATION)
        self.assertEqual(result, ("slow60.mp4", ["slow60.mp4"]))

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp")
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration")
    def test_60_1s_kept_unchanged_no_probe(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing
        result = apply_short_processing("v60_1.mp4", 60.1, True)
        self.assertEqual(result, ("v60_1.mp4", []))
        mock_probe.assert_not_called()
        mock_slow.assert_not_called()

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp")
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration")
    def test_75s_kept_unchanged_no_probe(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing
        result = apply_short_processing("v75.mp4", 75.0, True)
        self.assertEqual(result, ("v75.mp4", []))
        mock_probe.assert_not_called()
        mock_slow.assert_not_called()

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp")
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration")
    def test_short_disabled_keeps_unchanged(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing
        result = apply_short_processing("v48.mp4", 48.0, False)
        self.assertEqual(result, ("v48.mp4", []))
        mock_probe.assert_not_called()
        mock_slow.assert_not_called()

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp", return_value=("slow50.mp4", ["slow50.mp4"]))
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration", return_value=50.0)
    def test_zero_duration_reprobes_then_slows(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing
        result = apply_short_processing("v0.mp4", 0, True)
        mock_probe.assert_called_once_with("v0.mp4")
        mock_slow.assert_called_once()
        self.assertEqual(result, ("slow50.mp4", ["slow50.mp4"]))

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp", return_value=("slow45.mp4", ["slow45.mp4"]))
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration", return_value=None)
    def test_probe_none_falls_back_to_reported_duration(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing
        result = apply_short_processing("v45.mp4", 45.0, True)
        mock_slow.assert_called_once()
        self.assertEqual(result, ("slow45.mp4", ["slow45.mp4"]))

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp", return_value=("slow48.mp4", ["slow48.mp4"]))
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration", return_value=0)
    def test_probe_zero_uses_reported_duration(self, mock_probe, mock_slow):
        from youtube_monitor.core import apply_short_processing
        result = apply_short_processing("v48.mp4", 48.0, True)
        mock_slow.assert_called_once()
        self.assertEqual(result, ("slow48.mp4", ["slow48.mp4"]))

    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp", side_effect=RuntimeError("transcode failed"))
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration", return_value=None)
    def test_slowdown_failure_raises_but_source_intact(self, mock_probe, mock_slow):
        import tempfile
        from pathlib import Path
        from youtube_monitor.core import apply_short_processing
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "v48.mp4"
            src.write_bytes(b"original")
            with self.assertRaises(RuntimeError):
                apply_short_processing(str(src), 48.0, True)
            self.assertEqual(src.read_bytes(), b"original")


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
