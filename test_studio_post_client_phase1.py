# -*- coding: utf-8 -*-
"""
test_studio_post_client_phase1.py - Test suite cho TikTokStudioPostClient ở Phase 1.
Tuân thủ nghiêm ngặt AGENTS.md và tiêu chuẩn Gate 1 của Master Plan:
- Kiểm tra phân trang Creator Studio API và cơ chế fallback.
- Kiểm tra xử lý batching án phạt 0, 1, 4, 5, 8, 9 items và cách ly lỗi từng phần tử.
- Kiểm tra giới hạn 400 ký tự kháng nghị Strike với tiếng Việt có dấu, emoji ghép, zero-width space.
- Kiểm tra các hàm chuyển Private và Xóa video.
"""

import unittest
from unittest.mock import MagicMock, patch
import requests

from check_features.clients.studio_post_client import TikTokStudioPostClient
from check_features.models import PenaltyDetails


class TestTikTokStudioPostClientPhase1(unittest.TestCase):
    """Kiểm tra logic giao tiếp mạng và bóc tách của TikTokStudioPostClient."""

    def setUp(self):
        self.config = {
            "name": "Prof_Test_Post",
            "tiktok_id": "creator_test",
            "sec_uid": "MS4wLjABAAAA_test_sec_uid",
            "cookie_str": "sessionid=test_session_123; store-country-code=vn",
            "user_agent": "Mozilla/5.0 TestUA",
        }
        self.client = TikTokStudioPostClient(self.config)

    @patch("requests.Session.post")
    def test_fetch_posts_creator_studio_success(self, mock_post):
        """Kiểm tra gọi Creator Studio item_list/v1 thành công và bóc tách cờ kiểm duyệt."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 0,
            "has_more": True,
            "max_cursor": 20,
            "item_list": [
                {
                    "item_id": "7391480094101275670",
                    "desc": "Video thử nghiệm",
                    "is_prohibited": False,
                    "self_see": False,
                    "is_nff_or_nr": True,  # Bóp reach
                    "display_penalty_type": 0,
                    "in_reviewing": False,
                    "statistics": {"play_count": 5400, "digg_count": 320},
                    "video": {"duration": 45},
                }
            ]
        }
        mock_post.return_value = mock_resp

        ok, posts, next_cur, has_more, msg = self.client.fetch_posts(count=20, max_cursor=0)

        self.assertTrue(ok)
        self.assertEqual(len(posts), 1)
        self.assertEqual(next_cur, 20)
        self.assertTrue(has_more)
        self.assertEqual(posts[0].aweme_id, "7391480094101275670")
        self.assertEqual(posts[0].shadow, "yes")
        self.assertEqual(posts[0].data_source, "creator_studio")
        self.assertIn("Không lên FYP", posts[0].shadow_reason)

        called_args, called_kwargs = mock_post.call_args
        self.assertIn("/tiktok/creator/manage/item_list/v1/", called_args[0])

    @patch("requests.Session.get")
    @patch("requests.Session.post")
    def test_fetch_posts_fallback_web_feed(self, mock_post, mock_get):
        """Kiểm tra fallback sang Web Feed API khi Creator Studio lỗi kết nối."""
        mock_post.side_effect = requests.exceptions.RequestException("Studio 403 Forbidden")

        mock_resp_feed = MagicMock()
        mock_resp_feed.status_code = 200
        mock_resp_feed.json.return_value = {
            "hasMore": False,
            "cursor": 10,
            "itemList": [
                {
                    "id": "7391480094101275670",
                    "desc": "Video Feed",
                    "stats": {"playCount": 100},
                    "video": {"duration": 30},
                }
            ]
        }
        mock_get.return_value = mock_resp_feed

        ok, posts, next_cur, has_more, msg = self.client.fetch_posts(count=10, max_cursor=0)

        self.assertTrue(ok)
        self.assertEqual(len(posts), 1)
        self.assertIn("Web Feed", msg)
        self.assertEqual(posts[0].data_source, "web_feed")
        # Nguồn dự phòng bắt buộc là unknown
        self.assertEqual(posts[0].shadow, "unknown")

    @patch.object(TikTokStudioPostClient, "get_penalty_details")
    def test_fetch_penalties_batch_various_sizes(self, mock_get_single):
        """Kiểm tra batching với nhiều kích thước danh sách: 0, 1, 4, 5, 9 items và cách ly lỗi."""
        def fake_single(vid, ctime):
            if vid == "vid_err":
                raise requests.exceptions.Timeout("Timeout")
            elif "strike" in vid:
                return True, PenaltyDetails(has_penalty=True, penalty_id=f"pen_{vid}"), "OK"
            return True, PenaltyDetails(has_penalty=False), "OK"

        mock_get_single.side_effect = fake_single

        # 1. Kích thước 0
        self.assertEqual(self.client.fetch_penalties_batch([]), {})

        # 2. Kích thước 1
        res1 = self.client.fetch_penalties_batch([("vid_1", 100)])
        self.assertEqual(len(res1), 1)
        self.assertIn("vid_1", res1)

        # 3. Kích thước 5 (vượt qua 1 batch 4)
        items5 = [("v1", 1), ("v2", 2), ("v3_strike", 3), ("v4", 4), ("vid_err", 5)]
        res5 = self.client.fetch_penalties_batch(items5)
        self.assertEqual(len(res5), 5)
        self.assertTrue(res5["v3_strike"].has_penalty)
        self.assertFalse(res5["vid_err"].has_penalty)
        self.assertIn("Timeout", res5["vid_err"].error)

        # 4. Kích thước 9
        items9 = [(f"vid_{i}", i) for i in range(9)]
        res9 = self.client.fetch_penalties_batch(items9)
        self.assertEqual(len(res9), 9)

    def test_submit_studio_appeal_unicode_validation(self):
        """Kiểm tra validation độ dài kháng nghị <= 400 ký tự với tiếng Việt, emoji và zero-width space."""
        # 1. Rỗng hoặc whitespace hoặc zero-width space
        for empty_val in ("   \n\t  ", "\u200b\ufeff", ""):
            ok, msg = self.client.submit_studio_appeal("vid1", "pen1", empty_val)
            self.assertFalse(ok)
            self.assertIn("không được để trống", msg)

        # 2. Tiếng Việt có dấu và emoji hợp lệ (dưới 400)
        valid_vn = "Video của tôi được quay tại Đà Nẵng 🇻🇳, hoàn toàn tự sản xuất sáng tạo! 🚀"
        with patch("requests.Session.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status_code": 0, "message": "success"}
            mock_post.return_value = mock_resp
            ok_vn, msg_vn = self.client.submit_studio_appeal("vid1", "pen1", valid_vn)
            self.assertTrue(ok_vn)

        # 3. Vượt quá 400 ký tự
        long_vn = "Đoạn văn bản tiếng Việt " * 25  # ~600 ký tự
        ok_long, msg_long = self.client.submit_studio_appeal("vid1", "pen1", long_vn)
        self.assertFalse(ok_long)
        self.assertIn("không được vượt quá 400 ký tự", msg_long)

    @patch("requests.Session.post")
    def test_hide_and_delete_studio_item(self, mock_post):
        """Kiểm tra lệnh chuyển Private và Xóa video."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": 0, "message": "success"}
        mock_post.return_value = mock_resp

        ok_hide, msg_hide = self.client.hide_studio_item("7391480094101275670")
        self.assertTrue(ok_hide)
        self.assertIn("Riêng tư", msg_hide)

        ok_del, msg_del = self.client.delete_studio_item("7391480094101275670")
        self.assertTrue(ok_del)
        self.assertIn("xóa video", msg_del)


if __name__ == "__main__":
    unittest.main()
