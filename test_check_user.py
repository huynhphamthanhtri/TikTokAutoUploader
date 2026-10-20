# -*- coding: utf-8 -*-
"""
Unit tests for F1 (CheckUser, setNickname, Cooldown) & Common Appeal Dialog.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from check_features.models import UserInfo
from check_features.clients.account_client import TikTokAccountClient
from check_features.ui.common_appeal_dialog import ManualAppealDialog


class TestCheckUserAndCommonAppeal(unittest.TestCase):

    def test_nickname_cooldown_calculation(self):
        """Kiểm tra tính toán chu kỳ Cooldown 7 ngày của Nickname."""
        now = int(time.time())

        # Trường hợp 1: Chưa đổi bao giờ (0 hoặc None) -> Cooldown = 0
        u1 = UserInfo(nickname="Creator1", nickname_modify_time=0)
        self.assertEqual(u1.nickname_cooldown_days, 0)

        # Trường hợp 2: Vừa đổi cách đây 3 ngày -> Còn 4 ngày
        u2 = UserInfo(nickname="Creator2", nickname_modify_time=now - (3 * 86400))
        self.assertEqual(u2.nickname_cooldown_days, 4)

        # Trường hợp 3: Vừa đổi cách đây 1 giờ -> Còn 7 ngày
        u3 = UserInfo(nickname="Creator3", nickname_modify_time=now - 3600)
        self.assertEqual(u3.nickname_cooldown_days, 7)

        # Trường hợp 4: Đã qua 8 ngày -> Cooldown = 0 (Được phép đổi)
        u4 = UserInfo(nickname="Creator4", nickname_modify_time=now - (8 * 86400))
        self.assertEqual(u4.nickname_cooldown_days, 0)

    def test_set_nickname_validation(self):
        """Kiểm tra validation tên hiển thị khi gọi set_nickname."""
        client = TikTokAccountClient({})

        # 1. Tên rỗng hoặc toàn khoảng trắng
        ok, msg = client.set_nickname("")
        self.assertFalse(ok)
        self.assertIn("trống", msg.lower())

        ok, msg = client.set_nickname("   ")
        self.assertFalse(ok)

        # 2. Tên quá dài (> 30 ký tự)
        too_long = "a" * 31
        ok, msg = client.set_nickname(too_long)
        self.assertFalse(ok)
        self.assertIn("30 ký tự", msg)

    @patch("requests.Session.post")
    def test_set_nickname_api_responses(self, mock_post):
        """Kiểm tra xử lý phản hồi từ server khi đổi nickname."""
        client = TikTokAccountClient({})

        # Case 1: Server TikTok chấp nhận
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": "success", "data": {"screen_name": "NewCreatorName"}}
        mock_post.return_value = mock_resp

        ok, msg = client.set_nickname("NewCreatorName")
        self.assertTrue(ok)
        self.assertIn("thành công", msg)
        self.assertEqual(client.config.get("nickname"), "NewCreatorName")

        # Case 2: Server TikTok từ chối
        mock_resp_fail = MagicMock()
        mock_resp_fail.status_code = 200
        mock_resp_fail.json.return_value = {
            "message": "error",
            "data": {"description": "You can only change your nickname once every 7 days."},
        }
        mock_post.return_value = mock_resp_fail

        ok, msg = client.set_nickname("AnotherName")
        self.assertFalse(ok)
        self.assertIn("7 days", msg)

    def test_manual_appeal_dialog_limit_constant(self):
        """Xác nhận giới hạn nghiêm ngặt 400 ký tự theo quy định TikTok."""
        self.assertEqual(ManualAppealDialog.MAX_CHARS, 400)


if __name__ == "__main__":
    unittest.main()
