# -*- coding: utf-8 -*-
"""
test_title_stutter_helper.py - Kiểm thử cơ chế phát hiện và khử lặp tiêu đề bài đăng:
1. Phát hiện chính xác khi công thức lặp lại stem ngay sau {filename}.
2. Không báo động nhầm khi người dùng cố ý lặp từ trong caption (ví dụ "Haha Haha", "Hot Hot").
3. Khử lặp an toàn giữ nguyên các hashtag và nội dung phía sau.
"""

from unittest import TestCase
from schedule_calculator import detect_title_stutter, strip_title_stutter, parse_naming_formula


class TestTitleStutterHelper(TestCase):
    def test_detect_title_stutter_real_case(self):
        """Tái hiện đúng case trong screenshot của người dùng."""
        stem = "British Food Clash #1392"
        formula = "{filename} British Food Clash #1392 #thisorthat #britishhumour"
        self.assertTrue(detect_title_stutter(formula, stem))

    def test_detect_title_stutter_negative(self):
        """Không báo động khi công thức chuẩn hoặc có từ lặp có chủ ý."""
        stem = "British Food Clash #1392"
        # Công thức chuẩn
        formula1 = "{filename} #thisorthat #britishhumour"
        self.assertFalse(detect_title_stutter(formula1, stem))

        # Công thức chỉ có {filename}
        formula2 = "{filename}"
        self.assertFalse(detect_title_stutter(formula2, stem))

        # Tiêu đề cố ý lặp lại từ nhưng không phải stem
        formula3 = "{filename} Haha Haha siêu phẩm"
        self.assertFalse(detect_title_stutter(formula3, stem))

    def test_strip_title_stutter_preserves_hashtags(self):
        """Khử lặp phần stem thừa, bảo toàn nguyên vẹn toàn bộ hashtag."""
        stem = "British Food Clash #1392"
        formula = "{filename} British Food Clash #1392 #thisorthat #britishhumour"
        cleaned_formula = strip_title_stutter(formula, stem)
        self.assertEqual(cleaned_formula, "{filename} #thisorthat #britishhumour")

        # Khi parse ra tiêu đề cuối cùng: không bị lặp đúp stem
        final_title = parse_naming_formula(cleaned_formula, f"{stem}.mp4")
        self.assertEqual(final_title, "British Food Clash #1392 #thisorthat #britishhumour")
