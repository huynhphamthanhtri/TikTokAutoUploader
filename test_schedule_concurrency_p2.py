# -*- coding: utf-8 -*-
"""
test_schedule_concurrency_p2.py - Bộ kiểm thử Concurrency giữa 2 tiến trình độc lập cho Phase 2.

Tuân thủ nghiêm ngặt:
- AGENTS.md Quy tắc 14: Zero Live Data Mutation (100% test sử dụng tempfile/tmp_path cô lập).
- AGENTS.md Quy tắc 15: Kiểm chứng Concurrency giữa hai TIẾN TRÌNH THỰC SỰ (Multi-process)
  để chứng minh BEGIN IMMEDIATE transaction hoạt động ở tầng SQLite engine, không bị phụ thuộc vào _FILE_LOCK nội bộ Python.
- Dùng mp.Barrier để đồng bộ thời điểm tranh chấp vi giây chính xác giữa hai tiến trình.
- Kiểm tra trực tiếp đường production mgr.commit_plan_atomic() khi database bị khóa.
- Đảm bảo 100% dọn dẹp tiến trình, không để lại zombie process.
"""

import datetime
import json
import multiprocessing as mp
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from schedule_contracts import (
    ConflictSnapshot,
    PlannedTask,
    SchedulePlan,
    StalePlanError,
)
from schedule_manager import ScheduleManager
from schedule_service import commit_schedule_plan


# Helper top-level functions for multiprocessing workers
def _worker_hold_write_lock(
    db_path: str,
    p2_ready_event: mp.Event,
    lock_acquired_event: mp.Event,
    p2_done_event: mp.Event,
    exit_queue: mp.Queue,
):
    """Tiến trình 1: Đợi Tiến trình 2 sẵn sàng, mở BEGIN IMMEDIATE, đợi Tiến trình 2 thử commit xong rồi mới nhả khóa."""
    conn = None
    try:
        if not p2_ready_event.wait(timeout=5.0):
            exit_queue.put("TIMEOUT_WAITING_FOR_P2")
            return

        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE;")
        lock_acquired_event.set()

        if not p2_done_event.wait(timeout=5.0):
            exit_queue.put("TIMEOUT_WAITING_FOR_P2_DONE")
            return

        conn.execute("COMMIT;")
        exit_queue.put("SUCCESS")
    except Exception as e:
        exit_queue.put(f"ERROR: {e}")
    finally:
        if conn:
            conn.close()


def _worker_attempt_commit(
    db_path: str,
    plan_id: str,
    profile_name: str,
    p2_ready_event: mp.Event,
    lock_acquired_event: mp.Event,
    p2_done_event: mp.Event,
    result_queue: mp.Queue,
):
    """Tiến trình 2: Khởi tạo trước, đợi Tiến trình 1 giữ khóa, sau đó gọi commit_plan_atomic và xác nhận dính lỗi khóa SQLite."""
    try:
        mgr = ScheduleManager(storage_file=Path(db_path))
        orig_get_conn = mgr._get_connection
        def _get_fast_fail_conn():
            c = orig_get_conn()
            c.execute("PRAGMA busy_timeout = 150;")
            return c
        mgr._get_connection = _get_fast_fail_conn

        now = datetime.datetime.now(datetime.timezone.utc)
        pt = PlannedTask(
            slot_id="TRUA",
            profile_name=profile_name,
            tiktok_account="@user",
            video_path="C:/mock/v.mp4",
            canonical_video_path="C:/mock/v.mp4",
            video_filename="v.mp4",
            title="Video Concurrency",
            profile_timezone="America/New_York",
            scheduled_epoch=int((now + datetime.timedelta(days=1)).timestamp()),
            scheduled_time_local="2026-10-26T12:00:00",
            execution_mode="SERVER_SCHEDULE",
            delete_source_after_post=True,
        )
        plan = SchedulePlan(
            plan_id=plan_id,
            created_at=now.isoformat(),
            seed=1,
            profile_assignments={profile_name: (pt,)},
            unassigned_videos=(),
            resolution_mode="APPEND",
            requested_profile_names=(profile_name,),
        )

        p2_ready_event.set()

        if not lock_acquired_event.wait(timeout=5.0):
            result_queue.put(("ERROR", "Timeout waiting for lock_acquired_event"))
            return

        try:
            res = mgr.commit_plan_atomic(plan)
            result_queue.put(("SUCCESS", res.plan_id))
        except sqlite3.OperationalError as op_err:
            err_msg = str(op_err).lower()
            if "locked" in err_msg or "busy" in err_msg:
                result_queue.put(("LOCKED", str(op_err)))
            else:
                result_queue.put(("ERROR", f"sqlite3.OperationalError unexpected: {op_err}"))
    except Exception as e:
        result_queue.put(("ERROR", f"{type(e).__name__}: {e}"))
    finally:
        p2_done_event.set()


