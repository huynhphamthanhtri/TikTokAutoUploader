"""Explicit catalog of TikTok Insights capabilities.

Only endpoints whose request contract is proven safe are enabled. Others are
kept as disabled metadata so adapters/UI can be developed without issuing
speculative requests.
"""

from readonly_policy import EndpointPolicy


ENDPOINTS = {
    "identity_web_user": EndpointPolicy(
        "identity_web_user", ("www.tiktok.com",), "/tiktokstudio/api/web/user", enabled=True
    ),
    "identity_app_context": EndpointPolicy(
        "identity_app_context", ("www.tiktok.com",), "/node-webapp/api/common-app-context", enabled=True
    ),
    "dashboard_overview": EndpointPolicy(
        "dashboard_overview", ("www.tiktok.com",),
        "/tiktok/v1/creator/incentives/analytics/dashboard_overview",
        allowed_query_keys=("start_date", "end_date"), enabled=False,
    ),
    "balance_summary": EndpointPolicy(
        "balance_summary", ("webcast.tiktok.com",),
        "/webcast/api/money/creator_earnings/v1/payout_summary",
        allowed_query_keys=("locale", "webcast_language"), enabled=True,
    ),
    "payout_rewards": EndpointPolicy(
        "payout_rewards", ("webcast.tiktok.com",),
        "/webcast/api/money/one-wallet/v1/business/rewards",
        allowed_query_keys=("business_line",), enabled=True,
    ),
    "payout_transaction": EndpointPolicy(
        "payout_transaction", ("webcast.tiktok.com",),
        "/webcast/api/money/one-wallet/v2/transactions/details",
        allowed_query_keys=("id", "bill_source"), enabled=True,
    ),
    "creative_rewards": EndpointPolicy(
        "creative_rewards", ("www.tiktok.com",), "/tiktok/v1/creator/incentives/profile", enabled=False
    ),
    "traffic_source": EndpointPolicy(
        "traffic_source", ("www.tiktok.com",), "/aweme/v2/data/insight/",
        methods=("POST",), allowed_body_keys=("insigh_type", "end_days", "type_requests"),
        content_type="application/json", enabled=False,
    ),
    "video_rank": EndpointPolicy(
        "video_rank", ("www.tiktok.com",),
        "/tiktok/v1/creator/incentives/analytics/dashboard_rank",
        allowed_query_keys=("start_date_epoch", "end_date_epoch", "page", "filter_type", "order_type", "surface_type", "currency_type", "app_installed"),
        enabled=False,
    ),
    "kyc_status": EndpointPolicy(
        "kyc_status", ("webcast.tiktok.com",), "/webcast/api/compliance/kyc/v1/info/detail",
        methods=("POST",), allowed_body_keys=("aid", "app_name", "browser_name", "browser_platform", "browser_online", "browser_language", "browser_version", "data_collection_enabled", "device_platform", "focus_state", "from_page", "history_len", "is_fullscreen", "is_page_visible", "screen_height", "screen_width", "user_is_login"),
        content_type="application/x-www-form-urlencoded", enabled=True,
    ),
    "payment_method": EndpointPolicy(
        "payment_method",
        ("webcast.tiktok.com", "aggr16-normal.tiktokv.us", "webcast16-normal-no1a.tiktokv.eu"),
        "/webcast/api/money/payout_onboarding/v2/onboarding_detail",
        allowed_query_keys=("wallet_type",), enabled=True,
    ),
}


def endpoint_policy(endpoint_id):
    return ENDPOINTS.get(str(endpoint_id or ""))
