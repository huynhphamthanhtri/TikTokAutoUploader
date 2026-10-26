"""
schedule_manager.py - Quản lý dữ liệu lưu trữ SQLite, hàng đợi và vòng đời lịch đăng TikTok.

Chịu trách nhiệm:
1. Lưu trữ SQLite (scheduled_posts.db) bền vững với chế độ WAL (Write-Ahead Logging) và index tối ưu.
2. Tự động chuyển đổi dữ liệu (Auto-migration) an toàn từ file JSON cũ nếu có.
3. Cơ chế Khóa & Nhận việc nguyên tử (Atomic Task Claiming) chống race condition đăng trùng video.
4. Kiểm tra tham chiếu file nguồn trước khi xóa (bảo vệ video dùng chung giữa nhiều bài).
5. Quản lý trạng thái: PENDING, SERVER_SCHEDULED, QUEUED, UPLOADING, SUCCESS, FAILED, MISSED_RESCHEDULED, CANCELLED.
6. Bộ phát hiện và giải quyết xung đột lịch (Conflict Guard: APPEND, OVERWRITE, SKIP) kèm nhận biết bài đã nạp Server TikTok.
7. Bộ quét phục hồi lỡ hẹn thông minh (Smart Reschedule), dàn đều các ngày tiếp theo tuân thủ giới hạn bài/ngày.
"""

from __future__ import annotations

from collections import defaultdict
import datetime
import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from schedule_contracts import (
    ConflictSnapshot,
    InvalidPlanCommitError,
    PlannedTask,
    ScheduleCommitError,
    ScheduleCommitResult,
    SchedulePlan,
    StalePlanError,
)

logger = logging.getLogger(__name__)

def _get_app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

DEFAULT_DATA_DIR = _get_app_base_dir() / "data"
DEFAULT_DB_FILE = DEFAULT_DATA_DIR / "scheduled_posts.db"
DEFAULT_LEGACY_JSON_FILE = DEFAULT_DATA_DIR / "scheduled_posts.json"

_FILE_LOCK = threading.Lock()


class _InterProcessFileLock:
    """Khóa tệp liên tiến trình (Inter-Process File Lock) an toàn đa nền tảng Windows/Linux (F08)."""
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.lock_path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                start_t = time.time()
                while True:
                    try:
                        self._fd.seek(0)
                        msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except (IOError, OSError):
                        if time.time() - start_t > 5.0:
                            msvcrt.locking(self._fd.fileno(), msvcrt.LK_LOCK, 1)
                            break
                        time.sleep(0.02)
            else:
                import fcntl
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
            return self
        except Exception:
            try:
                self._fd.close()
            except Exception:
                pass
            self._fd = None
            raise

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    self._fd.seek(0)
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                try:
                    self._fd.close()
                except Exception:
                    pass
                self._fd = None


