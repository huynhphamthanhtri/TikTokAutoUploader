# -*- coding: utf-8 -*-
"""
test_schedule_execution_hardened.py - Bộ kiểm thử cô lập kiểm chứng luồng thực thi và Auto-Fallback (Phase 2).

Tuân thủ nghiêm ngặt:
- AGENTS.md Quy tắc 14: Zero Live Data Mutation (100% test sử dụng tempfile/tmp_path cô lập, mock upload engine).
- DEC-01 (Intent Guard): Chặn đứng task SERVER_SCHEDULE nếu epoch không hợp lệ, không tự ý đăng ngay.
- DEC-07: force_immediate=True ép sched_epoch=None cho thao tác Đăng Ngay Lập Tức.
- DEC-08: Auto-fallback sang LOCAL_QUEUE khi TikTok trả lý do schedule_disabled (ab_value='0').
- DEC-06: Bảo vệ video nguồn nếu update_task thất bại.
"""

import datetime
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from schedule_manager import ScheduleManager
import main


class TestScheduleExecutionHardened(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_exec_")
        self.db_file = Path(self.test_dir) / "test_scheduled_posts.db"
        self.mgr = ScheduleManager(storage_file=self.db_file)
        main.global_schedule_manager = self.mgr
        main.profiles = {
            "Prof_Test": {"config": {"tiktok_account": "user_test"}}
        }

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_server_schedule_rejects_invalid_epoch(self):
        """DEC-01 (Intent Guard): Task SERVER_SCHEDULE có epoch <= 0 bị từ chối và đổi sang FAILED."""
        self.mgr.add_tasks([
            {
                "id": "task_bad_epoch",
                "profile_name": "Prof_Test",
                "video_path": "fake.mp4",
                "execution_mode": "SERVER_SCHEDULE",
                "status": "PENDING",
            }
        ])
        self.mgr.update_task("task_bad_epoch", {"scheduled_epoch": 0})
        t = self.mgr.get_task_by_id("task_bad_epoch")

        with patch("main.run_direct_upload") as mock_upload:
            ok, msg = main._execute_schedule_task_upload(t, force_immediate=False)
            self.assertFalse(ok)
            self.assertIn("epoch không hợp lệ", msg)
            mock_upload.assert_not_called()

        updated_t = self.mgr.get_task_by_id("task_bad_epoch")
        self.assertEqual(updated_t["status"], "FAILED")

    def test_post_now_forces_immediate_epoch_none(self):
        """DEC-07: Khi force_immediate=True, schedule_time truyền vào run_direct_upload phải là None."""
        future_epoch = int((datetime.datetime.now() + datetime.timedelta(days=2)).timestamp())
        self.mgr.add_tasks([
            {
                "id": "task_force_now",
                "profile_name": "Prof_Test",
                "video_path": "fake.mp4",
                "execution_mode": "SERVER_SCHEDULE",
                "scheduled_epoch": future_epoch,
                "status": "PENDING",
            }
        ])
        t = self.mgr.get_task_by_id("task_force_now")

        mock_res = MagicMock()
        mock_res.outcome = "posted"
        mock_res.message = "Đã đăng thành công"
        mock_res.details = {"item_id": "78910", "video_url": "https://tiktok.com/vid1"}

        with patch("main.run_direct_upload", return_value=mock_res) as mock_upload:
            ok, msg = main._execute_schedule_task_upload(t, force_immediate=True)
            self.assertTrue(ok)
            mock_upload.assert_called_once()
            _, kwargs = mock_upload.call_args
            self.assertIsNone(kwargs.get("schedule_time"), "force_immediate=True phải ép schedule_time=None!")

        updated_t = self.mgr.get_task_by_id("task_force_now")
        self.assertEqual(updated_t["status"], "SUCCESS", "Đăng ngay lập tức phải hoàn tất ở trạng thái SUCCESS!")

    def test_auto_fallback_to_local_queue_on_schedule_disabled(self):
        """DEC-08: Tự động chuyển đổi sang LOCAL_QUEUE nếu TikTok từ chối vì ab_value='0'."""
        future_epoch = int((datetime.datetime.now() + datetime.timedelta(days=1)).timestamp())
        self.mgr.add_tasks([
            {
                "id": "task_ab0_fallback",
                "profile_name": "Prof_Test",
                "video_path": "fake.mp4",
                "execution_mode": "SERVER_SCHEDULE",
                "scheduled_epoch": future_epoch,
                "status": "PENDING",
            }
        ])
        t = self.mgr.get_task_by_id("task_ab0_fallback")

        mock_res = MagicMock()
        mock_res.outcome = "rejected"
        mock_res.message = "Tài khoản không được TikTok cấp quyền lên lịch: ab_value='0'"
        mock_res.details = {"rejection_scope": "account_blocked", "reason": "schedule_disabled"}

        with patch("main.run_direct_upload", return_value=mock_res):
            ok, msg = main._execute_schedule_task_upload(t, force_immediate=False)
            self.assertTrue(ok)
            self.assertIn("LOCAL_QUEUE", msg)

        updated_t = self.mgr.get_task_by_id("task_ab0_fallback")
        self.assertEqual(updated_t["execution_mode"], "LOCAL_QUEUE", "Phải đổi execution_mode sang LOCAL_QUEUE!")
        self.assertEqual(updated_t["status"], "PENDING", "Task phải ở trạng thái PENDING để daemon loop sau này chạy tiếp!")
        self.assertIn("LOCAL_QUEUE", updated_t["error_message"])

    def test_source_deletion_requires_update_ok(self):
        """DEC-06: File video nguồn không bị xóa nếu update_task gặp lỗi hoặc không thành công."""
        test_video_path = Path(self.test_dir) / "source_video.mp4"
        test_video_path.write_bytes(b"dummy_video_content")

        self.mgr.add_tasks([
            {
                "id": "task_del_guard",
                "profile_name": "Prof_Test",
                "video_path": str(test_video_path),
                "execution_mode": "LOCAL_QUEUE",
                "delete_source_after_post": 1,
                "status": "PENDING",
            }
        ])
        t = self.mgr.get_task_by_id("task_del_guard")

        mock_res = MagicMock()
        mock_res.outcome = "posted"
        mock_res.message = "Đã đăng thành công"
        mock_res.details = {"item_id": "111", "video_url": "https://tiktok.com/vid"}

        # Giả lập update_task trả về False (DB lock timeout hoặc optimistic locking fail)
        with patch("main.run_direct_upload", return_value=mock_res), \
             patch.object(self.mgr, "update_task", return_value=False):
            main._execute_schedule_task_upload(t, force_immediate=True)

        # File video vẫn phải còn nguyên vẹn trên đĩa!
        self.assertTrue(test_video_path.exists(), "File video tuyệt đối không được bị xóa nếu DB update_task chưa thành công!")


if __name__ == "__main__":
    unittest.main()
