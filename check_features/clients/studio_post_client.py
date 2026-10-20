# -*- coding: utf-8 -*-
"""
check_features.clients.studio_post_client - Client giao tiếp API Creator Studio cho bài đăng TikTok.
Hỗ trợ F2 (CheckPost, Penalty, Kháng Strike, Ẩn/Xóa).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple
import requests

from ..models import AwemePost
from ..models import PenaltyDetails
from core_helpers import format_requests_proxy

logger = logging.getLogger("TikTokStudioPostClient")


class TikTokStudioPostClient:
    """Client giao tiếp API Creator Studio & Web Feed của TikTok."""

    def __init__(self, profile_config: Dict[str, Any], timeout: int = 15):
        self.config = profile_config or {}
        self.timeout = timeout
        self.session = requests.Session()
        self._init_session()

    def _init_session(self) -> None:
        cookie_str = (
            self.config.get("cookie_str")
            or self.config.get("cookies")
            or self.config.get("cookie")
            or ""
        )
        if cookie_str:
            self.session.headers.update({"Cookie": str(cookie_str).strip()})

        self.session.headers.update({
            "User-Agent": (
                self.config.get("user_agent")
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/creator-center",
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
        Lấy danh sách bài đăng có phân trang cursor theo đặc tả checkPost.
        Trả về (success, posts, next_cursor, has_more, message).
        """
        endpoint = "https://www.tiktok.com/api/post/item_list/"
        sec_uid = str(self.config.get("sec_uid") or "")
        uname = str(self.config.get("tiktok_id") or self.config.get("tiktok_account") or "").lstrip("@")

        params = {
            "count": count,
            "cursor": max_cursor,
            "type": 1,
        }
        if sec_uid:
            params["secUid"] = sec_uid

        posts: List[AwemePost] = []
        try:
            resp = self.session.get(endpoint, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                raw_list = data.get("itemList") or data.get("aweme_list") or []
                has_more = bool(data.get("hasMore", False))
                next_cursor = int(data.get("cursor") or (max_cursor + len(raw_list)))

                for item in raw_list:
                    post = self._parse_raw_post(item, uname)
                    posts.append(post)

                return True, posts, next_cursor, has_more, f"Đã lấy {len(posts)} bài đăng."

        except Exception as e:
            logger.debug(f"fetch_posts error: {e}")

        # Fallback qua Mobile API nếu có sec_uid
        if sec_uid:
            try:
                m_url = "https://www.tiktok.com/aweme/v1/aweme/post/"
                m_params = {
                    "count": count,
                    "sec_user_id": sec_uid,
                    "max_cursor": max_cursor,
                    "aid": "1988",
                }
                resp_m = self.session.get(m_url, params=m_params, timeout=self.timeout)
                if resp_m.status_code == 200:
                    d_m = resp_m.json()
                    m_list = d_m.get("aweme_list") or []
                    has_more = bool(d_m.get("has_more", 0))
                    next_cursor = int(d_m.get("max_cursor", 0))
                    for it in m_list:
                        posts.append(self._parse_raw_post(it, uname))
                    return True, posts, next_cursor, has_more, f"Đã lấy {len(posts)} bài đăng (Mobile API)."
            except Exception as e:
                logger.debug(f"Mobile fallback error: {e}")

        return False, [], max_cursor, False, "Không thể tải danh sách bài đăng từ server TikTok."

    def _parse_raw_post(self, it: Dict[str, Any], username: str) -> AwemePost:
        """Chuyển đổi từ raw TikTok item sang AwemePost DTO."""
        aweme_id = str(it.get("id") or it.get("aweme_id") or "")
        desc = str(it.get("desc") or "")
        stats = it.get("stats") or it.get("statistics") or {}
        video = it.get("video") or {}

        # URL tải không watermark
        download_url = ""
        play_addr = video.get("playAddr") or video.get("play_addr") or {}
        url_list = play_addr.get("url_list") or [] if isinstance(play_addr, dict) else []
        if url_list:
            download_url = url_list[0]
        elif isinstance(video.get("downloadAddr"), str):
            download_url = video["downloadAddr"]

        # Thumbnail cover
        cover_url = ""
        cov = video.get("cover") or it.get("cover") or {}
        if isinstance(cov, dict):
            c_list = cov.get("url_list") or []
            cover_url = c_list[0] if c_list else ""
        elif isinstance(cov, str):
            cover_url = cov

        # Group ID (phát hiện video trùng/repost)
        group_id = str(it.get("group_id") or it.get("groupId") or "")
        if not group_id:
            # Check trong text extra hoặc dupe
            group_id = str(it.get("item_comment_settings") or "")

        # Penalty flag
        penalty_flag = bool(
            it.get("risk_warn")
            or it.get("warn_info")
            or it.get("penaltyFlag")
            or it.get("is_restricted")
        )

        # Shadowban detect
        shadow = "no"
        shadow_reason = ""
        if it.get("is_restricted") or "shadow" in str(it).lower():
            shadow = "yes"
            shadow_reason = "Bị bóp phân phối / Hạn chế hiển thị"
        elif it.get("risk_warn"):
            shadow = "suspect"
            shadow_reason = "Có cảnh báo rủi ro"

        share_url = f"https://www.tiktok.com/@{username}/video/{aweme_id}" if (username and aweme_id) else ""

        return AwemePost(
            aweme_id=aweme_id,
            desc=desc,
            cover=cover_url,
            duration=int(video.get("duration") or 0),
            create_time=int(it.get("createTime") or it.get("create_time") or 0),
            device="web" if it.get("source") == "web" else "phone",
            source=str(it.get("source") or "Upload"),
            play=int(stats.get("playCount") or stats.get("play_count") or 0),
            like=int(stats.get("diggCount") or stats.get("digg_count") or 0),
            comment=int(stats.get("commentCount") or stats.get("comment_count") or 0),
            share=int(stats.get("shareCount") or stats.get("share_count") or 0),
            save=int(stats.get("collectCount") or stats.get("collect_count") or 0),
            region=str(it.get("region") or ""),
            shadow=shadow,
            shadow_reason=shadow_reason,
            is_top=bool(it.get("is_top") or it.get("top")),
            ai_label=bool(it.get("aigc_label_type") or it.get("aiLabel")),
            risk_warn=bool(it.get("risk_warn")),
            is_delete=bool(it.get("is_delete")),
            is_private=bool(it.get("secret") or it.get("is_private") or it.get("visibility") == 1),
            penalty_flag=penalty_flag,
            group_id=group_id,
            share_url=share_url,
            download_url=download_url,
        )

    def get_penalty_details(self, vid: str, create_time: int) -> Tuple[bool, PenaltyDetails, str]:
        """
        Lấy chi tiết án phạt (Strike) của video theo getPenaltyDetails.
        Trả về (success, PenaltyDetails, message).
        """
        if not vid:
            return False, PenaltyDetails(), "ID video không hợp lệ."

        endpoint = "https://www.tiktok.com/creator_center/api/item/penalty_detail/"
        params = {"vid": vid, "create_time": create_time}

        try:
            resp = self.session.get(endpoint, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("hasPenalty") or data.get("has_penalty"):
                    p = PenaltyDetails(
                        has_penalty=True,
                        penalty_id=str(data.get("penaltyId") or data.get("penalty_id") or vid),
                        issuer=str(data.get("issuer") or "TikTok Trust & Safety"),
                        reason_codes=data.get("reasonCodes") or data.get("reason_codes") or ["Community Guidelines"],
                        appeal_status=int(data.get("appealStatus") or data.get("appeal_status") or 0),
                    )
                    return True, p, "Đã lấy chi tiết án phạt."
                else:
                    return True, PenaltyDetails(has_penalty=False), "Video không có án phạt."
        except Exception as e:
            logger.debug(f"get_penalty_details error: {e}")

        return False, PenaltyDetails(has_penalty=False, error="Không thể kết nối lấy chi tiết án phạt."), "Lỗi kết nối."

    def submit_studio_appeal(self, vid: str, penalty_id: str, reason_text: str) -> Tuple[bool, str]:
        """Gửi kháng nghị án phạt Studio Strike theo submitStudioAppeal."""
        clean_text = reason_text.strip()
        if not clean_text:
            return False, "Nội dung kháng nghị không được để trống."
        if len(clean_text) > 400:
            return False, "Nội dung kháng nghị không được vượt quá 400 ký tự."

        endpoint = "https://www.tiktok.com/creator_center/api/item/appeal/"
        payload = {
            "vid": vid,
            "penalty_id": penalty_id,
            "description": clean_text,
        }

        try:
            resp = self.session.post(endpoint, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                res_data = resp.json()
                if res_data.get("status_code") == 0 or res_data.get("message") == "success":
                    return True, "Đã gửi kháng nghị Strike thành công! Đang chờ TikTok xét duyệt."
                err = res_data.get("status_msg") or res_data.get("message") or "TikTok từ chối đơn kháng nghị."
                return False, f"TikTok thông báo: {err}"
        except Exception as e:
            return False, f"Lỗi kết nối khi gửi kháng nghị: {e}"

        return False, "Không thể gửi kháng nghị lúc này."

    def hide_studio_item(self, aweme_id: str) -> Tuple[bool, str]:
        """Ẩn video sang chế độ Riêng tư (hideStudioItem)."""
        endpoint = "https://www.tiktok.com/creator_center/api/item/visibility/"
        payload = {"item_id": aweme_id, "visibility": 1}  # 1 = Private
        try:
            resp = self.session.post(endpoint, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                return True, "Đã ẩn video thành công (Chuyển sang chế độ Riêng tư)."
        except Exception as e:
            return False, f"Lỗi kết nối khi ẩn video: {e}"
        return False, "Không thể ẩn video vào lúc này."

    def delete_studio_item(self, aweme_id: str) -> Tuple[bool, str]:
        """Xóa vĩnh viễn video khỏi kênh (deleteStudioItem)."""
        endpoint = "https://www.tiktok.com/creator_center/api/item/delete/"
        payload = {"item_id": aweme_id}
        try:
            resp = self.session.post(endpoint, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                return True, "Đã xóa video vĩnh viễn khỏi kênh TikTok."
        except Exception as e:
            return False, f"Lỗi kết nối khi xóa video: {e}"
        return False, "Không thể xóa video vào lúc này."
