# -*- coding: utf-8 -*-
"""
TikTok Mobile Post Client
Chuyên biệt giao tiếp với API Mobile Feed (/aweme/v1/aweme/post/) của cụm máy chủ TikTok.
Sử dụng Module Ký Mobile (Metasec - Argus, Ladon, Gorgon, Khronos) chuẩn Python thuần.
Mục đích:
- Lấy danh sách video từ Feed công khai của profile theo sec_user_id.
- Trích xuất trường group_id chuẩn xác từ máy chủ TikTok để phát hiện video trùng lặp (is_dup).
- Hỗ trợ fallback luân phiên các cụm máy chủ (Alisg, USEast2, EU No1a, USEast8).
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as curl_requests
    HAS_CURL_CFFI = False

import requests

from check_features.services.mobile_signer import Metasec

logger = logging.getLogger(__name__)


def clean_id_string(val: Any) -> str:
    """Chuẩn hóa ID (aweme_id, group_id) thành chuỗi số nguyên sạch."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s or s.lower() in ("null", "none", "undefined", "0"):
        return ""
    if s.endswith(".0"):
        s = s[:-2]
    return s


class TikTokMobilePostClient:
    """Client giao tiếp Mobile Feed API sử dụng chữ ký Metasec."""

    HOSTS = [
        "api16-normal-c-alisg.ttapis.com",
        "api22-normal-c-useast2a.tiktokv.com",
        "api16-core-no1a.tiktokv.eu",
        "api19-normal-useast8.tiktokv.us",
    ]

    # Cấu hình thiết bị Mobile chuẩn Android Cronet
    DEFAULT_AID = 1233
    DEFAULT_APP_NAME = "musical_ly"
    DEFAULT_VERSION_CODE = "350401"
    DEFAULT_VERSION_NAME = "35.4.1"
    DEFAULT_SDK_VERSION = "v04.04.05-ov-android"
    DEFAULT_SDK_VERSION_CODE = 167904322
    DEFAULT_LICENSE_ID = 1611921764
    DEFAULT_DEVICE_TYPE = "Pixel 4 XL"
    DEFAULT_UA = (
        "com.ss.android.ugc.trill/350401 (Linux; U; Android 14; Pixel 4 XL "
        "Build/UP1A.231005.007; Cronet/TTNetVersion:f58efab5 2024-06-13 QuicVersion:5d23606e 2024-05-23)"
    )

    def __init__(
        self,
        proxy_string: Optional[str] = None,
        timeout: int = 10,
        signer_key: Optional[str] = None,
    ):
        self.proxy_string = proxy_string
        self.timeout = timeout
        self.signer = Metasec(key=signer_key)
        self.proxies = self._parse_proxy(proxy_string)
        self._feed_cache: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _parse_proxy(proxy_string: Optional[str]) -> Optional[Dict[str, str]]:
        if not proxy_string:
            return None
        parts = proxy_string.split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            p_url = f"http://{user}:{pwd}@{ip}:{port}"
            return {"http": p_url, "https": p_url}
        elif len(parts) == 2:
            ip, port = parts
            p_url = f"http://{ip}:{port}"
            return {"http": p_url, "https": p_url}
        return None

    def fetch_user_feed(
        self,
        sec_user_id: str,
        max_cursor: int = 0,
        count: int = 35,
        username: str = "",
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        Lấy danh sách bài đăng từ Mobile Feed API cho một sec_user_id.
        Trả về dict:
            {
                "success": bool,
                "aweme_list": list,
                "group_id_map": dict, # { aweme_id: group_id }
                "has_more": bool,
                "max_cursor": int,
                "error": str or None,
                "source": "live" | "cache" | "failed"
            }
        """
        if not sec_user_id:
            return {
                "success": False,
                "aweme_list": [],
                "group_id_map": {},
                "has_more": False,
                "max_cursor": 0,
                "error": "sec_user_id is empty",
                "source": "failed",
            }

        cache_key = f"{sec_user_id}_{max_cursor}"
        if use_cache and cache_key in self._feed_cache:
            return self._feed_cache[cache_key]

        last_error = None

        for host in self.HOSTS:
            try:
                result = self._request_feed_from_host(
                    host=host,
                    sec_user_id=sec_user_id,
                    max_cursor=max_cursor,
                    count=count,
                )
                if result.get("success"):
                    if use_cache:
                        self._feed_cache[cache_key] = result
                    # Lưu cache offline cho các lần chạy sau
                    self._save_offline_cache(username=username, sec_user_id=sec_user_id, data=result.get("data") or result)
                    return result
                else:
                    last_error = result.get("error")
            except Exception as e:
                logger.debug(f"[TikTokMobilePostClient] Host {host} error: {e}")
                last_error = str(e)

        # Fallback tầng 3: Đọc từ cache offline nếu tất cả cụm live bị chặn/rỗng (len=0)
        cached_result = self._load_offline_cache(username=username, sec_user_id=sec_user_id, max_cursor=max_cursor)
        if cached_result:
            if use_cache:
                self._feed_cache[cache_key] = cached_result
            return cached_result

        return {
            "success": False,
            "aweme_list": [],
            "group_id_map": {},
            "has_more": False,
            "max_cursor": 0,
            "error": last_error or "All mobile feed hosts failed",
            "source": "failed",
        }

    def _load_offline_cache(
        self,
        username: str,
        sec_user_id: str,
        max_cursor: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Đọc danh sách video và mapping group_id từ tệp cache feed offline."""
        candidate_paths = []
        clean_uname = username.lstrip("@").strip()
        if clean_uname:
            candidate_paths.append(os.path.join("data", "feeds", f"{clean_uname}_mobile.json"))
            candidate_paths.append(os.path.join("data", "feeds", f"{clean_uname}_mobile_api.json"))
            candidate_paths.append(os.path.join("data", "feeds", f"{clean_uname}.json"))
        if sec_user_id:
            candidate_paths.append(os.path.join("data", "feeds", f"{sec_user_id}_mobile.json"))
            candidate_paths.append(os.path.join("data", "feeds", f"{sec_user_id}.json"))

        for c_path in candidate_paths:
            if not os.path.exists(c_path):
                continue
            try:
                with open(c_path, "r", encoding="utf-8") as f:
                    c_data = json.load(f)
                raw_list = c_data.get("aweme_list") or []
                if not raw_list:
                    continue
                group_id_map: Dict[str, str] = {}
                for item in raw_list:
                    aid = clean_id_string(item.get("aweme_id") or item.get("id"))
                    if not aid:
                        continue
                    raw_gid = item.get("group_id") or item.get("groupId")
                    if raw_gid is None and isinstance(item.get("group_id_list"), dict):
                        gid_list = (
                            item.get("group_id_list", {}).get("GroupdIdList0")
                            or item.get("group_id_list", {}).get("GroupdIdList1")
                        )
                        if gid_list and len(gid_list) > 0:
                            raw_gid = gid_list[0]
                    gid = clean_id_string(raw_gid)
                    if gid:
                        group_id_map[aid] = gid

                has_more = bool(c_data.get("has_more", 0))
                next_cur = int(c_data.get("max_cursor", 0))
                return {
                    "success": True,
                    "aweme_list": raw_list,
                    "group_id_map": group_id_map,
                    "has_more": has_more,
                    "max_cursor": next_cur,
                    "error": None,
                    "source": "cache",
                }
            except Exception as e:
                logger.debug(f"[TikTokMobilePostClient] Cache read error on {c_path}: {e}")

        return None

    def _save_offline_cache(self, username: str, sec_user_id: str, data: Dict[str, Any]) -> None:
        """Lưu kết quả feed trả về từ máy chủ vào tệp offline cache."""
        try:
            os.makedirs(os.path.join("data", "feeds"), exist_ok=True)
            clean_uname = username.lstrip("@").strip()
            if clean_uname:
                p = os.path.join("data", "feeds", f"{clean_uname}_mobile.json")
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            if sec_user_id:
                p_sec = os.path.join("data", "feeds", f"{sec_user_id}_mobile.json")
                with open(p_sec, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"[TikTokMobilePostClient] Cache save note: {e}")

    def _request_feed_from_host(
        self,
        host: str,
        sec_user_id: str,
        max_cursor: int,
        count: int,
    ) -> Dict[str, Any]:
        """Gửi request ký Mobile đến một host cụ thể."""
        now = int(time.time())
        params_dict = {
            "aid": str(self.DEFAULT_AID),
            "sec_user_id": sec_user_id,
            "max_cursor": str(max_cursor),
            "count": str(count),
            "device_platform": "android",
            "app_name": self.DEFAULT_APP_NAME,
            "version_code": self.DEFAULT_VERSION_CODE,
            "version_name": self.DEFAULT_VERSION_NAME,
            "user_avatar_shrink": "144_144",
            "video_cover_shrink": "372_495",
            "sov_client_enable": "1",
            "sort_type": "0",
            "_rticket": str(now * 1000),
            "ts": str(now),
        }

        query_str = urlencode(params_dict)
        url = f"https://{host}/aweme/v1/aweme/post/?{query_str}"

        # Ký request bằng Metasec
        sig = self.signer.sign(
            url=url,
            app_id=self.DEFAULT_AID,
            app_version=self.DEFAULT_VERSION_NAME,
            app_launch_time=int(1730000000),
            device_type=self.DEFAULT_DEVICE_TYPE,
            sdk_version=self.DEFAULT_SDK_VERSION,
            sdk_version_code=self.DEFAULT_SDK_VERSION_CODE,
            license_id=self.DEFAULT_LICENSE_ID,
        )

        headers = {
            "User-Agent": self.DEFAULT_UA,
            "passport-sdk-version": "6010290",
            "x-ss-dp": str(self.DEFAULT_AID),
            "x-tt-dataflow-id": "134217728",
            "x-argus": sig["x-argus"],
            "x-ladon": sig["x-ladon"],
            "x-gorgon": sig["x-gorgon"],
            "x-khronos": str(sig["x-khronos"]),
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json",
            "Connection": "keep-alive",
        }

        # Gửi request (ưu tiên qua proxy nếu có cấu hình)
        resp = None
        try:
            if HAS_CURL_CFFI and self.proxies:
                resp = curl_requests.get(
                    url,
                    headers=headers,
                    proxies=self.proxies,
                    timeout=self.timeout,
                    impersonate="chrome124",
                )
            else:
                resp = requests.get(
                    url,
                    headers=headers,
                    proxies=self.proxies,
                    timeout=self.timeout,
                )
        except Exception as net_err:
            return {"success": False, "error": f"Network error on {host}: {net_err}"}

        if resp is None:
            return {"success": False, "error": f"No response from {host}"}

        if resp.status_code != 200:
            return {
                "success": False,
                "error": f"HTTP {resp.status_code} on {host}",
                "status_code": resp.status_code,
            }

        if len(resp.content) == 0:
            # Máy chủ trả về 0 byte (bị WAF / Janus rate limit)
            is_janus = "X-Janus-Info" in resp.headers
            return {
                "success": False,
                "error": f"Empty response (len=0, janus={is_janus}) on {host}",
                "is_janus": is_janus,
            }

        try:
            data = resp.json()
        except Exception as json_err:
            return {"success": False, "error": f"JSON decode error: {json_err}"}

        status_code = data.get("status_code")
        if status_code != 0:
            return {
                "success": False,
                "error": f"API status_code {status_code}: {data.get('status_msg')}",
                "data": data,
            }

        raw_list = data.get("aweme_list") or []
        group_id_map: Dict[str, str] = {}

        for item in raw_list:
            aid = clean_id_string(item.get("aweme_id") or item.get("id"))
            if not aid:
                continue

            raw_gid = item.get("group_id") or item.get("groupId")
            if raw_gid is None and isinstance(item.get("group_id_list"), dict):
                gid_list = (
                    item.get("group_id_list", {}).get("GroupdIdList0")
                    or item.get("group_id_list", {}).get("GroupdIdList1")
                )
                if gid_list and len(gid_list) > 0:
                    raw_gid = gid_list[0]

            gid = clean_id_string(raw_gid)
            if gid:
                group_id_map[aid] = gid

        has_more = bool(data.get("has_more", 0))
        next_cursor = int(data.get("max_cursor", 0))

        return {
            "success": True,
            "aweme_list": raw_list,
            "group_id_map": group_id_map,
            "has_more": has_more,
            "max_cursor": next_cursor,
            "error": None,
            "source": "live",
        }