def _worker_mirror_commit(
    json_path: str,
    plan_id: str,
    profile_name: str,
    result_queue: mp.Queue,
):
    """Tiến trình thực hiện commit trên ScheduleManager có kích hoạt JSON mirror."""
    try:
        mgr = ScheduleManager(storage_file=Path(json_path))
        now = datetime.datetime.now(datetime.timezone.utc)
        pt = PlannedTask(
            slot_id="TRUA",
            profile_name=profile_name,
            tiktok_account="@user",
            video_path=f"C:/mock/{plan_id}.mp4",
            canonical_video_path=f"C:/mock/{plan_id}.mp4",
            video_filename=f"{plan_id}.mp4",
            title=f"Mirror {plan_id}",
            profile_timezone="America/New_York",
            scheduled_epoch=int((now + datetime.timedelta(days=1)).timestamp()),
            scheduled_time_local="2026-10-26T12:00:00",
            execution_mode="SERVER_SCHEDULE",
            delete_source_after_post=True,
        )
        plan = SchedulePlan(
            plan_id=plan_id,
            created_at=now.isoformat(),
            seed=10,
            profile_assignments={profile_name: (pt,)},
            unassigned_videos=(),
            resolution_mode="APPEND",
            requested_profile_names=(profile_name,),
        )
        res = mgr.commit_plan_atomic(plan)
        result_queue.put(("SUCCESS", res.plan_id))
    except Exception as e:
        result_queue.put(("ERROR", f"{type(e).__name__}: {e}"))


def _worker_init_storage(db_path: str, barrier: mp.Barrier, result_queue: mp.Queue):
    """Tiến trình thực hiện khởi tạo ScheduleManager đồng thời từ con số 0 trên DB chưa tồn tại."""
    try:
        barrier.wait(timeout=5.0)
        mgr = ScheduleManager(storage_file=Path(db_path))
        result_queue.put(("SUCCESS", len(mgr.get_all_tasks())))
    except Exception as e:
        result_queue.put(("ERROR", f"{type(e).__name__}: {e}"))


def _worker_competing_commit_barrier(
    db_path: str,
    plan_id: str,
    profile_name: str,
    fingerprint: str,
    mode: str,
    barrier: mp.Barrier,
    result_queue: mp.Queue,
):
    """Tiến trình tham gia tranh chấp ghi đồng thời tại cùng thời điểm vi giây (nhờ Barrier)."""
    try:
        mgr = ScheduleManager(storage_file=Path(db_path))
        now = datetime.datetime.now(datetime.timezone.utc)
        pt = PlannedTask(
            slot_id="TRUA",
            profile_name=profile_name,
            tiktok_account="@user",
            video_path=f"C:/mock/{plan_id}.mp4",
            canonical_video_path=f"C:/mock/{plan_id}.mp4",
            video_filename=f"{plan_id}.mp4",
            title=f"Competing {plan_id}",
            profile_timezone="America/New_York",
            scheduled_epoch=int((now + datetime.timedelta(days=2)).timestamp()),
            scheduled_time_local="2026-10-27T12:00:00",
            execution_mode="SERVER_SCHEDULE",
            delete_source_after_post=True,
        )
        plan = SchedulePlan(
            plan_id=plan_id,
            created_at=now.isoformat(),
            seed=2,
            profile_assignments={profile_name: (pt,)},
            unassigned_videos=(),
            resolution_mode=mode,
            requested_profile_names=(profile_name,),
        )

        # Chờ cả 2 tiến trình sẵn sàng để xuất phát cùng thời điểm vi giây
        barrier.wait(timeout=5.0)

        # Production path: commit_plan_atomic
        res = mgr.commit_plan_atomic(plan, expected_snapshot_fingerprint=fingerprint)
        result_queue.put(("SUCCESS", res.plan_id))
    except StalePlanError as se:
        result_queue.put(("STALE", str(se)))
    except sqlite3.OperationalError as oe:
        result_queue.put(("LOCKED", str(oe)))
    except Exception as e:
        result_queue.put(("ERROR", f"{type(e).__name__}: {e}"))


