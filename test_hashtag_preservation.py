# -*- coding: utf-8 -*-
"""
test_hashtag_preservation.py - Bộ kiểm thử cô lập kiểm chứng bảo toàn Hashtag và giới hạn tiêu đề (Phase 3).

Tuân thủ:
- DEC-09: Bảo tồn nguyên vẹn dấu gạch dưới bên trong hashtag (ví dụ: #this_or_that, #food_tiktok_uk).
- Hỗ trợ hashtag tiếng Việt có dấu (#ẩm_thực_việt).
- Giới hạn độ dài caption không vượt quá 2,200 ký tự chuẩn TikTok Studio.
"""

import unittest
from schedule_calculator import parse_naming_formula, sanitize_filename_to_title


class TestHashtagPreservation(unittest.TestCase):
    def test_sanitize_preserves_hashtag_underscores(self):
        """Dấu gạch dưới trong hashtag được giữ nguyên, dấu gạch dưới trong chữ thường đổi thành space."""
        filename = "British_Food_Clash_#1392_#this_or_that_#food_tiktok_uk.mp4"
        title = sanitize_filename_to_title(filename)

        self.assertIn("#this_or_that", title)
        self.assertIn("#food_tiktok_uk", title)
        self.assertIn("#1392", title)
        self.assertTrue(title.startswith("British Food Clash"))

    def test_sanitize_vietnamese_hashtags(self):
        """Hỗ trợ hashtag tiếng Việt có dấu và dấu gạch dưới."""
        filename = "Mon_Ngon_Moi_Ngay_#ẩm_thực_việt_#món_ngon_mẹ_nấu.mp4"
        title = sanitize_filename_to_title(filename)

        self.assertIn("#ẩm_thực_việt", title)
        self.assertIn("#món_ngon_mẹ_nấu", title)
        self.assertTrue(title.startswith("Mon Ngon Moi Ngay"))

    def test_caption_length_clamped_at_2200(self):
        """Tiêu đề dài quá 2,200 ký tự được cắt tỉa an toàn."""
        huge_filename = "A" * 3000 + ".mp4"
        res = parse_naming_formula("{filename}", huge_filename)
        self.assertLessEqual(len(res), 2200)


if __name__ == "__main__":
    unittest.main()
