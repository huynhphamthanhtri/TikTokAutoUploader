# -*- coding: utf-8 -*-
"""
test_schedule_migrations.py - Bộ kiểm thử Schema Migration an toàn cho Phase 2.

Tuân thủ nghiêm ngặt:
- AGENTS.md Quy tắc 14: Zero Live Data Mutation (100% test sử dụng tempfile/tmp_path cô lập).
- Kiểm chứng tự động nâng cấp schema từ phiên bản cũ (v1) lên phiên bản mới (v2).
- Kiểm chứng không bao giờ tự ý import lại file JSON cũ nếu người dùng đã chủ động xóa sạch lịch.
- Kiểm chứng các index quan trọng hoạt động trơn tru.
"""

import json
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from schedule_manager import ScheduleManager


class TestScheduleMigrations(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_mig_")
        self.db_file = Path(self.test_dir) / "test_mig_posts.db"
        self.json_file = Path(self.test_dir) / "test_mig_posts.json"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_migration_adds_missing_columns_to_legacy_db(self):
        """P2-MIG-01: Hệ thống tự động phát hiện và bổ sung an toàn 10 cột mới cho DB cũ mà không làm mất dữ liệu."""
        # 1. Tạo thủ công bảng SQLite theo schema v1 cũ (chưa có version, plan_id, canonical_video_path...)
        conn = sqlite3.connect(str(self.db_file))
        with conn:
            conn.execute("""
                CREATE TABLE scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    profile_name TEXT NOT NULL,
                    tiktok_account TEXT DEFAULT '',
                    video_path TEXT NOT NULL,
                    video_filename TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    scheduled_time TEXT NOT NULL,
                    scheduled_epoch INTEGER NOT NULL,
                    golden_hour_slot TEXT DEFAULT 'TRUA',
                    execution_mode TEXT DEFAULT 'SERVER_SCHEDULE',
                    status TEXT DEFAULT 'PENDING',
                    upload_engine TEXT DEFAULT 'DIRECT_API',
                    delete_source_after_post INTEGER DEFAULT 1,
                    source_deleted INTEGER DEFAULT 0,
                    tiktok_item_id TEXT DEFAULT '',
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 2,
                    error_message TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    claim_worker_id TEXT DEFAULT '',
                    claimed_at TEXT,
                    lease_until INTEGER DEFAULT 0
                );
            """)
            conn.execute("""
                INSERT INTO scheduled_tasks (
                    id, profile_name, video_path, scheduled_time, scheduled_epoch,
                    created_at, updated_at
                ) VALUES (
                    'legacy_task_99', 'Prof_Old', 'C:/vids/old.mp4', '2026-10-25T10:00:00',
                    1700000000, '2026-10-25T08:00:00', '2026-10-25T08:00:00'
                );
            """)
            conn.execute("CREATE INDEX idx_legacy_sched_epoch ON scheduled_tasks(scheduled_epoch);")
            conn.execute("""
                CREATE TRIGGER trig_legacy_test_audit
                AFTER UPDATE ON scheduled_tasks
                BEGIN
                    SELECT 1;
                END;
            """)
            conn.execute("PRAGMA user_version = 1;")
        conn.close()

        # 2. Khởi tạo ScheduleManager trỏ vào DB v1 cũ này
        mgr = ScheduleManager(storage_file=self.db_file)

        # 3. Kiểm tra các cột mới đã được bổ sung
        c = mgr._get_connection()
        try:
            cols = {r["name"].lower() for r in c.execute("PRAGMA table_info(scheduled_tasks);").fetchall()}
            expected_new_cols = {
                "version", "plan_id", "slot_id", "last_attempt_id", "remote_project_id",
                "published_at", "profile_timezone", "canonical_video_path",
                "source_size_bytes", "source_mtime_ns"
            }
            for col in expected_new_cols:
                self.assertIn(col, cols, f"Cột {col} chưa được migration bổ sung vào scheduled_tasks!")

            # Kiểm tra bảng schedule_plans
            plans_table = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schedule_plans';").fetchone()
            self.assertIsNotNone(plans_table, "Bảng schedule_plans chưa được tạo!")

            # Kiểm tra user_version đã nâng lên 2
            user_ver = c.execute("PRAGMA user_version;").fetchone()[0]
            self.assertEqual(user_ver, 2)
        finally:
            c.close()

        # 4. Xác nhận dữ liệu cũ hoàn toàn nguyên vẹn
        task = mgr.get_task_by_id("legacy_task_99")
        self.assertIsNotNone(task)
        self.assertEqual(task["profile_name"], "Prof_Old")
        self.assertEqual(task["version"], 1)  # Giá trị default
        self.assertEqual(task["plan_id"], "")

    def test_migration_does_not_reimport_json_if_db_already_initialized(self):
        """P2-MIG-02 (Zero Re-import Bug): Nếu user đã chủ động xóa sạch lịch hoặc DB đã tồn tại, không tự ý import lại file JSON cũ."""
        # 1. Tạo file JSON cũ chứa dữ liệu rác/lịch cũ
        legacy_data = [
            {
                "id": "task_json_stale_99",
                "profile_name": "Prof_JSON",
                "video_path": "C:/v/stale.mp4",
                "scheduled_time": "2026-10-25T12:00:00",
                "status": "PENDING",
            }
        ]
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f)

        # 2. Tạo một file SQLite .db đã tồn tại trước (người dùng đã khởi tạo DB và xóa hết task)
        conn = sqlite3.connect(str(self.db_file))
        with conn:
            conn.execute("""
                CREATE TABLE scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    profile_name TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    scheduled_time TEXT NOT NULL,
                    scheduled_epoch INTEGER NOT NULL,
                    status TEXT DEFAULT 'PENDING',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE TABLE _schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            conn.execute("INSERT INTO _schema_meta (key, value) VALUES ('migration_completed', '1');")
            conn.execute("PRAGMA user_version = 2;")
        conn.close()

        # Xác nhận JSON cũ vẫn còn nguyên dữ liệu chưa hề bị xóa
        with open(self.json_file, "r", encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 1, "File JSON phải còn dữ liệu để chứng minh DB không re-import!")

        # 3. Khởi tạo ScheduleManager trỏ vào DB đã tồn tại này (db_existed_before = True)
        mgr = ScheduleManager(storage_file=self.db_file)

        # 4. KHÔNG ĐƯỢC tự động import lại task cũ từ file JSON!
        remaining_tasks = mgr.get_all_tasks()
        self.assertEqual(
            len(remaining_tasks),
            0,
            "Lỗi nghiêm trọng: Tự ý import lại dữ liệu từ JSON cũ khi database .db đã tồn tại!",
        )

    def test_indexes_created_and_functional(self):
        """P2-MIG-03: Kiểm tra toàn bộ 5 index cần thiết và đúng cấu trúc cột trong SQLite."""
        mgr = ScheduleManager(storage_file=self.db_file)
        conn = mgr._get_connection()
        try:
            indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index';").fetchall()}
            expected_indexes = {
                "idx_sched_status_epoch": ["status", "execution_mode", "scheduled_epoch"],
                "idx_sched_profile_status": ["profile_name", "status"],
                "idx_sched_video_path": ["video_path"],
                "idx_sched_canonical_path": ["canonical_video_path"],
                "idx_sched_plan": ["plan_id"],
            }
            for idx_name, expected_cols in expected_indexes.items():
                self.assertIn(idx_name, indexes, f"Chỉ mục {idx_name} chưa được tạo!")
                # Xác thực cấu trúc cột của index
                cols_info = conn.execute(f"PRAGMA index_info({idx_name});").fetchall()
                actual_cols = [c["name"] for c in cols_info]
                self.assertEqual(
                    actual_cols,
                    expected_cols,
                    f"Index {idx_name} có cấu trúc cột {actual_cols} không khớp kỳ vọng {expected_cols}!"
                )
        finally:
            conn.close()


    def test_migration_failure_and_retry_lifecycle(self):
        """P2-MIG-04: Phân biệt DB khởi tạo dở do lỗi JSON với DB đã hoàn tất. Cho phép thử lại khi sửa JSON và chống re-import khi xóa task."""
        # Bước 1: Tạo file JSON bị lỗi cú pháp
        with open(self.json_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json syntax ...")

        # Bước 2: Khởi tạo ScheduleManager thất bại, văng RuntimeError
        with self.assertRaises(RuntimeError):
            ScheduleManager(storage_file=self.db_file)

        # Kiểm tra DB đã được tạo file nhưng chưa hoàn tất migration
        self.assertTrue(self.db_file.exists())
        conn = sqlite3.connect(str(self.db_file))
        tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_meta';").fetchone()
        if tbl:
            meta_row = conn.execute("SELECT value FROM _schema_meta WHERE key = 'migration_completed';").fetchone()
            self.assertIsNone(meta_row, "Khi migration lỗi, không được đánh dấu migration_completed!")
        user_ver = conn.execute("PRAGMA user_version;").fetchone()[0]
        self.assertEqual(user_ver, 0, "Khi migration lỗi, user_version phải giữ nguyên là 0!")
        conn.close()

        # Bước 2b: Kiểm tra JSON có cú pháp hợp lệ nhưng sai cấu trúc root (dạng dict {} thay vì list [])
        with open(self.json_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"some_key": "not a list"}))
        with self.assertRaises(RuntimeError):
            ScheduleManager(storage_file=self.db_file)
        conn = sqlite3.connect(str(self.db_file))
        tbl = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_meta';").fetchone()
        if tbl:
            meta_row = conn.execute("SELECT value FROM _schema_meta WHERE key = 'migration_completed';").fetchone()
            self.assertIsNone(meta_row, "Khi root JSON không phải list, migration phải từ chối và không ghi migration_completed!")
        user_ver_dict = conn.execute("PRAGMA user_version;").fetchone()[0]
        self.assertEqual(user_ver_dict, 0, "user_version phải giữ nguyên là 0 sau lỗi dict root!")
        conn.close()

        # Bước 3: Người dùng sửa file JSON hợp lệ
        valid_data = [
            {
                "id": "task_fixed_101",
                "profile_name": "Prof_Fixed",
                "video_path": "C:/v/fixed.mp4",
                "scheduled_time": "2026-10-25T14:00:00",
                "status": "PENDING",
            }
        ]
        with open(self.json_file, "w", encoding="utf-8") as f:
            json.dump(valid_data, f)

        # Bước 4: Khởi động lại ScheduleManager -> migration phải được THỬ LẠI và THÀNH CÔNG
        mgr = ScheduleManager(storage_file=self.db_file)
        tasks = mgr.get_all_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "task_fixed_101")

        # Xác nhận _schema_meta đã ghi nhận thành công và user_version = 2
        conn = mgr._get_connection()
        try:
            meta_val = conn.execute("SELECT value FROM _schema_meta WHERE key = 'migration_completed';").fetchone()[0]
            self.assertEqual(meta_val, "1")
            user_ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            self.assertEqual(user_ver, 2)
        finally:
            conn.close()

        # Bước 5: Người dùng chủ động xóa hết task trong DB
        mgr.delete_tasks(["task_fixed_101"])
        self.assertEqual(len(mgr.get_all_tasks()), 0)

        # Bước 6: Khởi động lại ScheduleManager lần nữa -> KHÔNG ĐƯỢC re-import từ JSON
        mgr_restart = ScheduleManager(storage_file=self.db_file)
        self.assertEqual(len(mgr_restart.get_all_tasks()), 0, "Không được re-import khi DB đã hoàn tất migration và người dùng xóa sạch task!")

    def test_migration_failure_rolls_back_schema_changes_on_legacy_db(self):
        """P2-MIG-05 (Schema Rollback Verification): Lỗi xảy ra trong quá trình migration phải rollback toàn bộ thay đổi schema trên DB v1 cũ."""
        # 1. Tạo thủ công bảng SQLite theo schema v1 cũ (chưa có version, plan_id, canonical_video_path...)
        conn = sqlite3.connect(str(self.db_file))
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("""
                CREATE TABLE scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    profile_name TEXT NOT NULL,
                    tiktok_account TEXT DEFAULT '',
                    video_path TEXT NOT NULL,
                    video_filename TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    scheduled_time TEXT NOT NULL,
                    scheduled_epoch INTEGER NOT NULL,
                    golden_hour_slot TEXT DEFAULT 'TRUA',
                    execution_mode TEXT DEFAULT 'SERVER_SCHEDULE',
                    status TEXT DEFAULT 'PENDING',
                    upload_engine TEXT DEFAULT 'DIRECT_API',
                    delete_source_after_post INTEGER DEFAULT 1,
                    source_deleted INTEGER DEFAULT 0,
                    tiktok_item_id TEXT DEFAULT '',
                    retry_count INTEGER DEFAULT 0,
                    max_retries INTEGER DEFAULT 2,
                    error_message TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    claim_worker_id TEXT DEFAULT '',
                    claimed_at TEXT,
                    lease_until INTEGER DEFAULT 0
                );
            """)
            conn.execute("""
                INSERT INTO scheduled_tasks (
                    id, profile_name, tiktok_account, video_path, video_filename,
                    title, scheduled_time, scheduled_epoch, golden_hour_slot,
                    execution_mode, status, upload_engine, delete_source_after_post,
                    source_deleted, tiktok_item_id, retry_count, max_retries,
                    error_message, created_at, updated_at, completed_at,
                    claim_worker_id, claimed_at, lease_until
                ) VALUES (
                    'legacy_task_roll', 'Prof_Rollback', 'acc_roll', 'C:/vids/old.mp4', 'old.mp4',
                    'Old Title', '2026-10-25T10:00:00', 1700000000, 'TRUA',
                    'SERVER_SCHEDULE', 'PENDING', 'DIRECT_API', 1,
                    0, '', 0, 2,
                    '', '2026-10-25T08:00:00', '2026-10-25T08:00:00', NULL,
                    '', NULL, 0
                );
            """)
            conn.execute("CREATE INDEX idx_legacy_sched_epoch ON scheduled_tasks(scheduled_epoch);")
            conn.execute("""
                CREATE TRIGGER trig_legacy_test_audit
                AFTER UPDATE ON scheduled_tasks
                BEGIN
                    SELECT 1;
                END;
            """)
            conn.execute("PRAGMA user_version = 1;")

            v1_cols_expected = [r["name"].lower() for r in conn.execute("PRAGMA table_info(scheduled_tasks);").fetchall()]
            v1_tables_expected = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            v1_master_sql_expected = [(r["type"], r["name"], r["sql"]) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name;").fetchall()]
            v1_table_info_full = [dict(r) for r in conn.execute("PRAGMA table_info(scheduled_tasks);").fetchall()]
            old_task_row_expected = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id = 'legacy_task_roll';").fetchone())
        conn.close()

        # 2. Bọc Connection qua ScheduleManager._get_connection để ném lỗi ngay khi PRAGMA user_version = 2
        orig_get_conn = ScheduleManager._get_connection
        interceptor_hits = []

        class ConnectionWrapper:
            def __init__(self, real_conn):
                self.__dict__["_real"] = real_conn

            def execute(self, sql, *args, **kwargs):
                if "PRAGMA user_version = 2" in sql:
                    interceptor_hits.append(sql)
                    raise sqlite3.OperationalError("Simulated crash right before migration commit!")
                return self._real.execute(sql, *args, **kwargs)

            def close(self):
                return self._real.close()

            def __getattr__(self, name):
                return getattr(self._real, name)

            def __setattr__(self, name, value):
                if name == "_real":
                    self.__dict__["_real"] = value
                else:
                    setattr(self._real, name, value)

        with patch.object(ScheduleManager, "_get_connection", lambda s: ConnectionWrapper(orig_get_conn(s))):
            with self.assertRaises(RuntimeError) as ctx_crash:
                ScheduleManager(storage_file=self.db_file)

        # Chứng minh lỗi thực sự được tiêm trúng điểm PRAGMA user_version = 2 chứ không phải lỗi sớm trước migration
        self.assertEqual(len(interceptor_hits), 1, "Interceptor phải bắt trúng chính xác câu lệnh PRAGMA user_version = 2!")
        self.assertIn("Simulated crash right before migration commit!", str(ctx_crash.exception))

        # 3. Mở kết nối trực tiếp kiểm tra DB: Toàn bộ schema mới phải bị ROLLBACK biến mất
        conn = sqlite3.connect(str(self.db_file))
        conn.row_factory = sqlite3.Row
        try:
            # user_version vẫn là 1
            user_ver = conn.execute("PRAGMA user_version;").fetchone()[0]
            self.assertEqual(user_ver, 1, "Khi migration lỗi, user_version của DB v1 cũ phải giữ nguyên là 1!")

            # Toàn bộ danh sách bảng phải khớp 100% với v1 cũ (không có bảng mới schedule_plans hay _schema_meta)
            actual_tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            self.assertEqual(actual_tables, v1_tables_expected, "Toàn bộ bảng phải khớp 100% v1 ban đầu!")

            # Toàn bộ cột của scheduled_tasks phải khớp 100% với schema v1 ban đầu (không có bất kỳ cột mới nào tồn tại)
            actual_cols = [r["name"].lower() for r in conn.execute("PRAGMA table_info(scheduled_tasks);").fetchall()]
            self.assertEqual(actual_cols, v1_cols_expected, "Toàn bộ schema cột phải khớp 100% với v1 cũ!")

            # Toàn bộ DDL schema trong sqlite_master (kiểu dữ liệu, constraint, index, trigger) phải khớp 100%
            actual_master_sql = [(r["type"], r["name"], r["sql"]) for r in conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name;").fetchall()]
            self.assertEqual(actual_master_sql, v1_master_sql_expected, "Toàn bộ DDL schema trong sqlite_master phải khớp 100% nguyên bản!")

            # Toàn bộ chi tiết định nghĩa cột trong PRAGMA table_info (types, notnull, dflt_value, pk) phải khớp 100%
            actual_table_info_full = [dict(r) for r in conn.execute("PRAGMA table_info(scheduled_tasks);").fetchall()]
            self.assertEqual(actual_table_info_full, v1_table_info_full, "Toàn bộ định nghĩa cột, kiểu dữ liệu, NOT NULL, DEFAULT và PK phải khớp 100%!")

            # Toàn bộ dữ liệu của task cũ phải còn nguyên vẹn 100%
            actual_task_row = dict(conn.execute("SELECT * FROM scheduled_tasks WHERE id = 'legacy_task_roll';").fetchone())
            self.assertEqual(actual_task_row, old_task_row_expected, "Toàn bộ dữ liệu của task cũ phải khớp 100% với ban đầu!")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
