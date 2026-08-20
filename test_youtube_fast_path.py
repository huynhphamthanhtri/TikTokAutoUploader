"""
test_youtube_fast_path.py - Unit tests for YouTube Fast Path Direct Enqueue & Path Claiming.
"""

import tempfile
import unittest
from pathlib import Path

from watchdog_service import claim_video_path, is_video_claimed


class TestYouTubeFastPath(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.video_file = Path(self.temp_dir.name) / "youtube_fast_video.mp4"
        self.video_file.write_bytes(b"sample_content_123")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_claim_video_path_deduplication(self):
        p_str = str(self.video_file)
        # First claim succeeds
        self.assertTrue(claim_video_path(p_str))
        self.assertTrue(is_video_claimed(p_str))

        # Duplicate claim by watchdog or subsequent event fails
        self.assertFalse(claim_video_path(p_str))
        # Case insensitive Windows check
        self.assertFalse(claim_video_path(p_str.upper()))


if __name__ == "__main__":
    unittest.main()
