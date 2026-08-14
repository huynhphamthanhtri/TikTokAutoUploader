"""Cookie conversion between Selenium/config and Patchright formats.

The module has no dependency on either browser engine. Invalid cookies return
``None`` from the single-cookie functions and are omitted by batch functions.
"""

import math
from collections.abc import Iterable, Mapping


_SAME_SITE = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
}

# TikTok session cookies: only these can prove an authenticated session.
# Guest cookies (msToken, ttwid, theme, country code, ...) are excluded.
TIKTOK_AUTH_PRIMARY = frozenset({"sessionid", "sessionid_ss", "sid_tt"})
TIKTOK_AUTH_SUPPORTING = frozenset(
    {"sid_guard", "uid_tt", "uid_tt_ss", "multi_sids", "ssid_ucp_v1"}
)
TIKTOK_AUTH_COOKIES = TIKTOK_AUTH_PRIMARY | TIKTOK_AUTH_SUPPORTING


def cookie_is_tiktok_auth(cookie):
    """True when the cookie is a TikTok session/auth cookie, not a guest cookie."""
    if not isinstance(cookie, Mapping):
        return False
    name = _safe_text(cookie.get("name"), allow_empty=False)
    return name in TIKTOK_AUTH_COOKIES


def tiktok_auth_cookie_names(cookies):
    """Names of TikTok auth cookies present in an iterable of cookie dicts."""
    return [cookie.get("name") for cookie in cookies if cookie_is_tiktok_auth(cookie)]


def has_primary_tiktok_auth_cookie(cookies):
    """True when at least one primary session cookie (sessionid/ss, sid_tt) is present."""
    names = {cookie.get("name") for cookie in cookies if isinstance(cookie, Mapping)}
    return bool(names & TIKTOK_AUTH_PRIMARY)


def _safe_text(value, *, allow_empty=True):
    if not isinstance(value, str) or (not allow_empty and not value):
        return None
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return None
    return value


def _common_cookie(cookie):
    if not isinstance(cookie, Mapping):
        return None

    name = _safe_text(cookie.get("name"), allow_empty=False)
    value = _safe_text(cookie.get("value"))
    domain = _safe_text(cookie.get("domain"), allow_empty=False)
    if name is None or value is None or domain is None:
        return None
    if ";" in name or any(char.isspace() for char in domain) or "/" in domain:
        return None

    result = {"name": name, "value": value, "domain": domain}
    path = cookie.get("path", "/")
    if _safe_text(path, allow_empty=False) is None or not path.startswith("/"):
        return None
    result["path"] = path

    for field in ("secure", "httpOnly"):
        if field in cookie:
            if not isinstance(cookie[field], bool):
                return None
            result[field] = cookie[field]

    if "sameSite" in cookie and cookie["sameSite"] is not None:
        raw_same_site = cookie["sameSite"]
        if not isinstance(raw_same_site, str):
            return None
        same_site = _SAME_SITE.get(raw_same_site.lower())
        if same_site is None:
            return None
        result["sameSite"] = same_site
    return result


def _expiration(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Cookie expiration must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Cookie expiration must be a finite number")
    # Selenium and Chromium exports commonly use -1 for a session cookie.
    return None if value < 0 else value


def selenium_cookie_to_patchright(cookie):
    """Convert one Selenium/config cookie, returning ``None`` if invalid."""
    result = _common_cookie(cookie)
    if result is None:
        return None
    try:
        expires = _expiration(cookie.get("expiry"))
    except ValueError:
        return None
    if expires is not None:
        result["expires"] = expires
    return result


def patchright_cookie_to_selenium(cookie):
    """Convert one Patchright cookie, returning ``None`` if invalid."""
    result = _common_cookie(cookie)
    if result is None:
        return None
    try:
        expires = _expiration(cookie.get("expires"))
    except ValueError:
        return None
    if expires is not None:
        result["expiry"] = int(expires)
    return result


def _convert_many(cookies, converter):
    if not isinstance(cookies, Iterable) or isinstance(cookies, (str, bytes, Mapping)):
        return []
    return [converted for cookie in cookies if (converted := converter(cookie)) is not None]


def selenium_cookies_to_patchright(cookies):
    """Convert valid cookies in an iterable and safely omit invalid entries."""
    return _convert_many(cookies, selenium_cookie_to_patchright)


def patchright_cookies_to_selenium(cookies):
    """Convert valid cookies in an iterable and safely omit invalid entries."""
    return _convert_many(cookies, patchright_cookie_to_selenium)
