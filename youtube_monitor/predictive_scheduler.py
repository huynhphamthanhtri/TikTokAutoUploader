"""Module điều phối Polling dự đoán (Predictive Scheduler) & Safety Net.

Thực hiện:
1. TẦNG 1: Micro-Polling theo cấu hình khi đồng hồ rơi vào cửa sổ dự đoán
   hoặc khung giờ thủ công của kênh.
2. TẦNG 2: Safety Net RSS quét thưa 3-5 phút ngoài khung giờ dự đoán để không bao giờ
   bị bỏ sót video đăng ngoài lịch, đồng thời tự học và bổ sung khung giờ mới.
3. Hỗ trợ kích hoạt tức thì (Fast-track) khi nhận callback WebSub tùy chọn.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import requests

from .key_manager import ApiKeyManager
from .polling_settings import normalize_settings
from .schedule_learner import (
    DEFAULT_TIMEZONE,
    PollingWindow,
    ScheduleLearner,
    resolve_timezone,
)

logger = logging.getLogger(__name__)

SAFETY_NET_INTERVAL_SECONDS = 180  # 3 phút quét thưa khi ngoài khung giờ
MICRO_POLL_INTERVAL_SECONDS = 1.0  # 1 giây/lần khi trong khung giờ dự đoán
RSS_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _channel_to_uploads_playlist(channel_id: str) -> str:
    cid = str(channel_id or "").strip()
    if cid.startswith("UC") and len(cid) > 2:
        return "UU" + cid[2:]
    return cid


class PredictiveScheduler:
    """Bộ điều phối 2 tầng cho YouTube Monitor."""

    def __init__(
        self,
        channels_store: Any,
        key_manager: ApiKeyManager,
        on_video_detected: Callable[[str, str, Optional[str], str, str], bool],
        stop_event: Optional[threading.Event] = None,
        safety_net_interval: float = SAFETY_NET_INTERVAL_SECONDS,
        micro_poll_interval: float = MICRO_POLL_INTERVAL_SECONDS,
        settings_provider=None,
        clock=None,
    ) -> None:
        self.channels_store = channels_store
        self.key_manager = key_manager
        self.on_video_detected = on_video_detected
        self.stop_event = stop_event or threading.Event()
        self.safety_net_interval = safety_net_interval
        self.micro_poll_interval = micro_poll_interval
        self.settings_provider = settings_provider
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._completed_windows = {}
        self._runtime_status = {}

        self._wake_event = threading.Event()
        self._fast_track_channels: Set[str] = set()
        self._lock = threading.RLock()
        self._last_safety_net_at: float = 0.0
        self._learner = ScheduleLearner(DEFAULT_TIMEZONE)

    def settings(self):
        raw = self.settings_provider() if self.settings_provider else {"poll_interval_seconds": self.micro_poll_interval}
        return normalize_settings(raw)

    def current_windows(self, channel_id, dt=None):
        """Concrete occurrences keyed by channel, schedule ID and anchor date.

        Learned days belong to the expected publication date; manual days to
        the start date, including windows crossing midnight.
        """
        dt = dt or self.clock()
        meta = self.channels_store.get_meta(channel_id) or {}
        local = dt.astimezone(resolve_timezone(meta.get("timezone") or DEFAULT_TIMEZONE))
        cfg = self.settings()
        found = {}
        for index, window in enumerate(self.get_channel_active_windows(meta)):
            if not window.enabled:
                continue
            learned = bool(window.expected_time and window.source == "LEARNED")
            anchor_time = window.expected_time if learned else window.start_time
            h, m = map(int, anchor_time.split(":"))
            # Find candidate anchor dates using the actual configured span.
            before = timedelta(minutes=cfg["window_before_minutes"]) if learned else timedelta(0)
            if learned:
                after = timedelta(minutes=cfg["window_after_minutes"])
            else:
                eh, em = map(int, window.end_time.split(":"))
                after = timedelta(minutes=(eh * 60 + em - h * 60 - m) % 1440)
            first = (local - after).date()
            last = (local + before).date()
            for day_offset in range((last - first).days + 1):
                day = first + timedelta(days=day_offset)
                if day.weekday() not in window.days:
                    continue
                anchor = datetime.combine(day, datetime.min.time(), tzinfo=local.tzinfo).replace(hour=h, minute=m)
                start, end = anchor - before, anchor + after
                if start <= local <= end:
                    token = (channel_id, window.id or str(index), day.isoformat())
                    found[token] = end
        with self._lock:
            self._completed_windows = {k: v for k, v in self._completed_windows.items() if v >= dt}
        return found

    def _window_completed(self, channel_id, dt=None):
        current = self.current_windows(channel_id, dt)
        with self._lock:
            return bool(current) and all(k in self._completed_windows for k in current)

    def get_runtime_status(self):
        result = {}
        for cid, meta in self.channels_store.all_items().items():
            if not meta.get("active", True):
                result[cid] = {"label": "Tạm dừng"}
            elif self._window_completed(cid):
                result[cid] = dict(self._runtime_status.get(cid, {"label": "Đã hoàn thành"}))
            elif self.current_windows(cid):
                result[cid] = {"label": "Đang quét"}
            else:
                result[cid] = {"label": "Ngoài khung giờ quét"}
        return result

    def record_detection(self, channel_id, video_id, current=None):
        """Called only after the shared deduplication policy accepts a video."""
        self.channels_store.update_meta(channel_id, last_known_video_id=video_id)
        current = self.current_windows(channel_id) if current is None else current
        if not current or not self.settings()["stop_after_detection"]:
            return False
        meta = self.channels_store.get_meta(channel_id) or {}
        with self._lock:
            self._completed_windows.update(current)
            self._runtime_status[channel_id] = {
                "label": "Đã phát hiện video",
                "detected_at": self.clock().astimezone(resolve_timezone(meta.get("timezone") or DEFAULT_TIMEZONE)).strftime("%H:%M:%S"),
                "skipped_seconds": max(0, (max(current.values()) - self.clock()).total_seconds()),
            }
        return True

    def trigger_immediate_poll(self, channel_id: Optional[str] = None) -> None:
        """Được gọi bởi WebSub callback để đánh thức scheduler quét API ngay lập tức."""
        with self._lock:
            if channel_id:
                self._fast_track_channels.add(channel_id)
            self._wake_event.set()

    def get_channel_active_windows(self, meta: Dict[str, Any]) -> List[PollingWindow]:
        """Tổng hợp toàn bộ khung giờ (học được + thủ công) của một kênh."""
        windows: List[PollingWindow] = []

        # 1. Khung giờ thủ công người dùng thêm
        manual_list = meta.get("manual_windows") or []
        for mw in manual_list:
            if isinstance(mw, dict):
                windows.append(PollingWindow.from_dict(mw))

        # 2. Khung giờ tự học từ lịch sử
        learned_dict = meta.get("schedule_learned") or {}
        pred_list = learned_dict.get("predicted_windows") or []
        for pw in pred_list:
            if isinstance(pw, dict):
                w_obj = PollingWindow.from_dict(pw)
                # Nếu không bị đè bởi manual window
                windows.append(w_obj)

        return windows

    def is_channel_in_window(self, channel_id: str, dt: Optional[datetime] = None) -> bool:
        meta = self.channels_store.get_meta(channel_id) or {}
        if not meta.get("active", True):
            return False
        if dt is None:
            dt = self.clock()
        return bool(self.current_windows(channel_id, dt))

    def get_active_channels_now(self, dt: Optional[datetime] = None) -> List[str]:
        """Các kênh có khung chưa hoàn tất hoặc được fast-track."""
        if dt is None:
            dt = self.clock()

        active_cids: List[str] = []
        with self._lock:
            fast_track = list(self._fast_track_channels)
            self._fast_track_channels.clear()

        for cid, meta in self.channels_store.all_items().items():
            if not meta.get("active", True):
                continue
            if self._window_completed(cid, dt):
                continue
            if cid in fast_track:
                active_cids.append(cid)
                continue
            if self.is_channel_in_window(cid, dt):
                active_cids.append(cid)

        return active_cids

    def poll_channel_via_api(self, channel_id: str, source: str = "API_PREDICTIVE", max_results: int = 2) -> int:
        """Kiểm tra video mới nhất của 1 kênh qua playlist uploads (1 unit quota)."""
        meta = self.channels_store.get_meta(channel_id) or {}
        if not meta.get("active", True):
            return 0
        current = self.current_windows(channel_id)
        if self._window_completed(channel_id):
            return 0

        uploads_id = meta.get("uploads_playlist_id") or _channel_to_uploads_playlist(channel_id)
        detected_count = 0
        now_iso = self.clock().isoformat()

        key = self.key_manager.get_active_key()
        if not key:
            logger.warning("[Scheduler] Không có API Key khả dụng để poll")
            return 0

        try:
            yt = self.key_manager.get_youtube_client(key)
            resp = yt.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=uploads_id,
                maxResults=max_results,
            ).execute()

            items = resp.get("items", [])
            # Process oldest first
            for it in reversed(items):
                sn = it.get("snippet", {})
                cd = it.get("contentDetails", {})
                vid = cd.get("videoId") or sn.get("resourceId", {}).get("videoId")
                pub = cd.get("videoPublishedAt") or sn.get("publishedAt") or ""
                if vid and vid != meta.get("last_known_video_id") and vid not in meta.get("seen", set()):
                    enqueued = self.on_video_detected(channel_id, vid, pub, now_iso, source)
                    if enqueued:
                        detected_count += 1
                        if self.record_detection(channel_id, vid, current):
                            break
                        logger.info(f"[{source}] Phát hiện video mới {vid} kênh {channel_id}")

        except Exception as e:
            err_type = self.key_manager.handle_api_error(key, e)
            logger.warning(f"[{source}] Lỗi poll API kênh {channel_id} (key ...{key[-4:]}): {err_type} - {e}")

        return detected_count

    def run_safety_net_check(self) -> int:
        """Chạy kiểm tra thưa (Safety Net) qua RSS (fallback sang API nếu RSS 404)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        total_detected = 0

        for cid, meta in self.channels_store.all_items().items():
            if self.stop_event.is_set():
                break
            if not meta.get("active", True):
                continue

            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
            rss_ok = False
            entries = []

            # 1. Thử cào RSS nhẹ nhàng (0 quota)
            try:
                r = requests.get(rss_url, headers={"User-Agent": RSS_USER_AGENT}, timeout=8)
                if r.status_code == 200:
                    rss_ok = True
                    # Bóc tách đơn giản regex videoId và published
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(r.text)
                    ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
                    for entry in root.findall("atom:entry", ns):
                        ve = entry.find("yt:videoId", ns)
                        pe = entry.find("atom:published", ns)
                        if ve is not None and ve.text:
                            entries.append((ve.text.strip(), pe.text.strip() if pe is not None else ""))
            except Exception:
                rss_ok = False

            # 2. Nếu RSS lỗi/404 -> Fallback kiểm tra 1 request Data API
            if not rss_ok:
                c = self.poll_channel_via_api(cid, source="API_SAFETY_NET", max_results=2)
                total_detected += c
                continue

            # 3. Xử lý video tìm thấy từ RSS
            for vid, published in reversed(entries[:3]):
                enqueued = self.on_video_detected(cid, vid, published, now_iso, "RSS_SAFETY_NET")
                if enqueued:
                    total_detected += 1
                    logger.info(f"[SafetyNet] Bắt được video {vid} kênh {cid} qua RSS")
                    # Tự động cập nhật lịch học của kênh
                    self._update_channel_learned_schedule(cid, published)

        self._last_safety_net_at = time.time()
        return total_detected

    def _update_channel_learned_schedule(self, channel_id: str, new_published_iso: str) -> None:
        """Cập nhật lịch đăng khi bắt được video mới."""
        meta = self.channels_store.get_meta(channel_id) or {}
        learned = meta.get("schedule_learned") or {}
        history = list(learned.get("history") or [])
        cur_windows = self.get_channel_active_windows(meta)

        up_hist, final_wins, has_new = self._learner.update_history_and_recluster(
            history, new_published_iso, cur_windows
        )

        learned["history"] = up_hist
        learned["predicted_windows"] = [w.to_dict() for w in final_wins if w.source == "LEARNED"]
        learned["last_analyzed_at"] = datetime.now(timezone.utc).isoformat()
        self.channels_store.update_meta(channel_id, schedule_learned=learned)
        if has_new:
            logger.info(f"[Learner] Tự động bổ sung khung giờ mới cho kênh {channel_id}")

    def run_loop(self) -> None:
        """Vòng lặp chính của scheduler."""
        logger.info("[Scheduler] Predictive Scheduler started with configurable polling")
        while not self.stop_event.is_set():
            now = time.time()
            active_cids = self.get_active_channels_now()

            if active_cids:
                # GIAI ĐOẠN 1: Micro-Polling trong cửa sổ dự đoán
                for cid in active_cids:
                    if self.stop_event.is_set():
                        break
                    self.poll_channel_via_api(cid, source="API_PREDICTIVE", max_results=1)

                # Đọc lại khoảng cách quét sau mỗi lượt.
                self._wake_event.wait(timeout=self.settings()["poll_interval_seconds"])
                self._wake_event.clear()
            else:
                # GIAI ĐOẠN 2: Ngoài cửa sổ dự đoán -> Safety Net
                if now - self._last_safety_net_at >= self.safety_net_interval:
                    self.run_safety_net_check()

                # Ngủ ngắn 5 giây để kiểm tra xem đã đến giờ mở cửa sổ dự đoán tiếp theo chưa
                self._wake_event.wait(timeout=5.0)
                self._wake_event.clear()

        logger.info("[Scheduler] Predictive Scheduler stopped")
