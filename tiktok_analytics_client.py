"""Read-only two-tier TikTok 30-day channel analytics collection."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from tiktok_monetization_client import TikTokMonetizationClient


ANALYTICS_OVERVIEW_URL = "https://www.tiktok.com/api/v1/creator/analytics/overview/"
PUBLIC_ITEM_LIST_URL = "https://www.tiktok.com/api/post/item_list/"
ANALYTICS_REFERER = "https://www.tiktok.com/creator-center/analytics"
ITEM_LIST_PAGE_SIZE = 35
ITEM_LIST_MAX_PAGES = 100
RAW_VIEW_SOURCES = {"STUDIO_ITEM_LIST", "BROWSER_STUDIO_ITEM_LIST"}


def _as_non_negative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _value(mapping: Dict[str, Any], *names: str) -> Optional[int]:
    for name in names:
        value = _as_non_negative_int(mapping.get(name))
        if value is not None:
            return value
    return None


class TikTokAnalyticsClient(TikTokMonetizationClient):
    """Primary vv_history analytics with a public-video-list fallback."""

    def _base_result(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "state": "NOT_AVAILABLE",
            "views_state": "NOT_AVAILABLE",
            "follower_state": "NOT_AVAILABLE",
            "primary_state": "NOT_ATTEMPTED",
            "fallback_state": "NOT_ATTEMPTED",
            "follower_count": None,
            "views_30d": None,
            "likes_30d": None,
            "comments_30d": None,
            "shares_30d": None,
            "videos_30d": None,
            "avg_daily_views": None,
            "fyp_views": None,
            "fyp_percent": None,
            "engagement_rate": None,
            "crp_qualified_views_30d": None,
            "days_count": 0,
            "calculation_source": "EMPTY",
            "auth_source": "NONE",
            "studio_observed_request_shapes": [],
            "studio_observed_response_shapes": [],
            "checked_at": now.isoformat(),
            "fresh_until": (now.timestamp() + 15 * 60),
            "source": {"followers": "public_profile", "views": "none"},
            "error_code": "",
            "error_message": "",
        }

    @staticmethod
    def _response_error(response: Any, source: str, result: Dict[str, Any]) -> bool:
        if response.status_code in (401, 403):
            result.update(error_code=f"{source}_HTTP_{response.status_code}", error_message="TikTok rejected the analytics request.")
            return True
        if response.status_code == 429:
            result.update(error_code=f"{source}_HTTP_429", error_message="TikTok rate limited the analytics request.")
            return True
        if response.status_code != 200:
            result.update(error_code=f"{source}_HTTP_{response.status_code}", error_message="TikTok analytics request failed.")
            return True
        return False

    def _headers(self, referer: str) -> Dict[str, str]:
        headers = self._get_headers()
        headers["Referer"] = referer
        return headers

    def _fetch_primary(self, result: Dict[str, Any]) -> bool:
        from browser_patchright_glue import (
            ProfileBusyError,
            SessionSetupError,
            close_session,
            fetch_tiktok_studio_analytics,
            import_cookies,
            open_session,
            parse_cookie,
        )

        token = None
        try:
            token = open_session(self.config, self.profile_name, timeout=45)
            payload = fetch_tiktok_studio_analytics(
                token,
                cutoff_epoch=int(time.time()) - 30 * 86400,
                timeout=90,
            ) or {}
            from tiktok_account_inspection import to_plain
            result["studio_observed_request_shapes"] = to_plain(payload.get("observed_request_shapes") or ())
            result["studio_observed_response_shapes"] = to_plain(payload.get("observed_response_shapes") or ())
            first_state = str(payload.get("state") or "")
            first_api_code = _as_non_negative_int(payload.get("apiCode"))
            if first_state == "NO_AUTH" or (first_state == "STUDIO_API_REJECTED" and first_api_code == 8):
                saved_cookies = parse_cookie(self.config.get("cookie_str", ""))
                if not saved_cookies:
                    payload = {"state": "LOGIN_REQUIRED", "apiCode": first_api_code}
                else:
                    try:
                        import_cookies(token, saved_cookies, timeout=30)
                        result["auth_source"] = "COOKIE_FALLBACK"
                        payload = fetch_tiktok_studio_analytics(
                            token,
                            cutoff_epoch=int(time.time()) - 30 * 86400,
                            timeout=90,
                        ) or {}
                        result["studio_observed_request_shapes"] = to_plain(payload.get("observed_request_shapes") or ())
                        result["studio_observed_response_shapes"] = to_plain(payload.get("observed_response_shapes") or ())
                    except SessionSetupError:
                        payload = {"state": "LOGIN_REQUIRED", "apiCode": first_api_code}
                    retry_state = str(payload.get("state") or "")
                    retry_api_code = _as_non_negative_int(payload.get("apiCode"))
                    if retry_state == "NO_AUTH" or (retry_state == "STUDIO_API_REJECTED" and retry_api_code == 8):
                        payload = {"state": "LOGIN_REQUIRED", "apiCode": retry_api_code}
            else:
                result["auth_source"] = "PROFILE_SESSION"
        except ProfileBusyError:
            result.update(primary_state="PROFILE_IN_USE", error_code="PROFILE_IN_USE", error_message="Profile is already active; analytics did not open a second browser.")
            return False
        except SessionSetupError as exc:
            result.update(primary_state="STUDIO_TIMEOUT", error_code="STUDIO_SESSION_ERROR", error_message=str(exc))
            return False
        except Exception as exc:
            result.update(primary_state="NETWORK_ERROR", error_code="STUDIO_BROWSER_ERROR", error_message=type(exc).__name__)
            return False
        finally:
            if token is not None:
                close_session(token.handle, timeout=10)

        state = str(payload.get("state") or "STUDIO_SCHEMA_CHANGED")
        if state != "SUCCESS":
            api_code = _as_non_negative_int(payload.get("apiCode"))
            error_code = f"{state}_API_{api_code}" if api_code is not None else state
            message = "Session Studio hết hạn; mở Chrome để đăng nhập lại." if state == "LOGIN_REQUIRED" else "Creator Studio did not return a complete content list."
            result.update(primary_state=state, error_code=error_code, error_message=message)
            return False
        totals = payload.get("totals") if isinstance(payload.get("totals"), dict) else {}
        views = _as_non_negative_int(totals.get("views"))
        likes = _as_non_negative_int(totals.get("likes"))
        comments = _as_non_negative_int(totals.get("comments"))
        shares = _as_non_negative_int(totals.get("shares"))
        videos = _as_non_negative_int(totals.get("videos"))
        if None in (views, likes, comments, shares, videos):
            result.update(primary_state="STUDIO_SCHEMA_CHANGED", error_code="STUDIO_SCHEMA_CHANGED", error_message="Creator Studio totals were invalid.")
            return False
        engagements = likes + comments + shares
        result.update(
            primary_state="SUCCESS",
            views_state="SUCCESS",
            views_30d=views,
            videos_30d=videos,
            likes_30d=likes,
            comments_30d=comments,
            shares_30d=shares,
            avg_daily_views=round(views / 30),
            engagement_rate=round(engagements * 100 / views, 2) if views else 0.0,
            days_count=30,
            calculation_source="BROWSER_STUDIO_ITEM_LIST",
            auth_source=result.get("auth_source") or "PROFILE_SESSION",
            source={"followers": "public_profile", "views": "browser_studio_item_list"},
            error_code="",
            error_message="",
        )
        return True

    def _fetch_public_profile(self, result: Dict[str, Any]) -> str:
        unique_id = str(self.config.get("tiktok_account") or self.config.get("tiktok_id") or "").lstrip("@").strip()
        if not unique_id:
            result["follower_state"] = "NOT_AVAILABLE"
            return ""
        try:
            response = self.session.get(f"https://www.tiktok.com/@{unique_id}", headers=self._headers("https://www.tiktok.com/"), timeout=min(self.timeout, 8.0))
        except Exception:
            result["follower_state"] = "NETWORK_ERROR"
            return ""
        if response.status_code in (401, 403):
            result["follower_state"] = "NO_AUTH"
            return ""
        if response.status_code == 429:
            result["follower_state"] = "RATE_LIMITED"
            return ""
        if response.status_code != 200 or not response.text:
            result["follower_state"] = "NETWORK_ERROR"
            return ""
        follower = re.search(r'"followerCount":(\d+)', response.text)
        if follower:
            result.update(follower_state="SUCCESS", follower_count=int(follower.group(1)))
        else:
            result["follower_state"] = "ENDPOINT_CHANGED"
        sec_uid = re.search(r'"secUid":"([^"\\]+)', response.text)
        return sec_uid.group(1) if sec_uid else ""

    @staticmethod
    def _items(raw: Any) -> Optional[Iterable[Dict[str, Any]]]:
        if not isinstance(raw, dict):
            return None
        data = raw.get("data", raw)
        if not isinstance(data, dict):
            return None
        for name in ("itemList", "item_list"):
            value = data.get(name)
            if isinstance(value, list):
                return value
        return None

    def _fetch_fallback(self, sec_uid: str, result: Dict[str, Any]) -> None:
        if not sec_uid:
            result.update(fallback_state="NOT_AVAILABLE", error_code="SEC_UID_MISSING", error_message="Public profile did not expose secUid for the fallback request.")
            return
        cutoff_epoch = int(time.time()) - 30 * 86400
        cursor = 0
        seen_cursors = {cursor}
        totals = {"views": 0, "likes": 0, "comments": 0, "shares": 0, "videos": 0}
        for _page in range(ITEM_LIST_MAX_PAGES):
            params = {"secUid": sec_uid, "count": ITEM_LIST_PAGE_SIZE, "cursor": cursor, "aid": "1988", "app_name": "tiktok_web"}
            try:
                response = self.session.get(PUBLIC_ITEM_LIST_URL, params=params, headers=self._headers("https://www.tiktok.com/"), timeout=self.timeout)
            except Exception as exc:
                result.update(fallback_state="NETWORK_ERROR", error_code="FALLBACK_REQUEST_FAILED", error_message=str(exc))
                break
            if self._response_error(response, "FALLBACK", result):
                result["fallback_state"] = "NO_AUTH" if response.status_code in (401, 403) else "RATE_LIMITED" if response.status_code == 429 else "API_REJECTED"
                break
            try:
                raw = response.json()
            except ValueError:
                result.update(fallback_state="ENDPOINT_CHANGED", error_code="FALLBACK_INVALID_JSON", error_message="Public Item List did not return JSON.")
                break
            if isinstance(raw, dict) and raw.get("code", raw.get("status_code", 0)) != 0:
                code = raw.get("code", raw.get("status_code"))
                result.update(fallback_state="API_REJECTED", error_code=f"FALLBACK_API_{code}", error_message=str(raw.get("msg") or raw.get("status_msg") or "Public Item List rejected the request."))
                break
            items = self._items(raw)
            if items is None:
                result.update(fallback_state="ENDPOINT_CHANGED", error_code="FALLBACK_ITEM_LIST_MISSING", error_message="Public Item List did not contain itemList.")
                break
            if not items:
                result["fallback_state"] = "SUCCESS"
                break
            reached_cutoff = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                create_time = _value(item, "createTime", "create_time")
                if create_time is None:
                    result.update(fallback_state="ENDPOINT_CHANGED", error_code="FALLBACK_CREATE_TIME_MISSING", error_message="Video item did not contain createTime.")
                    reached_cutoff = True
                    break
                if create_time < cutoff_epoch:
                    reached_cutoff = True
                    break
                stats = item.get("stats", item.get("statistics", {}))
                stats = stats if isinstance(stats, dict) else {}
                totals["views"] += _value(stats, "playCount", "play_count") or 0
                totals["likes"] += _value(stats, "diggCount", "digg_count") or 0
                totals["comments"] += _value(stats, "commentCount", "comment_count") or 0
                totals["shares"] += _value(stats, "shareCount", "share_count") or 0
                totals["videos"] += 1
            if result["fallback_state"] == "ENDPOINT_CHANGED":
                break
            data = raw.get("data", raw) if isinstance(raw, dict) else {}
            if reached_cutoff or not isinstance(data, dict) or not data.get("has_more", data.get("hasMore", False)):
                result["fallback_state"] = "SUCCESS"
                break
            next_cursor = _value(data, "cursor")
            if next_cursor is None or next_cursor in seen_cursors:
                result.update(fallback_state="ENDPOINT_CHANGED", error_code="FALLBACK_CURSOR_INVALID", error_message="Public Item List cursor was missing or repeated.")
                break
            cursor = next_cursor
            seen_cursors.add(cursor)
        else:
            result.update(fallback_state="PARTIAL", error_code="FALLBACK_MAX_PAGES", error_message="Public Item List reached the safety page limit.")

        if result["fallback_state"] == "SUCCESS":
            engagements = totals["likes"] + totals["comments"] + totals["shares"]
            result.update(
                views_state="SUCCESS",
                views_30d=totals["views"],
                likes_30d=totals["likes"],
                comments_30d=totals["comments"],
                shares_30d=totals["shares"],
                videos_30d=totals["videos"],
                avg_daily_views=round(totals["views"] / 30),
                engagement_rate=round(engagements * 100 / totals["views"], 2) if totals["views"] else 0.0,
                days_count=30,
                calculation_source="FALLBACK_ITEM_LIST",
                source={"followers": "public_profile", "views": "public_item_list"},
                error_code="",
                error_message="",
            )
        elif totals["videos"]:
            result.update(views_state="PARTIAL", views_30d=totals["views"], likes_30d=totals["likes"], comments_30d=totals["comments"], shares_30d=totals["shares"], videos_30d=totals["videos"])

    def fetch_analytics(self) -> Dict[str, Any]:
        result = self._base_result()
        primary_success = self._fetch_primary(result)
        if result.get("follower_state") != "SUCCESS":
            sec_uid = self._fetch_public_profile(result)
        else:
            sec_uid = None
        if not primary_success:
            # TikTok's public item-list endpoint currently returns HTML, not a
            # supported JSON contract. Do not turn that into a fake zero value.
            result["fallback_state"] = "DISABLED"
        if result["views_state"] == "SUCCESS" and result["follower_state"] == "SUCCESS":
            result["state"] = "SUCCESS"
        elif result["views_state"] == "SUCCESS" or result["follower_state"] == "SUCCESS":
            result["state"] = "PARTIAL"
        else:
            result["state"] = result["views_state"] if result["views_state"] != "NOT_AVAILABLE" else result["follower_state"]
        return result


def fetch_profile_analytics(profile_name: str, profile_config: Dict[str, Any]) -> Dict[str, Any]:
    return TikTokAnalyticsClient(profile_name, profile_config).fetch_analytics()
