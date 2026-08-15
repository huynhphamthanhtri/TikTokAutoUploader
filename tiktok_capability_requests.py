"""Request builders for verified read-only TikTok capability contracts."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence
from urllib.parse import urlencode

from tiktok_endpoint_catalog import endpoint_policy


@dataclass(frozen=True)
class CapabilityRequest:
    capability: str
    endpoint_id: str
    method: str
    url: str
    body: Mapping[str, object] = None
    headers: Mapping[str, str] = None


# Capabilities whose request contract is verified live and safe to fetch.
# Others (dashboard, creative_rewards, traffic, video_rank) stay passive-only
# and are excluded from active collection until their contracts are proven.
ACTIVE_CAPABILITIES = (
    "balance",
    "payout",
    "kyc",
    "payment",
)


def _date_range(days=30):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(1, int(days)))
    return start.isoformat(), end.isoformat()


def build_capability_requests(days=30, capabilities: Sequence[str] = None):
    start, end = _date_range(days)
    selected = set(capabilities or ACTIVE_CAPABILITIES)

    def url(endpoint_id, query=None):
        spec = endpoint_policy(endpoint_id)
        suffix = "?" + urlencode(query) if query else ""
        return "https://{}{}{}".format(spec.hosts[0], spec.path, suffix)

    builders = {
        "balance": (
            lambda: CapabilityRequest(
                "balance", "balance_summary", "GET",
                url("balance_summary", {"locale": "en", "webcast_language": "en"}),
            )
        ),
        "payout": (
            lambda: CapabilityRequest(
                "payout", "payout_rewards", "GET",
                url("payout_rewards", {"business_line": "CREATOR_FUND"}),
            )
        ),
        "kyc": (
            lambda: CapabilityRequest(
                "kyc", "kyc_status", "POST", url("kyc_status"),
                body={
                    "aid": 1988,
                    "app_name": "tiktok_web",
                    "browser_name": "Mozilla",
                    "browser_platform": "Win32",
                    "browser_online": True,
                    "browser_language": "en-US",
                    "browser_version": "5.0",
                    "data_collection_enabled": True,
                    "device_platform": "web_pc",
                    "focus_state": True,
                    "from_page": "creator_center",
                    "history_len": 1,
                    "is_fullscreen": False,
                    "is_page_visible": True,
                    "screen_height": 1440,
                    "screen_width": 2560,
                    "user_is_login": True,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        ),
        "payment": (
            lambda: CapabilityRequest(
                "payment", "payment_method", "GET",
                url("payment_method", {"wallet_type": "MONTHLY_EARNING"}),
            )
        ),
        "dashboard": (
            lambda: CapabilityRequest(
                "dashboard", "dashboard_overview", "GET",
                url("dashboard_overview", {"start_date": start, "end_date": end}),
            )
        ),
        "creative_rewards": (
            lambda: CapabilityRequest(
                "creative_rewards", "creative_rewards", "GET", url("creative_rewards")
            )
        ),
        "traffic": (
            lambda: CapabilityRequest(
                "traffic", "traffic_source", "POST", url("traffic_source"),
                body={
                    "insigh_type": "vv_traffic_source",
                    "end_days": int(days),
                    "type_requests": ["vv_traffic_source"],
                },
                headers={
                    "Content-Type": "application/json",
                    "Referer": "https://www.tiktok.com/tiktokstudio/analytics/",
                },
            )
        ),
    }

    return tuple(
        builder() for name, builder in builders.items() if name in selected
    )