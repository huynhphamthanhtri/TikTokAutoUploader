# -*- coding: utf-8 -*-
"""
check_features.services.check_info_service - Đồng bộ danh tính và chỉ số tài khoản TikTok (3-Tier Waterfall).
Chuẩn hoá 100% theo kiến trúc chuẩn:
- Bước 1: Studio User API (/tiktokstudio/api/web/user) với bộ parser nested schema chuẩn
- Bước 2: Fallback Setting HTML (/setting)
- Bước 3: Public Profile (/@{uniqueId}) kèm anti-mismatch UID validation.
Phân loại kết quả theo 3 trục độc lập: outcome, auth_state, error_code (Chống gán oan expired).
Nhận diện chuẩn xác:
- NOT_LOGGED_IN: Không có sessionid.
- SESSION_INVALID: Studio API BaseResp 20001 ("user not found") + redirect khách /foryou.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

from core_helpers import format_requests_proxy
from .cookie_extractor import CookieExtractor

logger = logging.getLogger("TikTokCheckInfoService")


@dataclass
class CheckInfoResult:
    """DTO kết quả sau khi thực thi quy trình Check Thông Tin."""
    success: bool = False
    status: str = "PENDING"  # SUCCESS | PARTIAL_SUCCESS | NOT_LOGGED_IN | SESSION_INVALID | COOKIE_EXPIRED | PROXY_ERROR | ERROR
    outcome: str = "PENDING"  # "SUCCESS" | "PARTIAL" | "FAILED"
    auth_state: str = "UNKNOWN"  # "VALID" | "EXPIRED" | "NOT_LOGGED_IN" | "UNKNOWN"
    error_code: Optional[str] = None  # NOT_LOGGED_IN | SESSION_INVALID | STUDIO_USER_NOT_FOUND | PROXY_ERROR | NETWORK_TIMEOUT | SCHEMA_UNSUPPORTED
    profile_name: str = ""
    user: Dict[str, Any] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)
    cookie_header: str = ""
    user_agent: str = ""
    identity_source: str = "none"  # "studio" | "setting" | "none"
    checked_at: str = ""
    error: Optional[str] = None

    def to_tiktok_account_dict(self) -> Dict[str, Any]:
        """Format thành JSON lưu trữ profiles.db / config."""
        return {
            "user": self.user,
            "stats": self.stats,
            "auth_state": self.auth_state,
            "last_error": self.error,
        }


def parse_studio_user_response(data: Any) -> Optional[Dict[str, Any]]:
    """
    Parser bóc tách dữ liệu danh tính từ TikTok Studio API (/tiktokstudio/api/web/user).
    Xử lý cấu trúc lồng sâu chuẩn:
    data -> userBaseInfo -> UserProfile -> UserBase
    """
    if not isinstance(data, dict):
        return None

    # Kiểm tra mã lỗi trả về từ TikTok Studio nếu có
    status_code = data.get("statusCode") if "statusCode" in data else data.get("code")
    if status_code is not None and status_code not in (0, "0"):
        return None

    user_base_info = data.get("userBaseInfo") or {}
    base_resp = user_base_info.get("BaseResp") or {}
    base_code = base_resp.get("StatusCode")
    base_msg = str(base_resp.get("StatusMessage") or "").lower()

    # Nhận diện rõ mã 20001: user not found
    if base_code == 20001 or "user not found" in base_msg:
        return {"_error_type": "USER_NOT_FOUND", "base_code": 20001}

    user_profile = user_base_info.get("UserProfile") or {}
    user_base = user_profile.get("UserBase") or {}

    # 1. UID
    raw_uid = str(
        data.get("userId")
        or user_base.get("Id")
        or data.get("user_id")
        or data.get("uid")
        or ""
    ).strip()
    uid = "" if raw_uid in ("0", "") else raw_uid

    # 2. Unique ID (@handle)
    unique_id = str(
        user_base.get("UniqId")
        or user_base.get("uniqueId")
        or data.get("unique_id")
        or data.get("uniqueId")
        or ""
    ).strip().lstrip("@")

    # Bắt buộc phải có ít nhất UID hoặc Handle
    if not uid and not unique_id:
        return None

    # 3. Nickname (Tên hiển thị)
    nickname = str(
        user_base.get("NickName")
        or user_base.get("nickname")
        or data.get("nickname")
        or data.get("nickName")
        or unique_id
    ).strip()

    # 4. Region
    reg_val = user_base.get("Region") or data.get("region")
    region_str = ""
    if isinstance(reg_val, dict):
        region_str = str(reg_val.get("Region") or reg_val.get("RegistrationCountry") or "").upper()
    elif isinstance(reg_val, str):
        region_str = reg_val.upper()

    # 5. SecUid
    sec_uid = str(user_base.get("SecUid") or data.get("sec_uid") or data.get("secUid") or "").strip()

    # 6. Avatar URI
    avatar_uri = ""
    avatars = user_base.get("Avatars") or []
    if isinstance(avatars, list) and len(avatars) > 0 and isinstance(avatars[0], dict):
        u_info = avatars[0].get("UrlInfo") or {}
        if isinstance(u_info, dict):
            avatar_uri = str(u_info.get("Uri") or (u_info.get("UrlList") or [""])[0] or "")
    if not avatar_uri:
        avatar_uri = str(data.get("avatar_url") or data.get("avatarUri") or "")

    # 7. Create time
    create_time = str(user_base.get("CreateTime") or user_base.get("create_time") or "")

    return {
        "uid": uid,
        "uniqueId": unique_id,
        "nickName": nickname,
        "avatarUri": avatar_uri,
        "region": region_str,
        "secUid": sec_uid,
        "ageGateRegion": region_str,
        "ageGateTime": create_time,
    }


class TikTokCheckInfoService:
    """Service điều phối quy trình Check Thông Tin (3-Tier Waterfall)."""

    def __init__(self, profile_name: str, profile_config: Dict[str, Any], timeout: float = 15.0):
        self.profile_name = profile_name
        self.config = profile_config or {}
        self.timeout = timeout
        self.session = requests.Session()

    def _setup_session(self, cookie_header: str, user_agent: str) -> None:
        """Khởi tạo session HTTP độc lập với Proxy và User-Agent của profile."""
        self.session.headers.update({
            "User-Agent": user_agent,
            "Cookie": cookie_header,
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            "Referer": "https://www.tiktok.com/",
        })

        # Cấu hình proxy theo chuẩn RFC 3986
        proxy_str = (
            self.config.get("proxy_string")
            or self.config.get("proxy")
            or ""
        ).strip()
        if proxy_str and self.config.get("use_proxy", True):
            p_scheme = str(self.config.get("proxy_type", "http") or "http").strip().lower()
            proxies = format_requests_proxy(proxy_str, scheme=p_scheme)
            if proxies:
                self.session.proxies = proxies

    def execute(self) -> CheckInfoResult:
        """Thực thi toàn bộ chuỗi 3-Tier Waterfall."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        res = CheckInfoResult(
            profile_name=self.profile_name,
            checked_at=now_str,
        )

        # ----------------------------------------------------------------------
        # Bước 0: Đọc Cookie & UA offline
        # ----------------------------------------------------------------------
        c_ok, cookie_header, cookies_map, c_msg = CookieExtractor.extract_cookies(self.config)
        ua, fp = CookieExtractor.extract_user_agent_and_fingerprint(self.config)
        res.cookie_header = cookie_header
        res.user_agent = ua

        if not c_ok:
            res.success = False
            res.outcome = "FAILED"
            res.auth_state = "UNKNOWN"
            res.status = "ERROR"
            res.error_code = "COOKIE_READ_FAILED"
            res.error = f"Lỗi trích xuất cookie offline: {c_msg}"
            return res

        # Kiểm tra sự hiện diện và giá trị thực tế của sessionid (không chấp nhận rỗng)
        sid_val = str(cookies_map.get("sessionid") or "").strip()
        if not sid_val and cookie_header:
            m_sid = re.search(r'(?:^|;\s*)sessionid=([^;]+)', str(cookie_header))
            if m_sid and m_sid.group(1).strip():
                sid_val = m_sid.group(1).strip()
        has_sessionid = bool(sid_val)

        if not has_sessionid:
            res.success = False
            res.outcome = "FAILED"
            res.auth_state = "NOT_LOGGED_IN"
            res.status = "NOT_LOGGED_IN"
            res.error_code = "NOT_LOGGED_IN"
            res.error = "Chưa đăng nhập TikTok (Không tìm thấy cookie phiên sessionid hợp lệ)."
            return res

        self._setup_session(cookie_header, ua)

        # ----------------------------------------------------------------------
        # Bước 1: Gọi TikTok Studio API (/tiktokstudio/api/web/user)
        # ----------------------------------------------------------------------
        user_data: Dict[str, Any] = {}
        identity_found = False

        try:
            studio_url = "https://www.tiktok.com/tiktokstudio/api/web/user"
            studio_headers = {
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://www.tiktok.com/tiktokstudio",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
            resp_studio = self.session.get(studio_url, headers=studio_headers, timeout=self.timeout)

            if resp_studio.status_code == 200:
                raw_s = resp_studio.json() if resp_studio.text else {}
                parsed = parse_studio_user_response(raw_s)
                if parsed:
                    if parsed.get("_error_type") == "USER_NOT_FOUND":
                        res.error_code = "STUDIO_USER_NOT_FOUND"
                    elif parsed.get("uid") or parsed.get("uniqueId"):
                        user_data = parsed
                        identity_found = True
                        res.identity_source = "studio"
                        res.auth_state = "VALID"
            elif resp_studio.status_code in (401, 403):
                # Mã xác thực thất bại
                logger.debug(f"Studio API trả HTTP {resp_studio.status_code}, chuẩn bị fallback /setting.")

        except (requests.exceptions.ProxyError, requests.exceptions.InvalidURL) as e:
            res.success = False
            res.outcome = "FAILED"
            res.auth_state = "UNKNOWN"
            res.status = "PROXY_ERROR"
            res.error_code = "PROXY_ERROR"
            res.error = f"Lỗi kết nối Proxy: {e}"
            return res
        except requests.exceptions.Timeout as e:
            logger.debug(f"Bước 1 Studio API Timeout: {e}")
            res.error_code = "NETWORK_TIMEOUT"
        except Exception as e:
            logger.debug(f"Bước 1 Studio API gặp lỗi: {e}")

        # ----------------------------------------------------------------------
        # Bước 2: Fallback sang cào HTML Setting (/setting)
        # ----------------------------------------------------------------------
        if not identity_found:
            try:
                setting_url = "https://www.tiktok.com/setting"
                setting_headers = {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": "document",
                }
                resp_setting = self.session.get(setting_url, headers=setting_headers, timeout=self.timeout)

                # Kiểm tra redirect login
                if resp_setting.status_code in (401, 403) or "/login" in resp_setting.url:
                    res.success = False
                    res.outcome = "FAILED"
                    res.auth_state = "EXPIRED"
                    res.status = "SESSION_INVALID"
                    res.error_code = "SESSION_INVALID"
                    res.error = "Phiên đăng nhập hết hạn (Bị chuyển hướng về trang login)."
                    return res

                # Kiểm tra redirect sang trang khách /foryou kết hợp với lỗi Studio user not found
                if "/foryou" in resp_setting.url and res.error_code == "STUDIO_USER_NOT_FOUND":
                    res.success = False
                    res.outcome = "FAILED"
                    res.auth_state = "EXPIRED"
                    res.status = "SESSION_INVALID"
                    res.error_code = "SESSION_INVALID"
                    res.error = "Phiên đăng nhập không còn hợp lệ trên máy chủ TikTok (Mã 20001: user not found)."
                    return res

                if resp_setting.status_code == 200 and resp_setting.text:
                    html_text = resp_setting.text
                    m_uniq = re.search(r'"uniqueId":"([^"]+)"', html_text)
                    m_uid = (
                        re.search(r'"userId":"([^"]+)"', html_text)
                        or re.search(r'"uid":"([^"]+)"', html_text)
                        or re.search(r'"id":"(\d{15,22})"', html_text)
                    )
                    m_nick = re.search(r'"nickName":"([^"]+)"', html_text) or re.search(r'"nickname":"([^"]+)"', html_text)
                    m_avatar = re.search(r'"avatarUri":\["([^"]+)"', html_text)
                    m_region = re.search(r'"region":"([A-Z]{2})"', html_text)
                    m_age_region = re.search(r'"ageGateRegion":"([A-Z]{2})"', html_text)
                    m_age_time = re.search(r'"ageGateTime":"?(\d+)"?', html_text)

                    if m_uniq and m_uniq.group(1):
                        unique_id = m_uniq.group(1).lstrip("@")
                        uid = m_uid.group(1) if m_uid else ""
                        nick = m_nick.group(1) if m_nick else unique_id
                        avatar = m_avatar.group(1).replace(r"\u0026", "&") if m_avatar else ""
                        reg = m_region.group(1).upper() if m_region else ""
                        age_reg = m_age_region.group(1).upper() if m_age_region else ""
                        age_time = m_age_time.group(1) if m_age_time else ""

                        user_data = {
                            "uid": uid,
                            "uniqueId": unique_id,
                            "nickName": nick,
                            "avatarUri": avatar,
                            "region": reg,
                            "secUid": "",
                            "ageGateRegion": age_reg,
                            "ageGateTime": age_time,
                        }
                        identity_found = True
                        res.identity_source = "setting"
                        res.auth_state = "VALID"

            except (requests.exceptions.ProxyError, requests.exceptions.InvalidURL) as e:
                res.success = False
                res.outcome = "FAILED"
                res.auth_state = "UNKNOWN"
                res.status = "PROXY_ERROR"
                res.error_code = "PROXY_ERROR"
                res.error = f"Lỗi Proxy khi truy cập /setting: {e}"
                return res
            except requests.exceptions.Timeout as e:
                logger.debug(f"Bước 2 Setting HTML Timeout: {e}")
                res.error_code = "NETWORK_TIMEOUT"
            except Exception as e:
                logger.debug(f"Bước 2 Setting HTML gặp lỗi: {e}")

        # Nếu cả 2 bước đều không có identity hợp lệ
        if not identity_found or not user_data.get("uniqueId"):
            res.success = False
            res.outcome = "FAILED"
            if res.error_code == "STUDIO_USER_NOT_FOUND":
                res.auth_state = "EXPIRED"
                res.status = "SESSION_INVALID"
                res.error_code = "SESSION_INVALID"
                res.error = "Phiên đăng nhập không còn hợp lệ trên máy chủ TikTok (Mã 20001: user not found)."
            elif res.auth_state == "EXPIRED":
                res.status = "SESSION_INVALID"
                res.error_code = "SESSION_INVALID"
                res.error = "Phiên đăng nhập hết hạn hoặc bị thu hồi (TikTok xác nhận)."
            elif res.auth_state == "NOT_LOGGED_IN":
                res.status = "NOT_LOGGED_IN"
                res.error = "Chưa đăng nhập TikTok (Thiếu cookie phiên sessionid)."
            else:
                res.auth_state = "UNKNOWN"
                res.status = "ERROR"
                res.error_code = res.error_code or "SCHEMA_UNSUPPORTED"
                res.error = "Không thể đọc thông tin tài khoản qua Studio API và Setting HTML (Lỗi phân tích hoặc bị hạn chế kết nối)."
            return res

        res.user = user_data

        # ----------------------------------------------------------------------
        # Bước 3: Cào Public Profile (/@{uniqueId}) lấy Stats
        # ----------------------------------------------------------------------
        stats_data = {
            "followerCount": 0,
            "followingCount": 0,
            "heartCount": 0,
            "videoCount": 0,
            "friendCount": 0,
        }
        stats_verified = False

        unique_id = user_data["uniqueId"]
        auth_uid = user_data.get("uid", "")

        try:
            pub_url = f"https://www.tiktok.com/@{unique_id}"
            pub_headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Mode": "navigate",
            }
            resp_pub = self.session.get(pub_url, headers=pub_headers, timeout=min(self.timeout, 12.0))

            if resp_pub.status_code == 200 and resp_pub.text:
                body_pub = resp_pub.text

                # 1. Bóc tách JSON rehydration
                m_rehyd = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([\s\S]*?)</script>', body_pub)
                if m_rehyd:
                    try:
                        p_json = json.loads(m_rehyd.group(1))
                        u_detail = p_json.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
                        u_info = u_detail.get("userInfo", {})
                        p_user = u_info.get("user", {})
                        p_stats = u_info.get("stats", {})

                        # Anti-mismatch check
                        pub_user_id = str(p_user.get("id") or "")
                        if not auth_uid or not pub_user_id or (pub_user_id == auth_uid):
                            stats_data = {
                                "followerCount": int(p_stats.get("followerCount") or 0),
                                "followingCount": int(p_stats.get("followingCount") or 0),
                                "heartCount": int(p_stats.get("heartCount") or p_stats.get("heart") or 0),
                                "videoCount": int(p_stats.get("videoCount") or 0),
                                "friendCount": int(p_stats.get("friendCount") or 0),
                            }
                            stats_verified = True
                        else:
                            logger.warning(f"Anti-mismatch: Public UID {pub_user_id} != Auth UID {auth_uid}")
                    except Exception as e:
                        logger.debug(f"Lỗi parse rehydration JSON: {e}")

                # 2. Fallback SIGI_STATE nếu chưa có stats
                if not stats_verified:
                    m_sigi = re.search(r'<script id="SIGI_STATE"[^>]*>([\s\S]*?)</script>', body_pub)
                    if m_sigi:
                        try:
                            s_json = json.loads(m_sigi.group(1))
                            u_stats = s_json.get("UserModule", {}).get("stats", {}).get(unique_id, {})
                            if u_stats:
                                stats_data = {
                                    "followerCount": int(u_stats.get("followerCount") or 0),
                                    "followingCount": int(u_stats.get("followingCount") or 0),
                                    "heartCount": int(u_stats.get("heartCount") or u_stats.get("heart") or 0),
                                    "videoCount": int(u_stats.get("videoCount") or 0),
                                    "friendCount": int(u_stats.get("friendCount") or 0),
                                }
                                stats_verified = True
                        except Exception as e:
                            logger.debug(f"Lỗi parse SIGI_STATE: {e}")

                # 3. Fallback regex thô nếu JSON bị băm
                if not stats_verified:
                    m_f = re.search(r'"followerCount":(\d+)', body_pub)
                    m_v = re.search(r'"videoCount":(\d+)', body_pub)
                    m_h = re.search(r'"heartCount":(\d+)', body_pub) or re.search(r'"heart":(\d+)', body_pub)
                    if m_f or m_v:
                        stats_data["followerCount"] = int(m_f.group(1)) if m_f else 0
                        stats_data["videoCount"] = int(m_v.group(1)) if m_v else 0
                        stats_data["heartCount"] = int(m_h.group(1)) if m_h else 0
                        stats_verified = True

        except Exception as e:
            logger.debug(f"Bước 3 Public Profile gặp lỗi: {e}")

        res.stats = stats_data
        res.success = True
        res.auth_state = "VALID"

        if stats_verified:
            res.outcome = "SUCCESS"
            res.status = "SUCCESS"
        else:
            # Identity đã xác thực thành công nhưng stats bị hạn chế/challenge
            res.outcome = "PARTIAL"
            res.status = "PARTIAL_SUCCESS"

        return res
