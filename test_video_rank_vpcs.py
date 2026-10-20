# -*- coding: utf-8 -*-
"""
Unit tests for F4 (VideoRankDialog, VPCS Violation Decoder, Penalty Expiry, and Manual Appeal).
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from check_features.models import VPCSVideo
from check_features.services.vpcs_violation_decoder import (
    decode_violation_text,
    calculate_penalty_expiry,
)
from check_features.clients.vpcs_rewards_client import TikTokRewardsClient


class TestVideoRankVPCS(unittest.TestCase):

    def test_decode_violation_text_placeholders(self):
        """Kiểm tra thay thế placeholder %s an toàn trong chuỗi mô tả vi phạm."""
        # 1. Không có %s
        t1 = "Video vi phạm tiêu chuẩn cộng đồng chung."
        self.assertEqual(decode_violation_text(t1, []), t1)

        # 2. Có 1 placeholder %s
        t2 = "Video bị hạn chế phân phối do vi phạm %s."
        args2 = [{"key": "Chính sách nội dung không nguyên bản"}]
        expected2 = "Video bị hạn chế phân phối do vi phạm Chính sách nội dung không nguyên bản."
        self.assertEqual(decode_violation_text(t2, args2), expected2)

        # 3. Có nhiều placeholder %s
        t3 = "Vi phạm %s tại phân đoạn %s."
        args3 = [{"key": "Bản quyền âm nhạc"}, {"key": "00:15 - 00:30"}]
        expected3 = "Vi phạm Bản quyền âm nhạc tại phân đoạn 00:15 - 00:30."
        self.assertEqual(decode_violation_text(t3, args3), expected3)

        # 4. Thiếu tham số trong args
        t4 = "Lỗi %s và lỗi %s."
        args4 = [{"key": "A"}]
        expected4 = "Lỗi A và lỗi %s."
        self.assertEqual(decode_violation_text(t4, args4), expected4)

    def test_calculate_penalty_expiry_33_days(self):
        """Kiểm tra mốc tính hạn hết gậy (+33 ngày = 33 * 86400s)."""
        now = int(time.time())

        # Đăng cách đây 10 ngày -> Còn 23 ngày
        posted_10d = now - (10 * 86400)
        exp1, days1 = calculate_penalty_expiry(posted_10d)
        self.assertEqual(exp1, posted_10d + (33 * 86400))
        self.assertEqual(days1, 23)

        # Đăng cách đây 32 ngày -> Còn 1 ngày
        posted_32d = now - (32 * 86400)
        exp2, days2 = calculate_penalty_expiry(posted_32d)
        self.assertEqual(days2, 1)

        # Đăng cách đây 35 ngày -> Đã hết gậy (days = 0)
        posted_35d = now - (35 * 86400)
        exp3, days3 = calculate_penalty_expiry(posted_35d)
        self.assertEqual(days3, 0)

    def test_vpcs_video_can_appeal_rule(self):
        """Kiểm tra quy tắc BR-F4-06 xác định video có được phép gửi kháng hay không."""
        # Video đủ điều kiện -> Không cần kháng
        v_ok = VPCSVideo(vid="1", eligibility=True)
        self.assertFalse(v_ok.can_appeal)

        # Video bị từ chối, chưa kháng (appeal_status=0 hoặc 1) -> Được kháng
        v_unappealed = VPCSVideo(vid="2", eligibility=False, appeal_status=0)
        self.assertTrue(v_unappealed.can_appeal)

        # Video đang được xét duyệt (appeal_status=2) -> Không được gửi tiếp
        v_appealing = VPCSVideo(vid="3", eligibility=False, appeal_status=2)
        self.assertFalse(v_appealing.can_appeal)

        # Video kháng thất bại (appeal_status=3) -> Được phép kháng lại
        v_failed = VPCSVideo(vid="4", eligibility=False, appeal_status=3)
        self.assertTrue(v_failed.can_appeal)

        # Video hoàn tất không còn lỗi (appeal_status=4, reasons=[]) -> Không được kháng
        v_done = VPCSVideo(vid="5", eligibility=False, appeal_status=4, ineligible_reasons=[])
        self.assertFalse(v_done.can_appeal)

        # Video kháng xịt (appeal_status=4, vẫn còn reasons) -> Được phép kháng lại
        v_rejected = VPCSVideo(vid="6", eligibility=False, appeal_status=4, ineligible_reasons=[{"title": "Err"}])
        self.assertTrue(v_rejected.can_appeal)

    @patch("requests.Session.post")
    def test_submit_vpcs_appeal_validation(self, mock_post):
        """Kiểm tra validation và submit đơn kháng VPCS."""
        client = TikTokRewardsClient({})

        # 1. Rỗng -> False
        ok, msg = client.submit_vpcs_appeal("799999", "")
        self.assertFalse(ok)
        self.assertIn("trống", msg)

        # 2. > 400 ký tự -> False
        too_long = "Nội dung giải trình " * 30
        ok, msg = client.submit_vpcs_appeal("799999", too_long)
        self.assertFalse(ok)
        self.assertIn("400 ký tự", msg)

        # 3. Hợp lệ -> Submit thành công
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": 0, "message": "success"}
        mock_post.return_value = mock_resp

        ok, msg = client.submit_vpcs_appeal("799999", "Video này hoàn toàn đáp ứng tiêu chí Quỹ Sáng Tạo.")
        self.assertTrue(ok)
        self.assertIn("thành công", msg)


if __name__ == "__main__":
    unittest.main()
