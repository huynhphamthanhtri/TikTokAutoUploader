# -*- coding: utf-8 -*-
"""
check_features.clients.account_client - Client giao tiếp API kiểm tra tài khoản và đổi Nickname.
Thực thi theo chuẩn IPC checkUser / setNickname trong tài liệu đặc tả F1.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple
import requests

from ..models import UserInfo
from ..error_codes import CheckFeatureErrorCode
from core_helpers import format_requests_proxy

logger = logging.getLogger("TikTokAccountClient")


class TikTokAccountClient:
    """Client giao tiếp HTTP với TikTok API để lấy thông tin tài khoản và đổi Nickname."""

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
            "Referer": "https://www.tiktok.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })

        proxy_str = (self.config.get("proxy_string") or self.config.get("proxy") or "").strip()
        if proxy_str and self.config.get("use_proxy", True):
            p_scheme = str(self.config.get("proxy_type", "http") or "http").strip().lower()
            proxies = format_requests_proxy(proxy_str, scheme=p_scheme)
            if proxies:
                self.session.proxies = proxies

    def check_user(self) -> Tuple[bool, UserInfo, str]:
        """
        Lấy thông tin tài khoản TikTok chi tiết theo đặc tả checkUser.
        Trả về (success, UserInfo, message).
        """
        user_info = UserInfo()

        # Ưu tiên 1: Lấy từ TikTok Studio User API
        try:
            studio_url = "https://www.tiktok.com/tiktokstudio/api/web/user"
            s_headers = {
                "Referer": "https://www.tiktok.com/tiktokstudio",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
            resp_s = self.session.get(studio_url, headers=s_headers, timeout=self.timeout)
            if resp_s.status_code == 200:
                from ..services.check_info_service import parse_studio_user_response
                parsed = parse_studio_user_response(resp_s.json())
                if parsed and (parsed.get("uid") or parsed.get("uniqueId")):
                    user_info.uid = parsed["uid"]
                    user_info.username = parsed["uniqueId"]
                    user_info.nickname = parsed["nickName"]
                    user_info.store_country = parsed.get("region") or ""
                    user_info.studio_ok = True
                    return True, user_info, "Lấy thông tin tài khoản thành công qua Studio API."
        except Exception as e:
            logger.debug(f"Studio API check_user error: {e}")

        # Thử lấy từ Creator Studio / Passport API
        endpoint = "https://www.tiktok.com/passport/web/account/info/"

        try:
            resp = self.session.get(endpoint, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                data_obj = data.get("data") or {}
                if data_obj and (data_obj.get("user_id") or data_obj.get("username") or data_obj.get("screen_name")):
                    user_info.uid = str(data_obj.get("user_id") or data_obj.get("uid") or "")
                    user_info.username = str(data_obj.get("username") or data_obj.get("unique_id") or "").lstrip("@")
                    user_info.nickname = str(data_obj.get("screen_name") or data_obj.get("nickname") or user_info.username)
                    user_info.email = data_obj.get("email") or None
                    user_info.mobile = data_obj.get("mobile") or None
                    user_info.is_email_verified = bool(data_obj.get("is_email_verified") or data_obj.get("email_verified"))
                    user_info.phone_collected = bool(data_obj.get("phone_collected") or data_obj.get("has_mobile"))
                    user_info.store_country = str(data_obj.get("store_country") or data_obj.get("country_code") or "").upper()
                    user_info.user_create_time = int(data_obj.get("create_time") or data_obj.get("user_create_time") or 0)
                    user_info.nickname_modify_time = int(data_obj.get("nickname_modify_time") or 0)
                    user_info.unique_id_modify_time = int(data_obj.get("unique_id_modify_time") or 0)
                    user_info.studio_ok = True
                    return True, user_info, "Lấy thông tin tài khoản thành công."
        except Exception as e:
            logger.debug(f"Passport endpoint error: {e}")

        # Fallback 1: Thử lấy qua Web Profile Detail API
        uname = (
            self.config.get("tiktok_id")
            or self.config.get("tiktok_account")
            or ""
        ).lstrip("@")

        if uname:
            try:
                detail_url = f"https://www.tiktok.com/api/user/detail/?uniqueId={uname}"
                resp2 = self.session.get(detail_url, timeout=self.timeout)
                if resp2.status_code == 200:
                    d2 = resp2.json().get("userInfo", {}).get("user", {})
                    if d2:
                        user_info.uid = str(d2.get("id") or "")
                        user_info.username = str(d2.get("uniqueId") or uname)
                        user_info.nickname = str(d2.get("nickname") or uname)
                        user_info.bio = str(d2.get("signature") or "")
                        user_info.user_create_time = int(d2.get("createTime") or 0)
                        user_info.nickname_modify_time = int(d2.get("nicknameModifyTime") or 0)
                        user_info.unique_id_modify_time = int(d2.get("uniqueIdModifyTime") or 0)
                        user_info.store_country = str(d2.get("region") or "").upper()
                        return True, user_info, "Lấy thông tin hồ sơ thành công qua Profile API."
            except Exception as e:
                logger.debug(f"Profile detail endpoint error: {e}")

        # Fallback 2: Đọc dữ liệu đã lưu trong config của profile
        if uname or self.config.get("account_uuid"):
            user_info.username = uname
            user_info.nickname = str(self.config.get("nickname") or uname)
            user_info.uid = str(self.config.get("tiktok_user_id") or self.config.get("tiktok_uid") or "")
            user_info.bio = str(self.config.get("bio") or "")
            fp = self.config.get("fingerprint", {}) or {}
            user_info.store_country = str(fp.get("geo_country_code") or "").upper()
            return True, user_info, "Đọc thông tin từ bộ nhớ cấu hình tài khoản."

        return False, user_info, "Không thể kết nối lấy thông tin tài khoản TikTok."

    def set_nickname(self, new_nickname: str) -> Tuple[bool, str]:
        """
        Đổi Tên hiển thị (Nickname) cho tài khoản TikTok theo đặc tả setNickname.
        - Kiểm tra độ dài và định dạng.
        - Trả về (success, message).
        """
        clean_name = str(new_nickname or "").strip()
        if not clean_name:
            return False, "Nickname không được để trống."
        if len(clean_name) > 30:
            return False, "Nickname không được vượt quá 30 ký tự."

        endpoint = "https://www.tiktok.com/passport/web/account/modify_user_info/"
        payload = {"screen_name": clean_name}

        try:
            resp = self.session.post(endpoint, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("message") == "success" or data.get("data", {}).get("screen_name") == clean_name:
                    # Cập nhật lại config local
                    self.config["nickname"] = clean_name
                    return True, f"Đã đổi nickname thành công: {clean_name}"
                err_msg = data.get("data", {}).get("description") or data.get("message") or "Yêu cầu bị TikTok từ chối."
                return False, f"TikTok từ chối: {err_msg}"
        except Exception as e:
            return False, f"Lỗi kết nối khi gửi yêu cầu đổi tên: {e}"

        return False, "Không thể đổi tên hiển thị vào lúc này. Vui lòng kiểm tra lại cookie tài khoản."
