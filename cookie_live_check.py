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


@dataclass(frozen=True)
class CookieCheckResult:
    account_uuid: str = ""
    profile_name: str = ""
    state: CookieCheckState = CookieCheckState.PENDING
    source: CookieSource = CookieSource.NONE
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