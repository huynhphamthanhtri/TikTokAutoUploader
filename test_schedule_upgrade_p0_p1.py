"""
test_schedule_upgrade_p0_p1.py - Bộ kiểm thử tính đúng đắn nghiệp vụ cho gói nâng cấp P0 & P1 của Lịch Đăng.

Bao phủ:
1. SQLite Storage & Migration: Khởi tạo, WAL mode, chuyển đổi tự động từ file JSON cũ.
2. Atomic Task Claiming (P0): Chống race-condition khi nhiều luồng cùng nhận 1 task, kiểm tra thời hạn lease.
3. Resource Integrity (P0): Kiểm tra tham chiếu file nguồn trước khi xóa, bảo vệ file video dùng chung.
4. Server Schedule Conflict Guard (P0): Phát hiện và đếm chính xác số lượng task đã nạp lên server TikTok.
5. Smart Reschedule (P1): Dời lịch lỡ hẹn thông minh đa ngày tuân thủ max_daily_posts, chống dồn bài 1 ngày.
"""

import concurrent.futures
import datetime
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from schedule_manager import ScheduleManager


class TestScheduleUpgradeP0P1(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_p0_p1_")
        self.db_file = Path(self.test_dir) / "scheduled_posts.db"
        self.json_file = Path(self.test_dir) / "scheduled_posts.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # =========================================================================
    # 1. TEST SQLITE STORAGE & AUTO-MIGRATION FROM JSON
    # =========================================================================

    def test_auto_migration_from_legacy_json(self):
        """Hệ thống tự động phát hiện và chuyển đổi nguyên vẹn 100% dữ liệu từ JSON cũ sang SQLite."""
        # 1. Tạo file JSON cũ mô phỏng dữ liệu lịch của người dùng trước khi nâng cấp
        legacy_data = [
            {
                "id": "task_legacy_01",
                "profile_name": "Profile_Legacy_1",
                "tiktok_account": "user_legacy",
                "video_path": "C:/videos/leg_01.mp4",
                "title": "Tiêu đề cũ 1",
                "scheduled_time": "2026-10-25T12:00:00",
                "execution_mode": "SERVER_SCHEDULE",
                "status": "SERVER_SCHEDULED",
            },
            {
                "id": "task_legacy_02",
                "profile_name": "Profile_Legacy_2",
                "tiktok_account": "user_legacy_2",
                "video_path": "C:/videos/leg_02.mp4",
                "title": "Tiêu đề cũ 2",
                "scheduled_time": "2026-10-25T18:00:00",
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            },
        ]
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f, ensure_ascii=False)

        # 2. Khởi tạo ScheduleManager mới trỏ vào db_file
        mgr = ScheduleManager(storage_file=self.db_file)

        # 3. Xác minh SQLite đã nạp đủ 2 task
        tasks = mgr.get_all_tasks()
        self.assertEqual(len(tasks), 2)
        task_map = {t["id"]: t for t in tasks}
        self.assertIn("task_legacy_01", task_map)
        self.assertIn("task_legacy_02", task_map)
        self.assertEqual(task_map["task_legacy_01"]["status"], "SERVER_SCHEDULED")
        self.assertEqual(task_map["task_legacy_02"]["status"], "PENDING")

        # 4. Xác minh file JSON cũ đã được đổi tên backup sang .json.bak
        bak_file = self.json_file.with_suffix(".json.bak")
        self.assertTrue(bak_file.exists())

    # =========================================================================
    # 2. TEST ATOMIC TASK CLAIMING (CHỐNG ĐĂNG TRÙNG GIỮA DAEMON & MANUAL)
    # =========================================================================

    def test_atomic_claim_prevents_duplicate_workers(self):
        """Chứng minh: Khi 10 luồng cùng tranh chấp claim 1 task, chỉ DUY NHẤT 1 luồng thành công."""
        mgr = ScheduleManager(storage_file=self.db_file)
        mgr.add_tasks([{
            "id": "task_compete_01",
            "profile_name": "Prof_Compete",
            "video_path": "C:/videos/clip.mp4",
            "scheduled_time": datetime.datetime.now().isoformat(),
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
        }])

        results = []
        def attempt_claim(worker_idx):
            worker_id = f"worker_{worker_idx}"
            success = mgr.claim_task_for_execution("task_compete_01", worker_id=worker_id, lease_seconds=60)
            return worker_idx, success

        # Chạy 10 luồng đồng thời cố gắng claim task_compete_01
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(attempt_claim, i) for i in range(10)]
            for fut in concurrent.futures.as_completed(futures):
                results.append(fut.result())

        success_count = sum(1 for _, ok in results if ok)
        fail_count = sum(1 for _, ok in results if not ok)

        # ĐÚNG ĐẮN VỀ MẶT NGHIỆP VỤ: Đúng 1 luồng nhận được task, 9 luồng còn lại bị từ chối
        self.assertEqual(success_count, 1)
        self.assertEqual(fail_count, 9)

        # Trạng thái task trong DB phải là UPLOADING
        t = mgr.get_task_by_id("task_compete_01")
        self.assertEqual(t["status"], "UPLOADING")
        self.assertTrue(t["claim_worker_id"].startswith("worker_"))

    def test_atomic_claim_lease_expiration_allows_reclaim(self):
        """Nếu worker trước bị sập (crash) và hết hạn lease, worker mới có quyền reclaim task."""
        mgr = ScheduleManager(storage_file=self.db_file)
        mgr.add_tasks([{
            "id": "task_lease_test",
            "profile_name": "Prof_Lease",
            "video_path": "C:/videos/clip.mp4",
            "scheduled_time": datetime.datetime.now().isoformat(),
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
        }])

        # Worker 1 claim với lease 1 giây
        ok1 = mgr.claim_task_for_execution("task_lease_test", worker_id="crashed_worker", lease_seconds=1)
        self.assertTrue(ok1)

        # Ngay lập tức Worker 2 claim -> Phải thất bại vì còn trong hạn lease
        ok2 = mgr.claim_task_for_execution("task_lease_test", worker_id="worker_2", lease_seconds=60)
        self.assertFalse(ok2)

        # Giả lập lùi lease_until về quá khứ (hết hạn lease)
        past_epoch = int((datetime.datetime.now() - datetime.timedelta(seconds=10)).timestamp())
        mgr.update_task("task_lease_test", {"lease_until": past_epoch})

        # Worker 3 claim lại -> Phải thành công giải cứu task bị treo
        ok3 = mgr.claim_task_for_execution("task_lease_test", worker_id="rescue_worker", lease_seconds=60)
        self.assertTrue(ok3)
        t = mgr.get_task_by_id("task_lease_test")
        self.assertEqual(t["claim_worker_id"], "rescue_worker")

    def test_claim_due_tasks_batch(self):
        """claim_due_tasks tự động quét và claim đúng các task LOCAL_QUEUE đến hạn."""
        now = datetime.datetime.now()
        past_due = (now - datetime.timedelta(minutes=5)).isoformat()
        future_due = (now + datetime.timedelta(hours=2)).isoformat()

        mgr = ScheduleManager(storage_file=self.db_file)
        mgr.add_tasks([
            {
                "id": "task_due_1",
                "profile_name": "P1",
                "video_path": "C:/vid1.mp4",
                "scheduled_time": past_due,
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            },
            {
                "id": "task_due_2",
                "profile_name": "P2",
                "video_path": "C:/vid2.mp4",
                "scheduled_time": past_due,
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            },
            {
                "id": "task_future_1",
                "profile_name": "P3",
                "video_path": "C:/vid3.mp4",
                "scheduled_time": future_due,
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            },
            {
                "id": "task_server_past",
                "profile_name": "P4",
                "video_path": "C:/vid4.mp4",
                "scheduled_time": past_due,
                "execution_mode": "SERVER_SCHEDULE",  # Server schedule không được daemon nhận
                "status": "PENDING",
            },
        ])

        # Claim tối đa 2 task
        claimed = mgr.claim_due_tasks(worker_id="daemon", limit=2)
        claimed_ids = [t["id"] for t in claimed]

        self.assertEqual(len(claimed), 2)
        self.assertIn("task_due_1", claimed_ids)
        self.assertIn("task_due_2", claimed_ids)
        self.assertNotIn("task_future_1", claimed_ids)
        self.assertNotIn("task_server_past", claimed_ids)

        for t in claimed:
            self.assertEqual(t["status"], "UPLOADING")
            self.assertEqual(t["claim_worker_id"], "daemon")

    # =========================================================================
    # 3. TEST RESOURCE INTEGRITY: BẢO VỆ FILE NGUỒN DÙNG CHUNG KHI XÓA
    # =========================================================================

    def test_shared_video_reference_check(self):
        """is_video_referenced_by_pending_tasks ngăn chặn xóa file video đang dùng chung."""
        mgr = ScheduleManager(storage_file=self.db_file)
        shared_video = "C:/common/promo_video.mp4"

        mgr.add_tasks([
            {
                "id": "task_p1",
                "profile_name": "Profile_01",
                "video_path": shared_video,
                "status": "PENDING",
            },
            {
                "id": "task_p2",
                "profile_name": "Profile_02",
                "video_path": shared_video,
                "status": "PENDING",
            },
        ])

        # Giả lập Task P1 upload xong và chuẩn bị xóa file:
        # Code kiểm tra xem ngoài task_p1, còn task nào khác dùng shared_video không
        is_ref = mgr.is_video_referenced_by_pending_tasks(shared_video, exclude_task_id="task_p1")
        self.assertTrue(is_ref, "Phải phát hiện task_p2 vẫn đang chờ dùng shared_video -> KHÔNG ĐƯỢC XÓA!")

        # Task P1 đổi status sang SUCCESS
        mgr.update_task("task_p1", {"status": "SUCCESS"})

        # Task P2 upload xong và chuẩn bị xóa file:
        is_ref_after_p2 = mgr.is_video_referenced_by_pending_tasks(shared_video, exclude_task_id="task_p2")
        self.assertFalse(is_ref_after_p2, "Không còn task nào khác chờ dùng -> ĐƯỢC PHÉP XÓA FILE!")

    # =========================================================================
    # 4. TEST SERVER SCHEDULE OVERWRITE DETECTION
    # =========================================================================

    def test_check_profile_conflicts_counts_server_scheduled(self):
        """check_profile_conflicts phân loại rõ ràng số bài local và số bài đã nạp lên máy chủ TikTok."""
        mgr = ScheduleManager(storage_file=self.db_file)
        mgr.add_tasks([
            {
                "profile_name": "Prof_Server",
                "status": "SERVER_SCHEDULED",
                "execution_mode": "SERVER_SCHEDULE",
                "scheduled_time": "2026-10-21T12:00:00",
            },
            {
                "profile_name": "Prof_Server",
                "status": "PENDING",
                "execution_mode": "LOCAL_QUEUE",
                "scheduled_time": "2026-10-21T18:00:00",
            },
            {
                "profile_name": "Prof_LocalOnly",
                "status": "PENDING",
                "execution_mode": "LOCAL_QUEUE",
                "scheduled_time": "2026-10-21T12:00:00",
            },
        ])

        conflicts = mgr.check_profile_conflicts(["Prof_Server", "Prof_LocalOnly"])

        self.assertIn("Prof_Server", conflicts)
        server_info = conflicts["Prof_Server"]
        self.assertEqual(server_info["pending_count"], 2)
        self.assertEqual(server_info["server_scheduled_count"], 1)
        self.assertEqual(server_info["local_pending_count"], 1)

        self.assertIn("Prof_LocalOnly", conflicts)
        local_info = conflicts["Prof_LocalOnly"]
        self.assertEqual(local_info["pending_count"], 1)
        self.assertEqual(local_info["server_scheduled_count"], 0)
        self.assertEqual(local_info["local_pending_count"], 1)

    # =========================================================================
    # 5. TEST SMART RESCHEDULE MULTI-DAY (CHỐNG DỒN BÀI VÀO 1 NGÀY KHI TẮT MÁY LÂU)
    # =========================================================================

    def test_smart_reschedule_distributes_multi_day(self):
        """Chứng minh logic mới: 5 task lỡ hẹn với max 2 bài/ngày được dàn đều qua 3 ngày khác nhau."""
        mgr = ScheduleManager(storage_file=self.db_file)
        
        # Tạo 5 task bị lỡ giờ từ 2 ngày trước
        now = datetime.datetime.now()
        past_base = now - datetime.timedelta(days=2)
        missed_tasks = []
        for i in range(5):
            t_dt = past_base + datetime.timedelta(hours=i * 2)
            missed_tasks.append({
                "id": f"missed_multi_{i}",
                "profile_name": "Prof_MultiDay",
                "video_path": f"C:/vid_{i}.mp4",
                "scheduled_time": t_dt.isoformat(),
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            })
        mgr.add_tasks(missed_tasks)

        # Chạy recover_missed_tasks với max_daily_posts=2
        recovered = mgr.recover_missed_tasks(max_daily_posts=2)
        self.assertEqual(len(recovered), 5)

        # Thu thập các ngày được dời tới
        rescheduled_dates = []
        for t in recovered:
            self.assertEqual(t["status"], "MISSED_RESCHEDULED")
            dt = datetime.datetime.fromisoformat(t["scheduled_time"])
            rescheduled_dates.append(dt.date())

        from collections import Counter
        counts_by_date = Counter(rescheduled_dates)

        # RÀO CHẮN NGHIỆP VỤ:
        # 1. Không có bất kỳ ngày nào được vượt quá 2 bài!
        for d, cnt in counts_by_date.items():
            self.assertLessEqual(cnt, 2, f"Ngày {d} bị dồn {cnt} bài, vượt quá giới hạn 2 bài/ngày!")

        # 2. 5 bài phải được phân bổ qua ít nhất 3 ngày riêng biệt (2 + 2 + 1)!
        self.assertEqual(len(counts_by_date), 3, f"Số ngày phân bổ thực tế: {len(counts_by_date)} (yêu cầu 3 ngày)")


if __name__ == "__main__":
    unittest.main()
