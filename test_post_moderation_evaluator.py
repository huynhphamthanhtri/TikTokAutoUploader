# -*- coding: utf-8 -*-
"""
test_post_moderation_evaluator.py - Test suite cho động cơ đánh giá kiểm duyệt & trùng lặp F2.
Tuân thủ nghiêm ngặt AGENTS.md và tiêu chuẩn Gate 1 của Master Plan:
- Bao phủ đầy đủ 7 bậc của Bảng quyết định kiểm duyệt.
- Kiểm tra các trường hợp xung đột cờ (Hierarchy of Priorities).
- Kiểm tra bảo toàn định dạng chuỗi ID 64-bit chống lỗi làm tròn JS/Python và chuỗi '00'.
- Kiểm tra bẫy kiểu dữ liệu: string 'false', '0', None.
- Kiểm tra cách ly nguồn dữ liệu (data_source tracking).
- Kiểm tra trường hợp thiếu 1 hoặc 2 cờ trong 5 cờ bắt buộc -> unknown.
- Kiểm tra xử lý ký tự vô hình zero-width space.
"""

import unittest
from check_features.services.post_moderation_evaluator import (
    PostModerationEvaluator,
    ModerationEvaluation,
    parse_safe_bool,
    parse_safe_int,
    clean_id_string,
    sanitize_appeal_text,
)


