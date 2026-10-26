"""Module quản lý danh sách YouTube Data API Keys (Multi-Key Pool).

Hỗ trợ nạp nhiều API key, tự động xoay tua thông minh, phân loại lỗi chính xác
(hết quota ngày -> tạm ngưng đến 00:00 UTC; rate limit -> cooldown; lỗi 5xx/timeout -> retry).
"""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


def _redact_key(msg: str, key: Optional[str] = None) -> str:
    """Ẩn các chuỗi API key nhạy cảm trong error message hoặc url query params."""
    if not msg:
        return ""
    if key and len(key) >= 6:
        msg = msg.replace(key, f"...{key[-4:]}")
    msg = re.sub(r"([?&]key=)[^& '\"<>]*", r"\1[REDACTED]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"AIza[0-9A-Za-z-_]{30,40}", "AIza[REDACTED]", msg)
    return msg


def _parse_google_http_error(exc: HttpError) -> Tuple[int, str, List[str], List[str]]:
    """Phân tích phòng thủ payload lỗi của Google HttpError.

    Trả về:
        (status_code, primary_reason, all_reasons, all_messages)
    """
    status_code = getattr(exc.resp, "status", 0) if hasattr(exc, "resp") else 0
    all_reasons: List[str] = []
    all_messages: List[str] = [str(exc)]

    content_str = ""
    try:
        raw = getattr(exc, "content", b"")
        if isinstance(raw, (bytes, bytearray)):
            content_str = raw.decode("utf-8", errors="replace")
        elif raw:
            content_str = str(raw)
    except Exception:
        content_str = ""

    if content_str:
        all_messages.append(content_str)
        try:
            data = json.loads(content_str)
            if isinstance(data, dict):
                err = data.get("error", {})
                if isinstance(err, dict):
                    top_msg = err.get("message")
                    if top_msg:
                        all_messages.append(str(top_msg))
                    top_status = err.get("status")
                    if top_status:
                        all_reasons.append(str(top_status))

                    errors_list = err.get("errors", [])
                    if isinstance(errors_list, list):
                        for e_item in errors_list:
                            if isinstance(e_item, dict):
                                r = e_item.get("reason")
                                if r:
                                    all_reasons.append(str(r))
                                m = e_item.get("message")
                                if m:
                                    all_messages.append(str(m))
        except Exception:
            pass

    primary_reason = all_reasons[0] if all_reasons else "unknown"
    return status_code, primary_reason, all_reasons, all_messages


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
        self._thread_local = threading.local()
        if keys:
            for k in keys:
                self.add_key(k)

    def _get_thread_client_cache(self) -> Dict[str, Any]:
        if not hasattr(self._thread_local, "clients"):
            self._thread_local.clients = {}
        return self._thread_local.clients

    def invalidate_thread_client(self, key: str) -> None:
        """Hủy client trong cache của thread hiện tại khi socket bị reset hoặc lỗi transport."""
        key = str(key or "").strip()
        cache = self._get_thread_client_cache()
        cache.pop(key, None)
        with self._lock:
            self._client_cache.pop(key, None)

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
            self.invalidate_thread_client(key)
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

        # Ưu tiên client cách ly theo thread để tránh race condition trên socket httplib2
        cache = self._get_thread_client_cache()
        client = cache.get(chosen_key)
        if client is not None:
            return client

        client = build("youtube", "v3", developerKey=chosen_key, cache_discovery=False)
        cache[chosen_key] = client
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
                status_code, primary_reason, all_reasons, all_messages = _parse_google_http_error(exc)

                is_invalid_key = (
                    any(r in ("keyInvalid", "API_KEY_INVALID") for r in all_reasons)
                    or any("api key not valid" in m.lower() or "key not valid" in m.lower() for m in all_messages)
                )

                sanitized_msg = _redact_key(str(exc), key)
                info.last_error_message = f"[{status_code} {primary_reason}] {sanitized_msg}"[:200]

                # 1. Hết Quota ngày
                if status_code == 403 and any(r in ("quotaExceeded", "dailyLimitExceeded") for r in all_reasons):
                    info.status = "QUOTA_EXCEEDED"
                    info.quota_resets_at = _next_midnight_utc()
                    info.retry_after = 0.0
                    logger.warning(f"[ApiKeyManager] Key ...{key[-6:]} hết quota ngày, tạm ngưng đến 00:00 UTC")
                    return "QUOTA_EXCEEDED"

                # 2. Rate Limit ngắn hạn
                if (status_code in (403, 429) and any(r in ("rateLimitExceeded", "userRateLimitExceeded") for r in all_reasons)) or status_code == 429:
                    info.status = "RATE_LIMITED"
                    info.retry_after = now + 60.0  # cooldown 1 phút
                    logger.warning(f"[ApiKeyManager] Key ...{key[-6:]} bị rate-limit, cooldown 60s")
                    return "RATE_LIMITED"

                # 3. Key không hợp lệ (400 badRequest với API key not valid, 400 keyInvalid, hoặc 401)
                if (status_code == 400 and is_invalid_key) or status_code == 401:
                    info.status = "INVALID"
                    info.quota_resets_at = 0.0
                    info.retry_after = 0.0
                    logger.error(f"[ApiKeyManager] Key ...{key[-6:]} không hợp lệ hoặc đã bị vô hiệu hóa bởi Google ({status_code} {primary_reason})")
                    return "INVALID"

                # 4. API chưa được bật trên Google Cloud
                if status_code == 403 and any(r in ("accessNotConfigured",) for r in all_reasons):
                    info.status = "DISABLED"
                    info.quota_resets_at = 0.0
                    info.retry_after = 0.0
                    logger.error(f"[ApiKeyManager] Project của key ...{key[-6:]} chưa bật YouTube Data API v3")
                    return "DISABLED"

                # 5. Server error từ Google (500, 502, 503, 504) -> Giữ nguyên key, không xoay vô tội vạ
                if 500 <= status_code < 600:
                    logger.warning(f"[ApiKeyManager] Google 5xx ({status_code}) trên key ...{key[-6:]}, giữ key để retry")
                    return "SERVER_ERROR"

                # 6. Các lỗi client khác (400 tham số caller, 404 không tìm thấy) -> Không đánh đồng với lỗi mạng/socket
                if 400 <= status_code < 500:
                    logger.warning(f"[ApiKeyManager] Lỗi client ({status_code} {primary_reason}) trên key ...{key[-6:]}: {sanitized_msg}")
                    return "BAD_REQUEST"

            # Lỗi mạng / timeout / connection reset (socket exception thực tế)
            self.invalidate_thread_client(key)
            info.last_error_message = _redact_key(str(exc), key)[:200]
            logger.warning(f"[ApiKeyManager] Lỗi mạng/socket trên key ...{key[-6:]}: {_redact_key(str(exc), key)}")
            return "NETWORK_ERROR"

    def execute_with_pool(
        self,
        request_builder: Callable[[Any], Any],
        action_desc: str = "YouTube API Request",
        max_key_rotations: int = 3,
        max_network_retries: int = 2,
        deadline_seconds: float = 30.0,
    ) -> Any:
        """Thực thi một request Google YouTube API với cơ chế tự động xoay key và retry mạng.
        
        Quy trình:
        1. Thuê key khả dụng từ pool (round-robin).
        2. Dựng và gửi request qua client riêng của thread.
        3. Nếu gặp WinError 10054 / ConnectionResetError / timeout: hủy client cũ của thread và retry với backoff.
        4. Nếu gặp Quota Exceeded (403) hoặc Rate Limit (429): gọi handle_api_error và tự động xoay sang key kế tiếp.
        5. Nếu gặp lỗi tham số BadRequest (400) hoặc lỗi logic người dùng: ném lỗi ngay lập tức, không xoay key mù quáng.
        """
        start_time = time.monotonic()
        attempted_keys: Set[str] = set()
        rotations_left = max_key_rotations
        last_error: Optional[Exception] = None

        while rotations_left >= 0:
            if time.monotonic() - start_time > deadline_seconds:
                raise TimeoutError(f"Hết thời gian chờ {deadline_seconds}s khi thực thi {action_desc} qua API pool")

            # 1. Chọn key khả dụng chưa thử trong operation này
            chosen_key: Optional[str] = None
            with self._lock:
                now = time.time()
                keys_list = list(self._keys_map.values())
                n = len(keys_list)
                for step in range(n):
                    idx = (self._rr_index + step) % n
                    candidate = keys_list[idx]
                    if candidate.key not in attempted_keys and candidate.is_usable(now):
                        self._rr_index = (idx + 1) % n
                        candidate.last_used_at = now
                        chosen_key = candidate.key
                        break

            if not chosen_key:
                if not attempted_keys:
                    raise ValueError(f"Không có YouTube API key nào khả dụng trong pool để thực thi {action_desc}")
                break

            attempted_keys.add(chosen_key)

            # 2. Thử thực thi với key đã chọn (kèm retry mạng cho socket 10054)
            network_retries_left = max_network_retries

            while network_retries_left >= 0:
                if time.monotonic() - start_time > deadline_seconds:
                    raise TimeoutError(f"Hết thời gian chờ {deadline_seconds}s khi thực thi {action_desc}")

                client = self.get_youtube_client(chosen_key)
                try:
                    req = request_builder(client)
                    if hasattr(req, "execute") and callable(getattr(req, "execute")):
                        res = req.execute(num_retries=0)
                    else:
                        res = req
                    # Thành công: reset consecutive_errors cho key này
                    with self._lock:
                        if chosen_key in self._keys_map:
                            self._keys_map[chosen_key].consecutive_errors = 0
                    return res

                except (ConnectionResetError, ConnectionError, OSError, socket.error) as net_err:
                    last_error = net_err
                    # Đóng và loại bỏ transport client bị reset socket trong thread
                    self.invalidate_thread_client(chosen_key)
                    if network_retries_left > 0:
                        network_retries_left -= 1
                        logger.warning(
                            f"[ApiKeyManager] Socket bị reset ({net_err}) trên key ...{chosen_key[-4:]} khi {action_desc}, "
                            f"đang thử lại socket mới ({network_retries_left} lượt còn lại)..."
                        )
                        time.sleep(0.3)
                        continue
                    else:
                        # Hết lượt retry mạng trên key này -> ghi nhận network error và thử xoay key
                        self.handle_api_error(chosen_key, net_err)
                        break

                except HttpError as http_err:
                    last_error = http_err
                    err_type = self.handle_api_error(chosen_key, http_err)
                    if err_type in ("QUOTA_EXCEEDED", "RATE_LIMITED", "INVALID", "DISABLED"):
                        logger.warning(
                            f"[ApiKeyManager] Key ...{chosen_key[-4:]} bị {err_type} khi {action_desc}. "
                            f"Đang tự động xoay sang key kế tiếp trong pool..."
                        )
                        break
                    elif err_type == "SERVER_ERROR":
                        if network_retries_left > 0:
                            network_retries_left -= 1
                            time.sleep(0.5)
                            continue
                        break
                    else:
                        # BadRequest (400) hoặc lỗi caller: ném ngay lập tức, không xoay key mù quáng
                        raise

                except Exception as unk_err:
                    last_error = unk_err
                    err_str = str(unk_err).lower()
                    if any(term in err_str for term in ("10054", "connection aborted", "forcibly closed", "reset by peer")):
                        self.invalidate_thread_client(chosen_key)
                        if network_retries_left > 0:
                            network_retries_left -= 1
                            time.sleep(0.3)
                            continue
                    raise

            rotations_left -= 1

        if last_error:
            raise last_error
        raise RuntimeError(f"Toàn bộ API key trong pool đều không thể hoàn thành {action_desc}")

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
            status_code, primary_reason, all_reasons, all_messages = _parse_google_http_error(e)
            if status_code == 403 and any(r in ("quotaExceeded", "dailyLimitExceeded") for r in all_reasons):
                with self._lock:
                    if key in self._keys_map:
                        self._keys_map[key].status = "QUOTA_EXCEEDED"
                        self._keys_map[key].quota_resets_at = _next_midnight_utc()
                        self._keys_map[key].retry_after = 0.0
                return False, "API Key hợp lệ nhưng đã hết quota ngày."
            if status_code == 403 and any(r in ("accessNotConfigured",) for r in all_reasons):
                with self._lock:
                    if key in self._keys_map:
                        self._keys_map[key].status = "DISABLED"
                        self._keys_map[key].quota_resets_at = 0.0
                        self._keys_map[key].retry_after = 0.0
                return False, "Chưa bật YouTube Data API v3 trên Google Cloud Console."
            is_invalid = (
                status_code == 401
                or any(r in ("keyInvalid", "API_KEY_INVALID") for r in all_reasons)
                or any("api key not valid" in m.lower() or "key not valid" in m.lower() for m in all_messages)
            )
            if (status_code in (400, 401)) and is_invalid:
                with self._lock:
                    if key in self._keys_map:
                        self._keys_map[key].status = "INVALID"
                        self._keys_map[key].quota_resets_at = 0.0
                        self._keys_map[key].retry_after = 0.0
                return False, "API Key không hợp lệ hoặc đã bị vô hiệu hóa bởi Google."
            return False, f"Lỗi Google API: {primary_reason} ({status_code})"
        except Exception as e:
            return False, f"Không kiểm tra được: {_redact_key(str(e), key)}"

    def load_from_config(self, cfg: Dict[str, Any]) -> None:
        """Nạp danh sách keys từ config với cơ chế Non-Destructive Merge (bảo tồn trạng thái runtime)."""
        with self._lock:
            raw_keys = cfg.get("api_keys") or []
            meta_pool = cfg.get("api_keys_pool") or []

            # Tập hợp các key hợp lệ từ config (giữ nguyên thứ tự xuất hiện)
            ordered_keys: List[str] = []
            seen_keys: Set[str] = set()
            meta_dict: Dict[str, Dict[str, Any]] = {}
            if isinstance(meta_pool, list):
                for item in meta_pool:
                    if isinstance(item, dict) and item.get("key"):
                        k_str = str(item["key"]).strip()
                        if k_str and k_str not in seen_keys:
                            seen_keys.add(k_str)
                            ordered_keys.append(k_str)
                            meta_dict[k_str] = item

            if isinstance(raw_keys, list):
                for k in raw_keys:
                    k_str = str(k).strip()
                    if k_str and k_str not in seen_keys:
                        seen_keys.add(k_str)
                        ordered_keys.append(k_str)

            # 1. Loại bỏ các key không còn trong config
            for existing_key in list(self._keys_map.keys()):
                if existing_key not in seen_keys:
                    del self._keys_map[existing_key]
                    self._client_cache.pop(existing_key, None)
                    self.invalidate_thread_client(existing_key)

            # 2. Thêm key mới hoặc cập nhật key cũ mà KHÔNG ghi đè trạng thái runtime đang sống
            for k in ordered_keys:
                if k not in self._keys_map:
                    if k in meta_dict:
                        self._keys_map[k] = ApiKeyInfo.from_dict(meta_dict[k])
                    else:
                        self._keys_map[k] = ApiKeyInfo(key=k)
                else:
                    curr = self._keys_map[k]
                    # Nếu key trong bộ nhớ đang ACTIVE nhưng file config đã lưu trạng thái lỗi hợp lệ
                    if curr.status == "ACTIVE" and k in meta_dict:
                        meta_info = ApiKeyInfo.from_dict(meta_dict[k])
                        if meta_info.status != "ACTIVE":
                            now = time.time()
                            if not meta_info.is_usable(now):
                                curr.status = meta_info.status
                                curr.retry_after = meta_info.retry_after
                                curr.quota_resets_at = meta_info.quota_resets_at
                                curr.last_error_message = meta_info.last_error_message

    def save_to_config(self, cfg: Dict[str, Any]) -> None:
        with self._lock:
            cfg["api_keys"] = [k for k in self._keys_map.keys()]
            cfg["api_keys_pool"] = [info.to_dict() for info in self._keys_map.values()]
