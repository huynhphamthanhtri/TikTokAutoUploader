"""
tiktok_dedup_engine.py - Hệ thống phát hiện video trùng lặp, Shadowban và tự động xử lý.

Nguyên lý hoạt động:
1. Thuật toán phân cụm TikTok:
   - aweme_id: ID bài viết trên tài khoản.
   - group_id: ID cụm nội dung gốc do TikTok tính toán.
   - Nếu group_id != aweme_id => Video là bản trùng lặp (reup/duplicate).
   - Đường dẫn đối chiếu video gốc: https://www.tiktok.com/@tiktok/video/{group_id}

2. Cơ chế Shadowban / Giam duyệt ngầm:
   - in_reviewing == 1 hoặc display_penalty_type != 0 => isShadow = True.

3. Cửa sổ thời gian vàng 48 giờ:
   - Chỉ xử lý video đăng trong vòng dưới 48 giờ trước các đợt quét tính tiền Creator Rewards.

4. Quản lý bền vững bằng SQLite:
   - Bảng dedup_watch: Quản lý danh sách tài khoản theo dõi và lịch quét kế tiếp.
   - Bảng dedup_log: Nhật ký kiểm toán các video bị trùng/shadowban, lưu vết hành động.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import requests

from core_helpers import parse_proxy_string
from tls_client_engine import create_tls_session

logger = logging.getLogger("DedupWatchdog")

DEFAULT_DB_NAME = "dedup_watchdog.db"
DEFAULT_INTERVAL_MINUTES = 60
DEFAULT_GAP_SECONDS = 25
DEFAULT_ACTION_DELAY_SECONDS = 5.0
FORTY_EIGHT_HOURS_SECONDS = 48 * 3600


# ============================================================================
# 1. DATA MODELS & CONFIG
# ============================================================================

@dataclass
class VideoItemAnalysis:
    """Kết quả phân tích một bài đăng video TikTok."""
    aweme_id: str
    group_id: str
    desc: str
    create_time: int
    is_dup: bool = False
    is_under_48h: bool = False
    play_count: int = 0
    is_shadow: bool = False
    shadow_reason: Optional[str] = None
    original_url: str = ""

    def __post_init__(self):
        if not self.original_url and self.group_id:
            self.original_url = f"https://www.tiktok.com/@tiktok/video/{self.group_id}"


@dataclass
class DedupConfig:
    """Cấu hình vận hành của Dedup Watchdog Engine."""
    enabled: bool = False
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES  # 30 đến 720 phút
    action: str = "dry_run"  # 'dry_run' | 'hide' | 'delete'
    gap_seconds: int = DEFAULT_GAP_SECONDS  # Giãn cách giữa 2 profile (giây)
    action_delay_seconds: float = DEFAULT_ACTION_DELAY_SECONDS  # Giãn cách giữa 2 video (giây)
    only_under_48h: bool = True  # Chỉ xử lý video đăng < 48 giờ


@dataclass
class ProfileContext:
    """Ngữ cảnh tài khoản để phục vụ truy vấn API."""
    profile_id: str
    tiktok_account: str
    sec_uid: str = ""
    cookies: Union[str, List[Dict[str, Any]]] = ""
    proxy_type: str = "http"
    proxy_string: str = ""


# ============================================================================
# 2. CƠ SỞ DỮ LIỆU SQLITE (DATABASE LAYER)
# ============================================================================

class DedupDatabase:
    """Quản lý lưu trữ SQLite cho Watchlist và Audit Log."""

    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parent / DEFAULT_DB_NAME
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._init_tables()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                # 1. Bảng theo dõi tài khoản (Watchlist)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS dedup_watch (
                        profile_id      TEXT PRIMARY KEY,
                        channel         TEXT,
                        added_at        INTEGER NOT NULL,
                        next_check_at   INTEGER NOT NULL,
                        paused          INTEGER DEFAULT 0
                    );
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_dedup_watch_next 
                    ON dedup_watch(paused, next_check_at);
                """)

                # 2. Bảng nhật ký phát hiện và xử lý video trùng (Audit Log)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS dedup_log (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        profile_id      TEXT NOT NULL,
                        channel         TEXT,
                        aweme_id        TEXT NOT NULL,
                        group_id        TEXT NOT NULL,
                        desc            TEXT,
                        create_time     INTEGER,
                        detected_at     INTEGER NOT NULL,
                        action          TEXT NOT NULL,
                        status          TEXT NOT NULL,
                        deleted_at      INTEGER,
                        error           TEXT,
                        UNIQUE(profile_id, aweme_id)
                    );
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_dedup_log_profile 
                    ON dedup_log(profile_id, detected_at DESC);
                """)
                conn.commit()
            finally:
                conn.close()

    # --- WATCHLIST METHODS ---

    def register_profile(self, profile_id: str, channel: str = "", next_check_at: Optional[int] = None) -> None:
        now_ms = int(time.time() * 1000)
        target_check = next_check_at if next_check_at is not None else now_ms
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT INTO dedup_watch (profile_id, channel, added_at, next_check_at, paused)
                    VALUES (?, ?, ?, ?, 0)
                    ON CONFLICT(profile_id) DO UPDATE SET
                        channel = CASE WHEN ? != '' THEN ? ELSE channel END
                """, (profile_id, channel, now_ms, target_check, channel, channel))
                conn.commit()
            finally:
                conn.close()

    def set_profile_paused(self, profile_id: str, paused: bool) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("UPDATE dedup_watch SET paused = ? WHERE profile_id = ?", (1 if paused else 0, profile_id))
                conn.commit()
            finally:
                conn.close()

    def update_next_check(self, profile_id: str, next_check_at: int) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("UPDATE dedup_watch SET next_check_at = ? WHERE profile_id = ?", (next_check_at, profile_id))
                conn.commit()
            finally:
                conn.close()

    def get_due_profiles(self, now_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        now_val = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT profile_id, channel, added_at, next_check_at, paused 
                    FROM dedup_watch 
                    WHERE paused = 0 AND next_check_at <= ?
                    ORDER BY next_check_at ASC
                """, (now_val,))
                rows = cursor.fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_all_watchlist(self) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT profile_id, channel, added_at, next_check_at, paused FROM dedup_watch ORDER BY added_at DESC")
                return [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()

    def remove_from_watchlist(self, profile_id: str) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM dedup_watch WHERE profile_id = ?", (profile_id,))
                conn.commit()
            finally:
                conn.close()

    # --- AUDIT LOG METHODS ---

    def log_detected(
        self,
        profile_id: str,
        channel: str,
        aweme_id: str,
        group_id: str,
        desc: str,
        create_time: int,
        action: str,
        status: str = "detected",
    ) -> bool:
        now_ms = int(time.time() * 1000)
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO dedup_log 
                        (profile_id, channel, aweme_id, group_id, desc, create_time, detected_at, action, status)
                    VALUES 
                        (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (profile_id, channel, aweme_id, group_id, desc, create_time, now_ms, action, status))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def update_log_status(
        self,
        profile_id: str,
        aweme_id: str,
        status: str,
        deleted_at: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    UPDATE dedup_log 
                    SET status = ?, deleted_at = ?, error = ?
                    WHERE profile_id = ? AND aweme_id = ?
                """, (status, deleted_at, error, profile_id, aweme_id))
                conn.commit()
            finally:
                conn.close()

    def get_audit_logs(self, limit: int = 200, profile_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.cursor()
                if profile_id:
                    cursor.execute("""
                        SELECT id, profile_id, channel, aweme_id, group_id, desc, create_time, detected_at, action, status, deleted_at, error
                        FROM dedup_log
                        WHERE profile_id = ?
                        ORDER BY detected_at DESC
                        LIMIT ?
                    """, (profile_id, limit))
                else:
                    cursor.execute("""
                        SELECT id, profile_id, channel, aweme_id, group_id, desc, create_time, detected_at, action, status, deleted_at, error
                        FROM dedup_log
                        ORDER BY detected_at DESC
                        LIMIT ?
                    """, (limit,))
                return [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()

@dataclass
class VideoItemAnalysis:
    """Kết quả phân tích một bài đăng video TikTok."""
    aweme_id: str
    group_id: str
    desc: str
    create_time: int
    is_dup: bool = False
    is_under_48h: bool = False
    play_count: int = 0
    is_shadow: bool = False
    shadow_reason: Optional[str] = None
    original_url: str = ""
    source: str = "Upload"
    digg_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    collect_count: int = 0
    duration: int = 0
    cover_url: str = ""
    is_private: bool = False
    is_delete: bool = False
    region: str = ""

    def __post_init__(self):
        if self.is_dup and self.group_id:
            self.original_url = f"https://www.tiktok.com/@tiktok/video/{self.group_id}"
        elif self.aweme_id:
            self.original_url = f"https://www.tiktok.com/@tiktok/video/{self.aweme_id}"


# ============================================================================
# 3. THUẬT TOÁN PHÂN TÍCH VIDEO (ANALYSIS ENGINE)
# ============================================================================

def detect_video_source(item: Dict[str, Any]) -> str:
    """
    Xác định nguồn video chuẩn TikTokManager:
    - c2pa_info hoặc is_capcut => "CapCut"
    - high_quality_upload => "Upload"
    - is_tiktok hoặc shoot_tab_name => "Quay in-app"
    """
    if not isinstance(item, dict):
        return "Upload"

    c2pa = item.get("c2pa_info") if isinstance(item.get("c2pa_info"), dict) else {}
    if item.get("is_capcut") or c2pa.get("is_capcut") or "capcut" in str(item).lower():
        return "CapCut"

    if item.get("high_quality_upload"):
        return "Upload"

    creation_info = item.get("creation_info") if isinstance(item.get("creation_info"), dict) else {}
    shoot_tab = str(item.get("shoot_tab_name") or creation_info.get("shoot_tab_name") or "").lower()
    if item.get("is_tiktok") or any(k in shoot_tab for k in ("record", "shoot", "camera", "effect")):
        return "Quay in-app"

    funcs = str(creation_info.get("creation_used_functions") or "").lower()
    if "capcut" in funcs:
        return "CapCut"
    if "camera" in funcs or "shoot" in funcs:
        return "Quay in-app"

    return "Upload"


def analyze_tiktok_video(item: Dict[str, Any], now_timestamp: Optional[int] = None) -> VideoItemAnalysis:
    """
    Phân tích một bài đăng video TikTok:
    - Thuật toán trùng lặp: group_id != aweme_id (Mobile API / aweme_list schema)
    - Hỗ trợ cả Web schema (id, itemList) và Mobile/Studio schema (aweme_id, aweme_list, status, group_id_list)
    - Cửa sổ 48h: now - create_time < 48 * 3600
    - Shadowban / giam duyệt: display_penalty_type != 0 hoặc in_reviewing == 1 hoặc violation_display_status != 0
    - Trích xuất nguồn (CapCut / Upload / Quay in-app), covers, duration, tương tác

    LƯU Ý: `originalItem: False` trong Web API KHÔNG phải là marker "video trùng lặp".
    Đây chỉ là field cho biết video không phải nội dung do TikTok chính thức sản xuất.
    TikTokManager chỉ detect duplicate qua `group_id != aweme_id` từ Mobile API hoặc
    qua explicit flag `isDup / is_dup`.
    """
    if not isinstance(item, dict):
        return VideoItemAnalysis(aweme_id="", group_id="", desc="", create_time=0)

    # aweme_id
    aweme_id = str(item.get("aweme_id") or item.get("id") or item.get("awemeId") or "").strip()

    # group_id: trích xuất từ group_id, groupId hoặc group_id_list
    raw_group_id = item.get("group_id") or item.get("groupId")
    if raw_group_id is None and isinstance(item.get("group_id_list"), dict):
        gid_list = item.get("group_id_list", {}).get("GroupdIdList0") or item.get("group_id_list", {}).get("GroupdIdList1")
        if gid_list and len(gid_list) > 0:
            raw_group_id = gid_list[0]

    # is_dup (theo chuẩn TikTokManager):
    # 1. Cluster Leader mismatch (Mobile API / aweme_list): raw_group_id != aweme_id
    #    Đây là source of truth duy nhất - cùng nội dung gốc sẽ có cùng group_id.
    # 2. Explicit dup flags: isDup hoặc is_dup được set True (thường từ backend/DB)
    # KHÔNG DÙNG: originalItem is False - đây là field Web API không liên quan đến duplicate.
    has_dup_flag = bool(item.get("isDup") is True or item.get("is_dup") is True)
    is_cluster_dup = bool(raw_group_id is not None and str(raw_group_id).strip() and str(raw_group_id).strip() != aweme_id)
    is_dup = bool(is_cluster_dup or has_dup_flag)

    group_id = str(raw_group_id).strip() if raw_group_id is not None and str(raw_group_id).strip() else aweme_id

    # create_time & 48-hour window
    now_sec = int(now_timestamp if now_timestamp is not None else time.time())
    create_time = int(item.get("create_time") or item.get("createTime") or 0)
    is_under_48h = bool(create_time > 0 and (now_sec - create_time) < FORTY_EIGHT_HOURS_SECONDS)

    # Status & Nested Status (Mobile / Studio schema)
    status_obj = item.get("status") if isinstance(item.get("status"), dict) else {}

    raw_in_reviewing = item.get("in_reviewing")
    if raw_in_reviewing is None:
        raw_in_reviewing = status_obj.get("in_reviewing")
    is_reviewing = bool(raw_in_reviewing is True or str(raw_in_reviewing).strip() in ("1", "true"))

    display_penalty_type = int(item.get("display_penalty_type") or 0)
    is_prohibited = bool(status_obj.get("is_prohibited"))
    is_self_see = bool(status_obj.get("self_see"))
    is_nff = bool(item.get("is_nff_or_nr") or status_obj.get("is_nff_or_nr"))

    has_penalty = bool(display_penalty_type != 0 or is_prohibited or is_self_see or is_nff)

    shadow_reason = None
    if display_penalty_type != 0:
        shadow_reason = f"Án phạt reach (code={display_penalty_type})"
    elif is_nff:
        shadow_reason = "Không lên FYP (NFF/NR)"
    elif is_prohibited:
        shadow_reason = "Bị cấm hiển thị"
    elif is_self_see:
        shadow_reason = "Chỉ mình tôi (self_see)"
    elif is_reviewing:
        shadow_reason = "Đang review (in_reviewing=1)"

    # Stats
    stats = item.get("statistics") or item.get("stats") or {}
    play_count = int(stats.get("play_count") or stats.get("playCount") or item.get("play") or 0)
    digg_count = int(stats.get("digg_count") or stats.get("diggCount") or item.get("like") or 0)
    comment_count = int(stats.get("comment_count") or stats.get("commentCount") or item.get("comment") or 0)
    share_count = int(stats.get("share_count") or stats.get("shareCount") or 0)
    collect_count = int(stats.get("collect_count") or stats.get("collectCount") or 0)

    # Video details: duration, cover
    video_info = item.get("video") if isinstance(item.get("video"), dict) else {}
    duration = int(video_info.get("duration") or item.get("duration") or 0)

    cover_url = ""
    for ckey in ("origin_cover", "dynamic_cover", "cover", "cover_url"):
        cobj = video_info.get(ckey) or item.get(ckey)
        if isinstance(cobj, dict):
            u_list = cobj.get("url_list") or []
            if u_list:
                cover_url = u_list[0]
                break
        elif isinstance(cobj, str) and cobj:
            cover_url = cobj
            break

    # Privacy & Delete status
    is_private = bool(item.get("is_private") or status_obj.get("is_private") or status_obj.get("private_status") == 1)
    is_delete = bool(item.get("is_delete") or status_obj.get("is_delete"))

    # Region
    region = str(item.get("region") or "").upper()

    # Description
    desc = str(item.get("desc") or item.get("description") or "")

    # Source
    source = detect_video_source(item)

    return VideoItemAnalysis(
        aweme_id=aweme_id,
        group_id=group_id,
        desc=desc,
        create_time=create_time,
        is_dup=is_dup,
        is_under_48h=is_under_48h,
        play_count=play_count,
        is_shadow=bool(has_penalty or is_reviewing),
        shadow_reason=shadow_reason,
        source=source,
        digg_count=digg_count,
        comment_count=comment_count,
        share_count=share_count,
        collect_count=collect_count,
        duration=duration,
        cover_url=cover_url,
        is_private=is_private,
        is_delete=is_delete,
        region=region,
    )


# ============================================================================
# 4. TIKTOK DEDUP API CLIENT
# ============================================================================

def format_cookie_header(cookies_input: Union[str, List[Dict[str, Any]]]) -> str:
    """Chuyển đổi cookies (chuỗi hoặc danh sách dict) thành Header Cookie HTTP chuẩn."""
    if not cookies_input:
        return ""
    if isinstance(cookies_input, str):
        cookies_str = cookies_input.strip()
        if cookies_str.startswith("[") and cookies_str.endswith("]"):
            try:
                parsed = json.loads(cookies_str)
                if isinstance(parsed, list):
                    cookies_input = parsed
                else:
                    return cookies_str
            except Exception:
                return cookies_str
        else:
            return cookies_str

    if isinstance(cookies_input, list):
        pairs = []
        for c in cookies_input:
            if isinstance(c, dict):
                name = str(c.get("name") or "").strip()
                val = str(c.get("value") or "").strip()
                if name:
                    pairs.append(f"{name}={val}")
        return "; ".join(pairs)

    return ""


class TikTokDedupApiClient:
    """Client giao tiếp với TikTok Web API và TikTok Studio API."""

    def __init__(self, context: ProfileContext, timeout: float = 15.0):
        self.context = context
        self.timeout = timeout

        proxy_cfg = None
        if context.proxy_string:
            proxy_cfg = {
                "use_proxy": True,
                "proxy_type": context.proxy_type or "http",
                "proxy_string": context.proxy_string,
            }

        self.session = create_tls_session(proxy_cfg=proxy_cfg, timeout=timeout)
        cookie_header = format_cookie_header(context.cookies)

        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Cookie": cookie_header,
            "Referer": "https://www.tiktok.com/tiktokstudio/content",
            "Origin": "https://www.tiktok.com",
            "Accept": "application/json, text/plain, */*",
        })

    def fetch_post_feed(self, count: int = 35, sec_uid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lấy feed bài đăng của tài khoản qua API Web."""
        target_sec_uid = sec_uid or self.context.sec_uid or ""
        url = "https://www.tiktok.com/api/post/item_list/"
        params = {
            "count": count,
            "secUid": target_sec_uid,
            "cursor": 0,
            "type": 1,
        }
        resp = self.session.get(url, params=params, timeout=self.timeout)
        if resp.status_code != 200:
            logger.warning(f"Fetch feed thất bại: HTTP {resp.status_code} ({resp.text[:100]})")
            return []
        try:
            data = resp.json()
            return data.get("itemList") or data.get("aweme_list") or []
        except Exception as e:
            logger.warning(f"Lỗi parse JSON feed: {e}")
            return []

    @staticmethod
    def extract_username(channel_or_url: str) -> str:
        """Trích xuất username chuẩn hóa từ link hoặc chuỗi @username."""
        text = str(channel_or_url or "").strip()
        m = re.search(r"tiktok\.com/@([A-Za-z0-9._]+)", text)
        if m:
            return m.group(1)
        if text.startswith("@"):
            return text[1:]
        return text

    def resolve_channel_metadata(self, channel_or_url: str) -> Dict[str, Any]:
        """Lấy thông tin kênh (secUid, nickname, followers, videoCount) từ trang TikTok công khai."""
        username = self.extract_username(channel_or_url)
        if not username:
            return {}

        url = f"https://www.tiktok.com/@{username}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            html = resp.text

            sec_m = re.search(r'"secUid":"([^"]+)"', html)
            uid_m = re.search(r'"uniqueId":"([^"]+)"', html)
            nick_m = re.search(r'"nickname":"([^"]*)"', html)
            ava_m = re.search(r'"avatarThumb":"([^"]+)"', html)
            fol_m = re.search(r'"followerCount":(\d+)', html)
            vid_m = re.search(r'"videoCount":(\d+)', html)

            data = {
                "uniqueId": uid_m.group(1) if uid_m else username,
                "nickname": nick_m.group(1) if nick_m else username,
                "secUid": sec_m.group(1) if sec_m else "",
                "avatar": ava_m.group(1) if ava_m else "",
                "followers": int(fol_m.group(1)) if fol_m else 0,
                "videoCount": int(vid_m.group(1)) if vid_m else 0,
            }
            if data.get("secUid"):
                self.context.sec_uid = data["secUid"]
            return data
        except Exception as e:
            logger.warning(f"Lỗi khi resolve channel {username}: {e}")
            return {"uniqueId": username, "nickname": username, "secUid": "", "followers": 0, "videoCount": 0}

    @classmethod
    def fetch_posts_browser(
        cls,
        username: str,
        timeout_ms: int = 25000,
        cookies: Union[str, List[Dict[str, Any]], None] = None,
    ) -> List[Dict[str, Any]]:
        """
        Thu thập danh sách bài viết trực tiếp từ TikTok bằng Headless Browser (Patchright/Playwright).

        Ưu tiên bắt /aweme/v1/aweme/post/ (Mobile API - có group_id thực).
        Fallback: /api/post/item_list/ (Web API - không có group_id).

        cookies: Nếu được cung cấp (JSON list hoặc raw string) sẽ được inject vào browser
        context để TikTok nhận ra session đã đăng nhập → trả về Mobile API có group_id.

        QUAN TRỌNG: Chỉ lưu cache nếu data là aweme_list schema (có group_id).
        KHÔNG lưu itemList schema vào cache vì sẽ block Mobile API ở lần sau.
        """
        uname = cls.extract_username(username)
        if not uname:
            return []

        # Parse cookies thành dạng playwright mong đợi: list of dicts với 'name', 'value', 'domain'
        pw_cookies: List[Dict[str, Any]] = []
        if cookies:
            raw_list: List[Dict[str, Any]] = []
            if isinstance(cookies, list):
                raw_list = cookies
            elif isinstance(cookies, str):
                stripped = cookies.strip()
                if stripped.startswith("["):
                    try:
                        raw_list = json.loads(stripped)
                    except Exception:
                        # Parse raw key=value; key=value string
                        for part in stripped.split(";"):
                            if "=" in part:
                                k, v = part.strip().split("=", 1)
                                raw_list.append({"name": k, "value": v})
                else:
                    # Raw cookie string
                    for part in stripped.split(";"):
                        if "=" in part:
                            k, v = part.strip().split("=", 1)
                            raw_list.append({"name": k, "value": v})
            for c in raw_list:
                name = c.get("name") or c.get("key", "")
                value = c.get("value", "")
                if not name:
                    continue
                domain = c.get("domain", ".tiktok.com")
                if not domain.startswith("."):
                    domain = "." + domain
                pw_cookie: Dict[str, Any] = {
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": c.get("path", "/"),
                    "secure": bool(c.get("secure", True)),
                    "httpOnly": bool(c.get("httpOnly", False)),
                }
                if c.get("expiry") or c.get("expires"):
                    pw_cookie["expires"] = int(c.get("expiry") or c.get("expires"))
                pw_cookies.append(pw_cookie)

        try:
            import asyncio
            from patchright.async_api import async_playwright

            async def _run():
                aweme_list_items = []   # Mobile API /aweme/v1/aweme/post/ - có group_id
                item_list_items = []    # Web API /api/post/item_list/ - không có group_id

                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    ctx = await browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    )
                    # Inject cookies nếu có → TikTok nhận diện session đăng nhập
                    if pw_cookies:
                        try:
                            await ctx.add_cookies(pw_cookies)
                            logger.debug(f"Injected {len(pw_cookies)} cookies into browser for @{uname}")
                        except Exception as ce:
                            logger.debug(f"Cookie inject warning: {ce}")

                    page = await ctx.new_page()

                    async def on_resp(res):
                        url = res.url
                        # Priority 1: Mobile API (có group_id)
                        if "/aweme/v1/aweme/post/" in url or "/aweme/v1/post/" in url:
                            try:
                                data = await res.json()
                                items = data.get("aweme_list") or []
                                if items:
                                    aweme_list_items.extend(items)
                                    logger.debug(f"Browser captured {len(items)} aweme_list items (with group_id) for @{uname}")
                            except Exception:
                                pass
                        # Fallback: Web API (không có group_id)
                        elif "item_list" in url and "tiktok.com" in url:
                            try:
                                data = await res.json()
                                items = data.get("itemList") or data.get("aweme_list") or []
                                if items:
                                    item_list_items.extend(items)
                                    logger.debug(f"Browser captured {len(items)} itemList items (no group_id) for @{uname}")
                            except Exception:
                                pass

                    page.on("response", on_resp)
                    try:
                        await page.goto(f"https://www.tiktok.com/@{uname}", timeout=timeout_ms, wait_until="networkidle")
                        await page.wait_for_timeout(3000)
                        if not aweme_list_items and not item_list_items:
                            await page.evaluate("window.scrollBy(0, 1200)")
                            await page.wait_for_timeout(3000)
                    except Exception as ex:
                        logger.debug(f"Browser navigation warning for @{uname}: {ex}")
                    finally:
                        await browser.close()

                return aweme_list_items, item_list_items

            aweme_items, web_items = asyncio.run(_run())

            # Ưu tiên Mobile API (có group_id)
            if aweme_items:
                try:
                    feed_dir = Path(__file__).resolve().parent / "data" / "feeds"
                    feed_dir.mkdir(parents=True, exist_ok=True)
                    with open(feed_dir / f"{uname}_mobile.json", "w", encoding="utf-8") as f:
                        json.dump({"aweme_list": aweme_items}, f, ensure_ascii=False, indent=2)
                    logger.debug(f"Saved {len(aweme_items)} aweme_list items (with group_id) to cache for @{uname}")
                except Exception:
                    pass
                return aweme_items

            # Fallback Web API - KHÔNG lưu cache (không có group_id - sẽ gây false negative)
            if web_items:
                logger.debug(f"Browser fallback: {len(web_items)} itemList items (no group_id) for @{uname} - NOT caching")
                return web_items

            return []
        except Exception as e:
            logger.debug(f"Browser fetch error for @{uname}: {e}")
            return []

    def fetch_aweme_post_mobile(self, sec_uid: str, count: int = 35, max_cursor: int = 0) -> List[Dict[str, Any]]:
        """
        Gọi TikTok Mobile API /aweme/v1/aweme/post/ để lấy danh sách bài viết có group_id thực.
        Đây là ENDPOINT CHÍNH TikTokManager sử dụng (bóc tách từ decompile CheckDupView.js).
        
        Kết quả trả về là aweme_list schema với:
        - aweme_id: ID bài đăng
        - group_id: ID nhóm (cluster). Nếu group_id != aweme_id → video trùng lặp (isDup=True)
        - isDup: bool - TikTok đánh dấu trực tiếp
        - statistics.play_count, digg_count, etc.
        """
        if not sec_uid:
            return []

        url = "https://www.tiktok.com/aweme/v1/aweme/post/"
        params = {
            "count": count,
            "sec_user_id": sec_uid,
            "max_cursor": max_cursor,
            "aid": "1988",
            "app_name": "tiktok_web",
        }
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                items = data.get("aweme_list") or []
                if items:
                    logger.debug(f"Mobile API /aweme/v1/aweme/post/ OK: {len(items)} items")
                    return items
                logger.debug(f"Mobile API returned empty aweme_list, status={data.get('status_code')}")
        except Exception as e:
            logger.debug(f"Mobile API /aweme/v1/aweme/post/ error: {e}")
        return []

    def fetch_profile_posts(self, count: int = 35) -> List[Dict[str, Any]]:
        """
        Lấy danh sị bài viết của Profile với group_id thực (theo chuẩn TikTokManager).

        THỨ TỰ ƯU TIÊN (bóc tách từ CheckDupView.js decompile):
        1. Mobile API /aweme/v1/aweme/post/ → aweme_list schema, có group_id thực + isDup
           Đây là endpoint TikTokManager dùng qua api.checkDupLoad() IPC call
        2. Browser (Patchright) capture /aweme/v1/aweme/post/ → same schema
        3. Creator Studio API → có thể có group_id nhưng cần cookie đúng account
        4. Web Feed API (/api/post/item_list/) → KHÔNG có group_id, sẽ detect 0 dup
        5. Cache data/feeds/{user}_mobile.json → aweme_list, có group_id (từ browser capture)
        6. Cache data/feeds/{user}.json → itemList, KHÔNG có group_id (offline fallback)
        """
        uname = self.extract_username(self.context.tiktok_account) if self.context.tiktok_account else ""
        sec_uid = self.context.sec_uid or ""

        # 0. Resolve sec_uid nếu chưa có
        if not sec_uid and uname:
            meta = self.resolve_channel_metadata(uname)
            sec_uid = meta.get("secUid", "")
            if sec_uid:
                self.context.sec_uid = sec_uid

        # 1. Mobile API /aweme/v1/aweme/post/ → group_id thực (TikTokManager endpoint)
        if sec_uid:
            items = self.fetch_aweme_post_mobile(sec_uid, count=count)
            if items:
                logger.debug(f"Mobile API OK: {len(items)} items with group_id for @{uname}")
                return items

        # 2. Browser (Patchright) capture /aweme/v1/aweme/post/ → group_id
        #    Inject cookies của profile để TikTok nhận session đăng nhập → Mobile API có group_id
        if uname:
            browser_posts = self.fetch_posts_browser(
                uname,
                cookies=self.context.cookies if self.context.cookies else None,
            )
            if browser_posts:
                # Chỉ tin nếu là aweme_list schema (có group_id)
                has_group_id = any(item.get("group_id") for item in browser_posts[:3])
                if has_group_id:
                    logger.debug(f"Browser OK (aweme_list): {len(browser_posts)} items for @{uname}")
                    return browser_posts
                # itemList schema - không có group_id - ghi nhận nhưng không dùng ngay
                logger.debug(f"Browser captured itemList (no group_id) for @{uname}, continuing to try other sources")
                web_fallback = browser_posts  # Sẽ dùng nếu tất cả fail
            else:
                web_fallback = None
        else:
            web_fallback = None

        # 3. Creator Studio API (có group_id nếu cookie đúng account)
        try:
            url = "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/"
            cookies_dict = {}
            if isinstance(self.context.cookies, str):
                for part in self.context.cookies.split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        cookies_dict[k] = v
            csrf = cookies_dict.get("tt-csrf-token", "")
            headers = {
                "tt-csrf-token": csrf,
                "Referer": "https://www.tiktok.com/creator-center/content",
            }
            body = {
                "conditions": [],
                "sort_orders": [{"field_name": "post_time", "order": "desc"}],
                "cursor": 0,
                "count": count,
            }
            resp = self.session.post(url, json=body, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                status_code = data.get("status_code", -1)
                items = data.get("items") or data.get("itemList") or data.get("aweme_list") or []
                if items and status_code in (0, None):
                    logger.debug(f"Creator Studio API OK: {len(items)} items for @{uname}")
                    return items
                else:
                    logger.debug(f"Creator Studio API returned status={status_code}, no items")
        except Exception as e:
            logger.debug(f"Creator Studio item_list error: {e}")

        # 4. Web Feed API (KHÔNG có group_id - sẽ detect 0 dup, chỉ dùng khi không còn cách nào)
        if sec_uid:
            feed = self.fetch_post_feed(count=count, sec_uid=sec_uid)
            if feed:
                logger.debug(f"Web Feed API fallback: {len(feed)} items for @{uname} (no group_id, will detect 0 dup)")
                return feed

        # 5. Cache Mobile (aweme_list, có group_id) - từ browser capture lần trước
        if uname:
            mobile_cache = Path(__file__).resolve().parent / "data" / "feeds" / f"{uname}_mobile.json"
            if mobile_cache.exists():
                try:
                    with open(mobile_cache, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                        cand = raw.get("aweme_list") or []
                        if cand:
                            logger.debug(f"Mobile cache hit: {len(cand)} items for @{uname}")
                            return cand
                except Exception:
                    pass

        # 6. Dùng browser itemList nếu đã capture được (không có group_id)
        if web_fallback:
            logger.debug(f"Using browser itemList fallback: {len(web_fallback)} items for @{uname} (no group_id)")
            return web_fallback

        # 7. Cache Web Feed (itemList, KHÔNG có group_id) - offline last resort
        if uname:
            cached_feed = Path(__file__).resolve().parent / "data" / "feeds" / f"{uname}.json"
            if cached_feed.exists():
                try:
                    with open(cached_feed, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                        cand = raw.get("itemList") or raw.get("aweme_list") or (raw if isinstance(raw, list) else [])
                        if cand:
                            logger.debug(f"Web cache last resort: {len(cand)} items from {cached_feed.name} (no group_id)")
                            return cand
                except Exception:
                    pass

        # 8. Fallback cỹc cùng: Đọc từ các nguồn dữ liệu mẫu nếu khớp kênh
        candidates = [
            Path(__file__).resolve().parent / "datares.txt",
            Path(r"C:\Users\huynh\AppData\Local\Programs\tiktokmanager\_decompiled\datares.txt"),
            Path(__file__).resolve().parent / "scratch" / "datares.txt",
            Path(__file__).resolve().parent / "scratch" / "captured_feed.json",
        ]
        for c in candidates:
            if c.exists():
                try:
                    with open(c, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                        cand_list = raw.get("aweme_list") or (raw if isinstance(raw, list) else raw.get("itemList") or [])
                        if cand_list:
                            first_auth = (cand_list[0].get("author") or {}).get("unique_id") if isinstance(cand_list[0], dict) else None
                            if not uname or (first_auth and first_auth.lower() == uname.lower()):
                                return cand_list
                except Exception:
                    pass

        return []

    def fetch_channel_posts(self, channel_or_url: str, count: int = 35) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Tải bài đăng của kênh từ các nguồn khả dụng (Feed cache, Browser scraper, Web API)."""
        username = self.extract_username(channel_or_url)
        channel_info = self.resolve_channel_metadata(username)
        sec_uid = channel_info.get("secUid") or self.context.sec_uid

        posts = []

        # 1. Kiểm tra cache riêng theo username (ví dụ: data/feeds/user61172346518401.json)
        cached_file = Path(__file__).resolve().parent / "data" / "feeds" / f"{username}.json"
        if cached_file.exists():
            try:
                with open(cached_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    cand_list = raw.get("itemList") or raw.get("aweme_list") or (raw if isinstance(raw, list) else [])
                    if cand_list:
                        posts = cand_list
            except Exception:
                pass

        # 2. Nếu chưa có từ cache, gọi API Web với sec_uid
        if not posts and sec_uid:
            posts = self.fetch_post_feed(count=count, sec_uid=sec_uid)

        # 3. Nếu vẫn rỗng do anti-bot, thu thập bằng Headless Browser
        if not posts and username:
            posts = self.fetch_posts_browser(username)

        # 4. Fallback cuối cùng: dùng candidate payload nếu khớp username
        if not posts:
            candidates = [
                Path(__file__).resolve().parent / "datares.txt",
                Path(r"C:\Users\huynh\AppData\Local\Programs\tiktokmanager\_decompiled\datares.txt"),
                Path(__file__).resolve().parent / "scratch" / "datares.txt",
                Path(__file__).resolve().parent / "scratch" / "captured_feed.json",
            ]
            for c in candidates:
                if c.exists():
                    try:
                        with open(c, "r", encoding="utf-8") as f:
                            raw = json.load(f)
                            cand_list = raw.get("aweme_list") or (raw if isinstance(raw, list) else raw.get("itemList") or [])
                            if cand_list:
                                first_auth = (cand_list[0].get("author") or {}).get("unique_id") if isinstance(cand_list[0], dict) else None
                                if not username or (first_auth and first_auth.lower() == username.lower()):
                                    posts = cand_list
                                    break
                    except Exception:
                        pass

        # Bổ sung thông tin kênh nếu thiếu
        if posts and (not channel_info.get("uniqueId") or channel_info.get("uniqueId") == username):
            first_p = posts[0] if isinstance(posts[0], dict) else {}
            auth = first_p.get("author") or {}
            if auth.get("unique_id"):
                channel_info["uniqueId"] = auth.get("unique_id")
                channel_info["nickname"] = auth.get("nickname") or auth.get("unique_id")
                channel_info["secUid"] = auth.get("sec_uid") or channel_info.get("secUid", "")
                if not channel_info.get("videoCount"):
                    channel_info["videoCount"] = len(posts)

        return channel_info, posts

    def delete_post(self, aweme_id: str) -> bool:
        """Xóa bài viết vĩnh viễn qua TikTok Studio Web API."""
        url = "https://www.tiktok.com/tiktokstudio/api/web/item/delete"
        payload = {"item_id": str(aweme_id)}
        headers = {"Content-Type": "application/json"}
        resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code == 200:
            try:
                res_data = resp.json()
                return res_data.get("status_code") == 0
            except Exception:
                return False
        return False

    def set_post_private(self, aweme_id: str) -> bool:
        """Chuyển quyền riêng tư bài viết sang Private (Chỉ mình tôi)."""
        url = "https://www.tiktok.com/tiktokstudio/api/web/item/privacy"
        payload = {"item_id": str(aweme_id), "privacy": 1}
        headers = {"Content-Type": "application/json"}
        resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code == 200:
            try:
                res_data = resp.json()
                return res_data.get("status_code") == 0
            except Exception:
                return False
        return False


# ============================================================================
# 5. WATCHDOG WORKER & SCHEDULER
# ============================================================================

class DedupWatchdogWorker:
    """Tiến trình nền giám sát và xử lý video trùng lặp tự động."""

    def __init__(
        self,
        db: DedupDatabase,
        config: DedupConfig,
        profile_provider: Callable[[], Sequence[ProfileContext]],
        on_progress: Optional[Callable[[str], None]] = None,
    ):
        self.db = db
        self.config = config
        self.profile_provider = profile_provider
        self.on_progress = on_progress or (lambda msg: None)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._is_running = False

    def start(self) -> None:
        with self._lock:
            if self._is_running:
                return
            self._stop_event.clear()
            self._is_running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="tiktok-dedup-watchdog")
            self._thread.start()
            self.on_progress("[DedupWatchdog] Đã khởi động tiến trình giám sát video trùng lặp.")

    def stop(self) -> None:
        with self._lock:
            if not self._is_running:
                return
            self._stop_event.set()
            self._is_running = False
            self.on_progress("[DedupWatchdog] Đang dừng tiến trình giám sát...")

    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Ngủ an toàn và thoát ngay nếu _stop_event được set. Trả về False nếu bị stop."""
        return not self._stop_event.wait(timeout=seconds)

    def scan_profile_now(self, profile: ProfileContext) -> int:
        """Quét và xử lý tức thì cho 1 profile cụ thể (thủ công hoặc do worker gọi). Trả về số video trùng phát hiện.
        
        Sử dụng fetch_profile_posts() thay vì fetch_post_feed() để ưu tiên Mobile API data
        (có group_id thực) qua fallback chain: Studio API → Web Feed API → Browser Scraper → Cache.
        """
        client = TikTokDedupApiClient(profile)
        detected_count = 0
        try:
            self.on_progress(f"Đang quét bài đăng cho kênh: {profile.tiktok_account} (ID: {profile.profile_id})...")
            posts = client.fetch_profile_posts(count=35)

            for post in posts:
                if self._stop_event.is_set():
                    break

                analysis = analyze_tiktok_video(post)

                # Phát hiện trùng lặp độc lập: Bất kỳ video nào có is_dup đều được ghi nhận
                if analysis.is_dup:
                    detected_count += 1
                    msg_detected = f"⚠️ Phát hiện video trùng: aweme_id={analysis.aweme_id} (Gốc: {analysis.group_id})"
                    if analysis.is_shadow:
                        msg_detected += f" [Shadowban: {analysis.shadow_reason}]"
                    self.on_progress(msg_detected)

                    # 1. Luôn ghi nhận vào nhật ký Audit Log
                    inserted = self.db.log_detected(
                        profile_id=profile.profile_id,
                        channel=profile.tiktok_account,
                        aweme_id=analysis.aweme_id,
                        group_id=analysis.group_id,
                        desc=analysis.desc,
                        create_time=analysis.create_time,
                        action=self.config.action,
                        status="detected",
                    )

                    # 2. Thực thi hành động dựa trên cấu hình
                    action = self.config.action
                    if action == "dry_run":
                        self.on_progress("-> [DRY-RUN] Bỏ qua xử lý thực tế, chỉ ghi nhận log đối chiếu.")
                    elif action in ("delete", "hide") and self.config.only_under_48h and not analysis.is_under_48h:
                        self.on_progress(f"-> [BỎ QUA {action.upper()}] Video đã đăng > 48 giờ (chỉ tự động xử lý video < 48h để bảo vệ lịch sử kênh).")
                    elif action == "delete":
                        try:
                            ok = client.delete_post(analysis.aweme_id)
                            if ok:
                                self.db.update_log_status(
                                    profile.profile_id, analysis.aweme_id, status="deleted", deleted_at=int(time.time() * 1000)
                                )
                                self.on_progress(f"-> ✅ Đã XÓA vĩnh viễn video: {analysis.aweme_id}")
                            else:
                                self.db.update_log_status(
                                    profile.profile_id, analysis.aweme_id, status="failed", error="API delete trả về không thành công"
                                )
                                self.on_progress(f"-> ❌ Xóa video thất bại: {analysis.aweme_id}")
                        except Exception as e:
                            self.db.update_log_status(
                                profile.profile_id, analysis.aweme_id, status="failed", error=str(e)
                            )
                            self.on_progress(f"-> ❌ Lỗi khi xóa video {analysis.aweme_id}: {e}")
                    elif action == "hide":
                        try:
                            ok = client.set_post_private(analysis.aweme_id)
                            if ok:
                                self.db.update_log_status(
                                    profile.profile_id, analysis.aweme_id, status="hidden", deleted_at=int(time.time() * 1000)
                                )
                                self.on_progress(f"-> 🔒 Đã ẨN video sang Private: {analysis.aweme_id}")
                            else:
                                self.db.update_log_status(
                                    profile.profile_id, analysis.aweme_id, status="failed", error="API privacy trả về không thành công"
                                )
                                self.on_progress(f"-> ❌ Ẩn video thất bại: {analysis.aweme_id}")
                        except Exception as e:
                            self.db.update_log_status(
                                profile.profile_id, analysis.aweme_id, status="failed", error=str(e)
                            )
                            self.on_progress(f"-> ❌ Lỗi khi ẩn video {analysis.aweme_id}: {e}")

                    # Nghỉ an toàn giữa các lần xóa/ẩn
                    if action in ("delete", "hide") and self.config.action_delay_seconds > 0:
                        self._sleep_interruptible(self.config.action_delay_seconds)

        except Exception as e:
            self.on_progress(f"❌ Lỗi khi quét profile {profile.tiktok_account}: {e}")

        # Cập nhật lịch quét kế tiếp cho profile
        next_ms = int(time.time() * 1000) + int(self.config.interval_minutes * 60 * 1000)
        self.db.update_next_check(profile.profile_id, next_ms)
        return detected_count

    def _run_loop(self) -> None:
        """Vòng lặp định kỳ của Watchdog."""
        while not self._stop_event.is_set():
            if not self.config.enabled:
                self._sleep_interruptible(5.0)
                continue

            now_ms = int(time.time() * 1000)
            due_list = self.db.get_due_profiles(now_ms)

            if not due_list:
                # Không có profile nào đến hạn, nghỉ 10 giây rồi kiểm tra lại
                self._sleep_interruptible(10.0)
                continue

            available_profiles = {p.profile_id: p for p in self.profile_provider()}

            for item in due_list:
                if self._stop_event.is_set() or not self.config.enabled:
                    break

                pid = item["profile_id"]
                profile = available_profiles.get(pid)
                if not profile:
                    # Nếu profile không còn tồn tại trong cấu hình hiện tại, hoãn lại lần sau
                    self.db.update_next_check(pid, int(time.time() * 1000) + 300000)
                    continue

                self.scan_profile_now(profile)

                # Nghỉ giãn cách giữa 2 profile (gap_seconds)
                if self.config.gap_seconds > 0:
                    if not self._sleep_interruptible(float(self.config.gap_seconds)):
                        break

            self._sleep_interruptible(5.0)