class TestPostModerationEvaluator(unittest.TestCase):
    """Kiểm tra logic nghiệp vụ của PostModerationEvaluator."""

    def test_safe_helpers(self):
        """Kiểm tra các hàm helper chuẩn hóa kiểu dữ liệu chống bẫy bool('false') == True."""
        # 1. parse_safe_bool
        self.assertIsNone(parse_safe_bool(None))
        self.assertIsNone(parse_safe_bool(""))
        self.assertIsNone(parse_safe_bool("null"))
        self.assertFalse(parse_safe_bool("false"))
        self.assertFalse(parse_safe_bool("0"))
        self.assertFalse(parse_safe_bool(0))
        self.assertTrue(parse_safe_bool("true"))
        self.assertTrue(parse_safe_bool("1"))
        self.assertTrue(parse_safe_bool(1))

        # 2. clean_id_string
        self.assertEqual(clean_id_string(None), "")
        self.assertEqual(clean_id_string(""), "")
        self.assertEqual(clean_id_string("   "), "")
        self.assertEqual(clean_id_string("0"), "")
        self.assertEqual(clean_id_string("000"), "")
        self.assertEqual(clean_id_string("  7391480094101275670  "), "7391480094101275670")

        # 3. sanitize_appeal_text
        self.assertEqual(sanitize_appeal_text("\u200b\ufeff   \u200c"), "")
        self.assertEqual(sanitize_appeal_text("Kháng nghị \u200b video"), "Kháng nghị video")

    def test_tier1_prohibited(self):
        """Bậc 1: is_prohibited == True -> Cấm cứng."""
        raw = {
            "aweme_id": "7391480094101275670",
            "is_prohibited": True,
            "in_reviewing": True,  # Cờ phụ không được làm giảm mức phạt
        }
        res = PostModerationEvaluator.evaluate(raw, data_source="creator_studio")
        self.assertEqual(res.shadow, "yes")
        self.assertIn("Cấm cứng", res.shadow_reason)
        self.assertTrue(res.rule_evidence["is_prohibited"])

    def test_tier2_self_see(self):
        """Bậc 2: self_see == True hoặc private_status == 1 -> Chỉ mình thấy."""
        raw = {
            "aweme_id": "7391480094101275670",
            "self_see": "true",  # Chuỗi 'true'
            "is_prohibited": "false",  # Chuỗi 'false'
        }
        res = PostModerationEvaluator.evaluate(raw, data_source="creator_studio")
        self.assertEqual(res.shadow, "yes")
        self.assertIn("Chỉ mình thấy", res.shadow_reason)

    def test_tier3_nff_or_nr(self):
        """Bậc 3: is_nff_or_nr == True hoặc review_status == 2 -> Không lên FYP."""
        raw = {
            "aweme_id": "7391480094101275670",
            "is_nff_or_nr": True,
            "review_info": {"review_status": 2},
        }
        res = PostModerationEvaluator.evaluate(raw, data_source="creator_studio")
        self.assertEqual(res.shadow, "yes")
        self.assertIn("Không lên FYP", res.shadow_reason)

    def test_tier4_display_penalty(self):
        """Bậc 4: display_penalty_type > 0 -> Án phạt reach."""
        raw = {
            "aweme_id": "7391480094101275670",
            "display_penalty_type": "2",  # Chuỗi số '2'
        }
        res = PostModerationEvaluator.evaluate(raw, data_source="creator_studio")
        self.assertEqual(res.shadow, "yes")
        self.assertIn("Án phạt reach", res.shadow_reason)
        self.assertTrue(res.penalty_flag)

    def test_tier5_in_reviewing(self):
        """Bậc 5: in_reviewing == True -> Nghi vấn / Đang kiểm duyệt."""
        raw = {
            "aweme_id": "7391480094101275670",
            "in_reviewing": True,
            "is_prohibited": False,
            "self_see": False,
            "is_nff_or_nr": False,
        }
        res = PostModerationEvaluator.evaluate(raw, data_source="creator_studio")
        self.assertEqual(res.shadow, "suspect")
        self.assertIn("kiểm duyệt", res.shadow_reason)

    def test_tier6_normal_distribution_requires_all_5_flags(self):
        """Bậc 6: CHỈ ĐẠT KHI TẤT CẢ 5 CỜ KIỂM DUYỆT ĐỀU HỢP LỆ VÀ SẠCH."""
        raw_all_5 = {
            "aweme_id": "7391480094101275670",
            "is_prohibited": False,
            "self_see": False,
            "is_nff_or_nr": False,
            "display_penalty_type": 0,
            "in_reviewing": False,
        }
        res = PostModerationEvaluator.evaluate(raw_all_5, data_source="creator_studio")
        self.assertEqual(res.shadow, "no")
        self.assertIn("Không phát hiện cờ hạn chế", res.shadow_reason)

    def test_missing_or_null_flag_cannot_become_no(self):
        """P0 Fix: Dù có 3/5 cờ sạch nhưng 2 cờ còn lại thiếu hoặc null -> BẮT BUỘC là unknown."""
        # 1. Thiếu display_penalty_type và in_reviewing
        raw_partial_3 = {
            "aweme_id": "7391480094101275670",
            "is_prohibited": False,
            "self_see": False,
            "is_nff_or_nr": False,
            # thiếu display_penalty_type và in_reviewing
        }
        res = PostModerationEvaluator.evaluate(raw_partial_3, data_source="creator_studio")
        self.assertEqual(res.shadow, "unknown")
        self.assertIn("Không đủ cơ sở xác minh", res.shadow_reason)

        # 2. display_penalty_type là None/null
        raw_null_penalty = {
            "aweme_id": "7391480094101275670",
            "is_prohibited": False,
            "self_see": False,
            "is_nff_or_nr": False,
            "display_penalty_type": None,
            "in_reviewing": False,
        }
        self.assertEqual(PostModerationEvaluator.evaluate(raw_null_penalty, data_source="creator_studio").shadow, "unknown")

    def test_fallback_source_always_unknown(self):
        """P0 Fix: Khi dữ liệu đến từ Web Feed / Mobile API, shadow BẮT BUỘC là unknown."""
        raw = {
            "aweme_id": "7391480094101275670",
            "desc": "Video từ Web Feed không có cờ",
        }
        res_web = PostModerationEvaluator.evaluate(raw, data_source="web_feed")
        self.assertEqual(res_web.shadow, "unknown")
        self.assertIn("web_feed", res_web.shadow_reason)

        res_mob = PostModerationEvaluator.evaluate(raw, data_source="mobile_api")
        self.assertEqual(res_mob.shadow, "unknown")
        self.assertIn("mobile_api", res_mob.shadow_reason)

        res_def = PostModerationEvaluator.evaluate(raw)  # Mặc định data_source là unknown
        self.assertEqual(res_def.shadow, "unknown")

    def test_duplicate_detection_string_safety(self):
        """Kiểm tra nhận diện video trùng lặp bằng so sánh chuỗi 64-bit."""
        # 1. Trùng lặp rõ ràng: groupId khác awemeId
        raw_dup = {
            "aweme_id": "7391480094101275670",
            "group_id": "7391480094101275999",
        }
        res_dup = PostModerationEvaluator.evaluate(raw_dup, data_source="creator_studio")
        self.assertTrue(res_dup.is_duplicate)
        self.assertIn("7391480094101275999", res_dup.duplicate_reason)
        self.assertIn("7391480094101275999", res_dup.related_video_url)

        # 2. Không trùng: groupId bằng chính awemeId
        raw_clean = {
            "aweme_id": "7391480094101275670",
            "group_id": "7391480094101275670",
        }
        res_clean = PostModerationEvaluator.evaluate(raw_clean, data_source="creator_studio")
        self.assertFalse(res_clean.is_duplicate)
        self.assertEqual(res_clean.duplicate_reason, "")

        # 3. Không trùng: groupId là '0', '00' hoặc rỗng
        for fake_zero in ("0", "00", "000", "   ", ""):
            raw_zero = {"aweme_id": "7391480094101275670", "group_id": fake_zero}
            self.assertFalse(PostModerationEvaluator.evaluate(raw_zero, data_source="creator_studio").is_duplicate)


if __name__ == "__main__":
    unittest.main()