class ScheduleManager:
    """Quản lý tập trung các tác vụ lên lịch đăng TikTok với lưu trữ SQLite bền vững."""

    def __init__(self, storage_file: Optional[Path | str] = None):
        target_path = Path(storage_file or DEFAULT_DB_FILE)
        if target_path.suffix.lower() == ".json":
            self.legacy_json_file = target_path
            self.db_file = target_path.with_suffix(".db")
            self.storage_file = target_path  # Giữ nguyên thuộc tính cho backward compatibility
            self._sync_json_mirror = True
        else:
            self.db_file = target_path
            self.legacy_json_file = target_path.with_suffix(".json")
            self.storage_file = target_path
            self._sync_json_mirror = False

        self._ensure_storage_ready()

    def _get_connection(self) -> sqlite3.Connection:
        """Tạo kết nối SQLite an toàn với WAL mode và Row factory."""
        conn = sqlite3.connect(
            str(self.db_file),
            timeout=10.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _ensure_storage_ready(self) -> None:
        """Khởi tạo bảng SQLite, index, schema migration (P2) và tự động chuyển đổi dữ liệu từ JSON nếu có."""
        try:
            self.db_file.parent.mkdir(parents=True, exist_ok=True)
            db_existed_before = self.db_file.exists()
            init_lock = self.db_file.with_name(f"{self.db_file.name}.init.lock")
            with _FILE_LOCK, _InterProcessFileLock(init_lock):
                conn = self._get_connection()
                # Dùng autocommit mode kết hợp explicit BEGIN IMMEDIATE cho toàn bộ chuỗi init
                conn.isolation_level = None
                try:
                    conn.execute("BEGIN IMMEDIATE;")

                    # 1. Bảng scheduled_tasks cơ sở
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS scheduled_tasks (
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
                            lease_until INTEGER DEFAULT 0,
                            version INTEGER DEFAULT 1,
                            incarnation_id TEXT DEFAULT '',
                            cancelled_by_plan_id TEXT DEFAULT ''
                        );
                    """)

                    # 2. Schema Migration P2 an toàn: kiểm tra PRAGMA table_info trước khi ALTER TABLE
                    info_rows = conn.execute("PRAGMA table_info(scheduled_tasks);").fetchall()
                    existing_cols = {r["name"].lower() for r in info_rows}

                    extended_columns = [
                        ("tiktok_account", "TEXT DEFAULT ''"),
                        ("video_filename", "TEXT DEFAULT ''"),
                        ("title", "TEXT DEFAULT ''"),
                        ("golden_hour_slot", "TEXT DEFAULT 'TRUA'"),
                        ("execution_mode", "TEXT DEFAULT 'SERVER_SCHEDULE'"),
                        ("upload_engine", "TEXT DEFAULT 'DIRECT_API'"),
                        ("delete_source_after_post", "INTEGER DEFAULT 1"),
                        ("source_deleted", "INTEGER DEFAULT 0"),
                        ("tiktok_item_id", "TEXT DEFAULT ''"),
                        ("retry_count", "INTEGER DEFAULT 0"),
                        ("max_retries", "INTEGER DEFAULT 2"),
                        ("error_message", "TEXT DEFAULT ''"),
                        ("completed_at", "TEXT"),
                        ("claim_worker_id", "TEXT DEFAULT ''"),
                        ("claimed_at", "TEXT"),
                        ("lease_until", "INTEGER DEFAULT 0"),
                        ("version", "INTEGER DEFAULT 1"),
                        ("plan_id", "TEXT DEFAULT ''"),
                        ("slot_id", "TEXT DEFAULT ''"),
                        ("last_attempt_id", "TEXT DEFAULT ''"),
                        ("remote_project_id", "TEXT DEFAULT ''"),
                        ("published_at", "INTEGER DEFAULT 0"),
                        ("profile_timezone", "TEXT DEFAULT ''"),
                        ("canonical_video_path", "TEXT DEFAULT ''"),
                        ("source_size_bytes", "INTEGER DEFAULT 0"),
                        ("source_mtime_ns", "INTEGER DEFAULT 0"),
                        ("incarnation_id", "TEXT DEFAULT ''"),
                        ("cancelled_by_plan_id", "TEXT DEFAULT ''"),
                    ]
                    for col_name, col_def in extended_columns:
                        if col_name.lower() not in existing_cols:
                            conn.execute(f"ALTER TABLE scheduled_tasks ADD COLUMN {col_name} {col_def};")

                    # 3. Bảng lưu vết kế hoạch schedule_plans (P2)
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS schedule_plans (
                            plan_id TEXT PRIMARY KEY,
                            created_at TEXT NOT NULL,
                            seed INTEGER NOT NULL,
                            resolution_mode TEXT NOT NULL,
                            plan_json TEXT NOT NULL,
                            status TEXT DEFAULT 'COMMITTED',
                            payload_hash TEXT NOT NULL,
                            committed_at TEXT NOT NULL,
                            receipt_json TEXT NOT NULL
                        );
                    """)

                    # 4. Bảng metadata để kiểm soát trạng thái hoàn tất khởi tạo / migration (F06, F07)
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS _schema_meta (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        );
                    """)

                    # 5. Các chỉ mục (Indexes)
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_status_epoch ON scheduled_tasks(status, execution_mode, scheduled_epoch);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_profile_status ON scheduled_tasks(profile_name, status);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_video_path ON scheduled_tasks(video_path);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_canonical_path ON scheduled_tasks(canonical_video_path);")
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_plan ON scheduled_tasks(plan_id);")

                    # 6. Quản lý trạng thái hoàn tất migration (F06, F07)
                    meta_row = conn.execute("SELECT value FROM _schema_meta WHERE key = 'migration_completed';").fetchone()
                    migration_done = bool(meta_row and meta_row[0] == "1")

                    task_count_row = conn.execute("SELECT COUNT(*) FROM scheduled_tasks;").fetchone()
                    task_count = task_count_row[0] if task_count_row else 0

                    if not migration_done:
                        if task_count > 0:
                            # DB này đã có dữ liệu từ trước (phiên bản cũ) -> đánh dấu migration đã xong
                            conn.execute("INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('migration_completed', '1');")
                        elif self.legacy_json_file.exists():
                            # Thử import từ JSON; nếu lỗi, exception văng ra ngoài, ROLLBACK toàn bộ
                            self._migrate_from_json_unlocked(conn)
                            conn.execute("INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('migration_completed', '1');")
                        else:
                            # DB mới tinh và không có file JSON cũ
                            conn.execute("INSERT OR REPLACE INTO _schema_meta (key, value) VALUES ('migration_completed', '1');")

                    conn.execute("PRAGMA user_version = 2;")
                    conn.execute("COMMIT;")
                except Exception:
                    try:
                        conn.execute("ROLLBACK;")
                    except Exception:
                        pass
                    raise
                finally:
                    conn.close()

            # F08: Đồng bộ JSON mirror CHỈ SAU KHI SQLite COMMIT thành công và ra ngoài transaction
            if self._sync_json_mirror and self.legacy_json_file.exists():
                try:
                    c_mirror = self._get_connection()
                    try:
                        self._sync_legacy_mirror_unlocked(c_mirror)
                    finally:
                        c_mirror.close()
                except Exception as m_err:
                    logger.warning(f"Lỗi đồng bộ JSON mirror sau init: {m_err}")
        except Exception as e:
            logger.error(f"Lỗi khởi tạo storage SQLite lịch đăng: {e}")
            raise RuntimeError(f"Lỗi khởi tạo storage SQLite lịch đăng: {e}") from e

    def _migrate_from_json_unlocked(self, conn: sqlite3.Connection) -> None:
        """Chuyển đổi dữ liệu từ file JSON sang SQLite an toàn."""
        try:
            with open(self.legacy_json_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return
                data = json.loads(content)
                if not isinstance(data, list):
                    raise ValueError(
                        f"File JSON '{self.legacy_json_file}' không hợp lệ: root element phải là một danh sách JSON (list), "
                        f"thực tế nhận được: {type(data).__name__}."
                    )

            migrated_count = 0
            for item in data:
                t_id = item.get("id")
                if not t_id:
                    continue
                sched_time = str(item.get("scheduled_time") or datetime.datetime.now().isoformat())
                sched_epoch = item.get("scheduled_epoch")
                if type(sched_epoch) is not int or sched_epoch <= 0:
                    try:
                        sched_epoch = int(datetime.datetime.fromisoformat(sched_time).timestamp())
                    except Exception:
                        sched_epoch = int(datetime.datetime.now().timestamp())

                conn.execute("""
                    INSERT OR IGNORE INTO scheduled_tasks (
                        id, profile_name, tiktok_account, video_path, video_filename,
                        title, scheduled_time, scheduled_epoch, golden_hour_slot,
                        execution_mode, status, upload_engine, delete_source_after_post,
                        source_deleted, tiktok_item_id, retry_count, max_retries,
                        error_message, created_at, updated_at, completed_at,
                        claim_worker_id, claimed_at, lease_until, version,
                        incarnation_id, cancelled_by_plan_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, '')
                """, (
                    t_id,
                    str(item.get("profile_name") or "").strip(),
                    str(item.get("tiktok_account") or "").strip(),
                    str(item.get("video_path") or "").strip(),
                    str(item.get("video_filename") or Path(str(item.get("video_path") or "")).name).strip(),
                    str(item.get("title") or "").strip(),
                    sched_time,
                    sched_epoch,
                    str(item.get("golden_hour_slot") or "TRUA"),
                    str(item.get("execution_mode") or "SERVER_SCHEDULE"),
                    str(item.get("status") or "PENDING"),
                    str(item.get("upload_engine") or "DIRECT_API"),
                    1 if item.get("delete_source_after_post", True) else 0,
                    1 if item.get("source_deleted", False) else 0,
                    str(item.get("tiktok_item_id") or ""),
                    int(item.get("retry_count") or 0),
                    int(item.get("max_retries") or 2),
                    str(item.get("error_message") or ""),
                    str(item.get("created_at") or datetime.datetime.now().isoformat()),
                    str(item.get("updated_at") or datetime.datetime.now().isoformat()),
                    item.get("completed_at"),
                    str(item.get("claim_worker_id") or ""),
                    item.get("claimed_at"),
                    int(item.get("lease_until") or 0),
                    f"inc_{uuid.uuid4().hex}",
                ))
                migrated_count += 1

            logger.info(f"[AutoSchedule] Đã chuyển đổi thành công {migrated_count} task từ JSON sang SQLite.")
            if not self._sync_json_mirror:
                bak_file = self.legacy_json_file.with_suffix(".json.bak")
                try:
                    import shutil
                    shutil.copy2(self.legacy_json_file, bak_file)
                    logger.info(f"[AutoSchedule] Đã sao lưu file JSON cũ sang {bak_file}")
                except Exception as bak_err:
                    logger.warning(f"Không thể sao lưu file JSON backup: {bak_err}")
        except Exception as e:
            logger.error(f"Lỗi migrate dữ liệu từ JSON sang SQLite: {e}")
            raise RuntimeError(f"Lỗi migrate dữ liệu từ JSON sang SQLite: {e}") from e

    def _sync_legacy_mirror_unlocked(self, conn: sqlite3.Connection) -> None:
        """Ghi đồng bộ file JSON nếu caller/test khởi tạo ScheduleManager với đường dẫn .json (An toàn liên tiến trình F08)."""
        if not self._sync_json_mirror:
            return
        tmp_file = None
        lock_file = self.legacy_json_file.with_name(f"{self.legacy_json_file.name}.lock")
        try:
            with _InterProcessFileLock(lock_file):
                # Đọc DB ngay dưới khóa liên tiến trình để đảm bảo snapshot phản ánh chính xác trạng thái mới nhất
                rows = conn.execute("SELECT * FROM scheduled_tasks ORDER BY scheduled_time ASC").fetchall()
                tasks = [self._row_to_dict(r) for r in rows]
                tmp_file = self.legacy_json_file.with_name(
                    f"{self.legacy_json_file.name}.{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp"
                )
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(tasks, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                tmp_file.replace(self.legacy_json_file)
        except Exception as e:
            logger.warning(f"Không thể đồng bộ file JSON mirror: {e}")
        finally:
            if tmp_file is not None and tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass

    def _row_to_dict(self, row: Any) -> Dict[str, Any]:
        """Chuyển đổi sqlite3.Row sang dictionary đúng chuẩn."""
        if row is None:
            return {}
        d = dict(row)
        d["delete_source_after_post"] = bool(d.get("delete_source_after_post", 1))
        d["source_deleted"] = bool(d.get("source_deleted", 0))
        d["retry_count"] = int(d.get("retry_count") or 0)
        d["max_retries"] = int(d.get("max_retries") or 2)
        d["scheduled_epoch"] = int(d.get("scheduled_epoch") or 0)
        d["lease_until"] = int(d.get("lease_until") or 0)
        if "version" in d:
            d["version"] = int(d.get("version") or 1)
        if "published_at" in d:
            d["published_at"] = int(d.get("published_at") or 0)
        if "source_size_bytes" in d:
            d["source_size_bytes"] = int(d.get("source_size_bytes") or 0)
        if "source_mtime_ns" in d:
            d["source_mtime_ns"] = int(d.get("source_mtime_ns") or 0)
        if "incarnation_id" in d:
            d["incarnation_id"] = str(d.get("incarnation_id") or "")
        if "cancelled_by_plan_id" in d:
            d["cancelled_by_plan_id"] = str(d.get("cancelled_by_plan_id") or "")
        return d

    # =========================================================================
    # TASK CRUD OPERATIONS (SQLITE BACKED)
    # =========================================================================

    def add_tasks(self, tasks_data: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Thêm danh sách task mới vào hàng đợi, tự động sinh ID và timestamp."""
        created_tasks: List[Dict[str, Any]] = []
        now_str = datetime.datetime.now().isoformat()

        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                with conn:
                    for item in tasks_data:
                        task_id = item.get("id") or f"sched_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
                        sched_dt_str = item.get("scheduled_time") or now_str
                        sched_epoch = item.get("scheduled_epoch")
                        if type(sched_epoch) is not int or sched_epoch <= 0:
                            try:
                                sched_epoch = int(datetime.datetime.fromisoformat(sched_dt_str).timestamp())
                            except Exception:
                                sched_epoch = int(datetime.datetime.now().timestamp())

                        prof_name = str(item.get("profile_name") or "").strip()
                        account = str(item.get("tiktok_account") or "").strip()
                        v_path = str(item.get("video_path") or "").strip()
                        v_filename = str(item.get("video_filename") or Path(v_path).name).strip()
                        title = str(item.get("title") or "").strip()
                        slot = str(item.get("golden_hour_slot") or "TRUA")
                        exec_mode = str(item.get("execution_mode") or "SERVER_SCHEDULE")
                        status = str(item.get("status") or "PENDING")
                        engine = str(item.get("upload_engine") or "DIRECT_API")
                        del_src = 1 if item.get("delete_source_after_post", True) else 0
                        src_del = 1 if item.get("source_deleted", False) else 0
                        item_id = str(item.get("tiktok_item_id") or "")
                        retry_cnt = int(item.get("retry_count") or 0)
                        max_ret = int(item.get("max_retries") or 2)
                        err_msg = str(item.get("error_message") or "")
                        c_at = str(item.get("created_at") or now_str)
                        u_at = now_str
                        comp_at = item.get("completed_at")

                        # Point 1: Chống ghi đè task hiện hữu bằng add_tasks (phá vỡ version consistency & metadata)
                        existing_row = conn.execute("SELECT id FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
                        if existing_row:
                            raise ValueError(
                                f"Task ID '{task_id}' đã tồn tại trong cơ sở dữ liệu. "
                                f"add_tasks() chỉ chấp nhận thêm mới, không cho phép ghi đè làm reset version hoặc mất metadata. "
                                f"Vui lòng sử dụng update_task() kèm expected_version."
                            )

                        inc_id = f"inc_{uuid.uuid4().hex}"
                        conn.execute("""
                            INSERT INTO scheduled_tasks (
                                id, profile_name, tiktok_account, video_path, video_filename,
                                title, scheduled_time, scheduled_epoch, golden_hour_slot,
                                execution_mode, status, upload_engine, delete_source_after_post,
                                source_deleted, tiktok_item_id, retry_count, max_retries,
                                error_message, created_at, updated_at, completed_at,
                                claim_worker_id, claimed_at, lease_until, version,
                                incarnation_id, cancelled_by_plan_id
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, 0, 1, ?, '')
                        """, (
                            task_id, prof_name, account, v_path, v_filename,
                            title, sched_dt_str, sched_epoch, slot,
                            exec_mode, status, engine, del_src,
                            src_del, item_id, retry_cnt, max_ret,
                            err_msg, c_at, u_at, comp_at,
                            inc_id
                        ))

                        created_tasks.append({
                            "id": task_id,
                            "profile_name": prof_name,
                            "tiktok_account": account,
                            "video_path": v_path,
                            "video_filename": v_filename,
                            "title": title,
                            "scheduled_time": sched_dt_str,
                            "scheduled_epoch": sched_epoch,
                            "golden_hour_slot": slot,
                            "execution_mode": exec_mode,
                            "status": status,
                            "upload_engine": engine,
                            "delete_source_after_post": bool(del_src),
                            "source_deleted": bool(src_del),
                            "tiktok_item_id": item_id,
                            "retry_count": retry_cnt,
                            "max_retries": max_ret,
                            "error_message": err_msg,
                            "created_at": c_at,
                            "updated_at": u_at,
                            "completed_at": comp_at,
                            "claim_worker_id": "",
                            "claimed_at": None,
                            "lease_until": 0,
                            "version": 1,
                            "incarnation_id": inc_id,
                            "cancelled_by_plan_id": "",
                        })

                self._sync_legacy_mirror_unlocked(conn)
            finally:
                conn.close()

        return created_tasks

    def get_all_tasks(
        self,
        filter_status: Optional[str] = None,
        filter_profile: Optional[str] = None,
        filter_mode: Optional[str] = None,
        search_query: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách task kèm các bộ lọc tùy chọn và phân trang từ SQLite.

        Cơ chế phân trang & Stable Ordering:
        Sử dụng ORDER BY scheduled_time ASC, id ASC đảm bảo tính deterministic tuyệt đối
        cho bất kỳ tập dữ liệu ổn định nào (không có hiện tượng task hoán đổi vị trí do trùng scheduled_time).
        Lưu ý: Đối với offset-based pagination trong môi trường concurrent, khi có thao tác thêm/xóa
        bản ghi đồng thời giữa các lần lật trang, offset có thể dịch chuyển theo nguyên lý ACID SQLite;
        hệ thống giao diện tự động đồng bộ lại total_count và clamp trang hiện tại sau mỗi thao tác.
        """
        conditions: List[str] = []
        params: List[Any] = []

        raw_status = (filter_status or "").strip()
        raw_mode = (filter_mode or "").strip()
        q = (search_query or "").strip()

        if raw_status and raw_status.casefold() not in {"", "all", "tất cả", "tat ca", "tất cả trạng thái"}:
            conditions.append("UPPER(status) = ?")
            params.append(raw_status.upper())

        # Khớp chính xác profile_name (bảo toàn nguyên vẹn mọi tên profile hợp lệ kể cả 'all' hoặc 'tat ca')
        if filter_profile is not None and filter_profile != "" and filter_profile != "Tất cả":
            conditions.append("profile_name = ?")
            params.append(filter_profile)

        if raw_mode and raw_mode.casefold() not in {"", "all", "tất cả", "tat ca", "tất cả chế độ"}:
            up_mode = raw_mode.upper()
            if "TIKTOK" in up_mode or "SERVER" in up_mode:
                conditions.append("execution_mode = 'SERVER_SCHEDULE'")
            elif "MÁY" in up_mode or "LOCAL" in up_mode or "QUEUE" in up_mode:
                conditions.append("execution_mode = 'LOCAL_QUEUE'")
            else:
                conditions.append("UPPER(execution_mode) = ?")
                params.append(up_mode)

        if q:
            conditions.append("""(
                profile_name LIKE ? OR
                tiktok_account LIKE ? OR
                video_filename LIKE ? OR
                title LIKE ? OR
                id LIKE ?
            )""")
            like_q = f"%{q}%"
            params.extend([like_q, like_q, like_q, like_q, like_q])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT * FROM scheduled_tasks {where_clause} ORDER BY scheduled_time ASC, id ASC"

        if limit is not None and int(limit) > 0:
            clamped_limit = min(1000, max(1, int(limit)))
            query += " LIMIT ? OFFSET ?"
            params.extend([clamped_limit, max(0, int(offset))])

        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def count_tasks(
        self,
        filter_status: Optional[str] = None,
        filter_profile: Optional[str] = None,
        filter_mode: Optional[str] = None,
        search_query: Optional[str] = None,
    ) -> int:
        """Đếm tổng số task thỏa mãn các bộ lọc."""
        conditions: List[str] = []
        params: List[Any] = []

        raw_status = (filter_status or "").strip()
        raw_mode = (filter_mode or "").strip()
        q = (search_query or "").strip()

        if raw_status and raw_status.casefold() not in {"", "all", "tất cả", "tat ca", "tất cả trạng thái"}:
            conditions.append("UPPER(status) = ?")
            params.append(raw_status.upper())

        # Khớp chính xác profile_name (bảo toàn nguyên vẹn mọi tên profile hợp lệ kể cả 'all' hoặc 'tat ca')
        if filter_profile is not None and filter_profile != "" and filter_profile != "Tất cả":
            conditions.append("profile_name = ?")
            params.append(filter_profile)

        if raw_mode and raw_mode.casefold() not in {"", "all", "tất cả", "tat ca", "tất cả chế độ"}:
            up_mode = raw_mode.upper()
            if "TIKTOK" in up_mode or "SERVER" in up_mode:
                conditions.append("execution_mode = 'SERVER_SCHEDULE'")
            elif "MÁY" in up_mode or "LOCAL" in up_mode or "QUEUE" in up_mode:
                conditions.append("execution_mode = 'LOCAL_QUEUE'")
            else:
                conditions.append("UPPER(execution_mode) = ?")
                params.append(up_mode)

        if q:
            conditions.append("""(
                profile_name LIKE ? OR
                tiktok_account LIKE ? OR
                video_filename LIKE ? OR
                title LIKE ? OR
                id LIKE ?
            )""")
            like_q = f"%{q}%"
            params.extend([like_q, like_q, like_q, like_q, like_q])

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = f"SELECT COUNT(*) FROM scheduled_tasks {where_clause}"

        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                row = conn.execute(query, params).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()

    def get_distinct_profiles(self) -> List[str]:
        """Lấy danh sách các profile_name duy nhất hiện có trong cơ sở dữ liệu (tối ưu, không tải toàn bộ bản ghi)."""
        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT profile_name FROM scheduled_tasks "
                    "WHERE profile_name IS NOT NULL AND profile_name != '' "
                    "ORDER BY profile_name ASC"
                ).fetchall()
                return [r[0] for r in rows if r[0]]
            finally:
                conn.close()

    def get_task_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Tìm task theo ID duy nhất."""
        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)).fetchone()
                return self._row_to_dict(row) if row else None
            finally:
                conn.close()

    def update_task(
        self,
        task_id: str,
        updates: Dict[str, Any],
        *,
        expected_version: Optional[int] = None,
        expected_incarnation_id: Optional[str] = None,
    ) -> bool:
        """Cập nhật thông tin và trạng thái của một task (F04: Luôn tự động tăng version, hỗ trợ expected_incarnation_id chống ABA)."""
        if not updates:
            return False

        allowed_fields = {
            "profile_name", "tiktok_account", "video_path", "video_filename", "title",
            "scheduled_time", "scheduled_epoch", "golden_hour_slot", "execution_mode",
            "status", "upload_engine", "delete_source_after_post", "source_deleted",
            "tiktok_item_id", "retry_count", "max_retries", "error_message",
            "completed_at", "claim_worker_id", "claimed_at", "lease_until",
            "plan_id", "slot_id", "last_attempt_id", "remote_project_id",
            "published_at", "profile_timezone", "canonical_video_path",
            "source_size_bytes", "source_mtime_ns"
        }

        set_clauses: List[str] = []
        params: List[Any] = []

        safe_updates = dict(updates)
        if "profile_name" in safe_updates:
            norm_p = str(safe_updates["profile_name"]).strip()
            if not norm_p:
                raise ValueError("profile_name không được để trống khi update_task.")
            safe_updates["profile_name"] = norm_p

        for k, v in safe_updates.items():
            if k in allowed_fields:
                set_clauses.append(f"{k} = ?")
                if isinstance(v, bool):
                    params.append(1 if v else 0)
                else:
                    params.append(v)

        if not set_clauses:
            return False

        # F04: MỌI thao tác update_task BẮT BUỘC phải tăng version
        set_clauses.append("version = version + 1")

        now_str = datetime.datetime.now().isoformat()
        set_clauses.append("updated_at = ?")
        params.append(now_str)

        where_clauses = ["id = ?"]
        params.append(task_id)

        if expected_version is not None:
            where_clauses.append("version = ?")
            params.append(int(expected_version))

        if expected_incarnation_id is not None:
            where_clauses.append("incarnation_id = ?")
            params.append(str(expected_incarnation_id).strip())

        query = f"UPDATE scheduled_tasks SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)}"

        with _FILE_LOCK:
            conn = self._get_connection()
            success = False
            try:
                with conn:
                    cur = conn.execute(query, params)
                    success = cur.rowcount > 0
                if success:
                    self._sync_legacy_mirror_unlocked(conn)
                return success
            finally:
                conn.close()

    def cancel_tasks(self, task_ids: Sequence[str]) -> int:
        """Hủy các task chưa thực thi (chuyển sang CANCELLED, tăng version - F04)."""
        target_ids = list(set(task_ids))
        if not target_ids:
            return 0

        now_str = datetime.datetime.now().isoformat()
        placeholders = ",".join("?" * len(target_ids))
        query = f"""
            UPDATE scheduled_tasks
            SET status = 'CANCELLED', version = version + 1, updated_at = ?
            WHERE id IN ({placeholders}) AND status IN ('PENDING', 'QUEUED', 'MISSED_RESCHEDULED', 'FAILED')
        """

        with _FILE_LOCK:
            conn = self._get_connection()
            cancelled = 0
            try:
                with conn:
                    cur = conn.execute(query, [now_str] + target_ids)
                    cancelled = cur.rowcount
                if cancelled > 0:
                    self._sync_legacy_mirror_unlocked(conn)
                return cancelled
            finally:
                conn.close()

    def retry_tasks(self, task_ids: Sequence[str]) -> int:
        """Đặt lại trạng thái PENDING cho các task bị lỗi để thử lại (tăng version - F04)."""
        target_ids = list(set(task_ids))
        if not target_ids:
            return 0

        now_str = datetime.datetime.now().isoformat()
        placeholders = ",".join("?" * len(target_ids))
        query = f"""
            UPDATE scheduled_tasks
            SET status = 'PENDING', version = version + 1, error_message = '', retry_count = 0,
                claim_worker_id = '', claimed_at = NULL, lease_until = 0,
                updated_at = ?
            WHERE id IN ({placeholders}) AND status IN ('FAILED', 'CANCELLED')
        """

        with _FILE_LOCK:
            conn = self._get_connection()
            retried = 0
            try:
                with conn:
                    cur = conn.execute(query, [now_str] + target_ids)
                    retried = cur.rowcount
                if retried > 0:
                    self._sync_legacy_mirror_unlocked(conn)
                return retried
            finally:
                conn.close()

    def delete_tasks(self, task_ids: Sequence[str]) -> int:
        """Xóa vĩnh viễn danh sách task khỏi cơ sở dữ liệu."""
        target_ids = list(set(task_ids))
        if not target_ids:
            return 0

        placeholders = ",".join("?" * len(target_ids))
        query = f"DELETE FROM scheduled_tasks WHERE id IN ({placeholders})"

        with _FILE_LOCK:
            conn = self._get_connection()
            deleted = 0
            try:
                with conn:
                    cur = conn.execute(query, target_ids)
                    deleted = cur.rowcount
                if deleted > 0:
                    self._sync_legacy_mirror_unlocked(conn)
                return deleted
            finally:
                conn.close()

    def clear_finished_tasks(self) -> int:
        """Dọn dẹp toàn bộ các task đã hoàn thành (SUCCESS) hoặc đã hủy (CANCELLED)."""
        with _FILE_LOCK:
            conn = self._get_connection()
            cleared = 0
            try:
                with conn:
                    cur = conn.execute("DELETE FROM scheduled_tasks WHERE status IN ('SUCCESS', 'CANCELLED')")
                    cleared = cur.rowcount
                if cleared > 0:
                    self._sync_legacy_mirror_unlocked(conn)
                return cleared
            finally:
                conn.close()

    # =========================================================================
    # P0: ATOMIC TASK CLAIMING & RESOURCE INTEGRITY
    # =========================================================================

    def claim_task_for_execution(self, task_id: str, worker_id: str, lease_seconds: int = 180) -> bool:
        """Khóa và nhận việc nguyên tử (Atomic Claim) cho một task cụ thể.
        
        Chỉ nhận việc thành công nếu:
        1. Task đang ở trạng thái 'PENDING' hoặc 'QUEUED'.
        2. HOẶC Task đang ở trạng thái 'UPLOADING' nhưng lease_until < now_epoch (worker trước bị crash/treo).
        
        Trả về True nếu claim thành công, False nếu task đã bị worker khác nhận.
        """
        now = datetime.datetime.now()
        now_str = now.isoformat()
        now_epoch = int(now.timestamp())
        lease_until = now_epoch + lease_seconds

        with _FILE_LOCK:
            conn = self._get_connection()
            success = False
            try:
                with conn:
                    cur = conn.execute("""
                        UPDATE scheduled_tasks
                        SET status = 'UPLOADING',
                            version = version + 1,
                            claim_worker_id = ?,
                            claimed_at = ?,
                            lease_until = ?,
                            updated_at = ?
                        WHERE id = ?
                          AND (
                              status IN ('PENDING', 'QUEUED')
                              OR (status = 'UPLOADING' AND lease_until < ?)
                          )
                    """, (worker_id, now_str, lease_until, now_str, task_id, now_epoch))

                    success = cur.rowcount > 0
                if success:
                    self._sync_legacy_mirror_unlocked(conn)
                return success
            finally:
                conn.close()

    def claim_due_tasks(self, worker_id: str, limit: int = 2, lease_seconds: int = 180) -> List[Dict[str, Any]]:
        """Tìm và nhận nguyên tử các task LOCAL_QUEUE đã đến hạn thực thi (dùng cho daemon loop)."""
        now = datetime.datetime.now()
        now_str = now.isoformat()
        now_epoch = int(now.timestamp())
        lease_until = now_epoch + lease_seconds

        claimed_tasks: List[Dict[str, Any]] = []
        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                with conn:
                    # 1. Quét các task LOCAL_QUEUE đã đến hạn
                    rows = conn.execute("""
                        SELECT id FROM scheduled_tasks
                        WHERE execution_mode = 'LOCAL_QUEUE'
                          AND scheduled_epoch <= ?
                          AND (
                              status IN ('PENDING', 'QUEUED')
                              OR (status = 'UPLOADING' AND lease_until < ?)
                          )
                        ORDER BY scheduled_epoch ASC
                        LIMIT ?
                    """, (now_epoch, now_epoch, limit)).fetchall()

                    task_ids = [r["id"] for r in rows]
                    if hasattr(self, "_test_claim_interleave_hook") and callable(self._test_claim_interleave_hook):
                        self._test_claim_interleave_hook(conn, task_ids)
                    for tid in task_ids:
                        cur = conn.execute("""
                            UPDATE scheduled_tasks
                            SET status = 'UPLOADING',
                                version = version + 1,
                                claim_worker_id = ?,
                                claimed_at = ?,
                                lease_until = ?,
                                updated_at = ?
                            WHERE id = ?
                              AND execution_mode = 'LOCAL_QUEUE'
                              AND scheduled_epoch <= ?
                              AND (
                                  status IN ('PENDING', 'QUEUED')
                                  OR (status = 'UPLOADING' AND lease_until < ?)
                              )
                        """, (worker_id, now_str, lease_until, now_str, tid, now_epoch, now_epoch))

                        if cur.rowcount > 0:
                            t_row = conn.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (tid,)).fetchone()
                            if t_row:
                                claimed_tasks.append(self._row_to_dict(t_row))

                if claimed_tasks:
                    self._sync_legacy_mirror_unlocked(conn)
            finally:
                conn.close()

        return claimed_tasks

    def is_video_referenced_by_pending_tasks(self, video_path: str, exclude_task_id: str = "") -> bool:
        """Kiểm tra xem file video còn đang được tham chiếu bởi các task chưa hoàn thành khác hay không."""
        if not video_path:
            return False

        norm_path = str(video_path).strip()
        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                row = conn.execute("""
                    SELECT COUNT(*) as cnt FROM scheduled_tasks
                    WHERE video_path = ?
                      AND id != ?
                      AND status IN ('PENDING', 'QUEUED', 'UPLOADING')
                """, (norm_path, exclude_task_id)).fetchone()
                count = row["cnt"] if row else 0
                return count > 0
            finally:
                conn.close()

    # =========================================================================
    # CONFLICT GUARD & SMART RECOVERY
    # =========================================================================

    def check_profile_conflicts(self, profile_names: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Quét và trả về danh sách các profile đang có bài chờ đăng kèm số lượng Server Scheduled."""
        targets = set(str(p).strip() for p in profile_names if str(p).strip())
        if not targets:
            return {}

        conflicts: Dict[str, Dict[str, Any]] = {}
        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                placeholders = ",".join("?" * len(targets))
                query = f"""
                    SELECT * FROM scheduled_tasks
                    WHERE profile_name IN ({placeholders})
                      AND status IN ('PENDING', 'SERVER_SCHEDULED', 'QUEUED')
                    ORDER BY scheduled_epoch ASC
                """
                rows = conn.execute(query, list(targets)).fetchall()
                for r in rows:
                    t = self._row_to_dict(r)
                    prof = t.get("profile_name")
                    status = t.get("status")

                    info = conflicts.setdefault(prof, {
                        "pending_count": 0,
                        "server_scheduled_count": 0,
                        "local_pending_count": 0,
                        "latest_scheduled_time": None,
                        "tasks": [],
                    })
                    info["pending_count"] += 1
                    if status == "SERVER_SCHEDULED":
                        info["server_scheduled_count"] += 1
                    else:
                        info["local_pending_count"] += 1
                    info["tasks"].append(t)

                    sched_str = t.get("scheduled_time")
                    if sched_str:
                        try:
                            dt = datetime.datetime.fromisoformat(sched_str)
                            if info["latest_scheduled_time"] is None or dt > info["latest_scheduled_time"]:
                                info["latest_scheduled_time"] = dt
                        except Exception:
                            pass
            finally:
                conn.close()

        return conflicts

    def get_schedule_summary_map(self) -> Dict[str, Dict[str, Any]]:
        """Quét và trả về map tóm tắt lịch đăng của tất cả profile đang có bài chờ đăng."""
        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                rows = conn.execute("""
                    SELECT * FROM scheduled_tasks
                    WHERE status IN ('PENDING', 'SERVER_SCHEDULED', 'QUEUED', 'MISSED_RESCHEDULED')
                    ORDER BY scheduled_epoch ASC
                """).fetchall()
            finally:
                conn.close()

        summary: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            t = self._row_to_dict(r)
            prof = str(t.get("profile_name") or "").strip()
            if not prof:
                continue

            info = summary.setdefault(prof, {
                "pending_count": 0,
                "latest_scheduled_time": None,
                "latest_date_str": "",
                "badge": "",
            })
            info["pending_count"] += 1

            sched_str = t.get("scheduled_time")
            if sched_str:
                try:
                    dt = datetime.datetime.fromisoformat(sched_str)
                    if info["latest_scheduled_time"] is None or dt > info["latest_scheduled_time"]:
                        info["latest_scheduled_time"] = dt
                except Exception:
                    pass

        for prof, info in summary.items():
            dt = info["latest_scheduled_time"]
            count = info["pending_count"]
            if dt:
                date_str = dt.strftime("%d/%m")
                info["latest_date_str"] = date_str
                info["badge"] = f"📅 Đến {date_str} ({count} clip)"
            else:
                info["badge"] = f"📅 ({count} clip)"

        return summary

    def _get_conflict_snapshot_unlocked(self, conn: sqlite3.Connection, profile_names: Sequence[str]) -> ConflictSnapshot:
        """Thu thập ConflictSnapshot từ kết nối SQLite đang mở."""
        norm_profiles = tuple(sorted(set(str(p).strip() for p in profile_names if str(p).strip())))
        if not norm_profiles:
            return ConflictSnapshot(
                conflicted_profiles=(),
                snapshot_fingerprint=hashlib.sha256(b"empty_scope").hexdigest()[:16],
                existing_tasks={},
                can_read_tasks=True,
            )

        placeholders = ",".join("?" * len(norm_profiles))
        rows = conn.execute(f"""
            SELECT id, profile_name, status, scheduled_epoch, scheduled_time, execution_mode, version,
                   canonical_video_path, video_path, created_at, incarnation_id
            FROM scheduled_tasks
            WHERE profile_name IN ({placeholders})
              AND status IN ('PENDING', 'SERVER_SCHEDULED', 'QUEUED', 'UPLOADING')
            ORDER BY profile_name ASC, scheduled_epoch ASC, id ASC
        """, norm_profiles).fetchall()

        existing_tasks_map: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        fingerprint_items: List[Dict[str, Any]] = []

        for r in rows:
            p_name = r["profile_name"]
            t_dict = dict(r)
            existing_tasks_map[p_name].append(t_dict)
            fingerprint_items.append({
                "profile": p_name,
                "id": str(r["id"]),
                "incarnation_id": str(r["incarnation_id"] or ""),
                "status": str(r["status"]),
                "epoch": int(r["scheduled_epoch"] or 0),
                "version": int(r["version"] or 1) if "version" in r.keys() else 1,
                "mode": str(r["execution_mode"] or ""),
                "canonical_path": str(r["canonical_video_path"] or r["video_path"] or ""),
                "created_at": str(r["created_at"] or ""),
            })

        conflicted = tuple(sorted(p for p, tasks in existing_tasks_map.items() if len(tasks) > 0))
        fingerprint_payload = {
            "scope": list(norm_profiles),
            "conflicted_profiles": list(conflicted),
            "can_read_tasks": True,
            "tasks": fingerprint_items,
        }
        fingerprint_json = json.dumps(fingerprint_payload, sort_keys=True)
        fingerprint = hashlib.sha256(fingerprint_json.encode("utf-8")).hexdigest()[:16]

        frozen_tasks_map = {k: tuple(v) for k, v in existing_tasks_map.items()}
        return ConflictSnapshot(
            conflicted_profiles=conflicted,
            snapshot_fingerprint=fingerprint,
            existing_tasks=frozen_tasks_map,
            can_read_tasks=True,
        )

    def get_conflict_snapshot(self, profile_names: Sequence[str]) -> ConflictSnapshot:
        """Thu thập ConflictSnapshot chuẩn hóa cho danh sách profile (P2)."""
        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                return self._get_conflict_snapshot_unlocked(conn, profile_names)
            finally:
                conn.close()

    def commit_plan_atomic(
        self,
        plan: SchedulePlan,
        *,
        expected_snapshot_fingerprint: Optional[str] = None,
    ) -> ScheduleCommitResult:
        """Lưu trữ nguyên tử Kế hoạch Lên Lịch (SchedulePlan) xuống SQLite trong 1 transaction duy nhất (P2).
        
        Quy tắc an toàn:
        1. F02: Validate plan integrity bằng plan.validate_for_commit() trước bất kỳ thao tác nào.
        2. F03: Bắt buộc snapshot fingerprint hợp lệ đối với chế độ OVERWRITE.
        3. F01: Sử dụng BEGIN IMMEDIATE transaction từ đầu để serialized reads & writes (chống race condition giữa 2 process).
        4. F05: Idempotent check đối soát cả plan_id và payload_hash. Nếu cùng plan_id khác hash -> raise ScheduleCommitError.
        5. F05: Kiểm tra tính nguyên vẹn của receipt_json.
        6. Stale Plan Guard: Đối soát fingerprint trong cùng write transaction. Nếu lệch -> raise StalePlanError.
        7. OVERWRITE: Hủy các task PENDING/QUEUED của profile trong scope và tăng version.
        8. INSERT new tasks tường minh (không dùng OR REPLACE).
        9. Ghi biên lai vào schedule_plans.
        10. COMMIT; và sau đó mới đồng bộ JSON mirror (F08).
        """
        if plan is None:
            raise InvalidPlanCommitError("SchedulePlan không được để None.")

        # F02: Kiểm tra toàn vẹn kế hoạch
        plan.validate_for_commit()

        # F03: OVERWRITE bắt buộc phải có fingerprint không rỗng
        if plan.resolution_mode == "OVERWRITE":
            if not expected_snapshot_fingerprint or not str(expected_snapshot_fingerprint).strip():
                raise InvalidPlanCommitError(
                    "Kế hoạch OVERWRITE bắt buộc phải có expected_snapshot_fingerprint hợp lệ để chống stale commit."
                )

        # Xác định phạm vi profile chuẩn hóa
        target_profiles = [
            str(p).strip()
            for p in (plan.requested_profile_names or plan.profile_assignments.keys())
            if str(p).strip()
        ]
        now_str = datetime.datetime.now().isoformat()

        with _FILE_LOCK:
            conn = self._get_connection()
            # F01: Dùng autocommit mode kết hợp explicit BEGIN IMMEDIATE
            conn.isolation_level = None
            try:
                conn.execute("BEGIN IMMEDIATE;")

                # 1. Idempotent Check (F05)
                row_plan = conn.execute(
                    "SELECT payload_hash, receipt_json, committed_at FROM schedule_plans WHERE plan_id = ?",
                    (plan.plan_id,)
                ).fetchone()
                if row_plan:
                    existing_hash = row_plan["payload_hash"]
                    current_hash = plan.compute_payload_hash()
                    if existing_hash != current_hash:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Xung đột Idempotency: plan_id '{plan.plan_id}' đã tồn tại nhưng có nội dung payload_hash khác "
                            f"(DB: {existing_hash}, Current: {current_hash}). Không thể commit kế hoạch bị sửa đổi."
                        )
                    receipt_raw = row_plan["receipt_json"]
                    try:
                        receipt_data = json.loads(receipt_raw)
                    except Exception as json_err:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi schedule_plans cho plan '{plan.plan_id}' bị hỏng cú pháp receipt_json: {json_err}"
                        )

                    # F05: Kiểm tra cấu trúc, kiểu dữ liệu và tính nhất quán của receipt
                    if not isinstance(receipt_data, dict):
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' không phải là JSON Object."
                        )
                    rec_plan_id = receipt_data.get("plan_id")
                    if not rec_plan_id or rec_plan_id != plan.plan_id:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json có plan_id='{rec_plan_id}' không khớp với plan_id='{plan.plan_id}'."
                        )
                    if "created_task_ids" not in receipt_data:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' thiếu trường bắt buộc 'created_task_ids'."
                        )
                    if "cancelled_task_ids" not in receipt_data:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' thiếu trường bắt buộc 'cancelled_task_ids'."
                        )

                    rec_created = receipt_data["created_task_ids"]
                    if not isinstance(rec_created, (list, tuple)):
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' có created_task_ids không phải dạng danh sách."
                        )
                    rec_cancelled = receipt_data["cancelled_task_ids"]
                    if not isinstance(rec_cancelled, (list, tuple)):
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' có cancelled_task_ids không phải dạng danh sách."
                        )

                    for cid in rec_created:
                        if not isinstance(cid, str) or not cid.strip():
                            conn.execute("ROLLBACK;")
                            raise ScheduleCommitError(
                                f"Bản ghi receipt_json của plan '{plan.plan_id}' có created_task_ids chứa phần tử ID không hợp lệ: {cid!r}"
                            )
                    for cid in rec_cancelled:
                        if not isinstance(cid, str) or not cid.strip():
                            conn.execute("ROLLBACK;")
                            raise ScheduleCommitError(
                                f"Bản ghi receipt_json của plan '{plan.plan_id}' có cancelled_task_ids chứa phần tử ID không hợp lệ: {cid!r}"
                            )

                    # Invariant: created_task_ids không được chứa ID trùng lặp
                    if len(rec_created) != len(set(rec_created)):
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' có created_task_ids chứa ID trùng lặp."
                        )

                    # Invariant: cancelled_task_ids không được chứa ID trùng lặp
                    if len(rec_cancelled) != len(set(rec_cancelled)):
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' có cancelled_task_ids chứa ID trùng lặp."
                        )

                    # Invariant A: Kế hoạch APPEND không được phép có cancelled_task_ids
                    if plan.resolution_mode == "APPEND" and len(rec_cancelled) > 0:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Biên lai không hợp lệ: plan '{plan.plan_id}' có resolution_mode='APPEND' nhưng biên lai có chứa {len(rec_cancelled)} cancelled_task_ids."
                        )

                    # Invariant B: created_task_ids và cancelled_task_ids phải tách biệt hoàn toàn (disjoint)
                    if not set(rec_created).isdisjoint(set(rec_cancelled)):
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Biên lai không nhất quán: tập created_task_ids và cancelled_task_ids của plan '{plan.plan_id}' bị trùng lặp ID."
                        )

                    if len(rec_created) != plan.total_planned_tasks:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Biên lai không nhất quán: plan có {plan.total_planned_tasks} tasks nhưng biên lai ghi {len(rec_created)} created_task_ids."
                        )

                    # Invariant C: Kiểm tra resolution_mode trong receipt phải khớp với plan
                    rec_mode = receipt_data.get("resolution_mode")
                    if rec_mode != plan.resolution_mode:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' có resolution_mode='{rec_mode}' "
                            f"không khớp với resolution_mode của plan='{plan.resolution_mode}'."
                        )

                    # Invariant D: Kiểm tra committed_at trong receipt phải là chuỗi hợp lệ và khớp với DB
                    rec_committed_at = receipt_data.get("committed_at")
                    if not rec_committed_at or not isinstance(rec_committed_at, str) or not rec_committed_at.strip():
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' thiếu hoặc sai kiểu trường 'committed_at'."
                        )
                    if rec_committed_at != row_plan["committed_at"]:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Bản ghi receipt_json của plan '{plan.plan_id}' có committed_at='{rec_committed_at}' "
                            f"không khớp với cột committed_at='{row_plan['committed_at']}' trong bảng schedule_plans."
                        )

                    if rec_created:
                        placeholders = ",".join("?" * len(rec_created))
                        actual_rows = conn.execute(
                            f"SELECT id FROM scheduled_tasks WHERE plan_id = ? AND id IN ({placeholders})",
                            [plan.plan_id] + list(rec_created)
                        ).fetchall()
                        if len(actual_rows) != len(rec_created):
                            conn.execute("ROLLBACK;")
                            raise ScheduleCommitError(
                                f"Biên lai không nhất quán với DB: chỉ tìm thấy {len(actual_rows)}/{len(rec_created)} tasks trong DB thuộc plan '{plan.plan_id}'."
                            )

                    # Invariant E: Đối soát thực tế trong DB: các task đã hủy trong receipt phải khớp chính xác 100% với các task bị hủy bởi plan này
                    if target_profiles:
                        p_placeholders = ",".join("?" * len(target_profiles))
                        db_cancelled_rows = conn.execute(
                            f"SELECT id, status, profile_name FROM scheduled_tasks "
                            f"WHERE profile_name IN ({p_placeholders}) AND cancelled_by_plan_id = ?",
                            list(target_profiles) + [plan.plan_id]
                        ).fetchall()
                        db_cancelled_ids = {r["id"] for r in db_cancelled_rows}
                    else:
                        db_cancelled_rows = []
                        db_cancelled_ids = set()

                    rec_cancelled_ids = set(rec_cancelled)

                    if rec_cancelled_ids != db_cancelled_ids:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Biên lai không nhất quán: cancelled_task_ids trong receipt {sorted(rec_cancelled_ids)} "
                            f"không khớp với các task bị hủy bởi plan '{plan.plan_id}' trong DB {sorted(db_cancelled_ids)}."
                        )

                    non_cancelled = [r["id"] for r in db_cancelled_rows if r["status"] != "CANCELLED"]
                    if non_cancelled:
                        conn.execute("ROLLBACK;")
                        raise ScheduleCommitError(
                            f"Biên lai không nhất quán: các task sau trong cancelled_task_ids không ở trạng thái CANCELLED: {non_cancelled}"
                        )

                    conn.execute("ROLLBACK;")
                    res = ScheduleCommitResult(
                        plan_id=rec_plan_id,
                        created_task_ids=tuple(str(x) for x in rec_created),
                        cancelled_task_ids=tuple(str(x) for x in rec_cancelled),
                        already_committed=True,
                        resolution_mode=rec_mode,
                        committed_at=rec_committed_at,
                    )
                    if self._sync_json_mirror:
                        try:
                            self._sync_legacy_mirror_unlocked(conn)
                        except Exception as m_err:
                            logger.warning(f"Lỗi đồng bộ JSON mirror khi idempotent retry: {m_err}")
                    return res

                # 2. Stale Plan Guard (F01, F03)
                if expected_snapshot_fingerprint is not None:
                    current_snap = self._get_conflict_snapshot_unlocked(conn, target_profiles)
                    if current_snap.snapshot_fingerprint != expected_snapshot_fingerprint:
                        conn.execute("ROLLBACK;")
                        raise StalePlanError(
                            f"Dữ liệu hàng đợi đã thay đổi so với thời điểm lập lịch! "
                            f"(Expected fingerprint: {expected_snapshot_fingerprint}, Actual: {current_snap.snapshot_fingerprint}). "
                            f"Vui lòng tải lại preview."
                        )

                cancelled_task_ids: List[str] = []
                # 3. OVERWRITE mode: Hủy task cũ trong transaction
                if plan.resolution_mode == "OVERWRITE" and target_profiles:
                    placeholders = ",".join("?" * len(target_profiles))
                    cancel_rows = conn.execute(f"""
                        SELECT id FROM scheduled_tasks
                        WHERE profile_name IN ({placeholders})
                          AND status IN ('PENDING', 'QUEUED')
                    """, target_profiles).fetchall()
                    cancelled_task_ids = [r["id"] for r in cancel_rows]
                    if cancelled_task_ids:
                        cur_cancel = conn.execute(f"""
                            UPDATE scheduled_tasks
                            SET status = 'CANCELLED',
                                version = version + 1,
                                cancelled_by_plan_id = ?,
                                updated_at = ?
                            WHERE id IN ({','.join('?' * len(cancelled_task_ids))})
                              AND status IN ('PENDING', 'QUEUED')
                        """, [plan.plan_id, now_str] + cancelled_task_ids)
                        if cur_cancel.rowcount != len(cancelled_task_ids):
                            conn.execute("ROLLBACK;")
                            raise StalePlanError(
                                f"Một số task đã bị thay đổi trạng thái trước khi hủy (dự kiến {len(cancelled_task_ids)}, thực tế {cur_cancel.rowcount}). Rollback."
                            )

                # 4. INSERT new tasks (INSERT tường minh, không dùng OR REPLACE)
                created_task_ids: List[str] = []
                for prof_name, task_list in plan.profile_assignments.items():
                    for pt in task_list:
                        task_id = f"sched_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
                        del_src = 1 if pt.delete_source_after_post else 0
                        inc_id = f"inc_{uuid.uuid4().hex}"
                        conn.execute("""
                            INSERT INTO scheduled_tasks (
                                id, profile_name, tiktok_account, video_path, video_filename,
                                title, scheduled_time, scheduled_epoch, golden_hour_slot,
                                execution_mode, status, upload_engine, delete_source_after_post,
                                source_deleted, tiktok_item_id, retry_count, max_retries,
                                error_message, created_at, updated_at, completed_at,
                                claim_worker_id, claimed_at, lease_until,
                                version, plan_id, slot_id, last_attempt_id, remote_project_id,
                                published_at, profile_timezone, canonical_video_path,
                                source_size_bytes, source_mtime_ns,
                                incarnation_id, cancelled_by_plan_id
                            ) VALUES (
                                ?, ?, ?, ?, ?,
                                ?, ?, ?, ?,
                                ?, ?, ?, ?,
                                0, '', 0, 2,
                                '', ?, ?, NULL,
                                '', NULL, 0,
                                1, ?, ?, '', '',
                                0, ?, ?,
                                ?, ?,
                                ?, ''
                            )
                        """, (
                            task_id,
                            pt.profile_name,
                            pt.tiktok_account,
                            pt.video_path,
                            pt.video_filename,
                            pt.title,
                            pt.scheduled_time_local,
                            pt.scheduled_epoch,
                            pt.slot_id,
                            pt.execution_mode,
                            "PENDING",
                            "DIRECT_API",
                            del_src,
                            now_str,
                            now_str,
                            plan.plan_id,
                            pt.slot_id,
                            pt.profile_timezone,
                            pt.canonical_video_path,
                            getattr(pt, "source_size_bytes", 0),
                            getattr(pt, "source_mtime_ns", 0),
                            inc_id,
                        ))
                        created_task_ids.append(task_id)

                # 5. Lưu vết vào schedule_plans
                result = ScheduleCommitResult(
                    plan_id=plan.plan_id,
                    created_task_ids=tuple(created_task_ids),
                    cancelled_task_ids=tuple(cancelled_task_ids),
                    already_committed=False,
                    resolution_mode=plan.resolution_mode,
                    committed_at=now_str,
                )
                receipt_json = json.dumps(result.to_dict(), ensure_ascii=False)
                payload_hash = plan.compute_payload_hash()

                conn.execute("""
                    INSERT INTO schedule_plans (
                        plan_id, created_at, seed, resolution_mode, plan_json, status,
                        payload_hash, committed_at, receipt_json
                    ) VALUES (?, ?, ?, ?, ?, 'COMMITTED', ?, ?, ?)
                """, (
                    plan.plan_id,
                    plan.created_at,
                    plan.seed,
                    plan.resolution_mode,
                    json.dumps(plan.to_dict(), ensure_ascii=False),
                    payload_hash,
                    now_str,
                    receipt_json,
                ))

                # COMMIT transaction tường minh
                conn.execute("COMMIT;")

            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
            finally:
                conn.close()

        # F08: Đồng bộ JSON mirror CHỈ SAU KHI SQLite COMMIT thành công
        if self._sync_json_mirror:
            try:
                with _FILE_LOCK:
                    with self._get_connection() as c_mirror:
                        self._sync_legacy_mirror_unlocked(c_mirror)
            except Exception as mirror_err:
                logger.warning(f"Lỗi đồng bộ JSON mirror sau commit: {mirror_err}")

        return result

    def apply_conflict_resolution(
        self,
        resolution_mode: str,  # 'APPEND' | 'OVERWRITE' | 'SKIP'
        new_tasks: List[Dict[str, Any]],
        conflicted_profiles: Sequence[str],
    ) -> List[Dict[str, Any]]:
        """Áp dụng 1 trong 3 giải pháp xử lý xung đột: Nối tiếp, Ghi đè hoặc Bỏ qua."""
        mode = resolution_mode.upper()
        conflict_set = set(conflicted_profiles)

        if mode == "SKIP":
            return [t for t in new_tasks if t.get("profile_name") not in conflict_set]

        if mode == "OVERWRITE":
            with _FILE_LOCK:
                conn = self._get_connection()
                try:
                    now_str = datetime.datetime.now().isoformat()
                    placeholders = ",".join("?" * len(conflict_set))
                    with conn:
                        conn.execute(f"""
                            UPDATE scheduled_tasks
                            SET status = 'CANCELLED', version = version + 1, updated_at = ?
                            WHERE profile_name IN ({placeholders})
                              AND status IN ('PENDING', 'QUEUED')
                        """, [now_str] + list(conflict_set))
                        self._sync_legacy_mirror_unlocked(conn)
                finally:
                    conn.close()
            return new_tasks

        # Chế độ APPEND (Mặc định): Giữ nguyên new_tasks
        return new_tasks

    def recover_missed_tasks(self, max_daily_posts: int = 2) -> List[Dict[str, Any]]:
        """Quét và phục hồi thông minh (Smart Reschedule) các task Local Queue bị lỡ hẹn do tắt máy.
        
        Thay vì dồn toàn bộ vào ngày mai, thuật toán dàn đều các task vào các slot còn trống
        của các ngày kế tiếp, tuân thủ nghiêm ngặt giới hạn max_daily_posts.
        """
        now = datetime.datetime.now()
        now_epoch = int(now.timestamp())
        threshold_epoch = now_epoch - 900  # Quá 15 phút
        recovered: List[Dict[str, Any]] = []

        # Các khung giờ chuẩn mặc định
        golden_time_slots = [
            (8, 30),   # Sáng
            (12, 15),  # Trưa
            (18, 0),   # Chiều
            (20, 30),  # Tối
        ]

        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                # 1. Tìm các task lỡ hẹn
                rows = conn.execute("""
                    SELECT * FROM scheduled_tasks
                    WHERE execution_mode = 'LOCAL_QUEUE'
                      AND status = 'PENDING'
                      AND scheduled_epoch < ?
                    ORDER BY profile_name ASC, scheduled_epoch ASC
                """, (threshold_epoch,)).fetchall()

                if not rows:
                    return []

                from collections import defaultdict
                grouped_missed = defaultdict(list)
                for r in rows:
                    t = self._row_to_dict(r)
                    grouped_missed[t["profile_name"]].append(t)

                with conn:
                    for prof_name, missed_list in grouped_missed.items():
                        # Lấy các task tương lai đã có của profile này để tránh trùng ngày
                        future_rows = conn.execute("""
                            SELECT scheduled_time FROM scheduled_tasks
                            WHERE profile_name = ?
                              AND status IN ('PENDING', 'SERVER_SCHEDULED', 'QUEUED', 'UPLOADING')
                              AND scheduled_epoch >= ?
                        """, (prof_name, threshold_epoch)).fetchall()

                        posts_per_date = defaultdict(int)
                        for fr in future_rows:
                            s_time = fr["scheduled_time"]
                            try:
                                d_key = datetime.datetime.fromisoformat(s_time).date()
                                posts_per_date[d_key] += 1
                            except Exception:
                                pass

                        # Bắt đầu tìm slot từ ngày mai
                        target_day = (now + datetime.timedelta(days=1)).date()

                        for task in missed_list:
                            # Tìm ngày còn chỗ trống (< max_daily_posts)
                            while posts_per_date[target_day] >= max_daily_posts:
                                target_day += datetime.timedelta(days=1)

                            slot_idx = posts_per_date[target_day] % len(golden_time_slots)
                            slot_h, slot_m = golden_time_slots[slot_idx]
                            new_target_dt = datetime.datetime.combine(
                                target_day,
                                datetime.time(hour=slot_h, minute=slot_m),
                            )

                            new_target_str = new_target_dt.isoformat()
                            new_target_epoch = int(new_target_dt.timestamp())
                            err_msg = f"Tự động dời lịch thông minh sang {new_target_dt.strftime('%H:%M %d/%m')} do tắt máy."

                            conn.execute("""
                                UPDATE scheduled_tasks
                                SET scheduled_time = ?,
                                    scheduled_epoch = ?,
                                    status = 'MISSED_RESCHEDULED',
                                    version = version + 1,
                                    error_message = ?,
                                    updated_at = ?
                                WHERE id = ?
                            """, (new_target_str, new_target_epoch, err_msg, now.isoformat(), task["id"]))

                            task["scheduled_time"] = new_target_str
                            task["scheduled_epoch"] = new_target_epoch
                            task["status"] = "MISSED_RESCHEDULED"
                            task["error_message"] = err_msg
                            task["updated_at"] = now.isoformat()
                            recovered.append(task)

                            posts_per_date[target_day] += 1

                    self._sync_legacy_mirror_unlocked(conn)
            finally:
                conn.close()

        return recovered

    def get_summary_stats(self) -> Dict[str, int]:
        """Tổng hợp các chỉ số báo cáo thời gian thực cho Dashboard từ SQLite."""
        stats = {
            "pending": 0,
            "server_scheduled": 0,
            "posted_today": 0,
            "failed": 0,
            "total": 0,
        }

        today_prefix = f"{datetime.date.today().isoformat()}%"

        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                # Tổng số task
                row_total = conn.execute("SELECT COUNT(*) as cnt FROM scheduled_tasks").fetchone()
                stats["total"] = row_total["cnt"] if row_total else 0

                # Chờ đăng
                row_pending = conn.execute("""
                    SELECT COUNT(*) as cnt FROM scheduled_tasks
                    WHERE status IN ('PENDING', 'QUEUED', 'MISSED_RESCHEDULED')
                """).fetchone()
                stats["pending"] = row_pending["cnt"] if row_pending else 0

                # Đã lên lịch server
                row_server = conn.execute("""
                    SELECT COUNT(*) as cnt FROM scheduled_tasks
                    WHERE status = 'SERVER_SCHEDULED'
                """).fetchone()
                stats["server_scheduled"] = row_server["cnt"] if row_server else 0

                # Thất bại
                row_failed = conn.execute("""
                    SELECT COUNT(*) as cnt FROM scheduled_tasks
                    WHERE status = 'FAILED'
                """).fetchone()
                stats["failed"] = row_failed["cnt"] if row_failed else 0

                # Đã đăng hôm nay
                row_posted = conn.execute("""
                    SELECT COUNT(*) as cnt FROM scheduled_tasks
                    WHERE status = 'SUCCESS'
                      AND (completed_at LIKE ? OR updated_at LIKE ?)
                """, (today_prefix, today_prefix)).fetchone()
                stats["posted_today"] = row_posted["cnt"] if row_posted else 0

                return stats
            finally:
                conn.close()
