# -*- coding: utf-8 -*-
"""
test_schedule_commit_p2.py - Bộ kiểm thử tính đúng đắn nghiệp vụ cho Phase 2 (Lưu trữ nguyên tử & Commit Transaction).

Tuân thủ nghiêm ngặt:
- AGENTS.md Quy tắc 14: Zero Live Data Mutation (100% test sử dụng tempfile/tmp_path cô lập).
- Kiểm chứng khả năng rollback 100% khi gặp sự cố giữa chừng trong chế độ OVERWRITE.
- Kiểm chứng Stale Plan Guard (chống lưu preview lỗi thời).
- Kiểm chứng Idempotent Retry Commit (commit lặp lại cùng plan trả về biên lai cũ).
- Kiểm chứng Optimistic Locking version guard trong update_task.
"""

import datetime
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from schedule_contracts import (
    ConflictSnapshot,
    InvalidPlanCommitError,
    PlannedTask,
    ScheduleCommitError,
    ScheduleCommitResult,
    SchedulePlan,
    ScheduleRequest,
    StalePlanError,
    TimelineSlot,
)
from schedule_manager import ScheduleManager
from schedule_service import commit_schedule_plan


class TestScheduleCommitP2(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_commit_p2_")
        self.db_file = Path(self.test_dir) / "test_scheduled_posts.db"
        self.mgr = ScheduleManager(storage_file=self.db_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_sample_plan(
        self,
        plan_id: str = "plan_test_001",
        profile_name: str = "Prof_Alpha",
        task_count: int = 2,
        resolution_mode: str = "APPEND",
        conflicts: ConflictSnapshot = None,
    ) -> SchedulePlan:
        """Tạo đối tượng SchedulePlan hợp lệ phục vụ kiểm thử."""
        tasks = []
        now = datetime.datetime.now(datetime.timezone.utc)
        for i in range(task_count):
            sched_dt = now + datetime.timedelta(days=i + 1, hours=12)
            pt = PlannedTask(
                slot_id="TRUA",
                profile_name=profile_name,
                tiktok_account="@alpha_user",
                video_path=f"C:/mock_videos/vid_{i}.mp4",
                canonical_video_path=f"C:/mock_videos/vid_{i}.mp4",
                video_filename=f"vid_{i}.mp4",
                title=f"Sample Video {i}",
                profile_timezone="America/New_York",
                scheduled_epoch=int(sched_dt.timestamp()),
                scheduled_time_local=sched_dt.isoformat(),
                execution_mode="SERVER_SCHEDULE",
                delete_source_after_post=True,
                source_size_bytes=1024 * 1024 * 5,
                source_mtime_ns=1700000000000000000,
            )
            tasks.append(pt)

        return SchedulePlan(
            plan_id=plan_id,
            created_at=now.isoformat(),
            seed=42,
            profile_assignments={profile_name: tuple(tasks)},
            unassigned_videos=(),
            conflicts_identified=conflicts,
            resolution_mode=resolution_mode,
            requested_profile_names=(profile_name,),
        )

    def test_atomic_commit_append_mode(self):
        """P2-01: Commit thành công ở chế độ APPEND tạo đầy đủ tasks, metadata P2 và lưu vết schedule_plans."""
        plan = self._create_sample_plan(plan_id="plan_append_01", task_count=2, resolution_mode="APPEND")
        result = commit_schedule_plan(plan, manager=self.mgr)

        self.assertIsInstance(result, ScheduleCommitResult)
        self.assertEqual(result.plan_id, "plan_append_01")
        self.assertEqual(len(result.created_task_ids), 2)
        self.assertEqual(len(result.cancelled_task_ids), 0)
        self.assertFalse(result.already_committed)
        self.assertEqual(result.resolution_mode, "APPEND")

        # Kiểm tra SQLite storage thực tế
        all_tasks = self.mgr.get_all_tasks()
        self.assertEqual(len(all_tasks), 2)

        for t in all_tasks:
            self.assertEqual(t["status"], "PENDING")
            self.assertEqual(t["version"], 1)
            self.assertEqual(t["plan_id"], "plan_append_01")
            self.assertEqual(t["profile_timezone"], "America/New_York")
            self.assertTrue(t["canonical_video_path"].startswith("C:/mock_videos/vid_"))
            self.assertEqual(t["source_size_bytes"], 1024 * 1024 * 5)

        # Kiểm tra bảng schedule_plans
        conn = self.mgr._get_connection()
        try:
            plan_row = conn.execute("SELECT * FROM schedule_plans WHERE plan_id = ?", ("plan_append_01",)).fetchone()
            self.assertIsNotNone(plan_row)
            self.assertEqual(plan_row["status"], "COMMITTED")
            self.assertEqual(plan_row["resolution_mode"], "APPEND")
            receipt = json.loads(plan_row["receipt_json"])
            self.assertEqual(receipt["plan_id"], "plan_append_01")
            self.assertEqual(len(receipt["created_task_ids"]), 2)
        finally:
            conn.close()

    def test_atomic_commit_overwrite_mode_success(self):
        """P2-02: Commit ở chế độ OVERWRITE hủy chính xác các task cũ và chèn task mới trong cùng transaction."""
        # 1. Thêm 2 task cũ vào hàng đợi cho Prof_Alpha
        self.mgr.add_tasks([
            {
                "id": "old_task_01",
                "profile_name": "Prof_Alpha",
                "video_path": "C:/old/vid_01.mp4",
                "scheduled_time": "2026-10-26T12:00:00",
                "status": "PENDING",
            },
            {
                "id": "old_task_02",
                "profile_name": "Prof_Alpha",
                "video_path": "C:/old/vid_02.mp4",
                "scheduled_time": "2026-10-27T12:00:00",
                "status": "QUEUED",
            },
        ])

        # 2. Tạo snapshot và plan mới ở chế độ OVERWRITE
        snap = self.mgr.get_conflict_snapshot(["Prof_Alpha"])
        self.assertEqual(len(snap.conflicted_profiles), 1)

        plan = self._create_sample_plan(
            plan_id="plan_overwrite_01",
            profile_name="Prof_Alpha",
            task_count=3,
            resolution_mode="OVERWRITE",
            conflicts=snap,
        )

        result = commit_schedule_plan(plan, manager=self.mgr)
        self.assertEqual(len(result.cancelled_task_ids), 2)
        self.assertEqual(len(result.created_task_ids), 3)

        # 3. Xác thực trạng thái trong SQLite
        old_1 = self.mgr.get_task_by_id("old_task_01")
        old_2 = self.mgr.get_task_by_id("old_task_02")
        self.assertEqual(old_1["status"], "CANCELLED")
        self.assertEqual(old_2["status"], "CANCELLED")
        self.assertEqual(old_1["version"], 2)  # Phiên bản tăng sau khi hủy
        self.assertEqual(old_2["version"], 2)

        # Task mới phải là PENDING và version 1
        for tid in result.created_task_ids:
            new_t = self.mgr.get_task_by_id(tid)
            self.assertEqual(new_t["status"], "PENDING")
            self.assertEqual(new_t["version"], 1)
            self.assertEqual(new_t["plan_id"], "plan_overwrite_01")

    def test_atomic_commit_overwrite_rollback_on_failure(self):
        """P2-03 (Semantic Integrity): Nếu việc chèn task mới bị lỗi, 100% lịch cũ KHÔNG bị hủy (Transaction Rollback)."""
        # 1. Thêm 2 task cũ vào hàng đợi
        self.mgr.add_tasks([
            {
                "id": "critical_old_01",
                "profile_name": "Prof_Rollback",
                "video_path": "C:/old/vid_01.mp4",
                "status": "PENDING",
            },
            {
                "id": "critical_old_02",
                "profile_name": "Prof_Rollback",
                "video_path": "C:/old/vid_02.mp4",
                "status": "PENDING",
            },
        ])

        snap = self.mgr.get_conflict_snapshot(["Prof_Rollback"])
        plan = self._create_sample_plan(
            plan_id="plan_fail_inject",
            profile_name="Prof_Rollback",
            task_count=2,
            resolution_mode="OVERWRITE",
            conflicts=snap,
        )

        # 2. Tạo SQL Trigger giả lập lỗi crash I/O tại thời điểm INSERT INTO scheduled_tasks
        conn = self.mgr._get_connection()
        try:
            conn.execute("""
                CREATE TRIGGER fail_on_insert_trigger
                BEFORE INSERT ON scheduled_tasks
                BEGIN
                    SELECT RAISE(ABORT, 'Simulated disk I/O crash during task insertion!');
                END;
            """)
        finally:
            conn.close()

        try:
            with self.assertRaises(sqlite3.IntegrityError):
                commit_schedule_plan(plan, manager=self.mgr)
        finally:
            conn = self.mgr._get_connection()
            try:
                conn.execute("DROP TRIGGER IF EXISTS fail_on_insert_trigger;")
            finally:
                conn.close()

        # 3. KIỂM CHỨNG TÍNH NGUYÊN TỬ (ROLLBACK VERIFICATION):
        # Lịch cũ tuyệt đối KHÔNG được chuyển thành CANCELLED!
        t1 = self.mgr.get_task_by_id("critical_old_01")
        t2 = self.mgr.get_task_by_id("critical_old_02")
        self.assertEqual(t1["status"], "PENDING", "Lịch cũ không được phép bị hủy khi commit bị rollback!")
        self.assertEqual(t2["status"], "PENDING", "Lịch cũ không được phép bị hủy khi commit bị rollback!")
        self.assertEqual(t1["version"], 1, "Version của lịch cũ phải giữ nguyên vẹn!")

        # Không có task mới nào rò rỉ vào database
        all_tasks = self.mgr.get_all_tasks()
        self.assertEqual(len(all_tasks), 2)

        # Không có plan rác nào lưu vào schedule_plans
        conn = self.mgr._get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM schedule_plans WHERE plan_id = ?", ("plan_fail_inject",)).fetchone()
            self.assertEqual(row["cnt"], 0)
        finally:
            conn.close()

    def test_atomic_commit_stale_fingerprint_rejection(self):
        """P2-04 (Stale Plan Guard): Nếu dữ liệu hàng đợi bị thay đổi sau khi preview, commit lập tức từ chối."""
        # 1. Khởi tạo 1 task
        self.mgr.add_tasks([{
            "id": "stale_base_01",
            "profile_name": "Prof_Stale",
            "video_path": "C:/stale/vid.mp4",
            "status": "PENDING",
        }])

        # 2. Người dùng mở modal xem preview -> Thu thập snapshot
        snap = self.mgr.get_conflict_snapshot(["Prof_Stale"])
        plan = self._create_sample_plan(
            plan_id="plan_stale_01",
            profile_name="Prof_Stale",
            task_count=1,
            resolution_mode="OVERWRITE",
            conflicts=snap,
        )

        # 3. Trong lúc người dùng đang ngắm modal preview, worker bên ngoài claim task hoặc user sửa task
        self.mgr.update_task("stale_base_01", {"status": "UPLOADING", "claim_worker_id": "worker_99"})

        # 4. Người dùng bấm nút "Lưu Lịch" -> Bắt buộc phải raise StalePlanError
        with self.assertRaises(StalePlanError):
            commit_schedule_plan(plan, manager=self.mgr, expected_snapshot_fingerprint=snap.snapshot_fingerprint)

        # 5. Xác nhận không có task nào của plan_stale_01 được chèn
        conn = self.mgr._get_connection()
        try:
            cnt = conn.execute("SELECT COUNT(*) as c FROM scheduled_tasks WHERE plan_id = ?", ("plan_stale_01",)).fetchone()["c"]
            self.assertEqual(cnt, 0)
        finally:
            conn.close()

    def test_idempotent_retry_commit(self):
        """P2-05 (Idempotency): Commit lại cùng một plan_id trả về biên lai cũ mà không nhân đôi bản ghi."""
        plan = self._create_sample_plan(plan_id="plan_idempotent_01", task_count=2, resolution_mode="APPEND")

        # Lần 1: Commit mới tinh
        res1 = commit_schedule_plan(plan, manager=self.mgr)
        self.assertFalse(res1.already_committed)
        self.assertEqual(len(res1.created_task_ids), 2)

        # Lần 2: Bấm lại hoặc mạng retry gửi lại cùng plan
        res2 = commit_schedule_plan(plan, manager=self.mgr)
        self.assertTrue(res2.already_committed)
        self.assertEqual(res2.plan_id, res1.plan_id)
        self.assertEqual(res2.created_task_ids, res1.created_task_ids)

        # Tổng số task trong DB vẫn đúng là 2, không bị nhân đôi thành 4
        all_tasks = self.mgr.get_all_tasks()
        self.assertEqual(len(all_tasks), 2)

    def test_update_task_optimistic_locking(self):
        """P2-06 (Optimistic Locking): Sửa task có expected_version chống ghi đè chéo."""
        created = self.mgr.add_tasks([{
            "id": "task_opt_01",
            "profile_name": "Prof_Opt",
            "title": "Tiêu đề gốc",
            "status": "PENDING",
        }])[0]
        self.assertEqual(created.get("version", 1), 1)

        # Sửa thành công với expected_version = 1
        success = self.mgr.update_task("task_opt_01", {"title": "Tiêu đề lần 1"}, expected_version=1)
        self.assertTrue(success)
        t_after = self.mgr.get_task_by_id("task_opt_01")
        self.assertEqual(t_after["title"], "Tiêu đề lần 1")
        self.assertEqual(t_after["version"], 2)

        # Sửa thất bại khi truyền version cũ (expected_version = 1 trong khi DB đã lên 2)
        fail = self.mgr.update_task("task_opt_01", {"title": "Ghi đè stale"}, expected_version=1)
        self.assertFalse(fail)
        t_stale = self.mgr.get_task_by_id("task_opt_01")
        self.assertEqual(t_stale["title"], "Tiêu đề lần 1")  # Vẫn giữ nguyên
        self.assertEqual(t_stale["version"], 2)

    def test_invalid_plan_validation_rejection(self):
        """P2-07 (Validation Guard F02/F03): Kế hoạch không hợp lệ bị chặn ngay trước khi đụng vào DB."""
        now = datetime.datetime.now(datetime.timezone.utc)
        # Case 1: OVERWRITE với 0 tasks
        empty_plan = SchedulePlan(
            plan_id="plan_empty_01",
            created_at=now.isoformat(),
            seed=1,
            profile_assignments={},
            unassigned_videos=(),
            resolution_mode="OVERWRITE",
            requested_profile_names=("Prof_Alpha",),
        )
        with self.assertRaises(InvalidPlanCommitError):
            commit_schedule_plan(empty_plan, manager=self.mgr, expected_snapshot_fingerprint="some_fingerprint")

        # Case 2: OVERWRITE thiếu snapshot fingerprint (hoặc chuỗi rỗng)
        valid_plan = self._create_sample_plan(plan_id="plan_no_fp", resolution_mode="OVERWRITE")
        with self.assertRaises(InvalidPlanCommitError):
            commit_schedule_plan(valid_plan, manager=self.mgr, expected_snapshot_fingerprint="")

        with self.assertRaises(InvalidPlanCommitError):
            commit_schedule_plan(valid_plan, manager=self.mgr, expected_snapshot_fingerprint=None)

        # Case 3: Plan có profile assignment key lệch với pt.profile_name
        mismatch_task = PlannedTask(
            slot_id="TRUA",
            profile_name="Prof_Real",
            tiktok_account="@user",
            video_path="C:/vids/1.mp4",
            canonical_video_path="C:/vids/1.mp4",
            video_filename="1.mp4",
            title="Video",
            profile_timezone="UTC",
            scheduled_epoch=1800000000,
            scheduled_time_local="2026-10-26T12:00:00",
            execution_mode="SERVER_SCHEDULE",
            delete_source_after_post=True,
        )
        bad_map_plan = SchedulePlan(
            plan_id="plan_bad_map",
            created_at=now.isoformat(),
            seed=1,
            profile_assignments={"Prof_Different_Key": (mismatch_task,)},
            unassigned_videos=(),
            resolution_mode="APPEND",
            requested_profile_names=("Prof_Different_Key",),
        )
        with self.assertRaises(InvalidPlanCommitError):
            commit_schedule_plan(bad_map_plan, manager=self.mgr)

    def test_idempotent_retry_with_altered_payload_hash(self):
        """P2-08 (Idempotency Hash Guard F05): Cùng plan_id nhưng nội dung khác phải bị từ chối rõ ràng."""
        plan1 = self._create_sample_plan(plan_id="plan_hash_check_01", task_count=2, resolution_mode="APPEND")
        res1 = commit_schedule_plan(plan1, manager=self.mgr)
        self.assertFalse(res1.already_committed)

        # Tạo plan khác cùng plan_id nhưng task_count=3 (hoặc thay đổi nội dung)
        plan_altered = self._create_sample_plan(plan_id="plan_hash_check_01", task_count=3, resolution_mode="APPEND")
        with self.assertRaises(ScheduleCommitError) as ctx:
            commit_schedule_plan(plan_altered, manager=self.mgr)
        self.assertIn("payload_hash khác", str(ctx.exception))

    def test_idempotent_retry_with_corrupt_receipt(self):
        """P2-09 (Receipt Integrity F05): Bản ghi plan có receipt bị lỗi JSON, sai kiểu, rỗng, hoặc lệch plan_id phải báo ScheduleCommitError."""
        plan = self._create_sample_plan(plan_id="plan_corrupt_receipt_01", task_count=1, resolution_mode="APPEND")
        res = commit_schedule_plan(plan, manager=self.mgr)
        self.assertFalse(res.already_committed)

        corrupt_cases = [
            ("{INVALID_JSON...", "bị hỏng cú pháp"),
            ("[]", "không phải là JSON Object"),
            ("null", "không phải là JSON Object"),
            ("{}", "không khớp với plan_id"),
            ('{"plan_id": "wrong_id", "created_task_ids": ["task_1"], "cancelled_task_ids": []}', "không khớp với plan_id"),
            ('{"plan_id": "plan_corrupt_receipt_01", "cancelled_task_ids": []}', "thiếu trường bắt buộc 'created_task_ids'"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": ["task_1"]}', "thiếu trường bắt buộc 'cancelled_task_ids'"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": "not_a_list", "cancelled_task_ids": []}', "có created_task_ids không phải dạng danh sách"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": ["task_1"], "cancelled_task_ids": "bad"}', "có cancelled_task_ids không phải dạng danh sách"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": [""], "cancelled_task_ids": []}', "chứa phần tử ID không hợp lệ"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": [], "cancelled_task_ids": []}', "Biên lai không nhất quán: plan có 1 tasks nhưng biên lai ghi 0 created_task_ids"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": ["task_phantom_999"], "cancelled_task_ids": [], "resolution_mode": "APPEND", "committed_at": "' + res.committed_at + '"}', "Biên lai không nhất quán với DB"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": ["' + res.created_task_ids[0] + '"], "cancelled_task_ids": [], "resolution_mode": "WRONG_MODE", "committed_at": "' + res.committed_at + '"}', "không khớp với resolution_mode của plan"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": ["' + res.created_task_ids[0] + '"], "cancelled_task_ids": [], "resolution_mode": "APPEND", "committed_at": ""}', "thiếu hoặc sai kiểu trường 'committed_at'"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": ["' + res.created_task_ids[0] + '"], "cancelled_task_ids": [], "resolution_mode": "APPEND", "committed_at": "2020-01-01T00:00:00"}', "không khớp với cột committed_at"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": ["' + res.created_task_ids[0] + '"], "cancelled_task_ids": ["some_task_id"], "resolution_mode": "APPEND", "committed_at": "' + res.committed_at + '"}', "resolution_mode='APPEND' nhưng biên lai có chứa 1 cancelled_task_ids"),
            ('{"plan_id": "plan_corrupt_receipt_01", "created_task_ids": ["' + res.created_task_ids[0] + '", "' + res.created_task_ids[0] + '"], "cancelled_task_ids": [], "resolution_mode": "APPEND", "committed_at": "' + res.committed_at + '"}', "created_task_ids chứa ID trùng lặp"),
        ]

        for bad_receipt, expected_err_substr in corrupt_cases:
            conn = self.mgr._get_connection()
            try:
                with conn:
                    conn.execute(
                        "UPDATE schedule_plans SET receipt_json = ? WHERE plan_id = ?",
                        (bad_receipt, "plan_corrupt_receipt_01")
                    )
            finally:
                conn.close()

            # Retry commit cùng plan phải ném ScheduleCommitError
            with self.assertRaises(ScheduleCommitError) as ctx:
                commit_schedule_plan(plan, manager=self.mgr)
            self.assertIn(expected_err_substr, str(ctx.exception))

    def test_idempotent_retry_overwrite_corrupt_receipt(self):
        """P2-09b (Receipt Integrity OVERWRITE Mode): Kiểm tra các điều kiện toàn vẹn biên lai đặc thù cho chế độ OVERWRITE."""
        # 1. Tạo task ban đầu để OVERWRITE hủy nó
        self.mgr.add_tasks([{
            "id": "task_target_to_cancel_01",
            "profile_name": "Prof_OW_Rec",
            "video_path": "C:/vids/ow1.mp4",
            "status": "PENDING",
        }])
        snap = self.mgr.get_conflict_snapshot(["Prof_OW_Rec"])
        plan = self._create_sample_plan(
            plan_id="plan_ow_receipt_01",
            profile_name="Prof_OW_Rec",
            task_count=1,
            resolution_mode="OVERWRITE",
            conflicts=snap,
        )
        res = self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertEqual(len(res.cancelled_task_ids), 1)
        self.assertEqual(res.cancelled_task_ids[0], "task_target_to_cancel_01")

        # 2. Case: created_task_ids và cancelled_task_ids trùng lặp (không disjoint)
        bad_receipt_overlap = json.dumps({
            "plan_id": "plan_ow_receipt_01",
            "created_task_ids": [res.created_task_ids[0]],
            "cancelled_task_ids": [res.created_task_ids[0]],
            "resolution_mode": "OVERWRITE",
            "committed_at": res.committed_at,
        })
        conn = self.mgr._get_connection()
        try:
            with conn:
                conn.execute("UPDATE schedule_plans SET receipt_json = ? WHERE plan_id = ?", (bad_receipt_overlap, "plan_ow_receipt_01"))
        finally:
            conn.close()
        with self.assertRaises(ScheduleCommitError) as ctx_ov:
            self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertIn("bị trùng lặp ID", str(ctx_ov.exception))

        # 3. Case: cancelled_task_ids chứa phantom ID không có trong DB
        bad_receipt_phantom = json.dumps({
            "plan_id": "plan_ow_receipt_01",
            "created_task_ids": [res.created_task_ids[0]],
            "cancelled_task_ids": ["ghost_cancelled_id_99"],
            "resolution_mode": "OVERWRITE",
            "committed_at": res.committed_at,
        })
        conn = self.mgr._get_connection()
        try:
            with conn:
                conn.execute("UPDATE schedule_plans SET receipt_json = ? WHERE plan_id = ?", (bad_receipt_phantom, "plan_ow_receipt_01"))
        finally:
            conn.close()
        with self.assertRaises(ScheduleCommitError) as ctx_ph:
            self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertIn("không khớp với các task bị hủy bởi plan", str(ctx_ph.exception))

        # 4. Case: cancelled_task_ids bị xóa rỗng trong receipt sau khi OVERWRITE thực tế đã hủy task
        bad_receipt_empty = json.dumps({
            "plan_id": "plan_ow_receipt_01",
            "created_task_ids": [res.created_task_ids[0]],
            "cancelled_task_ids": [],
            "resolution_mode": "OVERWRITE",
            "committed_at": res.committed_at,
        })
        conn = self.mgr._get_connection()
        try:
            with conn:
                conn.execute("UPDATE schedule_plans SET receipt_json = ? WHERE plan_id = ?", (bad_receipt_empty, "plan_ow_receipt_01"))
        finally:
            conn.close()
        with self.assertRaises(ScheduleCommitError) as ctx_empty:
            self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertIn("không khớp với các task bị hủy bởi plan", str(ctx_empty.exception))

        # 5. Case: cancelled_task_ids chứa task thuộc profile khác (dù có trạng thái CANCELLED)
        self.mgr.add_tasks([{
            "id": "task_foreign_prof_99",
            "profile_name": "Prof_Foreign",
            "video_path": "C:/vids/foreign.mp4",
            "status": "CANCELLED",
        }])
        bad_receipt_foreign = json.dumps({
            "plan_id": "plan_ow_receipt_01",
            "created_task_ids": [res.created_task_ids[0]],
            "cancelled_task_ids": [res.cancelled_task_ids[0], "task_foreign_prof_99"],
            "resolution_mode": "OVERWRITE",
            "committed_at": res.committed_at,
        })
        conn = self.mgr._get_connection()
        try:
            with conn:
                conn.execute("UPDATE schedule_plans SET receipt_json = ? WHERE plan_id = ?", (bad_receipt_foreign, "plan_ow_receipt_01"))
        finally:
            conn.close()
        with self.assertRaises(ScheduleCommitError) as ctx_foreign:
            self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertIn("không khớp với các task bị hủy bởi plan", str(ctx_foreign.exception))

        # 6. Case: cancelled_task_ids có task tồn tại nhưng status != 'CANCELLED' (bị ai đó sửa sang PENDING)
        conn = self.mgr._get_connection()
        try:
            with conn:
                conn.execute("UPDATE scheduled_tasks SET status = 'PENDING' WHERE id = 'task_target_to_cancel_01'")
                conn.execute("UPDATE schedule_plans SET receipt_json = ? WHERE plan_id = ?", (json.dumps(res.to_dict()), "plan_ow_receipt_01"))
        finally:
            conn.close()
        with self.assertRaises(ScheduleCommitError) as ctx_not_canc:
            self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertIn("không ở trạng thái CANCELLED", str(ctx_not_canc.exception))

        # 7a. Case: cancelled_task_ids chứa duplicate ID (phát hiện bởi Codex Round 8)
        bad_receipt_dup_canc = json.dumps({
            "plan_id": "plan_ow_receipt_01",
            "created_task_ids": [res.created_task_ids[0]],
            "cancelled_task_ids": [res.cancelled_task_ids[0], res.cancelled_task_ids[0]],
            "resolution_mode": "OVERWRITE",
            "committed_at": res.committed_at,
        })
        conn = self.mgr._get_connection()
        try:
            with conn:
                # Trả lại status CANCELLED cho task để test kiểm tra tính duplicate của receipt
                conn.execute("UPDATE scheduled_tasks SET status = 'CANCELLED' WHERE id = 'task_target_to_cancel_01'")
                conn.execute("UPDATE schedule_plans SET receipt_json = ? WHERE plan_id = ?", (bad_receipt_dup_canc, "plan_ow_receipt_01"))
        finally:
            conn.close()
        with self.assertRaises(ScheduleCommitError) as ctx_dup_canc:
            self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertIn("chứa ID trùng lặp", str(ctx_dup_canc.exception))

        # 7b. Case: created_task_ids chứa duplicate ID
        bad_receipt_dup_created = json.dumps({
            "plan_id": "plan_ow_receipt_01",
            "created_task_ids": [res.created_task_ids[0], res.created_task_ids[0]],
            "cancelled_task_ids": [res.cancelled_task_ids[0]],
            "resolution_mode": "OVERWRITE",
            "committed_at": res.committed_at,
        })
        conn = self.mgr._get_connection()
        try:
            with conn:
                conn.execute("UPDATE schedule_plans SET receipt_json = ? WHERE plan_id = ?", (bad_receipt_dup_created, "plan_ow_receipt_01"))
        finally:
            conn.close()
        with self.assertRaises(ScheduleCommitError) as ctx_dup_created:
            self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertIn("chứa ID trùng lặp", str(ctx_dup_created.exception))

    def test_all_writers_increment_version(self):
        """P2-10 (Version Consistency F04): Mọi tác vụ ghi/thay đổi trạng thái đều phải tự động tăng version."""
        task = self.mgr.add_tasks([{
            "id": "task_version_all_writers",
            "profile_name": "Prof_Version",
            "title": "Gốc",
            "status": "PENDING",
            "video_path": "C:/v.mp4",
            "scheduled_time": "2026-10-26T12:00:00",
            "scheduled_epoch": 1800000000,
        }])[0]
        self.assertEqual(task.get("version", 1), 1)

        # 1. update_task không truyền expected_version
        self.mgr.update_task("task_version_all_writers", {"title": "Update không expected_version"})
        t1 = self.mgr.get_task_by_id("task_version_all_writers")
        self.assertEqual(t1["version"], 2)

        # 2. update_task truyền caller "version": 1 cố ý tự gán -> version trong dict bị strip và tăng lên 3
        self.mgr.update_task("task_version_all_writers", {"title": "Update với version=1", "version": 1})
        t2 = self.mgr.get_task_by_id("task_version_all_writers")
        self.assertEqual(t2["version"], 3)

        # 3. claim_task_for_execution -> tăng lên 4
        success_claim = self.mgr.claim_task_for_execution("task_version_all_writers", "worker_version_test")
        self.assertTrue(success_claim)
        t3 = self.mgr.get_task_by_id("task_version_all_writers")
        self.assertEqual(t3["version"], 4)
        self.assertEqual(t3["status"], "UPLOADING")

        # 4. Giả lập task bị fail, sau đó retry_tasks -> tăng lên 5
        self.mgr.update_task("task_version_all_writers", {"status": "FAILED", "error_message": "Network timeout"})
        t4 = self.mgr.get_task_by_id("task_version_all_writers")
        self.assertEqual(t4["version"], 5)

        self.mgr.retry_tasks(["task_version_all_writers"])
        t5 = self.mgr.get_task_by_id("task_version_all_writers")
        self.assertEqual(t5["version"], 6)
        self.assertEqual(t5["status"], "PENDING")

        # 5. cancel_tasks -> tăng lên 7
        self.mgr.cancel_tasks(["task_version_all_writers"])
        t6 = self.mgr.get_task_by_id("task_version_all_writers")
        self.assertEqual(t6["version"], 7)
        self.assertEqual(t6["status"], "CANCELLED")

    def test_rollback_partial_insert_failure(self):
        """P2-11 (Rollback on Partial Insert F01): Lỗi xảy ra giữa chừng khi đang chèn task mới phải rollback 100%."""
        snap = self.mgr.get_conflict_snapshot(["Prof_Partial"])
        plan = self._create_sample_plan(
            plan_id="plan_partial_fail_01",
            profile_name="Prof_Partial",
            task_count=3,
            resolution_mode="APPEND",
            conflicts=snap,
        )

        # Tạo trigger làm fail ở bản ghi thứ 2 (khi count = 1)
        conn = self.mgr._get_connection()
        try:
            conn.execute("""
                CREATE TRIGGER fail_on_second_insert
                BEFORE INSERT ON scheduled_tasks
                WHEN (SELECT COUNT(*) FROM scheduled_tasks WHERE plan_id = 'plan_partial_fail_01') = 1
                BEGIN
                    SELECT RAISE(ABORT, 'Crash on second task insertion!');
                END;
            """)
        finally:
            conn.close()

        try:
            with self.assertRaises(sqlite3.IntegrityError):
                commit_schedule_plan(plan, manager=self.mgr)
        finally:
            conn = self.mgr._get_connection()
            try:
                conn.execute("DROP TRIGGER IF EXISTS fail_on_second_insert;")
            finally:
                conn.close()

        # Xác nhận KHÔNG CÓ task nào của plan_partial_fail_01 tồn tại (bản ghi thứ 1 đã bị rollback)
        conn = self.mgr._get_connection()
        try:
            cnt = conn.execute("SELECT COUNT(*) as c FROM scheduled_tasks WHERE plan_id = ?", ("plan_partial_fail_01",)).fetchone()["c"]
            self.assertEqual(cnt, 0, "Bản ghi thứ nhất đáng lẽ phải được rollback hoàn toàn!")
        finally:
            conn.close()

    def test_profile_scope_normalization_cancels_old_tasks_on_overwrite(self):
        """P2-12 (Profile Normalization F02/F03): requested_profile_names chứa khoảng trắng được constructor chuẩn hóa và hủy đúng toàn bộ task cũ."""
        # 1. Thêm một task cũ với profile chuẩn
        self.mgr.add_tasks([{
            "id": "old_task_space_01",
            "profile_name": "Prof_Spaced",
            "video_path": "C:/vids/old_space.mp4",
            "status": "PENDING",
            "scheduled_time": "2026-10-26T12:00:00",
            "scheduled_epoch": 1800000000,
        }])

        # Kiểm tra update_task tự động chuẩn hóa profile_name có khoảng trắng
        self.mgr.update_task("old_task_space_01", {"profile_name": "   Prof_Spaced   "})
        t_chk = self.mgr.get_task_by_id("old_task_space_01")
        self.assertEqual(t_chk["profile_name"], "Prof_Spaced", "update_task phải tự động strip profile_name!")

        snap = self.mgr.get_conflict_snapshot(["Prof_Spaced"])

        # 2. Tạo plan OVERWRITE truyền trực tiếp requested_profile_names có khoảng trắng vào constructor
        now = datetime.datetime.now(datetime.timezone.utc)
        pt = PlannedTask(
            slot_id="TRUA",
            profile_name="Prof_Spaced",
            tiktok_account="@user",
            video_path="C:/vids/new.mp4",
            canonical_video_path="C:/vids/new.mp4",
            video_filename="new.mp4",
            title="Video Space Norm",
            profile_timezone="America/New_York",
            scheduled_epoch=int((now + datetime.timedelta(days=1)).timestamp()),
            scheduled_time_local="2026-10-27T12:00:00",
            execution_mode="SERVER_SCHEDULE",
            delete_source_after_post=True,
        )
        plan = SchedulePlan(
            plan_id="plan_space_norm_01",
            created_at=now.isoformat(),
            seed=1,
            profile_assignments={"Prof_Spaced": (pt,)},
            unassigned_videos=(),
            resolution_mode="OVERWRITE",
            conflicts_identified=snap,
            requested_profile_names=("   Prof_Spaced   ",),
        )
        # Xác nhận constructor __post_init__ đã tự động strip
        self.assertEqual(plan.requested_profile_names, ("Prof_Spaced",))

        # 3. Commit kế hoạch
        res = commit_schedule_plan(plan, manager=self.mgr, expected_snapshot_fingerprint=snap.snapshot_fingerprint)
        self.assertEqual(len(res.cancelled_task_ids), 1)
        self.assertIn("old_task_space_01", res.cancelled_task_ids)

        # 4. Kiểm tra task cũ đã chuyển sang CANCELLED
        old_task = self.mgr.get_task_by_id("old_task_space_01")
        self.assertEqual(old_task["status"], "CANCELLED")

    def test_plan_validation_rejects_empty_plans_and_invalid_modes(self):
        """P2-13 (Strict Plan Contracts F02): Từ chối plan có 0 tasks, resolution_mode không hợp lệ hoặc execution_mode sai."""
        # 1. Plan có 0 tasks
        empty_plan = SchedulePlan(
            plan_id="plan_empty_01",
            created_at=datetime.datetime.now().isoformat(),
            seed=1,
            profile_assignments={"Prof_A": ()},
            resolution_mode="APPEND",
            requested_profile_names=("Prof_A",),
        )
        with self.assertRaises(InvalidPlanCommitError):
            commit_schedule_plan(empty_plan, manager=self.mgr)

        # 2. Plan có resolution_mode không hợp lệ
        bad_mode_plan = self._create_sample_plan(plan_id="plan_bad_mode_01", task_count=1)
        object.__setattr__(bad_mode_plan, "resolution_mode", "INVALID_MODE")
        with self.assertRaises(InvalidPlanCommitError):
            commit_schedule_plan(bad_mode_plan, manager=self.mgr)

        # 3. PlannedTask constructor từ chối execution_mode không hợp lệ
        with self.assertRaises(ValueError) as ctx_exec:
            PlannedTask(
                slot_id="TRUA",
                profile_name="Prof_Exec",
                tiktok_account="@user",
                video_path="C:/vids/v.mp4",
                canonical_video_path="C:/vids/v.mp4",
                video_filename="v.mp4",
                title="Video Bad Exec",
                profile_timezone="America/New_York",
                scheduled_epoch=1800000000,
                scheduled_time_local="2026-10-27T12:00:00",
                execution_mode="BAD_EXECUTION_MODE",
                delete_source_after_post=True,
            )
        self.assertIn("execution_mode không hợp lệ", str(ctx_exec.exception))

        # 4. PlannedTask constructor từ chối profile_name rỗng
        with self.assertRaises(ValueError) as ctx_prof:
            PlannedTask(
                slot_id="TRUA",
                profile_name="",
                tiktok_account="@user",
                video_path="C:/vids/v.mp4",
                canonical_video_path="C:/vids/v.mp4",
                video_filename="v.mp4",
                title="Video Empty Prof",
                profile_timezone="America/New_York",
                scheduled_epoch=1800000000,
                scheduled_time_local="2026-10-27T12:00:00",
                execution_mode="SERVER_SCHEDULE",
                delete_source_after_post=True,
            )
        self.assertIn("profile_name không được để trống", str(ctx_prof.exception))

        # 5. PlannedTask constructor từ chối profile_name chứa khoảng trắng thừa
        with self.assertRaises(ValueError) as ctx_space:
            PlannedTask(
                slot_id="TRUA",
                profile_name="   Prof_Exec   ",
                tiktok_account="@user",
                video_path="C:/vids/v.mp4",
                canonical_video_path="C:/vids/v.mp4",
                video_filename="v.mp4",
                title="Video Space Prof",
                profile_timezone="America/New_York",
                scheduled_epoch=1800000000,
                scheduled_time_local="2026-10-27T12:00:00",
                execution_mode="SERVER_SCHEDULE",
                delete_source_after_post=True,
            )
        self.assertIn("chứa khoảng trắng thừa", str(ctx_space.exception))

    def test_add_tasks_rejects_duplicate_id_and_protects_version_consistency(self):
        """P2-14 (No Silent Overwrite / Version & Metadata Preservation): add_tasks từ chối ID trùng để không reset version hay làm mất metadata."""
        created = self.mgr.add_tasks([{
            "id": "task_dup_check_01",
            "profile_name": "Prof_Dup",
            "title": "Bản ghi gốc",
            "status": "PENDING",
            "video_path": "C:/v.mp4",
        }])[0]
        self.assertEqual(created["version"], 1)

        # Cập nhật version lên 2 và gán metadata P2 qua update_task
        self.mgr.update_task("task_dup_check_01", {
            "title": "Đã cập nhật lần 1",
            "plan_id": "plan_alpha_99",
            "slot_id": "TOI",
            "canonical_video_path": "C:/canon/v.mp4",
            "profile_timezone": "America/New_York",
        })
        task_v2 = self.mgr.get_task_by_id("task_dup_check_01")
        self.assertEqual(task_v2["version"], 2)
        self.assertEqual(task_v2["plan_id"], "plan_alpha_99")
        self.assertEqual(task_v2["slot_id"], "TOI")
        self.assertEqual(task_v2["canonical_video_path"], "C:/canon/v.mp4")
        self.assertEqual(task_v2["profile_timezone"], "America/New_York")

        # Gọi add_tasks với cùng ID task_dup_check_01 -> Phải ném ValueError, từ chối ghi đè
        with self.assertRaises(ValueError) as ctx:
            self.mgr.add_tasks([{
                "id": "task_dup_check_01",
                "profile_name": "Prof_Dup",
                "title": "Cố ý ghi đè",
            }])
        self.assertIn("đã tồn tại", str(ctx.exception))

        # Xác nhận version 2 và TOÀN BỘ metadata P2 của task vẫn nguyên vẹn
        task_after = self.mgr.get_task_by_id("task_dup_check_01")
        self.assertEqual(task_after["version"], 2)
        self.assertEqual(task_after["title"], "Đã cập nhật lần 1")
        self.assertEqual(task_after["plan_id"], "plan_alpha_99")
        self.assertEqual(task_after["slot_id"], "TOI")
        self.assertEqual(task_after["canonical_video_path"], "C:/canon/v.mp4")
        self.assertEqual(task_after["profile_timezone"], "America/New_York")

    def test_claim_due_tasks_respects_concurrent_mode_or_epoch_change(self):
        """P2-15 (Atomic Claim Interleaving Guard): claim_due_tasks từ chối nhận việc khi mode hoặc epoch bị sửa đổi ngay sau SELECT trước UPDATE."""
        now = datetime.datetime.now()
        now_epoch = int(now.timestamp())
        self.mgr.add_tasks([{
            "id": "task_claim_due_guard_1",
            "profile_name": "Prof_Claim",
            "video_path": "C:/v1.mp4",
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
            "scheduled_time": now.isoformat(),
            "scheduled_epoch": now_epoch - 10,
        }, {
            "id": "task_claim_due_guard_2",
            "profile_name": "Prof_Claim",
            "video_path": "C:/v2.mp4",
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
            "scheduled_time": now.isoformat(),
            "scheduled_epoch": now_epoch - 10,
        }])

        # Tái hiện Race Condition bằng Hook xen kẽ giữa SELECT và UPDATE:
        # Task 1: Bị tiến trình khác đổi execution_mode sang SERVER_SCHEDULE ngay sau SELECT
        # Task 2: Bị tiến trình khác dời scheduled_epoch sang tương lai ngay sau SELECT
        def interleave_hook(conn, task_ids):
            conn.execute("UPDATE scheduled_tasks SET execution_mode = 'SERVER_SCHEDULE' WHERE id = 'task_claim_due_guard_1'")
            conn.execute(f"UPDATE scheduled_tasks SET scheduled_epoch = {now_epoch + 3600} WHERE id = 'task_claim_due_guard_2'")

        self.mgr._test_claim_interleave_hook = interleave_hook

        # Worker thực hiện claim_due_tasks -> Nhờ điều kiện UPDATE nguyên tử, cả 2 task đều bị từ chối
        claimed = self.mgr.claim_due_tasks(worker_id="worker_test_guard", limit=10)
        self.assertEqual(len(claimed), 0, "Không được claim bất kỳ task nào khi mode hoặc epoch bị thay đổi sau SELECT!")

        # Dọn hook
        delattr(self.mgr, "_test_claim_interleave_hook")

        # Kiểm tra trạng thái trong DB: cả 2 task vẫn PENDING, không bị chuyển sang UPLOADING
        t1 = self.mgr.get_task_by_id("task_claim_due_guard_1")
        t2 = self.mgr.get_task_by_id("task_claim_due_guard_2")
        self.assertEqual(t1["status"], "PENDING")
        self.assertEqual(t1["claim_worker_id"], "")
        self.assertEqual(t2["status"], "PENDING")
        self.assertEqual(t2["claim_worker_id"], "")

    def test_stale_guard_detects_aba_delete_and_recreate(self):
        """P2-16 (ABA Stale Guard Detection): Stale Guard phát hiện task bị xóa và tạo lại qua add_tasks() dù giữ nguyên 100% trường cũ nhờ storage incarnation_id."""
        now = datetime.datetime.now()
        now_epoch = int(now.timestamp())

        # 1. Tạo task ban đầu
        self.mgr.add_tasks([{
            "id": "task_aba_target",
            "profile_name": "Prof_ABA",
            "video_path": "C:/vids/v1.mp4",
            "scheduled_time": now.isoformat(),
            "scheduled_epoch": now_epoch + 3600,
            "status": "PENDING",
            "execution_mode": "SERVER_SCHEDULE",
        }])

        old_task = self.mgr.get_task_by_id("task_aba_target")
        self.assertTrue(bool(old_task.get("incarnation_id")), "Task phải có incarnation_id do storage sinh!")

        # 2. Lấy snapshot và tạo plan OVERWRITE
        snap = self.mgr.get_conflict_snapshot(["Prof_ABA"])
        old_fingerprint = snap.snapshot_fingerprint

        plan = self._create_sample_plan(
            plan_id="plan_aba_overwrite_01",
            profile_name="Prof_ABA",
            task_count=1,
            resolution_mode="OVERWRITE",
            conflicts=snap,
        )

        # 3. Giả lập hiệu ứng ABA chuẩn xác theo kịch bản Codex:
        # Xóa task cũ rồi gọi add_tasks() với 100% dữ liệu cũ (kể cả created_at, epoch, mode, path...)
        self.mgr.delete_tasks(["task_aba_target"])
        recreated = self.mgr.add_tasks([{**old_task, "title": "Nội dung thay thế"}])[0]

        # Kiểm chứng nghiệp vụ: add_tasks() sinh incarnation_id mới, khác hoàn toàn old_task
        self.assertNotEqual(recreated["incarnation_id"], old_task["incarnation_id"])

        # 4. Khi commit với old_fingerprint, StalePlanError BẮT BUỘC phải được ném ra!
        with self.assertRaises(StalePlanError) as ctx:
            self.mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=old_fingerprint)
        self.assertIn("Dữ liệu hàng đợi đã thay đổi", str(ctx.exception))

    def test_update_task_optimistic_locking_rejects_stale_incarnation_aba(self):
        """P2-17 (Optimistic Locking Writer ABA Protection): update_task từ chối cập nhật khi task bị xóa và tạo lại cùng ID/version nhưng incarnation_id đã đổi."""
        # 1. Tạo task ban đầu
        task = self.mgr.add_tasks([{
            "id": "task_writer_aba",
            "profile_name": "Prof_Writer_ABA",
            "title": "Bản ghi gốc",
            "status": "PENDING",
            "video_path": "C:/v.mp4",
        }])[0]
        self.assertEqual(task["version"], 1)
        original_inc = task["incarnation_id"]
        self.assertTrue(bool(original_inc), "Task ban đầu phải có incarnation_id!")

        # 2. Xóa task và tạo lại cùng ID với version 1 (mô phỏng chuỗi gọi ABA)
        self.mgr.delete_tasks(["task_writer_aba"])
        recreated = self.mgr.add_tasks([{
            "id": "task_writer_aba",
            "profile_name": "Prof_Writer_ABA",
            "title": "Bản thay thế mới",
            "status": "PENDING",
            "video_path": "C:/v.mp4",
        }])[0]
        self.assertEqual(recreated["version"], 1)
        self.assertNotEqual(recreated["incarnation_id"], original_inc)

        # 3. Một stale writer (ví dụ editor cũ) cố cập nhật với expected_version=1 và expected_incarnation_id=original_inc
        success = self.mgr.update_task(
            "task_writer_aba",
            {"title": "Ghi từ editor cũ bị trễ"},
            expected_version=1,
            expected_incarnation_id=original_inc,
        )

        # Rào chắn nghiệp vụ: Thao tác BẮT BUỘC phải thất bại (False), không được ghi đè lên bản thay thế
        self.assertFalse(success, "update_task phải từ chối khi incarnation_id không khớp dù version bằng nhau!")

        # 4. Xác nhận dữ liệu của bản thay thế mới vẫn nguyên vẹn 100%
        current = self.mgr.get_task_by_id("task_writer_aba")
        self.assertEqual(current["title"], "Bản thay thế mới")
        self.assertEqual(current["incarnation_id"], recreated["incarnation_id"])
        self.assertEqual(current["version"], 1)


if __name__ == "__main__":
    unittest.main()
