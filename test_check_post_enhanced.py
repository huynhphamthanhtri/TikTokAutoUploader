# -*- coding: utf-8 -*-
"""
Unit tests for F2 Enhanced CheckPost: cursor pagination, penalty details, Strike appeal, and video download.
"""

import unittest
from unittest.mock import MagicMock, patch

from check_features.models import AwemePost, PenaltyDetails
from check_features.clients.studio_post_client import TikTokStudioPostClient


class TestCheckPostEnhanced(unittest.TestCase):

    def test_aweme_post_is_dup_property(self):
        """Kiểm tra logic nhận diện video trùng lặp (repost) qua group_id."""
        # 1. Không có group_id -> Không trùng
        p1 = AwemePost(aweme_id="71234567890", group_id="")
        self.assertFalse(p1.is_dup)

        # 2. group_id trùng với chính aweme_id -> Bản gốc, không trùng
        p2 = AwemePost(aweme_id="71234567890", group_id="71234567890")
        self.assertFalse(p2.is_dup)

        # 3. group_id khác aweme_id -> Video trùng lặp (repost)
        p3 = AwemePost(aweme_id="71234567890", group_id="79999999999")
        self.assertTrue(p3.is_dup)

    @patch("requests.Session.get")
    def test_fetch_posts_cursor_pagination(self, mock_get):
        """Kiểm tra phân trang cursor và bóc tách dữ liệu post."""
        client = TikTokStudioPostClient({})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "itemList": [
                {
                    "id": "71111111111",
                    "desc": "Test video 1",
                    "createTime": 1700000000,
                    "video": {"duration": 45, "playAddr": {"url_list": ["https://cdn.tiktok.com/clean1.mp4"]}},
                    "stats": {"playCount": 15000, "diggCount": 200},
                    "risk_warn": True,
                },
                {
                    "id": "72222222222",
                    "desc": "Test video 2",
                    "createTime": 1700001000,
                    "video": {"duration": 60, "playAddr": {"url_list": ["https://cdn.tiktok.com/clean2.mp4"]}},
                    "stats": {"playCount": 35000, "diggCount": 1200},
                    "group_id": "79999999999",
                },
            ],
            "cursor": 20,
            "hasMore": True,
        }
        mock_get.return_value = mock_resp

        ok, posts, next_cur, has_more, msg = client.fetch_posts(count=20, max_cursor=0)
        self.assertTrue(ok)
        self.assertEqual(len(posts), 2)
        self.assertEqual(next_cur, 20)
        self.assertTrue(has_more)

        # Kiểm tra post 1
        p1 = posts[0]
        self.assertEqual(p1.aweme_id, "71111111111")
        self.assertTrue(p1.penalty_flag)
        self.assertEqual(p1.download_url, "https://cdn.tiktok.com/clean1.mp4")

        # Kiểm tra post 2 (video trùng)
        p2 = posts[1]
        self.assertEqual(p2.group_id, "79999999999")
        self.assertTrue(p2.is_dup)

    @patch("requests.Session.post")
    @patch("requests.Session.get")
    def test_get_penalty_details_parsing(self, mock_get, mock_post):
        """Kiểm tra lấy chi tiết án phạt qua get_penalty_details."""
        client = TikTokStudioPostClient({})

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "hasPenalty": True,
            "penaltyId": "pen_12345",
            "issuer": "TikTok Moderation Team",
            "reasonCodes": ["Unoriginal Content", "Low Quality"],
            "appealStatus": 1,
        }
        mock_get.return_value = mock_resp
        mock_post.return_value = mock_resp

        ok, p_info, msg = client.get_penalty_details("71111111111", 1700000000)
        self.assertTrue(ok)
        self.assertTrue(p_info.has_penalty)
        self.assertEqual(p_info.penalty_id, "pen_12345")
        self.assertEqual(p_info.appeal_status, 1)
        self.assertIn("Unoriginal Content", p_info.reason_codes)

    @patch("requests.Session.post")
    def test_submit_studio_appeal_validation(self, mock_post):
        """Kiểm tra validation và submit kháng Strike (<= 400 ký tự)."""
        client = TikTokStudioPostClient({})

        # 1. Rỗng -> False
        ok, msg = client.submit_studio_appeal("71111111111", "pen_123", "")
        self.assertFalse(ok)
        self.assertIn("trống", msg)

        # 2. > 400 ký tự -> False
        too_long = "Lý do vi phạm " * 40
        ok, msg = client.submit_studio_appeal("71111111111", "pen_123", too_long)
        self.assertFalse(ok)
        self.assertIn("400 ký tự", msg)

        # 3. Hợp lệ -> Gọi API
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": 0, "message": "success"}
        mock_post.return_value = mock_resp

        valid_text = "Video này do tôi tự quay và biên tập 100%, kính mong TikTok xem xét lại."
        ok, msg = client.submit_studio_appeal("71111111111", "pen_123", valid_text)
        self.assertTrue(ok)
        self.assertIn("thành công", msg)


if __name__ == "__main__":
    unittest.main()
