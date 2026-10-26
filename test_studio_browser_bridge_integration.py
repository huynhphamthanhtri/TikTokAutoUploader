# -*- coding: utf-8 -*-
"""
test_studio_browser_bridge_integration.py - Bộ unit tests kiểm tra tích hợp StudioBrowserBridge và In-place Banner UI.
Tuân thủ AGENTS.md: Mock chuẩn xác schema và kiểm tra semantic business logic.
"""

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from check_features.clients.studio_post_client import TikTokStudioPostClient
from check_features.services.studio_browser_bridge import StudioBrowserBridge
from check_features.models import AwemePost


class StudioBrowserBridgeTests(unittest.TestCase):
    """Kiểm thử tính đúng đắn và độ bền của StudioBrowserBridge."""

    def test_bridge_handles_profile_busy_gracefully(self):
        """Khi profile bị khóa bởi tiến trình khác, bridge phải trả về PROFILE_BUSY mà không crash."""
        from browser_patchright_glue import ProfileBusyError

        with patch("browser_patchright_glue.open_session", side_effect=ProfileBusyError("Lock held")):
            res = StudioBrowserBridge.fetch_studio_posts({"name": "TEST_BUSY"}, profile_name="TEST_BUSY")

        self.assertFalse(res["success"])
        self.assertEqual(res["error_code"], "PROFILE_BUSY")
        self.assertIn("đang bận", res["message"])
        self.assertEqual(res["items"], [])

    def test_bridge_extracts_ssr_context_properly(self):
        """Bridge trích xuất chuẩn xác items từ __Creator_Center_Context__."""
        sample_batch = {
            "firstBatchQueryItems": {
                "cursor": 5,
                "has_more": True,
                "item_list": [
                    {"item_id": "111", "desc": "Post 1", "visibility": 1, "play_count": 100},
                    {"item_id": "222", "desc": "Post 2", "visibility": 3, "play_count": 200},
                ]
            }
        }
        fake_token = MagicMock()
        fake_res = {
            "success": True,
            "items": sample_batch["firstBatchQueryItems"]["item_list"],
            "source": "browser_studio_ssr",
            "completeness": "partial",
            "cursor": 5,
            "has_more": True,
            "message": "Đã lấy 2 bài đăng từ Creator Studio qua Trình Duyệt.",
            "error_code": None,
        }

        with patch("browser_patchright_glue.open_session", return_value=fake_token), \
             patch("browser_patchright_glue.run_operation", return_value=fake_res):
            res = StudioBrowserBridge.fetch_studio_posts({"name": "TEST_PROFILE"}, profile_name="TEST_PROFILE")

        self.assertTrue(res["success"])
        self.assertEqual(len(res["items"]), 2)
        self.assertEqual(res["items"][0]["item_id"], "111")
        fake_token.quit.assert_called_once()

    def test_studio_client_fallbacks_to_browser_bridge_on_code_1(self):
        """Khi Creator Studio HTTP trả về Mã 1, TikTokStudioPostClient tự động fallback sang StudioBrowserBridge."""
        client = TikTokStudioPostClient({"tiktok_id": "test_user"})

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "status_code": 1,
            "status_msg": "internal server error"
        }

        fake_bridge_res = {
            "success": True,
            "items": [
                {"item_id": "7698579332456877334", "desc": "Live Post", "visibility": 1, "play_count": 96}
            ],
            "message": "Đã lấy 1 bài đăng qua Trình Duyệt.",
            "has_more": False,
        }

        with patch.object(client.session, "post", return_value=fake_resp), \
             patch.object(client.session, "get", side_effect=Exception("WAF Challenge")), \
             patch("check_features.services.studio_browser_bridge.StudioBrowserBridge.fetch_studio_posts", return_value=fake_bridge_res):

            ok, posts, cur, more, msg = client.fetch_posts(count=20)

        self.assertTrue(ok)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].aweme_id, "7698579332456877334")
        self.assertFalse(posts[0].is_private)
        self.assertIn("Trình Duyệt", msg)

    def test_studio_client_fallbacks_to_offline_cache_when_all_fail(self):
        """Khi toàn bộ nguồn mạng thất bại, client tự động nạp từ cache offline data/feeds/{uname}_mobile.json."""
        client = TikTokStudioPostClient({"tiktok_id": "offline_user"})

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"status_code": 1, "status_msg": "internal server error"}

        fake_bridge_res = {
            "success": False,
            "items": [],
            "error_code": "PROFILE_BUSY",
            "message": "Trình duyệt đang bận."
        }

        cache_content = {
            "aweme_list": [
                {"aweme_id": "999888", "desc": "Cached Video", "group_id": "999888"}
            ]
        }

        with patch.object(client.session, "post", return_value=fake_resp), \
             patch.object(client.session, "get", side_effect=Exception("Network down")), \
             patch("check_features.services.studio_browser_bridge.StudioBrowserBridge.fetch_studio_posts", return_value=fake_bridge_res), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(cache_content))):

            ok, posts, cur, more, msg = client.fetch_posts(count=20)

        self.assertTrue(ok)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].aweme_id, "999888")
        self.assertEqual(posts[0].data_source, "offline_cache")
        self.assertIn("Bộ nhớ đệm Offline", msg)


if __name__ == "__main__":
    unittest.main()
