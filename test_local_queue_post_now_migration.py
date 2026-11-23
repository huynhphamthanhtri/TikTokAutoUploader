"""
test_local_queue_post_now_migration.py
Kiểm thử toàn diện cho quá trình chuyển đổi sang 100% Hàng đợi Máy tính (LOCAL_QUEUE),
xử lý nút 'Đăng ngay lập tức' (force_immediate=True), bảo vệ Option A (bảo lưu 90 bài SERVER_SCHEDULED),
phục hồi sau Sleep/Hibernate và hiển thị các chỉ số Dashboard mới.
"""

import datetime
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

if sys.platform == "win32":
    tcl_dir = os.path.join(sys.prefix, "tcl", "tcl8.6")
    tk_dir = os.path.join(sys.prefix, "tcl", "tk8.6")
    if os.path.exists(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.exists(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir

from schedule_contracts import ScheduleRequest
from schedule_manager import ScheduleManager


class TestLocalQueuePostNowMigration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_lq_migration_")
        self.db_path = os.path.join(self.test_dir, "scheduled_posts.db")
        self.mgr = ScheduleManager(storage_file=self.db_path)

        # Tạo file video giả lập
        self.v1 = Path(self.test_dir) / "vid1.mp4"
        self.v1.write_bytes(b"dummy video 1" * 100)
        self.v2 = Path(self.test_dir) / "vid2.mp4"
        self.v2.write_bytes(b"dummy video 2" * 100)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _add_single_task(self, task_data: dict) -> dict:
        return self.mgr.add_tasks([task_data])[0]

    def test_schedule_request_and_manager_defaults_to_local_queue(self):
        """1. Kiểm chứng ScheduleRequest và ScheduleManager mặc định là LOCAL_QUEUE."""
        req = ScheduleRequest(
            video_paths=(str(self.v1),),
            profile_names=("Profile_1",),
            profile_configs={"Profile_1": {}},
            formula="{filename}",
        )
        self.assertEqual(req.execution_mode, "LOCAL_QUEUE")

        task = self._add_single_task({
            "profile_name": "Profile_1",
            "video_path": str(self.v1),
            "scheduled_time": "2026-11-08T12:00:00",
            "scheduled_epoch": int(datetime.datetime.now().timestamp()) + 3600,
        })
        self.assertEqual(task["execution_mode"], "LOCAL_QUEUE")
        self.assertEqual(task["status"], "PENDING")

    def test_summary_stats_computes_total_success_including_historical_server(self):
        """2. Kiểm chứng get_summary_stats tính đúng total_success = SUCCESS + SERVER_SCHEDULED."""
        now = datetime.datetime.now()
        now_iso = now.isoformat()
        now_epoch = int(now.timestamp())

        # Thêm 1 bài SERVER_SCHEDULED lịch sử (Option A)
        self._add_single_task({
            "id": "task_server_hist_01",
            "profile_name": "Profile_1",
            "video_path": str(self.v1),
            "scheduled_time": (now + datetime.timedelta(days=2)).isoformat(),
            "scheduled_epoch": now_epoch + 172800,
            "execution_mode": "SERVER_SCHEDULE",
            "status": "SERVER_SCHEDULED",
        })

        # Thêm 1 bài SUCCESS đăng hôm nay
        self._add_single_task({
            "id": "task_success_01",
            "profile_name": "Profile_1",
            "video_path": str(self.v2),
            "scheduled_time": now_iso,
            "scheduled_epoch": now_epoch,
            "execution_mode": "LOCAL_QUEUE",
            "status": "SUCCESS",
            "completed_at": now_iso,
        })

        # Thêm 1 bài PENDING
        self._add_single_task({
            "id": "task_pending_01",
            "profile_name": "Profile_1",
            "video_path": str(self.v1),
            "scheduled_time": (now + datetime.timedelta(hours=3)).isoformat(),
            "scheduled_epoch": now_epoch + 10800,
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
        })

        # Thêm 1 bài FAILED
        self._add_single_task({
            "id": "task_failed_01",
            "profile_name": "Profile_1",
            "video_path": str(self.v1),
            "scheduled_time": now_iso,
            "scheduled_epoch": now_epoch,
            "execution_mode": "LOCAL_QUEUE",
            "status": "FAILED",
            "error_message": "Network timeout",
        })

        stats = self.mgr.get_summary_stats()
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["server_scheduled"], 1)
        self.assertEqual(stats["posted_today"], 1)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["total_success"], 2)  # 1 SERVER_SCHEDULED + 1 SUCCESS
        self.assertEqual(stats["total"], 4)

    def test_execute_schedule_task_upload_force_immediate(self):
        """3. Kiểm chứng _execute_schedule_task_upload khi force_immediate=True sẽ gọi schedule_time=None."""
        import main

        task = self._add_single_task({
            "profile_name": "Profile_1",
            "video_path": str(self.v1),
            "title": "Video Test Immediate",
            "scheduled_time": "2026-11-09T18:00:00",
            "scheduled_epoch": int(datetime.datetime.now().timestamp()) + 86400,
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
        })

        mock_upload_res = MagicMock()
        mock_upload_res.outcome = "posted"
        mock_upload_res.message = "Upload thành công"
        mock_upload_res.details = {"item_id": "77099998888", "video_url": "https://www.tiktok.com/video/77099998888"}

        with patch("main.global_schedule_manager", self.mgr), \
             patch("main.run_direct_upload", return_value=mock_upload_res) as mock_upload, \
             patch("main.update_status"):

            success, msg = main._execute_schedule_task_upload(task, force_immediate=True)

            self.assertTrue(success)
            self.assertEqual(msg, "Upload thành công")
            # QUAN TRỌNG: schedule_time BẮT BUỘC PHẢI LÀ None (đăng ngay)
            call_kwargs = mock_upload.call_args[1]
            self.assertIsNone(call_kwargs.get("schedule_time"))

            # Kiểm tra trạng thái database sau khi đăng ngay
            updated_task = self.mgr.get_task_by_id(task["id"])
            self.assertEqual(updated_task["status"], "SUCCESS")
            self.assertEqual(updated_task["execution_mode"], "LOCAL_QUEUE")
            self.assertEqual(updated_task["tiktok_item_id"], "77099998888")
            self.assertIsNotNone(updated_task["completed_at"])

    def test_post_now_protects_option_a_skips_historical_server_tasks(self):
        """4. Option A: 'Đăng ngay lập tức' bỏ qua bài SERVER_SCHEDULED để không đăng trùng lặp."""
        import main

        # Task 1: Historical SERVER_SCHEDULED
        t1 = self._add_single_task({
            "id": "task_server_protect_01",
            "profile_name": "Profile_1",
            "video_path": str(self.v1),
            "title": "Server Post Already",
            "scheduled_time": "2026-11-10T12:00:00",
            "scheduled_epoch": int(datetime.datetime.now().timestamp()) + 100000,
            "execution_mode": "SERVER_SCHEDULE",
            "status": "SERVER_SCHEDULED",
        })

        # Task 2: LOCAL_QUEUE PENDING
        t2 = self._add_single_task({
            "id": "task_local_pending_02",
            "profile_name": "Profile_1",
            "video_path": str(self.v2),
            "title": "Local Pending Video",
            "scheduled_time": "2026-11-10T12:00:00",
            "scheduled_epoch": int(datetime.datetime.now().timestamp()) + 100000,
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
        })

        mock_upload_res = MagicMock()
        mock_upload_res.outcome = "posted"
        mock_upload_res.message = "Posted successfully"
        mock_upload_res.details = {"item_id": "7711223344"}

        with patch("main.global_schedule_manager", self.mgr), \
             patch("main.run_direct_upload", return_value=mock_upload_res) as mock_upload, \
             patch("main.update_status"):

            raw_tasks = [self.mgr.get_task_by_id(tid) for tid in [t1["id"], t2["id"]]]
            ordered = [t for t in raw_tasks if t]

            executed_ids = []
            for t in ordered:
                if t.get("status") == "SERVER_SCHEDULED":
                    continue
                claimed = self.mgr.claim_task_for_execution(t["id"], worker_id="post_now", lease_seconds=600)
                if claimed:
                    executed_ids.append(t["id"])
                    main._execute_schedule_task_upload(t, force_immediate=True)

            # Task 1 SERVER_SCHEDULED không được chạy
            self.assertNotIn(t1["id"], executed_ids)
            # Task 2 LOCAL_QUEUE được chạy
            self.assertIn(t2["id"], executed_ids)
            # Đảm bảo run_direct_upload chỉ được gọi đúng 1 lần cho task 2
            self.assertEqual(mock_upload.call_count, 1)

            t1_after = self.mgr.get_task_by_id(t1["id"])
            self.assertEqual(t1_after["status"], "SERVER_SCHEDULED")

            t2_after = self.mgr.get_task_by_id(t2["id"])
            self.assertEqual(t2_after["status"], "SUCCESS")

    def test_daemon_claims_due_tasks_and_posts_immediate(self):
        """5. Background daemon phát hiện task LOCAL_QUEUE đến giờ và thực thi ngay lập tức."""
        import main

        now_epoch = int(datetime.datetime.now().timestamp())
        due_task = self._add_single_task({
            "id": "task_due_01",
            "profile_name": "Profile_1",
            "video_path": str(self.v1),
            "title": "Due Video",
            "scheduled_time": datetime.datetime.now().isoformat(),
            "scheduled_epoch": now_epoch - 10,
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
        })

        mock_upload_res = MagicMock()
        mock_upload_res.outcome = "posted"
        mock_upload_res.message = "Posted due task"
        mock_upload_res.details = {"item_id": "888889999"}

        with patch("main.global_schedule_manager", self.mgr), \
             patch("main.run_direct_upload", return_value=mock_upload_res) as mock_upload, \
             patch("main.update_status"):

            claimed = self.mgr.claim_due_tasks(worker_id="daemon_loop", limit=2, lease_seconds=600)
            self.assertEqual(len(claimed), 1)
            self.assertEqual(claimed[0]["id"], due_task["id"])

            success, _ = main._execute_schedule_task_upload(claimed[0], force_immediate=True)
            self.assertTrue(success)

            call_kwargs = mock_upload.call_args[1]
            self.assertIsNone(call_kwargs.get("schedule_time"))

            after_task = self.mgr.get_task_by_id(due_task["id"])
            self.assertEqual(after_task["status"], "SUCCESS")

    def test_recover_missed_tasks_on_wake_from_sleep(self):
        """6. Kiểm tra thuật toán phục hồi dời lịch thông minh khi PC thức dậy từ Sleep (> 120s trễ)."""
        now = datetime.datetime.now()
        now_epoch = int(now.timestamp())
        old_epoch = now_epoch - 3600  # Quá hạn 1 tiếng trước

        t1 = self._add_single_task({
            "id": "missed_01",
            "profile_name": "Profile_Wake",
            "video_path": str(self.v1),
            "title": "Missed 1",
            "scheduled_time": (now - datetime.timedelta(hours=1)).isoformat(),
            "scheduled_epoch": old_epoch,
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
        })
        t2 = self._add_single_task({
            "id": "missed_02",
            "profile_name": "Profile_Wake",
            "video_path": str(self.v2),
            "title": "Missed 2",
            "scheduled_time": (now - datetime.timedelta(minutes=45)).isoformat(),
            "scheduled_epoch": old_epoch + 900,
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
        })

        recovered = self.mgr.recover_missed_tasks(max_daily_posts=2)
        self.assertEqual(len(recovered), 2)

        rec1 = self.mgr.get_task_by_id(t1["id"])
        self.assertEqual(rec1["status"], "MISSED_RESCHEDULED")
        self.assertLessEqual(rec1["scheduled_epoch"], now_epoch + 10)

        rec2 = self.mgr.get_task_by_id(t2["id"])
        self.assertEqual(rec2["status"], "MISSED_RESCHEDULED")
        self.assertGreater(rec2["scheduled_epoch"], rec1["scheduled_epoch"])

    def test_auto_schedule_view_metric_cards_and_modal_default(self):
        """7. Kiểm tra AutoScheduleView hiển thị thẻ '🎉 Đã Hoàn Tất (Tổng)' và BulkScheduleModal mặc định LOCAL_QUEUE."""
        import tkinter as tk
        from schedule_ui import AutoScheduleView, BulkScheduleModal

        root = tk.Tk()
        root.withdraw()
        try:
            # Thêm các task với các trạng thái khác nhau
            now = datetime.datetime.now()
            self._add_single_task({
                "id": "t_server",
                "profile_name": "P1",
                "video_path": str(self.v1),
                "scheduled_time": (now + datetime.timedelta(days=2)).isoformat(),
                "scheduled_epoch": int((now + datetime.timedelta(days=2)).timestamp()),
                "status": "SERVER_SCHEDULED",
                "execution_mode": "SERVER_SCHEDULE",
            })
            self._add_single_task({
                "id": "t_success",
                "profile_name": "P1",
                "video_path": str(self.v2),
                "scheduled_time": now.isoformat(),
                "scheduled_epoch": int(now.timestamp()),
                "status": "SUCCESS",
                "execution_mode": "LOCAL_QUEUE",
                "completed_at": now.isoformat(),
            })
            self._add_single_task({
                "id": "t_pending",
                "profile_name": "P1",
                "video_path": str(self.v1),
                "scheduled_time": now.isoformat(),
                "scheduled_epoch": int(now.timestamp()) + 3600,
                "status": "PENDING",
                "execution_mode": "LOCAL_QUEUE",
            })

            view = AutoScheduleView(root, schedule_manager=self.mgr)
            # Kiểm tra các metric variables
            self.assertEqual(view.stat_pending_var.get(), "1")
            self.assertEqual(view.stat_today_var.get(), "1")
            # total_success = 1 SERVER_SCHEDULED + 1 SUCCESS = 2
            self.assertEqual(view.stat_total_success_var.get(), "2")
            self.assertEqual(view.stat_failed_var.get(), "0")

            # Kiểm tra BulkScheduleModal mặc định LOCAL_QUEUE
            modal = BulkScheduleModal(
                parent=root,
                selected_profiles=["P1"],
                profile_configs={"P1": {}},
                schedule_manager=self.mgr,
            )
            self.assertEqual(modal.exec_mode_var.get(), "LOCAL_QUEUE")
            modal.destroy()
            view.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
