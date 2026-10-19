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

import datetime
import json
import logging
import os
import sqlite3
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

def _get_app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

DEFAULT_DATA_DIR = _get_app_base_dir() / "data"
DEFAULT_DB_FILE = DEFAULT_DATA_DIR / "scheduled_posts.db"
DEFAULT_LEGACY_JSON_FILE = DEFAULT_DATA_DIR / "scheduled_posts.json"

_FILE_LOCK = threading.Lock()


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
        """Khởi tạo bảng SQLite, index và tự động chuyển đổi dữ liệu từ JSON nếu có."""
        try:
            self.db_file.parent.mkdir(parents=True, exist_ok=True)
            with _FILE_LOCK:
                conn = self._get_connection()
                try:
                    with conn:
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
                                lease_until INTEGER DEFAULT 0
                            );
                        """)
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_status_epoch ON scheduled_tasks(status, execution_mode, scheduled_epoch);")
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_profile_status ON scheduled_tasks(profile_name, status);")
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_sched_video_path ON scheduled_tasks(video_path);")

                    # Kiểm tra và migrate dữ liệu từ JSON cũ nếu DB mới tinh
                    row = conn.execute("SELECT COUNT(*) as count FROM scheduled_tasks").fetchone()
                    count = row["count"] if row else 0
                    if count == 0 and self.legacy_json_file.exists():
                        self._migrate_from_json_unlocked(conn)
                    elif self._sync_json_mirror and not self.legacy_json_file.exists():
                        self._sync_legacy_mirror_unlocked(conn)
                finally:
                    conn.close()
        except Exception as e:
            logger.error(f"Lỗi khởi tạo storage SQLite lịch đăng: {e}")

    def _migrate_from_json_unlocked(self, conn: sqlite3.Connection) -> None:
        """Chuyển đổi dữ liệu từ file JSON sang SQLite an toàn."""
        try:
            with open(self.legacy_json_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return
                data = json.loads(content)
                if not isinstance(data, list):
                    return

            migrated_count = 0
            with conn:
                for item in data:
                    t_id = item.get("id")
                    if not t_id:
                        continue
                    sched_time = str(item.get("scheduled_time") or datetime.datetime.now().isoformat())
                    try:
                        sched_epoch = int(datetime.datetime.fromisoformat(sched_time).timestamp())
                    except Exception:
                        sched_epoch = int(item.get("scheduled_epoch") or datetime.datetime.now().timestamp())

                    conn.execute("""
                        INSERT OR REPLACE INTO scheduled_tasks (
                            id, profile_name, tiktok_account, video_path, video_filename,
                            title, scheduled_time, scheduled_epoch, golden_hour_slot,
                            execution_mode, status, upload_engine, delete_source_after_post,
                            source_deleted, tiktok_item_id, retry_count, max_retries,
                            error_message, created_at, updated_at, completed_at,
                            claim_worker_id, claimed_at, lease_until
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            else:
                self._sync_legacy_mirror_unlocked(conn)
        except Exception as e:
            logger.error(f"Lỗi migrate dữ liệu từ JSON sang SQLite: {e}")

    def _sync_legacy_mirror_unlocked(self, conn: sqlite3.Connection) -> None:
        """Ghi đồng bộ file JSON nếu caller/test khởi tạo ScheduleManager với đường dẫn .json."""
        if not self._sync_json_mirror:
            return
        try:
            rows = conn.execute("SELECT * FROM scheduled_tasks ORDER BY scheduled_time ASC").fetchall()
            tasks = [self._row_to_dict(r) for r in rows]
            tmp_file = self.legacy_json_file.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp_file.replace(self.legacy_json_file)
        except Exception as e:
            logger.debug(f"Không thể đồng bộ file JSON mirror: {e}")

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

                        conn.execute("""
                            INSERT OR REPLACE INTO scheduled_tasks (
                                id, profile_name, tiktok_account, video_path, video_filename,
                                title, scheduled_time, scheduled_epoch, golden_hour_slot,
                                execution_mode, status, upload_engine, delete_source_after_post,
                                source_deleted, tiktok_item_id, retry_count, max_retries,
                                error_message, created_at, updated_at, completed_at,
                                claim_worker_id, claimed_at, lease_until
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, 0)
                        """, (
                            task_id, prof_name, account, v_path, v_filename,
                            title, sched_dt_str, sched_epoch, slot,
                            exec_mode, status, engine, del_src,
                            src_del, item_id, retry_cnt, max_ret,
                            err_msg, c_at, u_at, comp_at
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
    ) -> List[Dict[str, Any]]:
        """Lấy toàn bộ danh sách task kèm các bộ lọc tùy chọn từ SQLite."""
        conditions: List[str] = []
        params: List[Any] = []

        all_wildcards = {"", "all", "tất cả", "tat ca", "tất cả trạng thái", "tất cả profile", "tất cả chế độ"}
        raw_status = (filter_status or "").strip()
        raw_profile = (filter_profile or "").strip()
        raw_mode = (filter_mode or "").strip()
        q = (search_query or "").strip()

        if raw_status and raw_status.casefold() not in all_wildcards:
            conditions.append("UPPER(status) = ?")
            params.append(raw_status.upper())

        if raw_profile and raw_profile.casefold() not in all_wildcards:
            conditions.append("profile_name = ?")
            params.append(raw_profile)

        if raw_mode and raw_mode.casefold() not in all_wildcards:
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
        query = f"SELECT * FROM scheduled_tasks {where_clause} ORDER BY scheduled_time ASC"

        with _FILE_LOCK:
            conn = self._get_connection()
            try:
                rows = conn.execute(query, params).fetchall()
                return [self._row_to_dict(r) for r in rows]
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

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> bool:
        """Cập nhật thông tin và trạng thái của một task."""
        if not updates:
            return False

        allowed_fields = {
            "profile_name", "tiktok_account", "video_path", "video_filename", "title",
            "scheduled_time", "scheduled_epoch", "golden_hour_slot", "execution_mode",
            "status", "upload_engine", "delete_source_after_post", "source_deleted",
            "tiktok_item_id", "retry_count", "max_retries", "error_message",
            "completed_at", "claim_worker_id", "claimed_at", "lease_until"
        }

        set_clauses: List[str] = []
        params: List[Any] = []

        for k, v in updates.items():
            if k in allowed_fields:
                set_clauses.append(f"{k} = ?")
                if isinstance(v, bool):
                    params.append(1 if v else 0)
                else:
                    params.append(v)

        if not set_clauses:
            return False

        now_str = datetime.datetime.now().isoformat()
        set_clauses.append("updated_at = ?")
        params.append(now_str)
        params.append(task_id)

        query = f"UPDATE scheduled_tasks SET {', '.join(set_clauses)} WHERE id = ?"

        with _FILE_LOCK:
            conn = self._get_connection()
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
        """Hủy các task chưa thực thi (chuyển sang CANCELLED)."""
        target_ids = list(set(task_ids))
        if not target_ids:
            return 0

        now_str = datetime.datetime.now().isoformat()
        placeholders = ",".join("?" * len(target_ids))
        query = f"""
            UPDATE scheduled_tasks
            SET status = 'CANCELLED', updated_at = ?
            WHERE id IN ({placeholders}) AND status IN ('PENDING', 'QUEUED')
        """

        with _FILE_LOCK:
            conn = self._get_connection()
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
        """Đặt lại trạng thái PENDING cho các task bị lỗi để thử lại."""
        target_ids = list(set(task_ids))
        if not target_ids:
            return 0

        now_str = datetime.datetime.now().isoformat()
        placeholders = ",".join("?" * len(target_ids))
        query = f"""
            UPDATE scheduled_tasks
            SET status = 'PENDING', error_message = '', retry_count = 0,
                claim_worker_id = '', claimed_at = NULL, lease_until = 0,
                updated_at = ?
            WHERE id IN ({placeholders}) AND status IN ('FAILED', 'CANCELLED')
        """

        with _FILE_LOCK:
            conn = self._get_connection()
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
            try:
                with conn:
                    cur = conn.execute("""
                        UPDATE scheduled_tasks
                        SET status = 'UPLOADING',
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
                    for tid in task_ids:
                        cur = conn.execute("""
                            UPDATE scheduled_tasks
                            SET status = 'UPLOADING',
                                claim_worker_id = ?,
                                claimed_at = ?,
                                lease_until = ?,
                                updated_at = ?
                            WHERE id = ?
                              AND (
                                  status IN ('PENDING', 'QUEUED')
                                  OR (status = 'UPLOADING' AND lease_until < ?)
                              )
                        """, (worker_id, now_str, lease_until, now_str, tid, now_epoch))

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
                            SET status = 'CANCELLED', updated_at = ?
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
