"""Pure cookie live-check model. No Tkinter/browser dependencies.

Defines result states, classification and safe summary helpers so the
batch dialog only renders what a worker thread computes.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


import re


class CookieCheckState(str, Enum):
    PENDING = "PENDING"
    CHECKING = "CHECKING"
    LIVE = "LIVE"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"
    PROXY_ERROR = "PROXY_ERROR"
    PROFILE_BUSY = "PROFILE_BUSY"
    BROWSER_ERROR = "BROWSER_ERROR"
    PERSIST_ERROR = "PERSIST_ERROR"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class CookieSource(str, Enum):
    NONE = "NONE"
    PROFILE_SESSION = "PROFILE_SESSION"
    SAVED_COOKIE = "SAVED_COOKIE"


class CookieCheckMode(str, Enum):
    HTTP_FAST = "HTTP_FAST"
    BROWSER_FULL = "BROWSER_FULL"


@dataclass(frozen=True)
class CookieCheckResult:
    account_uuid: str = ""
    profile_name: str = ""
    state: CookieCheckState = CookieCheckState.PENDING
    source: CookieSource = CookieSource.NONE
    mode: CookieCheckMode = CookieCheckMode.BROWSER_FULL
    auth_cookie_names: tuple = ()
    checked_at: str = ""
    detail: str = ""
    kept_cookies: bool = False

    def display_source(self):
        return {
            CookieSource.NONE: "",
            CookieSource.PROFILE_SESSION: "Profile session",
            CookieSource.SAVED_COOKIE: "Cookie lưu",
        }.get(self.source, "")

    def display_state(self):
        return {
            CookieCheckState.PENDING: "Chờ",
            CookieCheckState.CHECKING: "Đang kiểm tra",
            CookieCheckState.LIVE: "Live",
            CookieCheckState.DEAD: "Die",
            CookieCheckState.UNKNOWN: "Không rõ",
            CookieCheckState.PROXY_ERROR: "Lỗi proxy",
            CookieCheckState.PROFILE_BUSY: "Profile bận",
            CookieCheckState.BROWSER_ERROR: "Lỗi browser",
            CookieCheckState.PERSIST_ERROR: "Lỗi lưu cookie",
            CookieCheckState.SKIPPED: "Bỏ qua",
            CookieCheckState.CANCELLED: "Đã dừng",
        }.get(self.state, str(self.state))


def primary_auth_cookie_names(cookies):
    """Names of primary TikTok auth cookies present in an iterable."""
    names = set()
    for cookie in cookies or ():
        try:
            name = str((cookie or {}).get("name") or "").strip()
        except Exception:
            name = ""
        if name in ("sessionid", "sessionid_ss", "sid_tt"):
            names.add(name)
    return tuple(sorted(names))


def any_auth_cookie_names(cookies):
    names = set()
    for cookie in cookies or ():
        try:
            name = str((cookie or {}).get("name") or "").strip()
        except Exception:
            name = ""
        if name:
            names.add(name)
    return tuple(sorted(names))


def classify_login_state(login_state, has_primary_cookie):
    """Map a TikTok login verdict to a stable check state.

    ``login_state`` is ``authenticated``, ``login_required`` or
    ``indeterminate``. When indeterminate the result must never be DEAD.
    """
    if login_state == "authenticated":
        return CookieCheckState.LIVE
    if login_state == "login_required":
        return CookieCheckState.DEAD
    return CookieCheckState.UNKNOWN


def build_summary(results):
    counts = {
        "total": len(results),
        "live": 0,
        "dead": 0,
        "unknown": 0,
        "proxy_error": 0,
        "profile_busy": 0,
        "browser_error": 0,
        "persist_error": 0,
        "skipped": 0,
        "cancelled": 0,
        "checking": 0,
        "pending": 0,
    }
    for result in results:
        state = result.state
        if state == CookieCheckState.LIVE:
            counts["live"] += 1
        elif state == CookieCheckState.DEAD:
            counts["dead"] += 1
        elif state == CookieCheckState.UNKNOWN:
            counts["unknown"] += 1
        elif state == CookieCheckState.PROXY_ERROR:
            counts["proxy_error"] += 1
        elif state == CookieCheckState.PROFILE_BUSY:
            counts["profile_busy"] += 1
        elif state == CookieCheckState.BROWSER_ERROR:
            counts["browser_error"] += 1
        elif state == CookieCheckState.PERSIST_ERROR:
            counts["persist_error"] += 1
        elif state == CookieCheckState.SKIPPED:
            counts["skipped"] += 1
        elif state == CookieCheckState.CANCELLED:
            counts["cancelled"] += 1
        elif state == CookieCheckState.CHECKING:
            counts["checking"] += 1
        else:
            counts["pending"] += 1
    return counts


_MASK_COOKIE_VALUE = re.compile(
    r"(cookie-auth|sessionid|sessionid_ss|sid_tt|sid_guard|uid_tt)\s*=\s*[^\s;]+"
)


def mask_detail(text, secrets=()):
    """Mask cookie values, proxy passwords and any supplied secret strings."""
    masked = str(text or "")
    for secret in secrets or ():
        if not secret:
            continue
        secret = str(secret)
        if secret and secret in masked:
            masked = masked.replace(secret, "***")
    for pattern in ("sessionid", "sessionid_ss", "sid_tt", "sid_guard", "uid_tt"):
        masked = masked.replace(pattern, "cookie-auth")
    masked = _MASK_COOKIE_VALUE.sub(r"cookie-auth=***", masked)
    return masked


def check_cookie_fast_http(cookie_raw, proxy_cfg=None, timeout=8.0):
    """Kiểm tra nhanh cookie qua TikTok Webcast / Passport API không cần mở browser.
    
    Returns: (CookieCheckState, detail_message, auth_cookie_names_tuple)
    """
    import requests
    from tiktok_monetization_client import build_cookie_string
    from core_helpers import parse_proxy_string

    cookie_str = build_cookie_string(cookie_raw)
    if not cookie_str:
        return CookieCheckState.DEAD, "Không có chuỗi cookie để kiểm tra", ()

    # Check primary auth cookies
    auth_names = []
    for piece in cookie_str.split(";"):
        k = piece.strip().split("=")[0].strip()
        if k in ("sessionid", "sessionid_ss", "sid_tt", "sid_guard"):
            auth_names.append(k)
    auth_tuple = tuple(sorted(set(auth_names)))
    if not auth_tuple:
        return CookieCheckState.DEAD, "Cookie thiếu token xác thực chính (sessionid)", ()

    session = requests.Session()
    if proxy_cfg and proxy_cfg.get("use_proxy") and proxy_cfg.get("proxy_string"):
        parsed = parse_proxy_string(proxy_cfg["proxy_string"])
        if parsed and parsed.get("ip") and parsed.get("port"):
            p_type = str(proxy_cfg.get("proxy_type", "http")).lower()
            u, p = parsed.get("user"), parsed.get("pass")
            if u and p:
                p_url = f"{p_type}://{u}:{p}@{parsed['ip']}:{parsed['port']}"
            else:
                p_url = f"{p_type}://{parsed['ip']}:{parsed['port']}"
            session.proxies = {"http": p_url, "https": p_url}

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.7559.96 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Cookie": cookie_str,
        "Referer": "https://www.tiktok.com/",
    }

    try:
        # 1. Check endpoint Webcast Creator Earnings (Nhạy với cookie die)
        url = "https://webcast.tiktok.com/webcast/api/money/creator_earnings/v1/payout_summary"
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code in (401, 403):
            return CookieCheckState.DEAD, "TikTok trả về 401/403 (Cookie hết hạn)", auth_tuple
        if resp.status_code == 200:
            try:
                data = resp.json()
                st_code = data.get("status_code", 0)
                st_msg = str(data.get("status_message", "")).lower()
                if st_code in (20003, 10003) or "not logged in" in st_msg or "login" in st_msg:
                    return CookieCheckState.DEAD, f"TikTok báo chưa đăng nhập (status {st_code})", auth_tuple
                return CookieCheckState.LIVE, "Cookie Live (Xác thực qua Webcast API)", auth_tuple
            except Exception:
                pass

        # 2. Fallback endpoint Passport Account Info
        url_pass = "https://www.tiktok.com/passport/web/account/info/"
        resp_pass = session.get(url_pass, headers=headers, timeout=timeout)
        if resp_pass.status_code == 200:
            try:
                p_data = resp_pass.json().get("data", {})
                if p_data.get("user_id") or p_data.get("user_id_str"):
                    return CookieCheckState.LIVE, "Cookie Live (Xác thực qua Passport API)", auth_tuple
            except Exception:
                pass

        return CookieCheckState.UNKNOWN, f"Không xác định được (HTTP {resp.status_code})", auth_tuple
    except requests.exceptions.ProxyError as e:
        return CookieCheckState.PROXY_ERROR, f"Lỗi kết nối Proxy: {type(e).__name__}", auth_tuple
    except requests.RequestException as e:
        err_str = str(e).lower()
        if "proxy" in err_str:
            return CookieCheckState.PROXY_ERROR, f"Lỗi Proxy: {type(e).__name__}", auth_tuple
        return CookieCheckState.UNKNOWN, f"Lỗi mạng: {type(e).__name__}", auth_tuple
    except Exception as e:
        return CookieCheckState.UNKNOWN, f"Lỗi kiểm tra: {type(e).__name__}", auth_tuple