class TestScheduleConcurrencyP2(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_conc_p2_")
        self.db_file = Path(self.test_dir) / "test_concurrency.db"
        self.mgr = ScheduleManager(storage_file=self.db_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_multiprocess_begin_immediate_blocks_concurrent_writer(self):
        """P2-CONC-01: Tiến trình 1 mở BEGIN IMMEDIATE thì Tiến trình 2 gọi commit_plan_atomic lập tức bị SQLite khóa (Handshake tất định)."""
        p2_ready_event = mp.Event()
        lock_acquired_event = mp.Event()
        p2_done_event = mp.Event()
        exit_q = mp.Queue()
        result_q = mp.Queue()

        p1 = mp.Process(
            target=_worker_hold_write_lock,
            args=(str(self.db_file), p2_ready_event, lock_acquired_event, p2_done_event, exit_q),
        )
        p2 = mp.Process(
            target=_worker_attempt_commit,
            args=(str(self.db_file), "plan_conc_02", "Prof_Conc", p2_ready_event, lock_acquired_event, p2_done_event, result_q),
        )

        try:
            p2.start()
            p1.start()

            p2.join(timeout=5.0)
            p1.join(timeout=5.0)

            p1_result = exit_q.get(timeout=2.0)
            self.assertEqual(p1_result, "SUCCESS")

            res_type, res_msg = result_q.get(timeout=2.0)
            self.assertEqual(
                res_type,
                "LOCKED",
                f"Production commit_plan_atomic đáng lẽ phải bị SQLite lock chặn đứng, thực tế: {res_type} ({res_msg})"
            )
            self.assertTrue(
                "locked" in res_msg.lower() or "busy" in res_msg.lower(),
                f"Thông điệp lỗi phải nêu rõ database is locked/busy: {res_msg}"
            )
        finally:
            for p in (p1, p2):
                if p.is_alive():
                    p.terminate()
                    p.join()

    def test_multiprocess_json_mirror_concurrency(self):
        """P2-CONC-03 (Process-safe Mirror F08): Nhiều tiến trình ghi đồng thời vào storage có JSON mirror mà không va chạm file tạm."""
        json_storage = Path(self.test_dir) / "test_mirror_conc.json"
        # Khởi tạo storage qua .json để kích hoạt _sync_json_mirror = True
        mgr_init = ScheduleManager(storage_file=json_storage)
        self.assertTrue(mgr_init._sync_json_mirror)

        res_q = mp.Queue()
        p_m1 = mp.Process(
            target=_worker_mirror_commit,
            args=(str(json_storage), "plan_m1", "Prof_Mirror_1", res_q),
        )
        p_m2 = mp.Process(
            target=_worker_mirror_commit,
            args=(str(json_storage), "plan_m2", "Prof_Mirror_2", res_q),
        )

        try:
            p_m1.start()
            p_m2.start()

            r1 = res_q.get(timeout=5.0)
            r2 = res_q.get(timeout=5.0)

            p_m1.join(timeout=5.0)
            p_m2.join(timeout=5.0)

            self.assertEqual(r1[0], "SUCCESS")
            self.assertEqual(r2[0], "SUCCESS")

            # Đọc lại file JSON mirror và xác nhận dữ liệu toàn vẹn
            self.assertTrue(json_storage.exists())
            with open(json_storage, "r", encoding="utf-8") as f:
                mirror_tasks = json.load(f)
            self.assertEqual(len(mirror_tasks), 2)
            plan_ids_in_mirror = {t.get("plan_id") for t in mirror_tasks}
            self.assertEqual(plan_ids_in_mirror, {"plan_m1", "plan_m2"})
        finally:
            for p in (p_m1, p_m2):
                if p.is_alive():
                    p.terminate()
                    p.join()

    def test_multiprocess_competing_overwrite_triggers_stale_guard(self):
        """P2-CONC-02: Hai tiến trình tranh chấp đồng thời bằng Barrier -> Đúng 1 SUCCESS và 1 STALE (Zero Race Condition)."""
        # 1. Thêm 1 task ban đầu vào DB
        self.mgr.add_tasks([{
            "id": "base_task_01",
            "profile_name": "Prof_Compete",
            "video_path": "C:/base.mp4",
            "status": "PENDING",
            "scheduled_time": "2026-10-26T12:00:00",
            "scheduled_epoch": 1800000000,
        }])

        # 2. Cả 2 cùng lấy snapshot ban đầu
        snap = self.mgr.get_conflict_snapshot(["Prof_Compete"])
        fp = snap.snapshot_fingerprint

        res_q = mp.Queue()
        barrier = mp.Barrier(2)

        p_a = mp.Process(
            target=_worker_competing_commit_barrier,
            args=(str(self.db_file), "plan_compete_A", "Prof_Compete", fp, "OVERWRITE", barrier, res_q),
        )
        p_b = mp.Process(
            target=_worker_competing_commit_barrier,
            args=(str(self.db_file), "plan_compete_B", "Prof_Compete", fp, "OVERWRITE", barrier, res_q),
        )

        try:
            p_a.start()
            p_b.start()

            # Nhận tường minh đúng 2 kết quả từ queue có timeout
            r1 = res_q.get(timeout=10.0)
            r2 = res_q.get(timeout=10.0)

            p_a.join(timeout=5.0)
            p_b.join(timeout=5.0)

            results = [r1, r2]
            statuses = [r[0] for r in results]

            # Bắt buộc chính xác 1 tiến trình SUCCESS và đúng 1 tiến trình STALE
            self.assertEqual(
                statuses.count("SUCCESS"),
                1,
                f"Bắt buộc phải có đúng 1 tiến trình SUCCESS, thực tế: {statuses}"
            )
            self.assertEqual(
                statuses.count("STALE"),
                1,
                f"Tiến trình còn lại bắt buộc phải bị STALE (chặn stale preview), thực tế: {statuses}"
            )

            # Kiểm tra tính toàn vẹn của DB: Chỉ duy nhất 1 plan_id được lưu
            all_tasks = self.mgr.get_all_tasks()
            active_tasks = [t for t in all_tasks if t["status"] == "PENDING"]
            plan_ids = {t["plan_id"] for t in active_tasks}
            self.assertEqual(len(plan_ids), 1, f"Chỉ được có đúng 1 plan_id active trong DB, thực tế: {plan_ids}")
        finally:
            for p in (p_a, p_b):
                if p.is_alive():
                    p.terminate()
                    p.join()

    def test_multiprocess_init_contention(self):
        """P2-CONC-04 (Multiprocess Init Contention): Hai tiến trình khởi tạo ScheduleManager đồng thời từ con số 0 mà không bị trùng cột ALTER TABLE hay duplicate import."""
        fresh_json = Path(self.test_dir) / "test_init_fresh.json"
        fresh_db = fresh_json.with_suffix(".db")
        self.assertFalse(fresh_db.exists())

        # Tạo file JSON chứa 2 task ban đầu
        with open(fresh_json, "w", encoding="utf-8") as f:
            json.dump([
                {"id": "init_task_1", "profile_name": "Prof_Init", "video_path": "C:/v1.mp4", "status": "PENDING"},
                {"id": "init_task_2", "profile_name": "Prof_Init", "video_path": "C:/v2.mp4", "status": "PENDING"},
            ], f)

        res_q = mp.Queue()
        barrier = mp.Barrier(2)

        p1 = mp.Process(target=_worker_init_storage, args=(str(fresh_db), barrier, res_q))
        p2 = mp.Process(target=_worker_init_storage, args=(str(fresh_db), barrier, res_q))

        try:
            p1.start()
            p2.start()

            r1 = res_q.get(timeout=10.0)
            r2 = res_q.get(timeout=10.0)

            p1.join(timeout=5.0)
            p2.join(timeout=5.0)

            self.assertEqual(r1, ("SUCCESS", 2))
            self.assertEqual(r2, ("SUCCESS", 2))

            # Xác nhận trạng thái SQLite: đúng 2 task, user_version = 2, migration_completed = 1
            conn = sqlite3.connect(str(fresh_db))
            try:
                cnt = conn.execute("SELECT COUNT(*) FROM scheduled_tasks").fetchone()[0]
                self.assertEqual(cnt, 2, "Chỉ được import đúng 2 task, không bị duplicate!")
                u_ver = conn.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(u_ver, 2)
                meta_val = conn.execute("SELECT value FROM _schema_meta WHERE key = 'migration_completed'").fetchone()[0]
                self.assertEqual(meta_val, "1")
            finally:
                conn.close()
        finally:
            for p in (p1, p2):
                if p.is_alive():
                    p.terminate()
                    p.join()


if __name__ == "__main__":
    unittest.main()
