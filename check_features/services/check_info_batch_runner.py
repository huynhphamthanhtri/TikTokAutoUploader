# -*- coding: utf-8 -*-
"""
check_features.services.check_info_batch_runner - Bộ điều phối Concurrency Pool 5 workers.
Điều phối kiểm tra hàng loạt tài khoản TikTok:
- ThreadPoolExecutor(max_workers=5).
- Run Guard chống race condition.
- Cập nhật nguyên tử config/DB profile và đồng bộ UI qua main thread.
- Thu thập danh sách chi tiết các profile thất bại (failed_items) phục vụ hiển thị pop-up.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from .check_info_service import TikTokCheckInfoService, CheckInfoResult

logger = logging.getLogger("CheckInfoBatchRunner")


class CheckInfoBatchRunner:
    """Bộ điều phối chạy batch Check Thông Tin đa luồng (pool 5 workers)."""

    def __init__(
        self,
        profiles_provider: Callable[[], Dict[str, Any]],
        on_profile_progress: Optional[Callable[[str, str], None]] = None,
        on_profile_done: Optional[Callable[[str, CheckInfoResult], None]] = None,
        on_batch_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        max_workers: int = 5,
    ):
        self.profiles_provider = profiles_provider
        self.on_profile_progress = on_profile_progress
        self.on_profile_done = on_profile_done
        self.on_batch_complete = on_batch_complete
        self.max_workers = min(max(1, max_workers), 5)

        self._is_running = False
        self._cancel_event = threading.Event()
        self._active_runs: Dict[str, str] = {}  # profile_name -> run_id
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._is_running

    def cancel(self) -> None:
        """Hủy đợt kiểm tra hiện tại."""
        self._cancel_event.set()

    def run_batch_async(self, target_profiles: List[str]) -> threading.Thread:
        """Khởi động worker thread chạy batch ngầm không chặn main UI."""
        self._cancel_event.clear()
        self._is_running = True
        worker_thread = threading.Thread(
            target=self._worker_batch_loop,
            args=(list(dict.fromkeys(target_profiles)),),  # Loại trùng
            daemon=True,
        )
        worker_thread.start()
        return worker_thread

    def _worker_batch_loop(self, target_names: List[str]) -> None:
        total = len(target_names)
        results: Dict[str, CheckInfoResult] = {}
        batch_id = str(uuid.uuid4())[:8]

        stats_summary: Dict[str, Any] = {
            "total": total,
            "success": 0,
            "partial": 0,
            "expired": 0,
            "not_logged_in": 0,
            "proxy_error": 0,
            "error": 0,
            "cancelled": 0,
            "failed_items": [],  # List[Dict[str, Any]]
        }

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_name = {}
            for name in target_names:
                if self._cancel_event.is_set():
                    break
                run_id = f"{batch_id}_{name}_{int(time.time())}"
                with self._lock:
                    self._active_runs[name] = run_id

                if self.on_profile_progress:
                    try:
                        self.on_profile_progress(name, "checking")
                    except Exception as e:
                        logger.debug(f"Progress callback error: {e}")

                future = executor.submit(self._execute_single_profile, name, run_id)
                future_to_name[future] = name

            for future in as_completed(future_to_name):
                name = future_to_name[future]
                if self._cancel_event.is_set():
                    stats_summary["cancelled"] += 1
                    continue

                try:
                    res: CheckInfoResult = future.result()
                    results[name] = res

                    # Phân loại thống kê chi tiết
                    if res.status == "SUCCESS":
                        stats_summary["success"] += 1
                    elif res.status == "PARTIAL_SUCCESS":
                        stats_summary["partial"] += 1
                    elif res.status == "NOT_LOGGED_IN" or getattr(res, "auth_state", "") == "NOT_LOGGED_IN":
                        stats_summary["not_logged_in"] += 1
                        stats_summary["failed_items"].append({
                            "profile": name,
                            "status": "NOT_LOGGED_IN",
                            "auth_state": "NOT_LOGGED_IN",
                            "status_badge": "⚪ Chưa đăng nhập",
                            "error": res.error or "Chưa đăng nhập TikTok (Thiếu cookie phiên sessionid).",
                            "suggested_action": "Mở profile và đăng nhập TikTok trên trình duyệt.",
                        })
                    elif res.status in ("SESSION_INVALID", "COOKIE_EXPIRED") or getattr(res, "auth_state", "") == "EXPIRED":
                        stats_summary["expired"] += 1
                        stats_summary["failed_items"].append({
                            "profile": name,
                            "status": "SESSION_INVALID",
                            "auth_state": "EXPIRED",
                            "status_badge": "🟠 Cần đăng nhập lại",
                            "error": res.error or "Phiên đăng nhập không còn hợp lệ trên TikTok (Mã 20001).",
                            "suggested_action": "Đăng nhập lại tài khoản trên trình duyệt.",
                        })
                    elif res.status == "PROXY_ERROR" or getattr(res, "error_code", "") == "PROXY_ERROR":
                        stats_summary["proxy_error"] += 1
                        stats_summary["failed_items"].append({
                            "profile": name,
                            "status": "PROXY_ERROR",
                            "auth_state": "UNKNOWN",
                            "status_badge": "🔴 Lỗi Proxy",
                            "error": res.error or "Không thể kết nối qua proxy của profile.",
                            "suggested_action": "Kiểm tra lại cấu hình Proxy (IP, Port, User, Pass).",
                        })
                    else:
                        stats_summary["error"] += 1
                        stats_summary["failed_items"].append({
                            "profile": name,
                            "status": res.status,
                            "auth_state": getattr(res, "auth_state", "UNKNOWN"),
                            "status_badge": "⚠️ Lỗi kiểm tra",
                            "error": res.error or "Lỗi kết nối hoặc xử lý thông tin.",
                            "suggested_action": "Kiểm tra lại đường truyền mạng hoặc thử lại sau.",
                        })

                    # Bắn sự kiện hoàn thành profile
                    if self.on_profile_done:
                        try:
                            self.on_profile_done(name, res)
                        except Exception as e:
                            logger.debug(f"Done callback error: {e}")

                except Exception as e:
                    logger.error(f"Lỗi không xác định khi check profile {name}: {e}")
                    stats_summary["error"] += 1
                    stats_summary["failed_items"].append({
                        "profile": name,
                        "status": "ERROR",
                        "auth_state": "UNKNOWN",
                        "status_badge": "⚠️ Lỗi ngoại lệ",
                        "error": str(e),
                        "suggested_action": "Thử lại sau.",
                    })

        self._is_running = False
        if self.on_batch_complete:
            try:
                self.on_batch_complete(stats_summary)
            except Exception as e:
                logger.debug(f"Batch complete callback error: {e}")

    def _execute_single_profile(self, profile_name: str, run_id: str) -> CheckInfoResult:
        """Xử lý kiểm tra cho 1 profile cụ thể."""
        all_profiles = self.profiles_provider() or {}
        prof_data = all_profiles.get(profile_name) or {}
        cfg = prof_data.get("config") or prof_data

        service = TikTokCheckInfoService(profile_name, cfg)
        res = service.execute()

        # Run guard: Kiểm tra xem run_id có còn hiệu lực không
        with self._lock:
            current_run = self._active_runs.get(profile_name)
            if current_run != run_id:
                logger.warning(f"Run Guard: Bỏ qua kết quả của {profile_name} do run_id lỗi thời.")
                return res

        # Cập nhật nguyên tử vào config profile
        self._apply_result_to_profile(profile_name, cfg, res)
        return res

    def _apply_result_to_profile(self, profile_name: str, cfg: Dict[str, Any], res: CheckInfoResult) -> None:
        """Cập nhật dữ liệu vào config của profile (Không ghi ô nhiễm user_agent vào root config)."""
        if res.cookie_header:
            cfg["cookie_str"] = res.cookie_header

        # Lưu thông tin chẩn đoán
        cfg["last_check_status"] = res.status
        cfg["last_check_time"] = res.checked_at
        if res.error:
            cfg["last_check_error"] = res.error

        if res.success and res.user:
            new_uid = str(res.user.get("uid") or "")
            old_uid = str(cfg.get("tiktok_user_id") or "")

            # Nếu UID thay đổi, reset stats cũ
            if old_uid and new_uid and old_uid != new_uid:
                logger.info(f"Phát hiện profile {profile_name} đổi UID từ {old_uid} sang {new_uid}. Reset stats cũ.")
                cfg.pop("tiktok_account_stats", None)

            cfg["tiktok_user_id"] = new_uid
            cfg["tiktok_id"] = res.user.get("uniqueId") or cfg.get("tiktok_id")
            cfg["tiktok_account"] = res.user.get("uniqueId") or cfg.get("tiktok_account")
            cfg["nickname"] = res.user.get("nickName") or cfg.get("nickname")
            cfg["avatar_url"] = res.user.get("avatarUri") or cfg.get("avatar_url")
            cfg["region"] = res.user.get("region") or cfg.get("region")
            if res.user.get("secUid"):
                cfg["sec_uid"] = res.user["secUid"]

            cfg["session_auth_state"] = "verified"

            # Lưu cấu trúc tiktok_account chuẩn
            cfg["tiktok_account_data"] = res.to_tiktok_account_dict()
            cfg["tiktok_account_last_checked"] = res.checked_at

        elif getattr(res, "auth_state", "") == "EXPIRED" or res.status in ("SESSION_INVALID", "COOKIE_EXPIRED"):
            # CHỈ khi có bằng chứng xác nhận phiên hết hạn từ TikTok
            cfg["session_auth_state"] = "expired"
        elif getattr(res, "auth_state", "") == "NOT_LOGGED_IN" or res.status == "NOT_LOGGED_IN":
            # Profile chưa từng login hoặc bị mất sessionid
            cfg["session_auth_state"] = "not_logged_in"
        else:
            # Lỗi mạng, proxy, timeout, parse: GIỮ NGUYÊN session_auth_state trước đó
            logger.info(f"Profile {profile_name} kiểm tra không hoàn tất ({res.status}): Giữ nguyên session_auth_state.")
