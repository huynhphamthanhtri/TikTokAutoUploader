"""Module quản lý danh sách YouTube Data API Keys (Multi-Key Pool).

Hỗ trợ nạp nhiều API key, tự động xoay tua thông minh, phân loại lỗi chính xác
(hết quota ngày -> tạm ngưng đến 00:00 UTC; rate limit -> cooldown; lỗi 5xx/timeout -> retry).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


def _next_midnight_utc() -> float:
    """Trả về timestamp của 00:00 UTC ngày tiếp theo (thời điểm Google reset quota)."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.timestamp()


@dataclass
class ApiKeyInfo:
    key: str
    status: str = "ACTIVE"  # "ACTIVE", "QUOTA_EXCEEDED", "RATE_LIMITED", "INVALID", "DISABLED"
    last_used_at: float = 0.0
    retry_after: float = 0.0
    quota_resets_at: float = 0.0
    consecutive_errors: int = 0
    last_error_message: str = ""

    def is_usable(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        if self.status == "ACTIVE":
            return True
        if self.status == "QUOTA_EXCEEDED":
            if self.quota_resets_at and now >= self.quota_resets_at:
                self.status = "ACTIVE"
                self.quota_resets_at = 0.0
                self.consecutive_errors = 0
                self.last_error_message = ""
                return True
            return False
        if self.status == "RATE_LIMITED":
            if self.retry_after and now >= self.retry_after:
                self.status = "ACTIVE"
                self.retry_after = 0.0
                self.consecutive_errors = 0
                return True
            return False
        return False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApiKeyInfo":
        return cls(
            key=str(data.get("key") or "").strip(),
            status=str(data.get("status") or "ACTIVE"),
            last_used_at=float(data.get("last_used_at") or 0.0),
            retry_after=float(data.get("retry_after") or 0.0),
            quota_resets_at=float(data.get("quota_resets_at") or 0.0),
            consecutive_errors=int(data.get("consecutive_errors") or 0),
            last_error_message=str(data.get("last_error_message") or ""),
        )


class ApiKeyManager:
    """Quản lý thread-safe pool các API Key của YouTube."""

    def __init__(self, keys: Optional[List[str]] = None) -> None:
        self._lock = threading.RLock()
        self._keys_map: Dict[str, ApiKeyInfo] = {}
        self._rr_index: int = 0
        self._client_cache: Dict[str, Any] = {}
        if keys:
            for k in keys:
                self.add_key(k)

    def add_key(self, key: str) -> bool:
        key = str(key or "").strip()
        if not key:
            return False
        with self._lock:
            if key not in self._keys_map:
                self._keys_map[key] = ApiKeyInfo(key=key)
                return True
            return False

    def remove_key(self, key: str) -> bool:
        key = str(key or "").strip()
        with self._lock:
            self._client_cache.pop(key, None)
            if key in self._keys_map:
                del self._keys_map[key]
                return True
            return False

    def get_keys(self) -> List[str]:
        with self._lock:
            return list(self._keys_map.keys())

    def get_all_status(self) -> List[Dict[str, Any]]:
        with self._lock:
            now = time.time()
            res = []
            for info in self._keys_map.values():
                info.is_usable(now)
                res.append(info.to_dict())
            return res

    def get_active_key(self) -> Optional[str]:
        with self._lock:
            if not self._keys_map:
                return None
            now = time.time()
            keys_list = list(self._keys_map.values())
            n = len(keys_list)

            # Round-robin scan to find next usable key
            for step in range(n):
                idx = (self._rr_index + step) % n
                candidate = keys_list[idx]
                if candidate.is_usable(now):
                    self._rr_index = (idx + 1) % n
                    candidate.last_used_at = now
                    return candidate.key

            return None

    def get_youtube_client(self, api_key: Optional[str] = None):
        chosen_key = str(api_key or "").strip()
        if not chosen_key:
            chosen_key = self.get_active_key() or ""
        if not chosen_key:
            raise ValueError("Không có YouTube API key khả dụng (hết quota, rate limited hoặc chưa nhập)")

        with self._lock:
            client = self._client_cache.get(chosen_key)
            if client is not None:
                return client

        client = build("youtube", "v3", developerKey=chosen_key, cache_discovery=False)
        with self._lock:
            self._client_cache[chosen_key] = client
            return client

    def handle_api_error(self, key: str, exc: Exception) -> str:
        """Phân tích lỗi từ Google API và cập nhật trạng thái của key."""
        key = str(key or "").strip()
        with self._lock:
            info = self._keys_map.get(key)
            if not info:
                return "Key không tồn tại trong pool"

            now = time.time()
            info.consecutive_errors += 1

            if isinstance(exc, HttpError):
                status_code = exc.resp.status if hasattr(exc, "resp") else 0
                reason = "unknown"
                message = str(exc)
                try:
                    data = json.loads(exc.content.decode("utf-8"))
                    err = data.get("error", {})
                    errors_list = err.get("errors", [{}])
                    if errors_list:
                        reason = errors_list[0].get("reason", "unknown")
                        message = errors_list[0].get("message", message)
                except Exception:
                    pass

                info.last_error_message = f"[{status_code} {reason}] {message}"[:200]

                # 1. Hết Quota ngày
                if status_code == 403 and reason in ("quotaExceeded", "dailyLimitExceeded"):
                    info.status = "QUOTA_EXCEEDED"
                    info.quota_resets_at = _next_midnight_utc()
                    logger.warning(f"[ApiKeyManager] Key ...{key[-6:]} hết quota ngày, tạm ngưng đến 00:00 UTC")
                    return "QUOTA_EXCEEDED"

                # 2. Rate Limit ngắn hạn
                if status_code in (403, 429) and reason in ("rateLimitExceeded", "userRateLimitExceeded"):
                    info.status = "RATE_LIMITED"
                    info.retry_after = now + 60.0  # cooldown 1 phút
                    logger.warning(f"[ApiKeyManager] Key ...{key[-6:]} bị rate-limit, cooldown 60s")
                    return "RATE_LIMITED"

                # 3. Key không hợp lệ
                if status_code == 400 and reason in ("keyInvalid", "badRequest"):
                    info.status = "INVALID"
                    logger.error(f"[ApiKeyManager] Key ...{key[-6:]} không hợp lệ hoặc đã bị xoá")
                    return "INVALID"

                # 4. API chưa được bật trên Google Cloud
                if status_code == 403 and reason in ("accessNotConfigured",):
                    info.status = "DISABLED"
                    logger.error(f"[ApiKeyManager] Project của key ...{key[-6:]} chưa bật YouTube Data API v3")
                    return "DISABLED"

                # 5. Server error từ Google (500, 502, 503, 504) -> Giữ nguyên key, không xoay vô tội vạ
                if 500 <= status_code < 600:
                    logger.warning(f"[ApiKeyManager] Google 5xx ({status_code}) trên key ...{key[-6:]}, giữ key để retry")
                    return "SERVER_ERROR"

            # Lỗi mạng / timeout chung
            info.last_error_message = str(exc)[:200]
            logger.warning(f"[ApiKeyManager] Lỗi mạng trên key ...{key[-6:]}: {exc}")
            return "NETWORK_ERROR"

    def test_key(self, api_key: str) -> Tuple[bool, str]:
        """Kiểm tra tính hợp lệ của một key độc lập."""
        key = str(api_key or "").strip()
        if not key:
            return False, "Key rỗng"
        try:
            yt = build("youtube", "v3", developerKey=key, cache_discovery=False)
            yt.channels().list(part="id", id="UC_x5XG1OV2P6uZZ5FSM9Ttw").execute()
            with self._lock:
                if key in self._keys_map:
                    self._keys_map[key].status = "ACTIVE"
                    self._keys_map[key].last_error_message = ""
                    self._keys_map[key].consecutive_errors = 0
            return True, "API Key hợp lệ và hoạt động tốt."
        except HttpError as e:
            try:
                data = json.loads(e.content.decode("utf-8"))
                reason = data.get("error", {}).get("errors", [{}])[0].get("reason", "unknown")
            except Exception:
                reason = "unknown"
            if e.resp.status == 403 and reason in ("quotaExceeded", "dailyLimitExceeded"):
                with self._lock:
                    if key in self._keys_map:
                        self._keys_map[key].status = "QUOTA_EXCEEDED"
                        self._keys_map[key].quota_resets_at = _next_midnight_utc()
                return False, "API Key hợp lệ nhưng đã hết quota ngày."
            if e.resp.status == 403 and reason in ("accessNotConfigured",):
                return False, "Chưa bật YouTube Data API v3 trên Google Cloud Console."
            return False, f"Lỗi Google API: {reason} ({e.resp.status})"
        except Exception as e:
            return False, f"Không kiểm tra được: {e}"

    def load_from_config(self, cfg: Dict[str, Any]) -> None:
        with self._lock:
            raw_keys = cfg.get("api_keys") or []
            meta_pool = cfg.get("api_keys_pool") or []
            self._keys_map.clear()

            # Load from detailed pool metadata if present
            if isinstance(meta_pool, list) and meta_pool:
                for item in meta_pool:
                    if isinstance(item, dict) and item.get("key"):
                        info = ApiKeyInfo.from_dict(item)
                        self._keys_map[info.key] = info

            # Merge with plain api_keys list for backwards compatibility
            if isinstance(raw_keys, list):
                for k in raw_keys:
                    k_str = str(k).strip()
                    if k_str and k_str not in self._keys_map:
                        self._keys_map[k_str] = ApiKeyInfo(key=k_str)

            self._client_cache = {k: c for k, c in self._client_cache.items() if k in self._keys_map}

    def save_to_config(self, cfg: Dict[str, Any]) -> None:
        with self._lock:
            cfg["api_keys"] = [k for k in self._keys_map.keys()]
            cfg["api_keys_pool"] = [info.to_dict() for info in self._keys_map.values()]
