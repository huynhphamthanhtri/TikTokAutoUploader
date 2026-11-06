# -*- coding: utf-8 -*-
"""
test_schedule_manager_hardened.py - Bộ kiểm thử cô lập kiểm chứng các giải pháp gia cố cho ScheduleManager (Phase 1).

Tuân thủ nghiêm ngặt:
- AGENTS.md Quy tắc 14: Zero Live Data Mutation (100% test sử dụng tempfile/tmp_path cô lập).
- DEC-02: claim_due_tasks & claim_task_for_execution nhận task MISSED_RESCHEDULED.
- DEC-03: claim_task_for_execution nhận task FAILED khi can thiệp thủ công (worker_id='post_now'/'manual_retry').
- DEC-04: delete_tasks cấm xóa task đang mang trạng thái UPLOADING.
- DEC-05: get_dashboard_statistics không tính task cũ được update_at vào posted_today.
- DEC-06: is_video_referenced_by_pending_tasks dùng canonical path & bảo vệ task FAILED chờ retry.
- DEC-08: check_profile_conflicts quét cả UPLOADING và MISSED_RESCHEDULED.
"""

import datetime
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from schedule_manager import ScheduleManager


class TestScheduleManagerHardened(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_hardened_")
        self.db_file = Path(self.test_dir) / "test_scheduled_posts.db"
        self.mgr = ScheduleManager(storage_file=self.db_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_claim_missed_rescheduled_in_claim_due_tasks(self):
        """DEC-02: claim_due_tasks tự động nhận các task LOCAL_QUEUE mang trạng thái MISSED_RESCHEDULED."""
        now = datetime.datetime.now()
        past_epoch = int((now - datetime.timedelta(minutes=5)).timestamp())

        # Thêm 1 task PENDING và 1 task MISSED_RESCHEDULED đều đã đến hạn
        created = self.mgr.add_tasks([
            {
                "id": "task_missed_01",
                "profile_name": "Prof_01",
                "video_path": "test.mp4",
                "scheduled_epoch": past_epoch,
                "scheduled_time": (now - datetime.timedelta(minutes=5)).isoformat(),
                "execution_mode": "LOCAL_QUEUE",
                "status": "MISSED_RESCHEDULED",
            },
            {
                "id": "task_pending_01",
                "profile_name": "Prof_01",
                "video_path": "test2.mp4",
                "scheduled_epoch": past_epoch,
                "scheduled_time": (now - datetime.timedelta(minutes=5)).isoformat(),
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            }
        ])

        # Ép trạng thái MISSED_RESCHEDULED vào task 1 (vì add_tasks mặc định có thể set PENDING)
        self.mgr.update_task("task_missed_01", {"status": "MISSED_RESCHEDULED"})

        claimed = self.mgr.claim_due_tasks(worker_id="daemon_worker", limit=5)
        claimed_ids = {t["id"] for t in claimed}

        self.assertIn("task_missed_01", claimed_ids, "Task MISSED_RESCHEDULED phải được claim_due_tasks nhận!")
        self.assertIn("task_pending_01", claimed_ids)
        self.assertEqual(len(claimed), 2)

    def test_claim_failed_task_for_post_now(self):
        """DEC-03: claim_task_for_execution cho phép nhận task FAILED khi worker_id là post_now/manual_retry."""
        self.mgr.add_tasks([
            {
                "id": "task_failed_01",
                "profile_name": "Prof_01",
                "video_path": "test.mp4",
                "execution_mode": "SERVER_SCHEDULE",
                "status": "PENDING",
            }
        ])
        self.mgr.update_task("task_failed_01", {"status": "FAILED", "error_message": "Lỗi API"})

        # 1. Thử claim bằng worker thông thường -> Phải bị từ chối
        normal_claim = self.mgr.claim_task_for_execution("task_failed_01", worker_id="daemon_loop")
        self.assertFalse(normal_claim, "Worker thông thường không được phép claim task FAILED!")

        # 2. Thử claim bằng post_now -> Phải được chấp nhận
        post_now_claim = self.mgr.claim_task_for_execution("task_failed_01", worker_id="post_now")
        self.assertTrue(post_now_claim, "Yêu cầu post_now phải claim được task FAILED!")

        t = self.mgr.get_task_by_id("task_failed_01")
        self.assertEqual(t["status"], "UPLOADING")
        self.assertEqual(t["claim_worker_id"], "post_now")

    def test_delete_tasks_blocks_uploading_status(self):
        """DEC-04: delete_tasks không được xóa các task đang mang trạng thái UPLOADING."""
        self.mgr.add_tasks([
            {
                "id": "task_uploading_01",
                "profile_name": "Prof_01",
                "video_path": "test.mp4",
                "status": "PENDING",
            },
            {
                "id": "task_pending_delete",
                "profile_name": "Prof_01",
                "video_path": "test.mp4",
                "status": "PENDING",
            }
        ])
        self.mgr.update_task("task_uploading_01", {"status": "UPLOADING"})

        deleted_count = self.mgr.delete_tasks(["task_uploading_01", "task_pending_delete"])

        # Chỉ được xóa task pending, task uploading phải được giữ nguyên
        self.assertEqual(deleted_count, 1)
        self.assertIsNotNone(self.mgr.get_task_by_id("task_uploading_01"), "Task UPLOADING không được bị xóa!")
        self.assertIsNone(self.mgr.get_task_by_id("task_pending_delete"))

    def test_video_referenced_protects_failed_with_retries(self):
        """DEC-06: is_video_referenced_by_pending_tasks bảo vệ video nếu còn task FAILED có thể retry."""
        test_video = str(Path(self.test_dir) / "sample_video.mp4")

        self.mgr.add_tasks([
            {
                "id": "task_finished_01",
                "profile_name": "Prof_A",
                "video_path": test_video,
                "status": "SUCCESS",
            },
            {
                "id": "task_failed_retryable",
                "profile_name": "Prof_B",
                "video_path": test_video,
                "status": "PENDING",
                "retry_count": 1,
                "max_retries": 2,
            }
        ])
        self.mgr.update_task("task_failed_retryable", {"status": "FAILED"})

        # Loại trừ task_finished_01 -> task_failed_retryable vẫn còn lượt retry (1 < 2) -> phải trả về True!
        referenced = self.mgr.is_video_referenced_by_pending_tasks(test_video, exclude_task_id="task_finished_01")
        self.assertTrue(referenced, "File video phải được bảo vệ nếu task còn lượt retry!")

        # Khi retry_count chạm max_retries -> không còn tham chiếu
        self.mgr.update_task("task_failed_retryable", {"retry_count": 2})
        referenced_after_exhaust = self.mgr.is_video_referenced_by_pending_tasks(test_video, exclude_task_id="task_finished_01")
        self.assertFalse(referenced_after_exhaust, "Khi task đã hết lượt retry, file không còn bị giữ.")

    def test_check_profile_conflicts_includes_uploading_and_missed(self):
        """DEC-08: check_profile_conflicts phát hiện cả task UPLOADING và MISSED_RESCHEDULED."""
        self.mgr.add_tasks([
            {
                "id": "task_up_01",
                "profile_name": "Prof_Conflict",
                "video_path": "v1.mp4",
                "status": "PENDING",
            },
            {
                "id": "task_missed_c01",
                "profile_name": "Prof_Conflict",
                "video_path": "v2.mp4",
                "status": "PENDING",
            }
        ])
        self.mgr.update_task("task_up_01", {"status": "UPLOADING"})
        self.mgr.update_task("task_missed_c01", {"status": "MISSED_RESCHEDULED"})

        conflicts = self.mgr.check_profile_conflicts(["Prof_Conflict"])
        self.assertIn("Prof_Conflict", conflicts)
        info = conflicts["Prof_Conflict"]
        self.assertEqual(info["pending_count"], 2, "Phải đếm đủ 2 task đang chiếm slot!")

    def test_posted_today_ignores_updated_at(self):
        """DEC-05: Thống kê bài đăng hôm nay chỉ căn cứ vào completed_at, không bị bóp méo bởi updated_at."""
        yesterday_str = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        today_str = datetime.date.today().isoformat()

        self.mgr.add_tasks([
            {
                "id": "task_old_success",
                "profile_name": "Prof_01",
                "video_path": "old.mp4",
                "status": "PENDING",
            }
        ])
        # Đánh dấu task hoàn thành hôm qua
        self.mgr.update_task("task_old_success", {
            "status": "SUCCESS",
            "completed_at": f"{yesterday_str}T10:00:00",
        })

        # Giả lập hôm nay có một chỉnh sửa trên bản ghi (updated_at gán bằng hôm nay)
        self.mgr.update_task("task_old_success", {"error_message": "Ghi chú cập nhật"})

        stats = self.mgr.get_summary_stats()
        self.assertEqual(stats["posted_today"], 0, "Task đăng hôm qua không được tính vào bài đăng hôm nay dù updated_at là hôm nay!")

        # Nếu một bài có completed_at hôm nay -> phải được tính
        self.mgr.add_tasks([
            {
                "id": "task_today_success",
                "profile_name": "Prof_01",
                "video_path": "today.mp4",
                "status": "PENDING",
            }
        ])
        self.mgr.update_task("task_today_success", {
            "status": "SUCCESS",
            "completed_at": f"{today_str}T08:30:00",
        })
        stats_after = self.mgr.get_summary_stats()
        self.assertEqual(stats_after["posted_today"], 1)


if __name__ == "__main__":
    unittest.main()
