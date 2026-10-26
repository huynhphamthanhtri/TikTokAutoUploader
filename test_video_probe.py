"""
test_video_probe.py - Bộ kiểm thử độc lập cho video_probe.py (Deep Video Validation).

Quy tắc bắt buộc:
- TUÂN THỦ 100% RULE 14: Zero Live Data Mutation.
- Toàn bộ file và thư mục test dùng tempfile.mkdtemp(), dọn dẹp sạch sẽ sau khi test.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from schedule_contracts import VideoProbeResult, VideoValidationPolicy
from video_probe import batch_probe_videos, clear_probe_cache, probe_video, _RAW_METADATA_CACHE


class TestVideoProbe(unittest.TestCase):
    def setUp(self):
        clear_probe_cache()
        self.test_dir = tempfile.mkdtemp(prefix="test_probe_")
        self.test_path = Path(self.test_dir)

    def tearDown(self):
        clear_probe_cache()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_file_not_found(self):
        """File không tồn tại phải trả về is_valid = False và issue FILE_NOT_FOUND."""
        non_existent = self.test_path / "does_not_exist.mp4"
        res = probe_video(non_existent)
        self.assertFalse(res.is_valid)
        self.assertIn("FILE_NOT_FOUND", res.issues)
        self.assertEqual(res.size_bytes, 0)

    def test_empty_zero_byte_file(self):
        """File 0 byte phải trả về is_valid = False và issue EMPTY_FILE_ZERO_BYTES."""
        empty_file = self.test_path / "zero.mp4"
        empty_file.write_bytes(b"")

        res = probe_video(empty_file)
        self.assertFalse(res.is_valid)
        self.assertIn("EMPTY_FILE_ZERO_BYTES", res.issues)
        self.assertEqual(res.size_bytes, 0)

    def test_f01_cache_across_policies(self):
        """F01: Cache lưu raw metadata; probe với policy dễ rồi policy chặt phải cho kết quả khác nhau."""
        test_file = self.test_path / "policy_test.mp4"
        test_file.write_bytes(b"dummy bytes for 2s video")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"duration":"2.0"}],"format":{"duration":"2.0"}}',
                stderr="",
            )

            # Lần 1: Policy dễ dãi (min_duration = 1.0s) -> HỢP LỆ
            lenient_policy = VideoValidationPolicy(min_duration_seconds=1.0)
            res1 = probe_video(test_file, policy=lenient_policy)
            self.assertTrue(res1.is_valid)
            self.assertEqual(mock_run.call_count, 1)

            # Lần 2: Policy chuẩn (min_duration = 3.0s) -> BỊ TỪ CHỐI (không được lấy nhầm is_valid=True cũ từ cache)
            strict_policy = VideoValidationPolicy(min_duration_seconds=3.0)
            res2 = probe_video(test_file, policy=strict_policy)
            self.assertFalse(res2.is_valid)
            self.assertTrue(any("DURATION_BELOW_MIN" in i for i in res2.issues))
            self.assertEqual(mock_run.call_count, 1)

    def test_f02_missing_ffprobe_returns_invalid_and_not_cached(self):
        """F02: Khi ffprobe không khả dụng, trả is_valid = False và KHÔNG lưu vào cache vĩnh viễn."""
        test_file = self.test_path / "valid_size.mp4"
        test_file.write_bytes(b"some content")

        res = probe_video(test_file, ffprobe_bin="non_existent_ffprobe_path_xyz")
        self.assertFalse(res.is_valid)
        self.assertIn("FFPROBE_NOT_AVAILABLE", res.issues)

        # Đảm bảo không lưu lỗi tool vào _RAW_METADATA_CACHE
        canon_path = str(test_file.resolve())
        cache_keys = [k for k in _RAW_METADATA_CACHE.keys() if k[0] == canon_path]
        self.assertEqual(len(cache_keys), 0)

    def test_f13_schema_robustness(self):
        """F13: Schema bất thường (null root, streams null, duration NaN) phải bị bắt an toàn."""
        test_file = self.test_path / "weird_schema.mp4"
        test_file.write_bytes(b"content")

        # Case 1: Root null
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="null", stderr="")
            res = probe_video(test_file)
            self.assertFalse(res.is_valid)
            self.assertTrue(any("INVALID_JSON" in i for i in res.issues))

        clear_probe_cache()
        # Case 2: Streams chứa null
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[null, {"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"duration":"15.0"}],"format":{"duration":"15.0"}}',
                stderr="",
            )
            res = probe_video(test_file)
            self.assertTrue(res.is_valid)

        clear_probe_cache()
        # Case 3: codec_name là integer (không phải string) không được ném AttributeError
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":12345,"width":1080,"height":1920,"duration":"15.0"}],"format":{"duration":"15.0"}}',
                stderr="",
            )
            res = probe_video(test_file)
            self.assertFalse(res.is_valid)
            self.assertTrue(any("INVALID_OR_MISSING_VIDEO_CODEC_SCHEMA" in i for i in res.issues))

        clear_probe_cache()
        # Case 4: duration là NaN hoặc Infinity
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"duration":"NaN"}],"format":{"duration":"Infinity"}}',
                stderr="",
            )
            res = probe_video(test_file)
            self.assertFalse(res.is_valid)
            self.assertTrue(any("DURATION_NAN_OR_NON_POSITIVE" in i for i in res.issues))

        clear_probe_cache()
        # Case 5: codec_name là dictionary object {"name": "h264"} bị từ chối schema
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":{"name":"h264"},"width":1080,"height":1920,"duration":"10.0"}],"format":{"duration":"10.0"}}',
                stderr="",
            )
            res = probe_video(test_file)
            self.assertFalse(res.is_valid)
            self.assertTrue(any("INVALID_OR_MISSING_VIDEO_CODEC_SCHEMA" in i for i in res.issues))

        clear_probe_cache()
        # Case 6: codec_name là string "not_h264" chứa substring "h264" nhưng không phải canonical codec hợp lệ
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":"not_h264","width":1080,"height":1920,"duration":"10.0"}],"format":{"duration":"10.0"}}',
                stderr="",
            )
            res = probe_video(test_file)
            self.assertFalse(res.is_valid)
            self.assertTrue(any("CODEC_NOT_RECOMMENDED" in i for i in res.issues))

    def test_probe_timeout(self):
        """Khi ffprobe bị timeout, trả is_valid=False và issue PROBE_TIMEOUT."""
        test_file = self.test_path / "timeout_test.mp4"
        test_file.write_bytes(b"content")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=15.0)):
            res = probe_video(test_file, timeout_seconds=15.0)
            self.assertFalse(res.is_valid)
            self.assertIn("PROBE_TIMEOUT", res.issues)

    def test_tool_missing_then_restored_cache_recovery(self):
        """F02 Lifecycle: Khi tool thiếu không được cache lỗi; khi tool khôi phục probe lại phải thành công."""
        test_file = self.test_path / "recovery_test.mp4"
        test_file.write_bytes(b"content")

        # Bước 1: Tool thiếu (shutil.which trả None)
        with patch("shutil.which", return_value=None):
            res_missing = probe_video(test_file)
            self.assertFalse(res_missing.is_valid)
            self.assertIn("FFPROBE_NOT_AVAILABLE", res_missing.issues)

        # Bước 2: Tool được cài đặt / khôi phục (shutil.which trả đường dẫn hợp lệ)
        with patch("shutil.which", return_value="/usr/bin/ffprobe"), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"duration":"10.0"}],"format":{"duration":"10.0"}}',
                stderr="",
            )
            res_recovered = probe_video(test_file)
            self.assertTrue(res_recovered.is_valid)
            self.assertEqual(mock_run.call_count, 1)

    def test_pixel_format_and_cache_immutability(self):
        """F08 / F13: pixel_format là chuỗi bất biến; sửa đổi kết quả probe không ảnh hưởng cache."""
        test_file = self.test_path / "pix_fmt_test.mp4"
        test_file.write_bytes(b"content")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"pix_fmt":"yuv420p","duration":"10.0"}],"format":{"duration":"10.0"}}',
                stderr="",
            )
            res1 = probe_video(test_file)
            self.assertEqual(res1.pixel_format, "yuv420p")

            # Gọi lại lần 2 từ cache
            res2 = probe_video(test_file)
            self.assertEqual(res2.pixel_format, "yuv420p")
            self.assertEqual(mock_run.call_count, 1)

    def test_probe_top_level_exception_resilience(self):
        """probe_video trực tiếp bắt trọn mọi exception không kiểm soát và trả kết quả có cấu trúc."""
        test_file = self.test_path / "crash_test.mp4"
        test_file.write_bytes(b"content")

        with patch("video_probe._get_raw_metadata", side_effect=RuntimeError("Simulated unexpected internal failure")):
            res = probe_video(test_file)
            self.assertFalse(res.is_valid)
            self.assertIn("UNHANDLED_EXCEPTION_IN_PROBE", res.issues[0])

    def test_explicit_ffprobe_bin_path(self):
        """ffprobe_bin được truyền tường minh vào probe_video độc lập với shutil.which."""
        test_file = self.test_path / "bin_test.mp4"
        test_file.write_bytes(b"content")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"duration":"10.0"}],"format":{"duration":"10.0"}}',
                stderr="",
            )
            custom_bin = "C:\\Custom\\ffmpeg\\bin\\ffprobe.exe"
            res = probe_video(test_file, ffprobe_bin=custom_bin)
            self.assertTrue(res.is_valid)
            # Kiểm tra subprocess được gọi với đúng binary được truyền vào
            called_cmd = mock_run.call_args[0][0]
            self.assertEqual(called_cmd[0], custom_bin)

    def test_corrupt_file_handling(self):
        """File hỏng container trả về lỗi ffprobe phải được đánh dấu CORRUPT_OR_UNREADABLE_MEDIA."""
        corrupt_file = self.test_path / "corrupt.mp4"
        corrupt_file.write_bytes(b"not a valid mp4 header content at all")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="Invalid data found when processing input",
            )

            res = probe_video(corrupt_file)
            self.assertFalse(res.is_valid)
            self.assertTrue(any("CORRUPT" in issue for issue in res.issues))

    def test_batch_probe_preserves_order(self):
        """batch_probe_videos phải giữ nguyên đúng thứ tự danh sách file đầu vào."""
        f1 = self.test_path / "vid1.mp4"
        f2 = self.test_path / "vid2.mp4"
        f3 = self.test_path / "vid3.mp4"

        f1.write_bytes(b"vid1 bytes")
        f2.write_bytes(b"vid2 bytes")
        f3.write_bytes(b"vid3 bytes")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout='{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,"duration":"10.0"}],"format":{"duration":"10.0"}}',
                stderr="",
            )
            results = batch_probe_videos([f1, f2, f3], max_workers=2)
            self.assertEqual(len(results), 3)
            self.assertEqual(results[0].canonical_path, str(f1.resolve()))
            self.assertEqual(results[1].canonical_path, str(f2.resolve()))
            self.assertEqual(results[2].canonical_path, str(f3.resolve()))


if __name__ == "__main__":
    unittest.main()
