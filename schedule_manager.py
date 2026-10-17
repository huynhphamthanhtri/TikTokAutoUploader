"""
schedule_manager.py - Quản lý dữ liệu lưu trữ, hàng đợi và vòng đời lịch đăng TikTok.

Chịu trách nhiệm:
1. Đọc/ghi dữ liệu an toàn ra file data/scheduled_posts.json (Atomic Write + Mutex Lock).
2. Quản lý trạng thái: PENDING, SERVER_SCHEDULED, QUEUED, UPLOADING, SUCCESS, FAILED, MISSED_RESCHEDULED, CANCELLED.
3. Bộ phát hiện và giải quyết xung đột lịch (Conflict Guard: APPEND, OVERWRITE, SKIP).
4. Bộ quét phục hồi tự động khi mở máy phát hiện bài bị lỡ giờ do tắt app.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
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
DEFAULT_SCHEDULE_FILE = DEFAULT_DATA_DIR / "scheduled_posts.json"

_FILE_LOCK = threading.Lock()


class ScheduleManager:
    """Quản lý tập trung các tác vụ lên lịch đăng TikTok với lưu trữ JSON bền vững."""

    def __init__(self, storage_file: Optional[Path | str] = None):
        self.storage_file = Path(storage_file or DEFAULT_SCHEDULE_FILE)
        self._ensure_storage_ready()

    def _ensure_storage_ready(self) -> None:
        """Đảm bảo thư mục và file JSON tồn tại."""
        try:
            self.storage_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.storage_file.exists():
                self._atomic_write_unlocked([])
        except Exception as e:
            logger.error(f"Lỗi khởi tạo storage lịch đăng: {e}")

    def _read_data_unlocked(self) -> List[Dict[str, Any]]:
        """Đọc danh sách task từ file JSON (chưa khóa mutex)."""
        if not self.storage_file.exists():
            return []
        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                data = json.loads(content)
                if isinstance(data, list):
                    return data
                return []
        except Exception as e:
            logger.error(f"Lỗi đọc file JSON lịch đăng: {e}")
            return []

    def _atomic_write_unlocked(self, data: List[Dict[str, Any]]) -> bool:
        """Ghi dữ liệu ra file tạm .tmp rồi đổi tên atomic để chống hỏng file."""
        tmp_file = self.storage_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            tmp_file.replace(self.storage_file)
            return True
        except Exception as e:
            logger.error(f"Lỗi ghi atomic file JSON lịch đăng: {e}")
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass
            return False

    def add_tasks(self, tasks_data: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Thêm danh sách task mới vào hàng đợi, tự động sinh ID và timestamp."""
        created_tasks: List[Dict[str, Any]] = []
        now_str = datetime.datetime.now().isoformat()

        with _FILE_LOCK:
            existing = self._read_data_unlocked()
            for item in tasks_data:
                task_id = item.get("id") or f"sched_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
                sched_dt_str = item.get("scheduled_time") or now_str
                try:
                    sched_epoch = int(datetime.datetime.fromisoformat(sched_dt_str).timestamp())
                except Exception:
                    sched_epoch = int(datetime.datetime.now().timestamp())

                task = {
                    "id": task_id,
                    "profile_name": str(item.get("profile_name") or "").strip(),
                    "tiktok_account": str(item.get("tiktok_account") or "").strip(),
                    "video_path": str(item.get("video_path") or "").strip(),
                    "video_filename": str(item.get("video_filename") or Path(str(item.get("video_path") or "")).name).strip(),
                    "title": str(item.get("title") or "").strip(),
                    "scheduled_time": sched_dt_str,
                    "scheduled_epoch": sched_epoch,
                    "golden_hour_slot": str(item.get("golden_hour_slot") or "TRUA"),
                    "execution_mode": str(item.get("execution_mode") or "SERVER_SCHEDULE"),  # SERVER_SCHEDULE | LOCAL_QUEUE
                    "status": str(item.get("status") or "PENDING"),  # PENDING | SERVER_SCHEDULED | QUEUED | UPLOADING | SUCCESS | FAILED | CANCELLED
                    "upload_engine": str(item.get("upload_engine") or "DIRECT_API"),
                    "delete_source_after_post": bool(item.get("delete_source_after_post", True)),
                    "source_deleted": bool(item.get("source_deleted", False)),
                    "tiktok_item_id": str(item.get("tiktok_item_id") or ""),
                    "retry_count": int(item.get("retry_count") or 0),
                    "max_retries": int(item.get("max_retries") or 2),
                    "error_message": str(item.get("error_message") or ""),
                    "created_at": str(item.get("created_at") or now_str),
                    "updated_at": now_str,
                    "completed_at": item.get("completed_at"),
                }
                existing.append(task)
                created_tasks.append(task)

            self._atomic_write_unlocked(existing)

        return created_tasks

    def get_all_tasks(
        self,
        filter_status: Optional[str] = None,
        filter_profile: Optional[str] = None,
        filter_mode: Optional[str] = None,
        search_query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Lấy toàn bộ danh sách task kèm các bộ lọc tùy chọn."""
        with _FILE_LOCK:
            tasks = self._read_data_unlocked()

        result: List[Dict[str, Any]] = []
        q = (search_query or "").strip().casefold()
        raw_status = (filter_status or "").strip()
        raw_profile = (filter_profile or "").strip()
        raw_mode = (filter_mode or "").strip()

        all_wildcards = {"", "all", "tất cả", "tat ca", "tất cả trạng thái", "tất cả profile", "tất cả chế độ"}

        status_active = raw_status.casefold() not in all_wildcards
        profile_active = raw_profile.casefold() not in all_wildcards
        mode_active = raw_mode.casefold() not in all_wildcards

        for t in tasks:
            if status_active:
                if t.get("status", "").upper() != raw_status.upper():
                    continue
            if profile_active:
                if t.get("profile_name") != raw_profile:
                    continue
            if mode_active:
                t_mode = t.get("execution_mode", "").upper()
                if "TIKTOK" in raw_mode.upper() or "SERVER" in raw_mode.upper():
                    if t_mode != "SERVER_SCHEDULE":
                        continue
                elif "MÁY" in raw_mode.upper() or "LOCAL" in raw_mode.upper() or "QUEUE" in raw_mode.upper():
                    if t_mode != "LOCAL_QUEUE":
                        continue
                elif t_mode != raw_mode.upper():
                    continue
            if q:
                match = (
                    q in t.get("profile_name", "").casefold() or
                    q in t.get("tiktok_account", "").casefold() or
                    q in t.get("video_filename", "").casefold() or
                    q in t.get("title", "").casefold() or
                    q in t.get("id", "").casefold()
                )
                if not match:
                    continue
            result.append(t)

        # Sắp xếp theo mốc thời gian dự kiến tăng dần
        return sorted(result, key=lambda x: str(x.get("scheduled_time", "")))

    def get_task_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Tìm task theo ID duy nhất."""
        with _FILE_LOCK:
            tasks = self._read_data_unlocked()
            for t in tasks:
                if t.get("id") == task_id:
                    return dict(t)
        return None

    def update_task(self, task_id: str, updates: Dict[str, Any]) -> bool:
        """Cập nhật thông tin và trạng thái của một task."""
        with _FILE_LOCK:
            tasks = self._read_data_unlocked()
            found = False
            for t in tasks:
                if t.get("id") == task_id:
                    t.update(updates)
                    t["updated_at"] = datetime.datetime.now().isoformat()
                    found = True
                    break
            if found:
                return self._atomic_write_unlocked(tasks)
        return False

    def cancel_tasks(self, task_ids: Sequence[str]) -> int:
        """Hủy các task chưa thực thi (chuyển sang CANCELLED)."""
        target_ids = set(task_ids)
        if not target_ids:
            return 0

        cancelled_count = 0
        now_str = datetime.datetime.now().isoformat()

        with _FILE_LOCK:
            tasks = self._read_data_unlocked()
            for t in tasks:
                if t.get("id") in target_ids and t.get("status") in ("PENDING", "QUEUED"):
                    t["status"] = "CANCELLED"
                    t["updated_at"] = now_str
                    cancelled_count += 1
            if cancelled_count > 0:
                self._atomic_write_unlocked(tasks)

        return cancelled_count

    def retry_tasks(self, task_ids: Sequence[str]) -> int:
        """Đặt lại trạng thái PENDING cho các task bị lỗi để thử lại."""
        target_ids = set(task_ids)
        if not target_ids:
            return 0

        retried_count = 0
        now_str = datetime.datetime.now().isoformat()

        with _FILE_LOCK:
            tasks = self._read_data_unlocked()
            for t in tasks:
                if t.get("id") in target_ids and t.get("status") in ("FAILED", "CANCELLED"):
                    t["status"] = "PENDING"
                    t["error_message"] = ""
                    t["retry_count"] = 0
                    t["updated_at"] = now_str
                    retried_count += 1
            if retried_count > 0:
                self._atomic_write_unlocked(tasks)

        return retried_count

    def delete_tasks(self, task_ids: Sequence[str]) -> int:
        """Xóa vĩnh viễn danh sách task khỏi file JSON."""
        target_ids = set(task_ids)
        if not target_ids:
            return 0

        with _FILE_LOCK:
            tasks = self._read_data_unlocked()
            initial_len = len(tasks)
            remaining = [t for t in tasks if t.get("id") not in target_ids]
            deleted_count = initial_len - len(remaining)
            if deleted_count > 0:
                self._atomic_write_unlocked(remaining)

        return deleted_count

    def clear_finished_tasks(self) -> int:
        """Dọn dẹp toàn bộ các task đã hoàn thành (SUCCESS) hoặc đã hủy (CANCELLED)."""
        with _FILE_LOCK:
            tasks = self._read_data_unlocked()
            initial_len = len(tasks)
            remaining = [t for t in tasks if t.get("status") not in ("SUCCESS", "CANCELLED")]
            cleared = initial_len - len(remaining)
            if cleared > 0:
                self._atomic_write_unlocked(remaining)
        return cleared

    # =========================================================================
    # CONFLICT GUARD & RECOVERY LOGIC
    # =========================================================================

    def check_profile_conflicts(self, profile_names: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """Quét và trả về danh sách các profile đang có bài chờ đăng.
        
        Trả về dict:
        {
            'Profile_A': {
                'pending_count': 3,
                'latest_scheduled_time': datetime,
                'tasks': [...]
            }
        }
        """
        targets = set(str(p).strip() for p in profile_names if str(p).strip())
        if not targets:
            return {}

        with _FILE_LOCK:
            tasks = self._read_data_unlocked()

        conflicts: Dict[str, Dict[str, Any]] = {}
        for t in tasks:
            prof = t.get("profile_name")
            status = t.get("status")
            if prof in targets and status in ("PENDING", "SERVER_SCHEDULED", "QUEUED"):
                info = conflicts.setdefault(prof, {
                    "pending_count": 0,
                    "latest_scheduled_time": None,
                    "tasks": [],
                })
                info["pending_count"] += 1
                info["tasks"].append(t)

                sched_str = t.get("scheduled_time")
                if sched_str:
                    try:
                        dt = datetime.datetime.fromisoformat(sched_str)
                        if info["latest_scheduled_time"] is None or dt > info["latest_scheduled_time"]:
                            info["latest_scheduled_time"] = dt
                    except Exception:
                        pass

        return conflicts

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
            # Bỏ qua các profile đang có lịch
            return [t for t in new_tasks if t.get("profile_name") not in conflict_set]

        if mode == "OVERWRITE":
            # Hủy toàn bộ task cũ của các profile bị xung đột
            with _FILE_LOCK:
                tasks = self._read_data_unlocked()
                now_str = datetime.datetime.now().isoformat()
                for t in tasks:
                    if t.get("profile_name") in conflict_set and t.get("status") in ("PENDING", "QUEUED"):
                        t["status"] = "CANCELLED"
                        t["updated_at"] = now_str
                self._atomic_write_unlocked(tasks)
            return new_tasks

        # Chế độ APPEND (Mặc định): Giữ nguyên new_tasks (đã được tính toán nối tiếp mốc thời gian từ calculator)
        return new_tasks

    def recover_missed_tasks(self) -> List[Dict[str, Any]]:
        """Quét và tự động dời các task Local Queue bị lỡ hẹn do tắt máy sang ngày hôm sau."""
        now = datetime.datetime.now()
        threshold = now - datetime.timedelta(minutes=15)
        recovered: List[Dict[str, Any]] = []

        with _FILE_LOCK:
            tasks = self._read_data_unlocked()
            modified = False
            for t in tasks:
                if t.get("execution_mode") == "LOCAL_QUEUE" and t.get("status") == "PENDING":
                    sched_str = t.get("scheduled_time")
                    if not sched_str:
                        continue
                    try:
                        sched_dt = datetime.datetime.fromisoformat(sched_str)
                    except Exception:
                        continue

                    # Nếu thời gian hẹn nhỏ hơn thời điểm hiện tại trừ 15 phút -> Bị lỡ hẹn do tắt máy
                    if sched_dt < threshold:
                        # Tự động dời sang ngày tiếp theo
                        tomorrow_dt = sched_dt + datetime.timedelta(days=1)
                        if tomorrow_dt < now:
                            tomorrow_dt = now + datetime.timedelta(days=1)

                        t["scheduled_time"] = tomorrow_dt.isoformat()
                        t["scheduled_epoch"] = int(tomorrow_dt.timestamp())
                        t["status"] = "MISSED_RESCHEDULED"
                        t["updated_at"] = now.isoformat()
                        t["error_message"] = f"Tự động dời lịch từ {sched_dt.strftime('%H:%M %d/%m')} do tắt máy tính."
                        recovered.append(t)
                        modified = True

            if modified:
                self._atomic_write_unlocked(tasks)

        return recovered

    def get_summary_stats(self) -> Dict[str, int]:
        """Tổng hợp các chỉ số báo cáo thời gian thực cho Dashboard."""
        with _FILE_LOCK:
            tasks = self._read_data_unlocked()

        today_str = datetime.date.today().isoformat()
        stats = {
            "pending": 0,
            "server_scheduled": 0,
            "posted_today": 0,
            "failed": 0,
            "total": len(tasks),
        }

        for t in tasks:
            st = t.get("status")
            if st in ("PENDING", "QUEUED", "MISSED_RESCHEDULED"):
                stats["pending"] += 1
            elif st == "SERVER_SCHEDULED":
                stats["server_scheduled"] += 1
            elif st == "FAILED":
                stats["failed"] += 1

            if st == "SUCCESS":
                completed_at = t.get("completed_at") or t.get("updated_at") or ""
                if str(completed_at).startswith(today_str):
                    stats["posted_today"] += 1

        return stats
