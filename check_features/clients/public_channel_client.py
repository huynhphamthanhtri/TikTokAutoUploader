# -*- coding: utf-8 -*-
"""
check_features.clients.public_channel_client - Client quét bài viết kênh TikTok công khai.
Hỗ trợ F3 (CheckDupView độc lập).
TUYỆT ĐỐI KHÔNG SỬ DỤNG COOKIE HAY PROXY CỦA PROFILE TÀI KHOẢN.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
import requests

from ..models import AwemePost, ChannelMeta
from core_helpers import format_requests_proxy

logger = logging.getLogger("TikTokPublicChannelClient")


class TikTokPublicChannelClient:
    """Client độc lập truy cập nguồn công khai của TikTok (không cookie, không proxy nội bộ)."""

    def __init__(self, proxy_str: Optional[str] = None, timeout: int = 15):
        self.timeout = timeout
        self.session = requests.Session()
        if proxy_str:
            proxies = format_requests_proxy(proxy_str)
            if proxies:
                self.session.proxies = proxies
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })

    @staticmethod
    def extract_username(channel_input: str) -> str:
        """
        Bóc tách username chuẩn từ input:
        - @username
        - https://www.tiktok.com/@username
        - https://www.tiktok.com/@username/video/123456789
        """
        raw = str(channel_input or "").strip()
        if not raw:
            return ""

        # Match từ URL
        match_url = re.search(r"@([a-zA-Z0-9_.-]+)", raw)
        if match_url:
            return match_url.group(1)

        # Match định dạng username đơn thuần
        cleaned = raw.lstrip("@").strip()
        # Loại bỏ query params nếu có
        cleaned = cleaned.split("?")[0].split("/")[0]
        return cleaned

    def resolve_channel_metadata(self, channel_input: str) -> Tuple[bool, ChannelMeta, str]:
        """
        Phân giải thông tin kênh và secUid từ username/link công khai.
        Trả về (success, ChannelMeta, message).
        """
        username = self.extract_username(channel_input)
        if not username:
            return False, ChannelMeta(), "Vui lòng nhập @username hoặc URL kênh TikTok hợp lệ."

        meta = ChannelMeta(unique_id=username)
        endpoint = f"https://www.tiktok.com/api/user/detail/?uniqueId={username}"

        try:
            resp = self.session.get(endpoint, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                u_info = data.get("userInfo", {})
                u_user = u_info.get("user", {})
                u_stats = u_info.get("stats", {})

                if u_user:
                    meta.nickname = str(u_user.get("nickname") or username)
                    meta.unique_id = str(u_user.get("uniqueId") or username)
                    meta.avatar = str(u_user.get("avatarLarger") or u_user.get("avatarThumb") or "")
                    meta.sec_uid = str(u_user.get("secUid") or "")
                    meta.followers = int(u_stats.get("followerCount") or 0)
                    meta.video_count = int(u_stats.get("videoCount") or 0)
                    return True, meta, f"Đã tìm thấy kênh: {meta.nickname} (@{meta.unique_id})"

        except Exception as e:
            logger.debug(f"resolve_channel_metadata error: {e}")

        # Thử fallback qua HTML page scrap
        try:
            page_url = f"https://www.tiktok.com/@{username}"
            resp_html = self.session.get(page_url, timeout=self.timeout)
            if resp_html.status_code == 200:
                match_sec = re.search(r'"secUid":"([^"]+)"', resp_html.text)
                if match_sec:
                    meta.sec_uid = match_sec.group(1)
                    meta.nickname = username
                    return True, meta, f"Đã tìm thấy secUid cho kênh @{username} qua trang công khai."
        except Exception as e:
            logger.debug(f"HTML fallback error: {e}")

        return False, meta, f"Không tìm thấy kênh hoặc kênh @{username} đang ở chế độ riêng tư."

    def fetch_channel_videos(
        self,
        sec_uid: str,
        count: int = 35,
        max_cursor: int = 0,
    ) -> Tuple[bool, List[AwemePost], int, bool, str]:
        """
        Lấy danh sách bài viết từ Mobile API /aweme/v1/aweme/post/ có chứa group_id thực.
        Trả về (success, posts, next_cursor, has_more, message).
        """
        if not sec_uid:
            return False, [], max_cursor, False, "secUid của kênh chưa được xác định."

        url = "https://www.tiktok.com/aweme/v1/aweme/post/"
        params = {
            "count": count,
            "sec_user_id": sec_uid,
            "max_cursor": max_cursor,
            "aid": "1988",
            "app_name": "tiktok_web",
        }

        posts: List[AwemePost] = []
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                raw_list = data.get("aweme_list") or []
                has_more = bool(data.get("has_more", 0))
                next_cursor = int(data.get("max_cursor", 0))

                for it in raw_list:
                    aweme_id = str(it.get("aweme_id") or it.get("id") or "")
                    group_id = str(it.get("group_id") or it.get("groupId") or "")
                    stats = it.get("statistics") or {}
                    video = it.get("video") or {}
                    cov = video.get("cover", {}).get("url_list", [""])[0] if isinstance(video.get("cover"), dict) else ""

                    # Bóc tách thiết bị và trạng thái
                    misc = it.get("misc_info") or ""
                    device_str = "phone"
                    if "web" in str(misc).lower() or it.get("create_scene") == 1:
                        device_str = "web"

                    # Kiểm tra shadowban
                    status_dict = it.get("status") or {}
                    control_dict = it.get("item_control") or {}
                    shadow_status = "no"
                    shadow_reason = ""
                    if status_dict.get("is_prohibited"):
                        shadow_status = "yes"
                        shadow_reason = "Bị cấm phân phối"
                    elif control_dict.get("can_repost") is False:
                        shadow_status = "suspect"
                        shadow_reason = "Hạn chế tương tác"

                    post = AwemePost(
                        aweme_id=aweme_id,
                        group_id=group_id,
                        desc=str(it.get("desc") or ""),
                        cover=cov,
                        play=int(stats.get("play_count") or 0),
                        like=int(stats.get("digg_count") or 0),
                        comment=int(stats.get("comment_count") or 0),
                        create_time=int(it.get("create_time") or 0),
                        device=device_str,
                        region=str(it.get("region") or ""),
                        shadow=shadow_status,
                        shadow_reason=shadow_reason,
                        share_url=str(it.get("share_url") or ""),
                    )
                    posts.append(post)

                return True, posts, next_cursor, has_more, f"Đã lấy {len(posts)} video công khai."

        except Exception as e:
            logger.debug(f"fetch_channel_videos error: {e}")

        return False, [], max_cursor, False, "Không thể kết nối lấy video công khai từ máy chủ TikTok."
