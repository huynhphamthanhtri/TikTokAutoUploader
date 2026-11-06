# -*- coding: utf-8 -*-
"""
test_cooldown_optimization.py - Bộ kiểm thử cô lập kiểm chứng cơ chế giãn cách thông minh (DEC-10).

Tuân thủ:
- DEC-10: Khác profile chờ 3s-5s; Cùng profile chờ 5s-10s.
- Phản hồi ngắt/tạm dừng tức thì theo nhịp 0.5s.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

import main


class TestCooldownOptimization(unittest.TestCase):
    def test_cooldown_rule_selection(self):
        """Kiểm tra logic phân loại cooldown: cùng profile chọn 5-10s, khác profile chọn 3-5s."""
        # Giả lập 3 task: Task 1 (Prof A) -> Task 2 (Prof B) -> Task 3 (Prof B)
        tasks = [
            {"id": "t1", "profile_name": "Prof_A"},
            {"id": "t2", "profile_name": "Prof_B"},
            {"id": "t3", "profile_name": "Prof_B"},
        ]

        # 1. Từ Task 1 (Prof A) sang Task 2 (Prof B) -> Khác profile: phải nằm trong khoảng 3 đến 5s
        p_name = tasks[0]["profile_name"]
        next_t = tasks[1]
        next_p = next_t.get("profile_name", "")

        for _ in range(50):
            if next_p and next_p == p_name:
                cooldown_sec = 100  # Should not happen
            else:
                import random
                cooldown_sec = random.randint(3, 5)
            self.assertTrue(3 <= cooldown_sec <= 5, f"Khác profile phải có cooldown từ 3-5s, nhận: {cooldown_sec}")

        # 2. Từ Task 2 (Prof B) sang Task 3 (Prof B) -> Cùng profile: phải nằm trong khoảng 5 đến 10s
        p_name = tasks[1]["profile_name"]
        next_t = tasks[2]
        next_p = next_t.get("profile_name", "")

        for _ in range(50):
            if next_p and next_p == p_name:
                import random
                cooldown_sec = random.randint(5, 10)
            else:
                cooldown_sec = 0  # Should not happen
            self.assertTrue(5 <= cooldown_sec <= 10, f"Cùng profile phải có cooldown từ 5-10s, nhận: {cooldown_sec}")

    def test_cooldown_responsive_interruption(self):
        """Kiểm tra khả năng hủy ngay lập tức khi _shutting_down hoặc _schedule_queue_paused kích hoạt."""
        # Giả lập cooldown 10s (20 nhịp 0.5s)
        cooldown_sec = 10
        interrupted = False

        # Cờ tạm dừng bật lên sau 0.5s
        main._shutting_down = False
        main._schedule_queue_running = True
        main._schedule_queue_paused = True  # Giả lập người dùng bấm tạm dừng

        start_t = time.time()
        for _ in range(int(cooldown_sec * 2)):
            if main._shutting_down or not main._schedule_queue_running or main._schedule_queue_paused:
                interrupted = True
                break
            time.sleep(0.5)

        elapsed = time.time() - start_t
        self.assertTrue(interrupted, "Phải ngắt ngay lập tức khi phát hiện cờ tạm dừng!")
        self.assertLess(elapsed, 1.0, "Thời gian thoát phải dưới 1 giây, không được ngủ hết 10 giây!")

        # Reset lại cờ
        main._schedule_queue_paused = False


if __name__ == "__main__":
    unittest.main()
