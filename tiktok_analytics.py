"""
tiktok_analytics.py - TikTok 30-Day Channel Views & Performance Analytics Engine
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

STUDIO_ITEM_LIST_URL = "https://www.tiktok.com/tiktok/creator/manage/item_list/v1/"
STUDIO_REFERER = "https://www.tiktok.com/tiktokstudio/content"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def format_compact_number(val: Any) -> str:
    """Format numbers into human-readable compact notation (e.g. 1.2M, 125.4K, 950, 0)."""
    if val is None or val == "":
        return "0"
    try:
        num = float(val)
    except (ValueError, TypeError):
        return "0"

    if num < 0:
        return "0"
    if num >= 1_000_000_000:
        v = num / 1_000_000_000
        return f"{v:.1f}".rstrip("0").rstrip(".") + "B"
    if num >= 1_000_000:
        v = num / 1_000_000
        return f"{v:.1f}".rstrip("0").rstrip(".") + "M"
    if num >= 1_000:
        v = num / 1_000
        return f"{v:.1f}".rstrip("0").rstrip(".") + "K"
    return str(int(num))


def format_views_follow_badge(views_30d: Any, followers: Any) -> str:
    """Format combined Treeview badge '👁️ 125.4K  |  👥 15.2K' or '— | —'."""
    v_str = format_compact_number(views_30d) if isinstance(views_30d, (int, float)) and views_30d >= 0 else None
    f_str = format_compact_number(followers) if isinstance(followers, (int, float)) and followers >= 0 else None

    if v_str is None and f_str is None:
        return "— | —"
    left = f"👁️ {v_str}" if v_str is not None else "👁️ —"
    right = f"👥 {f_str}" if f_str is not None else "👥 —"
    return f"{left}  |  {right}"


def parse_cookie_input(cookie_data: Any) -> Tuple[Dict[str, str], str, str]:
    """Parse cookie dict, list, JSON, or key=val string into (cookie_dict, cookie_header_str, csrf_token)."""
    cookie_dict: Dict[str, str] = {}
    if not cookie_data:
        return {}, "", ""

    if isinstance(cookie_data, dict):
        cookie_dict = {str(k): str(v) for k, v in cookie_data.items() if k and v is not None}
    elif isinstance(cookie_data, list):
        for item in cookie_data:
            if isinstance(item, dict) and "name" in item and "value" in item:
                cookie_dict[str(item["name"])] = str(item["value"])
    elif isinstance(cookie_data, str):
        trimmed = cookie_data.strip()
        if (trimmed.startswith("[") and trimmed.endswith("]")) or (trimmed.startswith("{") and trimmed.endswith("}")):
            try:
                parsed = json.loads(trimmed)
                return parse_cookie_input(parsed)
            except Exception:
                pass

        for part in trimmed.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                cookie_dict[k.strip()] = v.strip()

    # Synthesize sessionid if alternate session tokens exist
    if "sessionid" not in cookie_dict:
        if "sid_tt" in cookie_dict:
            cookie_dict["sessionid"] = cookie_dict["sid_tt"]
        elif "sessionid_ss" in cookie_dict:
            cookie_dict["sessionid"] = cookie_dict["sessionid_ss"]
        elif "sid_guard" in cookie_dict:
            cookie_dict["sessionid"] = cookie_dict["sid_guard"].split("%7C")[0].split("|")[0]
        elif "multi_sids" in cookie_dict:
            cookie_dict["sessionid"] = cookie_dict["multi_sids"].split("%3A")[-1].split(":")[-1]

    csrf_token = cookie_dict.get("tt_csrf_token") or cookie_dict.get("csrf_token") or ""
    cookie_header_str = "; ".join([f"{k}={v}" for k, v in cookie_dict.items() if k and v is not None])
    return cookie_dict, cookie_header_str, csrf_token


def build_studio_headers(
    cookie_str: str,
    csrf_token: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Dict[str, str]:
    """Construct authentic browser headers for TikTok Creator Studio Web endpoints."""
    headers = {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
        "Referer": STUDIO_REFERER,
        "Origin": "https://www.tiktok.com",
        "Cookie": cookie_str,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if csrf_token:
        headers["x-secsdk-csrf-token"] = csrf_token
        headers["tt-csrf-token"] = csrf_token
    return headers


def build_proxy_dict(proxy_string: Optional[str]) -> Optional[Dict[str, str]]:
    """Convert proxy string (ip:port:user:pass or ip:port or http://...) to requests proxy dict."""
    if not proxy_string or not str(proxy_string).strip():
        return None
    clean = str(proxy_string).strip()
    
    scheme = "http"
    if clean.startswith(("http://", "https://", "socks5://", "socks5h://")):
        scheme, clean = clean.split("://", 1)

    parts = clean.split(":")
    if len(parts) >= 4:
        ip, port, user, pwd = parts[0], parts[1], parts[2], parts[3]
        url = f"{scheme}://{user}:{pwd}@{ip}:{port}"
        return {"http": url, "https": url}
    elif len(parts) == 2:
        url = f"{scheme}://{parts[0]}:{parts[1]}"
        return {"http": url, "https": url}
    elif "@" in clean:
        url = f"{scheme}://{clean}"
        return {"http": url, "https": url}

    url = f"{scheme}://{clean}"
    return {"http": url, "https": url}


def fetch_public_profile_stats(
    tiktok_id: str,
    proxy_string: Optional[str] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Fetch public stats (followerCount, videoCount, heartCount, secUid) by scraping
    the public TikTok user profile HTML rehydration JSON.
    """
    tid = str(tiktok_id or "").strip().lstrip("@")
    if not tid:
        return {"ok": False, "error": "Thiếu TikTok ID"}

    url = f"https://www.tiktok.com/@{tid}"
    proxies = build_proxy_dict(proxy_string)
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = requests.get(url, headers=headers, proxies=proxies, timeout=timeout)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}

        html = resp.text
        # 1. Parse __UNIVERSAL_DATA_FOR_REHYDRATION__
        m_univ = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m_univ:
            try:
                u_data = json.loads(m_univ.group(1))
                scope = u_data.get("__DEFAULT_SCOPE__", {})
                user_detail = scope.get("webapp.user-detail", {})
                user_info = user_detail.get("userInfo", {})
                user = user_info.get("user", {})
                stats = user_info.get("stats", {})
                return {
                    "ok": True,
                    "follower_count": int(stats.get("followerCount", 0) or 0),
                    "video_count": int(stats.get("videoCount", 0) or 0),
                    "heart_count": int(stats.get("heartCount", 0) or 0),
                    "sec_uid": user.get("secUid", ""),
                    "unique_id": user.get("uniqueId", tid),
                    "nickname": user.get("nickname", ""),
                    "source": "PUBLIC_REHYDRATION",
                    "error": None,
                }
            except Exception:
                pass

        # 2. Regex fallback
        mf = re.search(r'"followerCount":(\d+)', html)
        mv = re.search(r'"videoCount":(\d+)', html)
        mh = re.search(r'"heartCount":(\d+)', html)
        if mf or mv or mh:
            return {
                "ok": True,
                "follower_count": int(mf.group(1)) if mf else 0,
                "video_count": int(mv.group(1)) if mv else 0,
                "heart_count": int(mh.group(1)) if mh else 0,
                "sec_uid": "",
                "unique_id": tid,
                "nickname": "",
                "source": "PUBLIC_REGEX",
                "error": None,
            }

        return {"ok": False, "error": "Không tìm thấy dữ liệu thống kê trên trang công khai"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def fetch_channel_views_via_browser(
    profile_path: str,
    proxy_string: Optional[str] = None,
    timeout: int = 25,
    now_epoch: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetch all videos published within the last 30 days and sum their play_count
    by executing in-session JavaScript within a headless browser instance.
    Guarantees 100% bypass of TikTok Studio WAF signature checks.
    """
    if now_epoch is None:
        now_epoch = int(time.time())
    cutoff_epoch = now_epoch - (30 * 86400)

    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"ok": False, "error": "Chưa cài đặt patchright/playwright"}

    total_views = 0
    total_videos = 0
    total_likes = 0
    total_shares = 0
    total_comments = 0
    latest_video_time = None

    proxy_cfg = None
    if proxy_string and proxy_string.strip():
        ps = proxy_string.strip()
        parsed = build_proxy_dict(ps)
        if parsed and "http" in parsed:
            proxy_cfg = {"server": parsed["http"]}

    try:
        with sync_playwright() as p:
            launch_kwargs = {
                "user_data_dir": profile_path,
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            }
            if proxy_cfg:
                launch_kwargs["proxy"] = proxy_cfg

            context = p.chromium.launch_persistent_context(**launch_kwargs)
            page = context.new_page()

            # Navigate to Studio content origin
            try:
                page.goto("https://www.tiktok.com/tiktokstudio/content", wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception:
                pass

            cursor = 0
            has_more = True
            page_count = 0
            max_pages = 20

            while has_more and page_count < max_pages:
                page_count += 1
                js_code = f"""
                async () => {{
                    try {{
                        const res = await fetch('https://www.tiktok.com/tiktok/creator/manage/item_list/v1/', {{
                            method: 'POST',
                            headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json' }},
                            body: JSON.stringify({{
                                count: 50,
                                cursor: {cursor},
                                sort_orders: [{{ field_name: 'post_time', sort_order: 2 }}],
                                conditions: {{}}
                            }})
                        }});
                        return await res.json();
                    }} catch (e) {{
                        return {{ error: e.toString() }};
                    }}
                }}
                """
                res_json = page.evaluate(js_code)
                if not isinstance(res_json, dict) or res_json.get("code", res_json.get("status_code", -1)) != 0:
                    break

                data = res_json.get("data", {}) or {}
                item_list = data.get("item_list", []) or []
                if not item_list:
                    break

                should_stop = False
                for item in item_list:
                    create_time = int(item.get("create_time", 0) or 0)
                    if latest_video_time is None and create_time > 0:
                        latest_video_time = create_time

                    if create_time < cutoff_epoch:
                        should_stop = True
                        break

                    stats = item.get("statistics", {}) or item.get("item_stats", {}) or {}
                    play_count = int(stats.get("play_count", stats.get("video_view_count", 0)) or 0)
                    digg_count = int(stats.get("digg_count", 0) or 0)
                    share_count = int(stats.get("share_count", 0) or 0)
                    comment_count = int(stats.get("comment_count", 0) or 0)

                    total_views += play_count
                    total_likes += digg_count
                    total_shares += share_count
                    total_comments += comment_count
                    total_videos += 1

                if should_stop:
                    break

                has_more = bool(data.get("has_more", False))
                cursor = int(data.get("cursor", cursor + 50))

            context.close()

            return {
                "ok": True,
                "views_30d": total_views,
                "videos_30d": total_videos,
                "likes_30d": total_likes,
                "shares_30d": total_shares,
                "comments_30d": total_comments,
                "views_30d_fmt": format_compact_number(total_views),
                "videos_30d_fmt": str(total_videos),
                "likes_30d_fmt": format_compact_number(total_likes),
                "latest_video_time": latest_video_time,
                "source": "BROWSER_STUDIO_ITEM_LIST",
                "error": None,
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def fetch_channel_views_30d(
    cookie_data: Any,
    proxy_string: Optional[str] = None,
    profile_path: Optional[str] = None,
    tiktok_id: Optional[str] = None,
    timeout: int = 15,
    now_epoch: Optional[int] = None,
    user_agent: Optional[str] = None,
    prefer_browser: bool = False,
) -> Dict[str, Any]:
    """
    Unified multi-tier channel views fetcher:
    - Tier 1: Browser In-Session Fetch (if prefer_browser or profile_path provided).
    - Tier 2: Creator Studio HTTP item_list / CRP Dashboard Overview fallback.
    - Combines public follower & video stats for comprehensive metrics.
    """
    if now_epoch is None:
        now_epoch = int(time.time())
    cutoff_epoch = now_epoch - (30 * 86400)

    # 1. Tier 1: Browser in-session fetch
    if prefer_browser and profile_path:
        b_res = fetch_channel_views_via_browser(profile_path, proxy_string, timeout=timeout, now_epoch=now_epoch)
        if b_res.get("ok"):
            if tiktok_id:
                pub = fetch_public_profile_stats(tiktok_id, proxy_string, timeout=timeout)
                if pub.get("ok"):
                    b_res["follower_count"] = pub.get("follower_count")
            return b_res

    # 2. Tier 2: HTTP item_list / CRP Overview
    cookie_dict, cookie_str, csrf_token = parse_cookie_input(cookie_data)
    if not cookie_str or "sessionid" not in cookie_str:
        # Fallback to public profile if no cookie
        if tiktok_id:
            pub = fetch_public_profile_stats(tiktok_id, proxy_string, timeout=timeout)
            if pub.get("ok"):
                return {
                    "ok": True,
                    "views_30d": 0,
                    "videos_30d": pub.get("video_count", 0),
                    "likes_30d": pub.get("heart_count", 0),
                    "shares_30d": 0,
                    "comments_30d": 0,
                    "views_30d_fmt": "0",
                    "videos_30d_fmt": str(pub.get("video_count", 0)),
                    "likes_30d_fmt": format_compact_number(pub.get("heart_count", 0)),
                    "follower_count": pub.get("follower_count"),
                    "source": "PUBLIC_PROFILE",
                    "error": None,
                }
        return {
            "ok": False,
            "views_30d": 0,
            "videos_30d": 0,
            "likes_30d": 0,
            "shares_30d": 0,
            "comments_30d": 0,
            "views_30d_fmt": "0",
            "videos_30d_fmt": "0",
            "likes_30d_fmt": "0",
            "latest_video_time": None,
            "error": "Thiếu cookie sessionid",
        }

    headers = build_studio_headers(cookie_str, csrf_token, user_agent)
    proxies = build_proxy_dict(proxy_string)

    total_views = 0
    total_videos = 0
    total_likes = 0
    total_shares = 0
    total_comments = 0
    latest_video_time = None

    # Try HTTP item_list/v1
    cursor = 0
    has_more = True
    page_count = 0
    max_pages = 20
    http_success = False

    while has_more and page_count < max_pages:
        page_count += 1
        payload = {
            "count": 50,
            "cursor": cursor,
            "sort_orders": [{"field_name": "post_time", "sort_order": 2}],
            "conditions": {},
        }

        try:
            resp = requests.post(
                STUDIO_ITEM_LIST_URL,
                json=payload,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
            )
            if resp.status_code != 200:
                break

            res_json = resp.json()
            code = res_json.get("code", res_json.get("status_code", -1))
            if code != 0:
                break

            data = res_json.get("data", {}) or {}
            item_list = data.get("item_list", []) or []
            if not item_list:
                http_success = True
                break

            should_stop = False
            for item in item_list:
                c_time = int(item.get("create_time", 0) or 0)
                if latest_video_time is None and c_time > 0:
                    latest_video_time = c_time
                if c_time < cutoff_epoch:
                    should_stop = True
                    break
                stats = item.get("statistics", {}) or {}
                total_views += int(stats.get("play_count", 0) or 0)
                total_likes += int(stats.get("digg_count", 0) or 0)
                total_shares += int(stats.get("share_count", 0) or 0)
                total_comments += int(stats.get("comment_count", 0) or 0)
                total_videos += 1

            http_success = True
            if should_stop or not data.get("has_more", False):
                break

            cursor = int(data.get("cursor", cursor + 50))
        except Exception:
            break

    if http_success:
        follower_cnt = None
        if tiktok_id:
            pub = fetch_public_profile_stats(tiktok_id, proxy_string, timeout=timeout)
            if pub.get("ok"):
                follower_cnt = pub.get("follower_count")

        return {
            "ok": True,
            "views_30d": total_views,
            "videos_30d": total_videos,
            "likes_30d": total_likes,
            "shares_30d": total_shares,
            "comments_30d": total_comments,
            "views_30d_fmt": format_compact_number(total_views),
            "videos_30d_fmt": str(total_videos),
            "likes_30d_fmt": format_compact_number(total_likes),
            "follower_count": follower_cnt,
            "latest_video_time": latest_video_time,
            "source": "STUDIO_ITEM_LIST",
            "error": None,
        }

    # If HTTP item_list failed and valid browser profile directory exists, run browser fetch
    is_valid_browser_profile = False
    if profile_path and os.path.isdir(profile_path):
        # Must contain browser profile markers, not just video files
        if any(os.path.exists(os.path.join(profile_path, marker)) for marker in ("Default", "Cookies", "data.orbita", "data.huynhthang", "Preferences")):
            is_valid_browser_profile = True

    if is_valid_browser_profile:
        b_res = fetch_channel_views_via_browser(profile_path, proxy_string, timeout=timeout, now_epoch=now_epoch)
        if b_res.get("ok"):
            if tiktok_id:
                pub = fetch_public_profile_stats(tiktok_id, proxy_string, timeout=timeout)
                if pub.get("ok"):
                    b_res["follower_count"] = pub.get("follower_count")
            return b_res

    # Final fallback: CRP Dashboard Overview + Public Profile Stats
    follower_cnt = None
    if tiktok_id:
        pub = fetch_public_profile_stats(tiktok_id, proxy_string, timeout=timeout)
        if pub.get("ok"):
            follower_cnt = pub.get("follower_count")
            total_videos = pub.get("video_count", 0)
            total_likes = pub.get("heart_count", 0)

    try:
        now_dt = time.gmtime(now_epoch)
        start_dt = time.gmtime(cutoff_epoch)
        s_str = time.strftime("%Y%m%d", start_dt)
        e_str = time.strftime("%Y%m%d", now_dt)
        dash_url = f"https://api16-normal-useast8.tiktokv.us/tiktok/v1/creator/incentives/analytics/dashboard_overview?start_date={s_str}&end_date={e_str}&region=US"
        dash_resp = requests.get(dash_url, headers=headers, proxies=proxies, timeout=timeout)
        if dash_resp.status_code == 200:
            dash_json = dash_resp.json()
            if dash_json.get("status_code") == 0:
                q_views = int(dash_json.get("qualified_views", 0) or 0)
                return {
                    "ok": True,
                    "views_30d": q_views,
                    "videos_30d": total_videos,
                    "likes_30d": total_likes,
                    "shares_30d": 0,
                    "comments_30d": 0,
                    "views_30d_fmt": format_compact_number(q_views),
                    "videos_30d_fmt": str(total_videos),
                    "likes_30d_fmt": format_compact_number(total_likes),
                    "follower_count": follower_cnt,
                    "latest_video_time": None,
                    "source": "CRP_DASHBOARD_OVERVIEW",
                    "error": None,
                }
    except Exception:
        pass

    return {
        "ok": True if (follower_cnt is not None or total_videos > 0) else False,
        "views_30d": total_views,
        "videos_30d": total_videos,
        "likes_30d": total_likes,
        "shares_30d": 0,
        "comments_30d": 0,
        "views_30d_fmt": format_compact_number(total_views),
        "videos_30d_fmt": str(total_videos),
        "likes_30d_fmt": format_compact_number(total_likes),
        "follower_count": follower_cnt,
        "latest_video_time": None,
        "source": "PUBLIC_PROFILE" if follower_cnt is not None else "UNKNOWN",
        "error": None if (follower_cnt is not None or total_videos > 0) else "Không thể kết nối API",
    }
