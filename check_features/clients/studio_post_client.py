# -*- coding: utf-8 -*-
"""
check_features.clients.studio_post_client - Client giao tiếp API Creator Studio & Feed cho bài đăng TikTok.
Hỗ trợ F2 (CheckPost, Penalty, Kháng Strike, Ẩn/Xóa, Phát hiện video trùng lặp).
Tuân thủ đặc tả dac_ta_chuc_nang_check_post.md & Hybrid Data Merger:
- Hợp nhất dữ liệu Feed (aweme_list / group_id thực) và Creator Studio (cờ kiểm duyệt).
- Chuẩn hóa Cookie header tự động từ JSON list / raw string.
- Tích hợp PostModerationEvaluator (Decision Table 7 bậc có data_source tracking).
- Batching 4 items cho getPenaltyDetails kèm cách ly lỗi.
- Giữ ID chuỗi an toàn 64-bit và không log thông tin nhạy cảm.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import requests

from ..models import AwemePost, PenaltyDetails, LivePostSnapshot
from ..services.post_moderation_evaluator import PostModerationEvaluator, clean_id_string, sanitize_appeal_text
from ..services.web_signer import WebSignerAdapter
from core_helpers import format_requests_proxy

logger = logging.getLogger("TikTokStudioPostClient")


def format_cookie_header(cookies_input: Any) -> str:
    """Chuyển đổi cookies (chuỗi raw, JSON string hoặc danh sách dict) thành Header Cookie HTTP chuẩn."""
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
        elif cookies_str.startswith("{") and cookies_str.endswith("}"):
            try:
                parsed = json.loads(cookies_str)
                if isinstance(parsed, dict):
                    return "; ".join(f"{k}={v}" for k, v in parsed.items())
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
    elif isinstance(cookies_input, dict):
        return "; ".join(f"{k}={v}" for k, v in cookies_input.items())

    return str(cookies_input)


def first_present(raw: Dict[str, Any], *keys: str) -> Any:
    """Trả về giá trị đầu tiên tồn tại trong raw và khác None (bảo toàn giá trị 0 hoặc False)."""
    if not isinstance(raw, dict):
        return None
    for k in keys:
        if k in raw and raw[k] is not None:
            return raw[k]
    return None


class TikTokStudioPostClient:
    """Client giao tiếp API Creator Studio & Web Feed của TikTok với kiến trúc Hybrid Data Merger."""

    def __init__(self, profile_config: Dict[str, Any], timeout: int = 15):
        self.config = profile_config or {}
        self.timeout = timeout
        self.session = requests.Session()
        self.user_agent = (
            self.config.get("user_agent")
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        self.signer = WebSignerAdapter(user_agent=self.user_agent)
        # Bộ nhớ đệm và trạng thái phân trang feed cô lập theo từng sec_uid:
        # {sec_uid: {"cache": {aid: gid}, "scanned_ids": set(), "cursor": 0, "exhausted": False}}
        self._feed_states: Dict[str, Dict[str, Any]] = {}
        self._init_session()

    def _init_session(self) -> None:
        cookie_raw = (
            self.config.get("cookie_str")
            or self.config.get("cookies")
            or self.config.get("cookie")
            or ""
        )
        cookie_str = format_cookie_header(cookie_raw)
        if cookie_str:
            self.session.headers.update({"Cookie": cookie_str})

        self.session.headers.update({
            "User-Agent": (
                self.config.get("user_agent")
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/tiktokstudio/content",
            "Accept": "application/json, text/plain, */*",
        })

        proxy_str = (self.config.get("proxy_string") or self.config.get("proxy") or "").strip()
        if proxy_str and self.config.get("use_proxy", True):
            p_scheme = str(self.config.get("proxy_type", "http") or "http").strip().lower()
            proxies = format_requests_proxy(proxy_str, scheme=p_scheme)
            if proxies:
                self.session.proxies = proxies

    def fetch_posts(self, count: int = 20, max_cursor: int = 0) -> Tuple[bool, List[AwemePost], int, bool, str]:
        """
        Lấy danh sách bài đăng có phân trang cursor theo đặc tả F2 (Hybrid Data Merger).
        Phối hợp nguồn Feed (có group_id phát hiện trùng lặp) và Creator Studio (cờ kiểm duyệt).
        Trả về (success, posts, next_cursor, has_more, message).
        """
        uname = str(self.config.get("tiktok_id") or self.config.get("tiktok_account") or "").lstrip("@")
        sec_uid = str(self.config.get("sec_uid") or "")
        proxy_str = (self.config.get("proxy_string") or self.config.get("proxy") or "").strip()
        posts: List[AwemePost] = []
        last_error_msg = ""

        # ----------------------------------------------------------------------
        # Cách 1: Creator Studio Item List API (Ưu tiên số 1 - Đầy đủ cờ kiểm duyệt)
        # ----------------------------------------------------------------------
        try:
            studio_url = "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/?locale=en&aid=1988"
            payload = {
                "cursor": max_cursor,
                "size": count,
                "query": {
                    "sort_orders": [{"field_name": "post_time", "order": 2}],
                    "conditions": [],
                    "is_recent_posts": False,
                }
            }
            cookie_str = self.session.headers.get("Cookie", "")
            headers = dict(self.session.headers)
            if cookie_str and "tt_csrf_token=" in cookie_str:
                for part in cookie_str.split(";"):
                    if "tt_csrf_token=" in part:
                        headers["tt-csrf-token"] = part.split("tt_csrf_token=")[1].split(";")[0].strip()

            signed_url, signed_headers, payload_str = self.signer.sign_request(
                studio_url,
                params=None,
                payload=payload,
                headers=headers,
                user_agent=self.user_agent,
            )
            resp = self.session.post(signed_url, json=payload, headers=signed_headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                sc = data.get("status_code")
                s_msg = str(data.get("status_msg") or data.get("message") or "")

                if (sc == 0 or sc is None) and ("item_list" in data or "itemList" in data):
                    raw_list = data.get("item_list") or data.get("itemList") or []
                    has_more = bool(data.get("has_more") or data.get("hasMore") or False)
                    next_cursor = int(data.get("cursor") or data.get("max_cursor") or (max_cursor + len(raw_list)))

                    for raw in raw_list:
                        post = self._parse_raw_post(raw, uname, data_source="creator_studio")
                        posts.append(post)

                    # BƯỚC ENRICH GROUP_ID: Nếu có sec_uid, bổ sung group_id từ feed
                    if sec_uid and posts:
                        self._enrich_posts_with_feed_group_id(posts, sec_uid, count=count, reset_pagination=(max_cursor == 0), username=uname)

                    return True, posts, next_cursor, has_more, f"Đã lấy {len(posts)} bài đăng từ Creator Studio."
                else:
                    if sc == 8 or "login" in s_msg.lower() or "expired" in s_msg.lower():
                        last_error_msg = f"Phiên đăng nhập TikTok đã hết hạn ({s_msg or 'Login expired'}). Vui lòng đăng nhập lại."
                    elif s_msg:
                        last_error_msg = f"TikTok phản hồi: {s_msg} (Mã {sc})"

                    # NẾU GẶP MÃ 1 ("internal server error" do WAF) HOẶC THÁCH THỨC CHỮ KÝ:
                    # Kích hoạt ngay StudioBrowserBridge để trích xuất dữ liệu chuẩn xác qua Trình Duyệt
                    if sc == 1 or "internal server error" in s_msg.lower():
                        logger.info("TikTok Creator Studio API trả về Mã 1. Tự động chuyển mạch sang StudioBrowserBridge...")
                        try:
                            from ..services.studio_browser_bridge import StudioBrowserBridge
                            bridge_res = StudioBrowserBridge.fetch_studio_posts(
                                self.config, profile_name=uname, count=count, timeout=self.timeout + 30
                            )
                            if bridge_res.get("success") and bridge_res.get("items"):
                                raw_list = bridge_res["items"]
                                for raw in raw_list:
                                    src = raw.get("data_source") or "browser_studio"
                                    posts.append(self._parse_raw_post(raw, uname, data_source=src))
                                if sec_uid and posts:
                                    self._enrich_posts_with_feed_group_id(
                                        posts, sec_uid, count=count, reset_pagination=(max_cursor == 0), username=uname
                                    )
                                return True, posts, len(posts), False, bridge_res.get("message") or f"Đã lấy {len(posts)} bài đăng qua Trình Duyệt."
                            elif bridge_res.get("error_code") == "PROFILE_BUSY":
                                last_error_msg = bridge_res.get("message") or "Trình duyệt profile đang bận."
                        except Exception as e_br:
                            logger.warning(f"Lỗi khi thực thi StudioBrowserBridge: {e_br}")
        except Exception as e:
            logger.debug(f"Creator Studio item_list error: {e}")

        # ----------------------------------------------------------------------
        # Cách 2: Fallback qua Web Feed API (/api/post/item_list/)
        # ----------------------------------------------------------------------
        try:
            feed_url = "https://www.tiktok.com/api/post/item_list/"
            params = {
                "count": count,
                "cursor": max_cursor,
                "type": 1,
            }
            if sec_uid:
                params["secUid"] = sec_uid

            resp_feed = self.session.get(feed_url, params=params, timeout=self.timeout)
            if resp_feed.status_code == 200:
                data = resp_feed.json()
                raw_list = data.get("itemList") or data.get("aweme_list") or []
                if raw_list:
                    has_more = bool(data.get("hasMore", False))
                    next_cursor = int(data.get("cursor") or (max_cursor + len(raw_list)))
                    for raw in raw_list:
                        posts.append(self._parse_raw_post(raw, uname, data_source="web_feed"))
                    return True, posts, next_cursor, has_more, f"Đã lấy {len(posts)} bài đăng (Web Feed)."
        except Exception as e:
            logger.debug(f"Web Feed fallback error: {e}")

        # ----------------------------------------------------------------------
        # Cách 3: Fallback qua Mobile API (/aweme/v1/aweme/post/)
        # ----------------------------------------------------------------------
        if sec_uid:
            try:
                from .mobile_post_client import TikTokMobilePostClient
                m_client = TikTokMobilePostClient(proxy_string=proxy_str, timeout=self.timeout)
                m_res = m_client.fetch_user_feed(sec_user_id=sec_uid, max_cursor=max_cursor, count=count, username=uname)
                if m_res.get("success") and m_res.get("aweme_list") and m_res.get("source") == "live":
                    has_more = bool(m_res.get("has_more", False))
                    next_cursor = int(m_res.get("max_cursor", max_cursor + len(m_res["aweme_list"])))
                    for it in m_res["aweme_list"]:
                        posts.append(self._parse_raw_post(it, uname, data_source="mobile_api"))
                    return True, posts, next_cursor, has_more, f"Đã lấy {len(posts)} bài đăng (Mobile API)."
            except Exception as e:
                logger.debug(f"Mobile Client fallback note: {e}")

            try:
                m_url = "https://www.tiktok.com/aweme/v1/aweme/post/"
                m_params = {
                    "count": count,
                    "sec_user_id": sec_uid,
                    "max_cursor": max_cursor,
                    "aid": "1988",
                    "app_name": "tiktok_web",
                }
                resp_m = self.session.get(m_url, params=m_params, timeout=self.timeout)
                if resp_m.status_code == 200 and resp_m.text.strip().startswith("{"):
                    d_m = resp_m.json()
                    m_list = d_m.get("aweme_list") or []
                    if m_list:
                        has_more = bool(d_m.get("has_more", 0))
                        next_cursor = int(d_m.get("max_cursor", 0))
                        for it in m_list:
                            posts.append(self._parse_raw_post(it, uname, data_source="mobile_api"))
                        return True, posts, next_cursor, has_more, f"Đã lấy {len(posts)} bài đăng (Mobile API)."
            except Exception as e:
                logger.debug(f"Mobile API fallback error: {e}")

        # ----------------------------------------------------------------------
        # Cách 4: Fallback qua Public Channel Client (F3 Engine qua sec_uid)
        # ----------------------------------------------------------------------
        if sec_uid:
            try:
                from .public_channel_client import TikTokPublicChannelClient
                pub_client = TikTokPublicChannelClient(proxy_str=proxy_str, timeout=self.timeout)
                ok_f, f_posts, f_cur, f_more, f_msg = pub_client.fetch_channel_videos(
                    sec_uid=sec_uid, count=count, max_cursor=max_cursor
                )
                if ok_f and f_posts:
                    for fp in f_posts:
                        fp.shadow = "unknown"
                        fp.shadow_reason = "Chưa rõ (Hết phiên Studio)" if "hết hạn" in last_error_msg.lower() else "Chưa rõ"
                        fp.data_source = "channel_feed"
                    dup_cnt = sum(1 for p in f_posts if p.is_dup)
                    return True, f_posts, f_cur, f_more, f"Đã lấy {len(f_posts)} bài đăng từ Feed, phát hiện {dup_cnt} bài trùng."
            except Exception as e:
                logger.debug(f"Public channel fallback error: {e}")

        # ----------------------------------------------------------------------
        # Cách 5: Fallback qua Offline Feed Cache (data/feeds/{uname}_mobile.json)
        # ----------------------------------------------------------------------
        if uname:
            try:
                import os
                from pathlib import Path
                feed_path = Path("data") / "feeds" / f"{uname}_mobile.json"
                if feed_path.exists():
                    with open(feed_path, "r", encoding="utf-8") as f_cache:
                        c_data = json.load(f_cache)
                    c_list = c_data.get("aweme_list") or []
                    if c_list:
                        cached_posts = []
                        for cit in c_list:
                            cp = self._parse_raw_post(cit, uname, data_source="offline_cache")
                            cp.shadow = "unknown"
                            cp.shadow_reason = "Chưa rõ (Bộ nhớ đệm Offline)"
                            cached_posts.append(cp)
                        if sec_uid and cached_posts:
                            self._enrich_posts_with_feed_group_id(cached_posts, sec_uid, count=count, username=uname)
                        dup_cnt = sum(1 for p in cached_posts if p.is_dup)
                        logger.info(f"Đã phục hồi {len(cached_posts)} bài đăng từ cache offline {feed_path}")
                        return True, cached_posts, len(cached_posts), False, f"Đã tải {len(cached_posts)} bài đăng từ Bộ nhớ đệm Offline ({dup_cnt} bài trùng)."
            except Exception as e_cache:
                logger.debug(f"Offline cache fallback error: {e_cache}")

        return False, [], max_cursor, False, last_error_msg or "Không thể tải danh sách bài đăng từ server TikTok."

    

    def fetch_live_snapshot(
        self,
        run_id: str = "",
        max_items: int = 100,
        max_pages: int = 10,
        page_size: int = 20,
        initial_cursor: int = 0,
    ) -> LivePostSnapshot:
        """
        Thu thập toàn bộ bài đăng Live từ Creator Studio qua WebSignerAdapter và phân trang an toàn.
        Tuân thủ hợp đồng Mốc 2 theo AGENTS.md:
        - Chống lặp cursor và phát hiện kẹt cursor (cursor không tịnh tiến).
        - Khử trùng lặp ID (aweme_id) giữa các trang.
        - Đóng gói DTO LivePostSnapshot với completeness 'COMPLETE' | 'PARTIAL'.
        - Tách biệt rõ ràng source 'live_studio' vs 'cache_offline'.
        - Quản lý định danh phiên run_id để tránh race condition trên UI.
        """
        import time
        import uuid
        now_ts = int(time.time())
        active_run_id = run_id or f"run_{uuid.uuid4().hex[:8]}"
        uname = str(self.config.get("tiktok_id") or self.config.get("tiktok_account") or "").lstrip("@")
        sec_uid = str(self.config.get("sec_uid") or "")

        posts: List[AwemePost] = []
        seen_ids: set[str] = set()
        seen_cursors: set[int] = set()

        cursor = initial_cursor
        has_more = True
        pages_count = 0
        completeness = "COMPLETE"
        error_msg: Optional[str] = None
        studio_contacted = False

        studio_url = "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/"

        cookie_str = self.session.headers.get("Cookie", "")
        headers_base = dict(self.session.headers)
        if cookie_str and "tt_csrf_token=" in cookie_str:
            for part in cookie_str.split(";"):
                if "tt_csrf_token=" in part:
                    headers_base["tt-csrf-token"] = part.split("tt_csrf_token=")[1].split(";")[0].strip()

        try:
            while has_more and pages_count < max_pages and len(posts) < max_items:
                # 1. Phát hiện lặp cursor
                if cursor in seen_cursors:
                    logger.warning(f"Phát hiện lặp cursor {cursor}. Dừng phân trang.")
                    completeness = "PARTIAL"
                    break
                seen_cursors.add(cursor)
                pages_count += 1

                params = {"locale": "en", "aid": "1988"}
                payload = {
                    "cursor": cursor,
                    "size": page_size,
                    "query": {
                        "sort_orders": [{"field_name": "post_time", "order": 2}],
                        "conditions": [],
                        "is_recent_posts": False,
                    }
                }

                signed_url, signed_headers, payload_str = self.signer.sign_request(
                    studio_url,
                    params=params,
                    payload=payload,
                    headers=headers_base,
                    user_agent=self.user_agent,
                )

                resp = self.session.post(
                    signed_url,
                    json=payload,
                    headers=signed_headers,
                    timeout=self.timeout
                )

                if resp.status_code != 200:
                    error_msg = f"HTTP {resp.status_code} từ Creator Studio."
                    completeness = "PARTIAL"
                    break

                data = resp.json()
                sc = data.get("status_code")
                s_msg = str(data.get("status_msg") or data.get("message") or "")

                if sc != 0 and sc is not None:
                    error_msg = f"TikTok Creator Studio trả về mã {sc}: {s_msg}"
                    completeness = "PARTIAL"
                    break

                # Đã kết nối và nhận phản hồi thành công từ Creator Studio API
                studio_contacted = True

                resp_has_more = bool(data.get("has_more") or data.get("hasMore") or False)
                raw_list = data.get("item_list") or data.get("itemList") or []
                if not raw_list:
                    if resp_has_more:
                        logger.warning("item_list rỗng nhưng API báo has_more=True. Dừng phân trang.")
                        has_more = True
                        completeness = "PARTIAL"
                        error_msg = "TikTok trả về danh sách bài rỗng dù has_more=True."
                    else:
                        has_more = False
                    break

                # 2. Khử trùng lặp theo ID và trích xuất
                for raw in raw_list:
                    post = self._parse_raw_post(raw, uname, data_source="creator_studio")
                    if post.aweme_id and post.aweme_id not in seen_ids:
                        seen_ids.add(post.aweme_id)
                        posts.append(post)

                has_more = resp_has_more
                raw_c = data.get("cursor")
                if raw_c is not None:
                    next_cursor = int(raw_c)
                elif data.get("max_cursor") is not None:
                    next_cursor = int(data["max_cursor"])
                else:
                    next_cursor = cursor + len(raw_list)

                # 3. Phát hiện kẹt cursor
                if has_more and next_cursor <= cursor:
                    logger.warning(f"Cursor không tịnh tiến ({cursor} -> {next_cursor}). Dừng phân trang.")
                    completeness = "PARTIAL"
                    break

                cursor = next_cursor

            if has_more:
                completeness = "PARTIAL"

            # 4. Enrich group_id nếu có sec_uid (được bọc try/except riêng để không làm hỏng snapshot live)
            if sec_uid and posts:
                try:
                    self._enrich_posts_with_feed_group_id(posts, sec_uid, count=len(posts), reset_pagination=True, username=uname)
                except Exception as e_feed:
                    logger.warning(f"Lỗi khi enrich feed group_id: {e_feed}")

            # Nếu đã kết nối thành công với Studio (kể cả kênh rỗng / API bất thường) hoặc đã lấy được ít nhất 1 bài
            if studio_contacted or len(posts) > 0:
                return LivePostSnapshot(
                    posts=posts,
                    source="live_studio",
                    fetched_at=now_ts,
                    completeness=completeness,
                    run_id=active_run_id,
                    error=error_msg,
                    next_cursor=cursor,
                    has_more=has_more,
                )

        except Exception as e:
            logger.warning(f"Lỗi khi thu thập LivePostSnapshot: {e}")
            error_msg = str(e)
            if len(posts) > 0:
                return LivePostSnapshot(
                    posts=posts,
                    source="live_studio",
                    fetched_at=now_ts,
                    completeness="PARTIAL",
                    run_id=active_run_id,
                    error=f"Lỗi phân trang: {error_msg}",
                    next_cursor=cursor,
                    has_more=has_more,
                )

        # ----------------------------------------------------------------------
        # Fallback Cache Offline nếu mất mạng / lỗi server hoàn toàn
        # ----------------------------------------------------------------------
        cached_posts = self._load_offline_cache_posts(uname, sec_uid)
        fallback_err = (
            f"Mất kết nối mạng ({error_msg}), hiển thị bản sao lưu offline."
            if error_msg
            else "Mất kết nối mạng, hiển thị bản sao lưu offline."
        ) if cached_posts else (error_msg or "Không lấy được dữ liệu.")

        return LivePostSnapshot(
            posts=cached_posts,
            source="cache_offline" if cached_posts else "live_studio",
            fetched_at=now_ts,
            completeness="PARTIAL",
            run_id=active_run_id,
            error=fallback_err,
            next_cursor=len(cached_posts),
            has_more=False,
        )

    def _load_offline_cache_posts(self, uname: str, sec_uid: str) -> List[AwemePost]:
        """Đọc bài đăng từ cache offline cục bộ (data/feeds/) khi hoàn toàn không có mạng."""
        import os
        candidate_paths = []
        if uname:
            candidate_paths.append(os.path.join("data", "feeds", f"{uname}_mobile.json"))
            candidate_paths.append(os.path.join("data", "feeds", f"{uname}_mobile_api.json"))
            candidate_paths.append(os.path.join("data", "feeds", f"{uname}.json"))
        if sec_uid:
            candidate_paths.append(os.path.join("data", "feeds", f"{sec_uid}_mobile.json"))
            candidate_paths.append(os.path.join("data", "feeds", f"{sec_uid}.json"))

        posts: List[AwemePost] = []
        seen_ids: set[str] = set()

        for c_path in candidate_paths:
            if not os.path.exists(c_path):
                continue
            try:
                with open(c_path, "r", encoding="utf-8") as f:
                    c_data = json.load(f)
                c_list = c_data.get("aweme_list") or c_data.get("itemList") or []
                for raw in c_list:
                    post = self._parse_raw_post(raw, uname, data_source="cache_offline")
                    if post.aweme_id and post.aweme_id not in seen_ids:
                        seen_ids.add(post.aweme_id)
                        posts.append(post)
                if posts:
                    break
            except Exception as e:
                logger.debug(f"Lỗi đọc cache offline {c_path}: {e}")

        return posts

    def _enrich_posts_with_feed_group_id(
        self,
        posts: List[AwemePost],
        sec_uid: str,
        count: int = 35,
        max_pages: int = 5,
        username: str = "",
        reset_pagination: bool = False,
    ) -> None:
        """
        Đối soát chéo danh sách bài viết với Feed API của kênh để gán group_id thực.
        Quy trình 3 tầng đối soát (Hybrid Data Merger):
        1. Nạp từ cache offline candidate (data/feeds/{uname}_mobile.json / {sec_uid}_mobile.json)
        2. Ký Mobile request qua TikTokMobilePostClient (Metasec)
        3. Fallback Web Feed API qua session.get
        """
        if not posts or not sec_uid:
            return

        state = self._feed_states.setdefault(sec_uid, {
            "cache": {},
            "scanned_ids": set(),
            "cursor": 0,
            "exhausted": False,
        })

        if reset_pagination:
            state["cursor"] = 0
            state["exhausted"] = False
            state["scanned_ids"].clear()

        feed_cache: Dict[str, str] = state["cache"]
        scanned_ids: set[str] = state["scanned_ids"]

        uname = username or str(self.config.get("tiktok_id") or self.config.get("tiktok_account") or "").lstrip("@")

        unmatched_ids = set()
        for p in posts:
            if p.aweme_id in feed_cache:
                p.group_id = feed_cache[p.aweme_id]
                p.data_source = "hybrid"
            elif p.aweme_id in scanned_ids:
                p.data_source = "hybrid"
            else:
                unmatched_ids.add(p.aweme_id)

        # 1. Thử đọc cache file offline (data/feeds/{uname}_mobile.json / {sec_uid}_mobile.json) nếu còn unmatched
        if unmatched_ids and (uname or sec_uid):
            try:
                import os
                candidate_paths = []
                if uname:
                    candidate_paths.append(os.path.join("data", "feeds", f"{uname}_mobile.json"))
                    candidate_paths.append(os.path.join("data", "feeds", f"{uname}_mobile_api.json"))
                    candidate_paths.append(os.path.join("data", "feeds", f"{uname}.json"))
                if sec_uid:
                    candidate_paths.append(os.path.join("data", "feeds", f"{sec_uid}_mobile.json"))
                    candidate_paths.append(os.path.join("data", "feeds", f"{sec_uid}.json"))

                for c_path in candidate_paths:
                    if not os.path.exists(c_path) or not unmatched_ids:
                        continue
                    with open(c_path, "r", encoding="utf-8") as f:
                        c_data = json.load(f)
                    c_list = c_data.get("aweme_list") or []
                    resolved_from_cache = set()
                    for it in c_list:
                        c_aid = clean_id_string(it.get("aweme_id") or it.get("id"))
                        if c_aid and c_aid in unmatched_ids:
                            raw_gid = it.get("group_id") or it.get("groupId")
                            if raw_gid is None and isinstance(it.get("group_id_list"), dict):
                                gid_list = it.get("group_id_list", {}).get("GroupdIdList0") or it.get("group_id_list", {}).get("GroupdIdList1")
                                if gid_list and len(gid_list) > 0:
                                    raw_gid = gid_list[0]
                            c_gid = clean_id_string(raw_gid)
                            if c_gid:
                                feed_cache[c_aid] = c_gid
                                scanned_ids.add(c_aid)
                                resolved_from_cache.add(c_aid)
                    for p in posts:
                        if p.aweme_id in resolved_from_cache:
                            p.group_id = feed_cache[p.aweme_id]
                            p.data_source = "hybrid"
                    unmatched_ids -= resolved_from_cache
            except Exception as ce:
                logger.debug(f"Feed mobile cache read note: {ce}")

        if not unmatched_ids or state["exhausted"]:
            return

        # 2. Thử TikTokMobilePostClient ký Metasec nếu còn unmatched
        if unmatched_ids and sec_uid:
            try:
                from .mobile_post_client import TikTokMobilePostClient
                proxy_str = (self.config.get("proxy_string") or self.config.get("proxy") or "").strip()
                m_client = TikTokMobilePostClient(proxy_string=proxy_str, timeout=min(6, self.timeout))
                feed_res = m_client.fetch_user_feed(
                    sec_user_id=sec_uid,
                    max_cursor=state["cursor"],
                    count=count,
                    username=uname,
                )
                if feed_res.get("success") and feed_res.get("aweme_list"):
                    g_map = feed_res.get("group_id_map", {})
                    resolved_now = set()
                    for it in feed_res.get("aweme_list", []):
                        aid = clean_id_string(it.get("aweme_id") or it.get("id"))
                        if not aid:
                            continue
                        scanned_ids.add(aid)
                        gid = g_map.get(aid) or clean_id_string(it.get("group_id") or it.get("groupId"))
                        if gid:
                            feed_cache[aid] = gid
                        if aid in unmatched_ids:
                            resolved_now.add(aid)

                    for p in posts:
                        if p.aweme_id in resolved_now:
                            p.data_source = "hybrid"
                            if p.aweme_id in feed_cache:
                                p.group_id = feed_cache[p.aweme_id]

                    unmatched_ids -= resolved_now
                    has_more = bool(feed_res.get("has_more", False))
                    next_cur = int(feed_res.get("max_cursor", 0))
                    if not has_more or next_cur <= 0 or next_cur == state["cursor"]:
                        state["exhausted"] = True
                    else:
                        state["cursor"] = next_cur
            except Exception as me:
                logger.debug(f"Mobile Post Client enrichment note: {me}")

        if not unmatched_ids or state["exhausted"]:
            return

        # 3. Fallback Web Feed API qua session.get nếu còn unmatched
        feed_url = "https://www.tiktok.com/aweme/v1/aweme/post/"
        pages_fetched = 0

        while unmatched_ids and pages_fetched < max_pages and not state["exhausted"]:
            pages_fetched += 1
            try:
                params = {
                    "count": count,
                    "sec_user_id": sec_uid,
                    "max_cursor": state["cursor"],
                    "aid": "1988",
                    "app_name": "tiktok_web",
                }
                feed_headers = {
                    "User-Agent": self.session.headers.get(
                        "User-Agent",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://www.tiktok.com/",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "en-US,en;q=0.9",
                }
                cookie_val = self.session.headers.get("Cookie")
                if cookie_val:
                    feed_headers["Cookie"] = cookie_val

                resp = self.session.get(feed_url, params=params, headers=feed_headers, timeout=min(6, self.timeout))
                if resp.status_code != 200 or not resp.text.strip().startswith("{"):
                    break

                data = resp.json()
                raw_feed = data.get("aweme_list") or []
                if not raw_feed:
                    state["exhausted"] = True
                    break

                for it in raw_feed:
                    aid = clean_id_string(it.get("aweme_id") or it.get("id"))
                    if not aid:
                        continue
                    scanned_ids.add(aid)

                    raw_gid = it.get("group_id") or it.get("groupId")
                    if raw_gid is None and isinstance(it.get("group_id_list"), dict):
                        gid_list = it.get("group_id_list", {}).get("GroupdIdList0") or it.get("group_id_list", {}).get("GroupdIdList1")
                        if gid_list and len(gid_list) > 0:
                            raw_gid = gid_list[0]
                    gid = clean_id_string(raw_gid)
                    if gid:
                        feed_cache[aid] = gid

                resolved_now = set()
                for aid in unmatched_ids:
                    if aid in scanned_ids:
                        resolved_now.add(aid)

                for p in posts:
                    if p.aweme_id in resolved_now:
                        p.data_source = "hybrid"
                        if p.aweme_id in feed_cache:
                            p.group_id = feed_cache[p.aweme_id]

                unmatched_ids -= resolved_now

                has_more = bool(data.get("has_more", 0))
                next_cur = int(data.get("max_cursor", 0))
                if not has_more or next_cur == state["cursor"] or next_cur <= 0:
                    state["exhausted"] = True
                    break
                state["cursor"] = next_cur

            except Exception as e:
                logger.debug(f"Feed group_id enrichment page {pages_fetched} error: {e}")
                break

    def _parse_raw_post(self, it: Dict[str, Any], username: str, data_source: str = "creator_studio") -> AwemePost:
        """Chuyển đổi từ raw TikTok item (cả schema phẳng Creator Studio lẫn schema Feed lồng nhau) sang AwemePost DTO."""
        aweme_id = clean_id_string(first_present(it, "item_id", "vid", "id", "aweme_id", "awemeId"))

        # group_id: trích xuất từ group_id, groupId hoặc group_id_list
        raw_group_id = it.get("group_id") or it.get("groupId")
        if raw_group_id is None and isinstance(it.get("group_id_list"), dict):
            gid_list = it.get("group_id_list", {}).get("GroupdIdList0") or it.get("group_id_list", {}).get("GroupdIdList1")
            if gid_list and len(gid_list) > 0:
                raw_group_id = gid_list[0]
        group_id = clean_id_string(raw_group_id)

        desc = str(first_present(it, "desc", "description") or "")
        stats = it.get("stats") or it.get("statistics") or {}
        video = it.get("video") or {}

        # URL tải không watermark (hỗ trợ cả download_urls list của Studio lẫn playAddr)
        download_url = ""
        dl_info = it.get("download_info")
        if isinstance(dl_info, dict):
            if dl_info.get("download_url"):
                download_url = dl_info["download_url"]
            elif isinstance(dl_info.get("download_urls"), list) and dl_info["download_urls"]:
                download_url = dl_info["download_urls"][0]
        if not download_url:
            raw_play = it.get("play_addr")
            if isinstance(raw_play, str) and raw_play.startswith("http"):
                download_url = raw_play
            elif isinstance(raw_play, list) and raw_play and isinstance(raw_play[0], str):
                download_url = raw_play[0]
            else:
                play_addr = video.get("playAddr") or video.get("play_addr") or {}
                url_list = play_addr.get("url_list") or [] if isinstance(play_addr, dict) else []
                if url_list:
                    download_url = url_list[0]
                elif isinstance(video.get("downloadAddr"), str):
                    download_url = video["downloadAddr"]

        # Thumbnail cover (hỗ trợ cover_url list của Studio, str và dict url_list)
        cover_url = ""
        raw_cov = it.get("cover_url") or video.get("cover") or it.get("cover") or video.get("origin_cover") or {}
        if isinstance(raw_cov, str):
            cover_url = raw_cov
        elif isinstance(raw_cov, list) and raw_cov:
            first_c = raw_cov[0]
            if isinstance(first_c, str):
                cover_url = first_c
            elif isinstance(first_c, dict):
                c_list = first_c.get("url_list") or []
                cover_url = c_list[0] if c_list else ""
        elif isinstance(raw_cov, dict):
            c_list = raw_cov.get("url_list") or []
            cover_url = c_list[0] if c_list else ""

        # Duration chuẩn hóa
        dur_raw = first_present(it, "duration") or video.get("duration") or 0
        dur_val = int(dur_raw or 0)
        if dur_val > 10000:
            dur_val = dur_val // 1000

        # Timestamp đăng bài
        ctime = int(first_present(it, "post_time", "create_time", "createTime") or 0)

        # Các chỉ số tương tác (hỗ trợ cả schema phẳng root và schema stats lồng nhau)
        play_cnt = int(first_present(it, "play_count", "playCount") or stats.get("playCount") or stats.get("play_count") or it.get("play") or 0)
        like_cnt = int(first_present(it, "like_count", "likeCount") or stats.get("diggCount") or stats.get("digg_count") or it.get("like") or 0)
        comm_cnt = int(first_present(it, "comment_count", "commentCount") or stats.get("commentCount") or stats.get("comment_count") or it.get("comment") or 0)
        share_cnt = int(first_present(it, "share_count", "shareCount") or stats.get("shareCount") or stats.get("share_count") or 0)
        save_cnt = int(first_present(it, "favorite_count", "favoriteCount") or stats.get("collectCount") or stats.get("collect_count") or 0)

        # Đánh giá qua PostModerationEvaluator (Decision Table 7 bậc có data_source)
        eval_res = PostModerationEvaluator.evaluate(it, data_source=data_source)

        # Đánh giá shadowban bổ sung theo schema Creator Studio (in_review, permissions)
        shadow_status = eval_res.shadow
        shadow_reason = eval_res.shadow_reason
        if it.get("in_review") is True or it.get("in_reviewing") is True:
            shadow_status = "suspect"
            shadow_reason = "Đang trong quá trình xét duyệt của TikTok"

        # Trạng thái riêng tư (bảo toàn giá trị: visibility == 1 là công khai)
        raw_vis = first_present(it, "visibility", "visibilityType")
        if raw_vis is not None:
            # TikTok Studio: 1 = Public (Everyone), 3 = Friends, 4 = Only Me (Private)
            is_private = int(raw_vis) in (3, 4) or bool(it.get("self_see") == 1 or it.get("secret") == 1)
        else:
            is_private = bool(
                it.get("secret") == 1
                or it.get("self_see") == 1
                or it.get("is_private") == 1
                or it.get("isPrivate") is True
            )

        is_delete = bool(it.get("is_delete") == 1 or it.get("isDelete") is True or it.get("status") == 2)
        is_pinned = bool(it.get("is_pinned") is True or it.get("is_top") is True or "Ghim" in eval_res.flags)

        share_url = f"https://www.tiktok.com/@{username}/video/{aweme_id}" if (username and aweme_id) else (
            it.get("share_info", {}).get("share_url") or f"https://www.tiktok.com/@tiktok/video/{aweme_id}"
        )

        return AwemePost(
            aweme_id=aweme_id,
            desc=desc,
            cover=cover_url,
            duration=dur_val,
            create_time=ctime,
            device=eval_res.device,
            source=eval_res.source_label,
            source_label=eval_res.source_label,
            play=play_cnt,
            like=like_cnt,
            comment=comm_cnt,
            share=share_cnt,
            save=save_cnt,
            region=str(it.get("region") or ""),
            shadow=shadow_status,
            shadow_reason=shadow_reason,
            is_top=is_pinned,
            ai_label=bool("AI" in eval_res.flags),
            risk_warn=bool("Cảnh báo" in eval_res.flags),
            is_paid=bool("Trả phí" in eval_res.flags),
            is_delete=is_delete,
            is_private=is_private,
            penalty_flag=eval_res.penalty_flag,
            group_id=group_id,
            duplicate_reason=eval_res.duplicate_reason,
            share_url=share_url,
            download_url=download_url,
            data_source=data_source,
        )

    def get_penalty_details(self, vid: str, create_time: int) -> Tuple[bool, PenaltyDetails, str]:
        """
        Lấy chi tiết án phạt (Strike) của video theo getPenaltyDetails.
        Trả về (success, PenaltyDetails, message).
        """
        clean_vid = clean_id_string(vid)
        if not clean_vid:
            return False, PenaltyDetails(has_penalty=False, error="ID video không hợp lệ."), "ID video không hợp lệ."

        endpoints = [
            "https://www.tiktok.com/mod/v1/getPenaltyDetails/",
            "https://www.tiktok.com/creator_center/api/item/penalty_detail/",
        ]

        payload = {"vid": clean_vid, "createTime": int(create_time or 0)}
        params = {"vid": clean_vid, "create_time": int(create_time or 0)}

        for ep in endpoints:
            try:
                if "/mod/v1/" in ep:
                    resp = self.session.post(ep, json=payload, timeout=self.timeout)
                else:
                    resp = self.session.get(ep, params=params, timeout=self.timeout)

                if resp.status_code == 200:
                    data = resp.json()
                    penalties = data.get("penalties") or data.get("top_penalty_details") or []
                    has_pen = bool(data.get("hasPenalty") or data.get("has_penalty") or penalties)

                    if has_pen:
                        top = penalties[0] if (penalties and isinstance(penalties[0], dict)) else data
                        p = PenaltyDetails(
                            has_penalty=True,
                            penalty_id=clean_id_string(top.get("penalty_id") or top.get("penaltyId") or clean_vid),
                            issuer=str(top.get("issuer") or "TRUST_AND_SAFETY"),
                            reason_codes=top.get("reason_codes") or top.get("reasonCodes") or ["unoriginal_content"],
                            appeal_status=int(top.get("appeal_status") or top.get("appealStatus") or 1),
                        )
                        return True, p, "Đã lấy chi tiết án phạt."
                    else:
                        return True, PenaltyDetails(has_penalty=False), "Video không có án phạt."
            except Exception as e:
                logger.debug(f"get_penalty_details endpoint {ep} error: {e}")

        return False, PenaltyDetails(has_penalty=False, error="Không thể kết nối lấy chi tiết án phạt."), "Lỗi kết nối."

    def fetch_penalties_batch(self, videos: List[Tuple[str, int]], max_workers: int = 4) -> Dict[str, PenaltyDetails]:
        """
        Tra cứu án phạt song song theo từng batch tối đa 4 video.
        Bảo đảm:
        - Mọi (vid, ctime) đầu vào đều có 1 entry trong dictionary kết quả.
        - Ghép kết quả theo ID video (không phụ thuộc thứ tự).
        - Cách ly lỗi từng phần tử (1 item lỗi không làm mất kết quả item khác).
        """
        if not videos:
            return {}

        results: Dict[str, PenaltyDetails] = {}
        # Khởi tạo mặc định để bảo đảm mọi ID đều có mặt
        for vid, _ in videos:
            results[str(vid)] = PenaltyDetails(has_penalty=False, error="Đang chờ xử lý...")

        # Chia thành các batch tối đa 4 video
        batch_size = 4
        chunks = [videos[i:i + batch_size] for i in range(0, len(videos), batch_size)]

        for chunk in chunks:
            worker_count = max(1, min(max_workers, len(chunk), 4))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_vid = {
                    executor.submit(self.get_penalty_details, vid, ctime): str(vid)
                    for vid, ctime in chunk
                }
                for future in as_completed(future_to_vid):
                    v_id = future_to_vid[future]
                    try:
                        ok, p_details, msg = future.result()
                        if ok:
                            results[v_id] = p_details
                        else:
                            results[v_id] = PenaltyDetails(has_penalty=False, error=msg)
                    except Exception as e:
                        results[v_id] = PenaltyDetails(has_penalty=False, error=str(e))

        return results

    def submit_studio_appeal(self, vid: str, penalty_id: str, reason_text: str, issuer: str = "TRUST_AND_SAFETY") -> Tuple[bool, str]:
        """Gửi kháng nghị án phạt Studio Strike theo submitStudioAppeal (giới hạn <= 400 ký tự)."""
        clean_text = sanitize_appeal_text(reason_text)
        if not clean_text:
            return False, "Nội dung kháng nghị không được để trống."
        if len(clean_text) > 400:
            return False, "Nội dung kháng nghị không được vượt quá 400 ký tự."

        endpoints = [
            ("https://www.tiktok.com/mod/v1/submitAppeals/", {
                "vid": str(vid),
                "penaltyId": str(penalty_id),
                "issuer": issuer,
                "appeal_details": clean_text,
            }),
            ("https://www.tiktok.com/creator_center/api/item/appeal/", {
                "vid": str(vid),
                "penalty_id": str(penalty_id),
                "description": clean_text,
            }),
        ]

        for ep, payload in endpoints:
            try:
                resp = self.session.post(ep, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    res_data = resp.json()
                    if res_data.get("status_code") == 0 or res_data.get("success") is True or res_data.get("message") == "success":
                        return True, "Đã gửi kháng nghị Strike thành công! Đang chờ TikTok xét duyệt."
                    err = res_data.get("status_message") or res_data.get("status_msg") or res_data.get("message")
                    if err:
                        return False, f"TikTok thông báo: {err}"
            except Exception as e:
                logger.debug(f"submit_studio_appeal ep {ep} error: {e}")

        return False, "Không thể gửi kháng nghị vào lúc này. Vui lòng thử lại sau."

    def hide_studio_item(self, aweme_id: str) -> Tuple[bool, str]:
        """Ẩn video sang chế độ Riêng tư (hideStudioItem)."""
        clean_id = clean_id_string(aweme_id)
        if not clean_id:
            return False, "ID video không hợp lệ."

        endpoints = [
            ("https://www.tiktok.com/tiktokstudio/api/web/item/visibility", {"itemId": clean_id, "visibilityType": 1}),
            ("https://www.tiktok.com/creator_center/api/item/visibility/", {"item_id": clean_id, "visibility": 1}),
        ]

        for ep, payload in endpoints:
            try:
                resp = self.session.post(ep, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    res_data = resp.json()
                    if res_data.get("status_code") == 0 or res_data.get("success") is True or res_data.get("message") == "success":
                        return True, "Đã chuyển video sang chế độ Riêng tư thành công."
            except Exception as e:
                logger.debug(f"hide_studio_item ep {ep} error: {e}")

        return False, "Không thể ẩn video vào lúc này."

    def delete_studio_item(self, aweme_id: str) -> Tuple[bool, str]:
        """Xóa vĩnh viễn video khỏi kênh (deleteStudioItem)."""
        clean_id = clean_id_string(aweme_id)
        if not clean_id:
            return False, "ID video không hợp lệ."

        endpoints = [
            ("https://www.tiktok.com/tiktokstudio/api/web/item/delete", {"itemId": clean_id}),
            ("https://www.tiktok.com/creator_center/api/item/delete/", {"item_id": clean_id}),
        ]

        for ep, payload in endpoints:
            try:
                resp = self.session.post(ep, json=payload, timeout=self.timeout)
                if resp.status_code == 200:
                    res_data = resp.json()
                    if res_data.get("status_code") == 0 or res_data.get("success") is True or res_data.get("message") == "success":
                        return True, "Đã xóa video vĩnh viễn khỏi kênh TikTok."
            except Exception as e:
                logger.debug(f"delete_studio_item ep {ep} error: {e}")

        return False, "Không thể xóa video vào lúc này."
