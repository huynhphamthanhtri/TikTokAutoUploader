# -*- coding: utf-8 -*-
"""
test_check_dup_public.py - Test suite kiểm tra tính năng F3 (CheckDup kênh công khai).
Đảm bảo tuân thủ nghiêm ngặt AGENTS.md:
- Kiểm tra tính đúng đắn nghiệp vụ (bóc tách username, format view, phát hiện video trùng theo group_id).
- Đảm bảo độc lập tuyệt đối, không dùng cookie hay proxy từ kho profile.
"""

import unittest
from unittest.mock import MagicMock, patch
from io import StringIO
import csv

from check_features.models import AwemePost, ChannelMeta
from check_features.clients.public_channel_client import TikTokPublicChannelClient
from check_features.ui.check_dup_view import format_views


class TestTikTokPublicChannelClient(unittest.TestCase):
    """Kiểm tra logic client quét kênh TikTok công khai."""

    def setUp(self):
        self.client = TikTokPublicChannelClient()

    def test_extract_username_formats(self):
        """Kiểm tra bóc tách username chuẩn từ nhiều dạng input."""
        cases = [
            ("@tiktok", "tiktok"),
            ("tiktok", "tiktok"),
            ("https://www.tiktok.com/@khaby.lame", "khaby.lame"),
            ("https://www.tiktok.com/@charlidamelio/video/7123456789012345678", "charlidamelio"),
            ("https://www.tiktok.com/@user_name?lang=vi-VN&is_copy_url=1", "user_name"),
            ("", ""),
            (None, ""),
        ]
        for inp, expected in cases:
            with self.subTest(inp=inp):
                self.assertEqual(self.client.extract_username(inp), expected)

    def test_format_views_display(self):
        """Kiểm tra format lượt xem BR-F3-04 (1.2M, 1.5K, số nguyên)."""
        self.assertEqual(format_views(1_200_000), "1.2M")
        self.assertEqual(format_views(2_000_000), "2M")
        self.assertEqual(format_views(15_400), "15.4K")
        self.assertEqual(format_views(1_000), "1K")
        self.assertEqual(format_views(950), "950")
        self.assertEqual(format_views(0), "0")

    def test_aweme_post_is_dup_detection(self):
        """Kiểm tra quy tắc nhận diện video trùng lặp (repost) dựa trên group_id."""
        # Case 1: Video gốc (group_id trùng với aweme_id)
        post_orig = AwemePost(aweme_id="7100000000000000001", group_id="7100000000000000001")
        self.assertFalse(post_orig.is_dup)

        # Case 2: Video gốc (không có group_id)
        post_no_group = AwemePost(aweme_id="7100000000000000002", group_id="")
        self.assertFalse(post_no_group.is_dup)

        # Case 3: Video trùng lặp (repost) - group_id khác aweme_id
        post_dup = AwemePost(aweme_id="7100000000000000003", group_id="7000000000000000000")
        self.assertTrue(post_dup.is_dup)
        self.assertEqual(post_dup.group_id, "7000000000000000000")

    @patch("requests.Session.get")
    def test_resolve_channel_metadata_success(self, mock_get):
        """Kiểm tra phân giải metadata kênh thành công."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "userInfo": {
                "user": {
                    "nickname": "TikTok Creator",
                    "uniqueId": "creator_official",
                    "secUid": "MS4wLjABAAAAfake_sec_uid_12345",
                    "avatarThumb": "https://p16-va.tiktokcdn.com/avatar.jpg",
                },
                "stats": {
                    "followerCount": 250000,
                    "videoCount": 128,
                },
            }
        }
        mock_get.return_value = mock_resp

        ok, meta, msg = self.client.resolve_channel_metadata("@creator_official")
        self.assertTrue(ok)
        self.assertEqual(meta.unique_id, "creator_official")
        self.assertEqual(meta.nickname, "TikTok Creator")
        self.assertEqual(meta.sec_uid, "MS4wLjABAAAAfake_sec_uid_12345")
        self.assertEqual(meta.followers, 250000)
        self.assertEqual(meta.video_count, 128)

    @patch("requests.Session.get")
    def test_fetch_channel_videos_and_dup_parsing(self, mock_get):
        """Kiểm tra lấy video công khai và bóc tách bài trùng kèm group_id."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "has_more": 1,
            "max_cursor": 1690000000000,
            "aweme_list": [
                {
                    "aweme_id": "7101",
                    "group_id": "7101",  # Gốc
                    "desc": "Original dance video",
                    "statistics": {"play_count": 50000, "digg_count": 2500, "comment_count": 100},
                    "create_time": 1680000000,
                    "region": "VN",
                    "misc_info": "phone",
                },
                {
                    "aweme_id": "7102",
                    "group_id": "6999",  # Trùng với video gốc 6999
                    "desc": "Reposted video from someone",
                    "statistics": {"play_count": 120000, "digg_count": 8000, "comment_count": 300},
                    "create_time": 1681000000,
                    "region": "US",
                    "status": {"is_prohibited": True},
                },
            ],
        }
        mock_get.return_value = mock_resp

        ok, posts, next_cursor, has_more, msg = self.client.fetch_channel_videos(
            sec_uid="MS4wLjABAAAAfake_sec_uid_12345", count=35, max_cursor=0
        )
        self.assertTrue(ok)
        self.assertEqual(len(posts), 2)
        self.assertTrue(has_more)
        self.assertEqual(next_cursor, 1690000000000)

        # Kiểm tra video 1
        p1 = posts[0]
        self.assertEqual(p1.aweme_id, "7101")
        self.assertFalse(p1.is_dup)
        self.assertEqual(p1.region, "VN")
        self.assertEqual(p1.shadow, "no")

        # Kiểm tra video 2 (trùng)
        p2 = posts[1]
        self.assertEqual(p2.aweme_id, "7102")
        self.assertTrue(p2.is_dup)
        self.assertEqual(p2.group_id, "6999")
        self.assertEqual(p2.region, "US")
        self.assertEqual(p2.shadow, "yes")


if __name__ == "__main__":
    unittest.main()
