"""Pure account-inspection model for TikTok profiles. No Tkinter/browser deps.

Defines result states, data groups (identity / analytics / monetization /
programs / payout), safe redaction helpers and a classification summary so
worker threads compute facts and the UI only renders them.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Mapping, Optional, Tuple

import re

from tiktok_capability_models import AccountCapabilities
from tiktok_schema_adapters import (
    adapt_balance,
    adapt_creative_rewards,
    adapt_dashboard,
    adapt_kyc,
    adapt_payment,
    adapt_payout,
    adapt_traffic,
    adapt_violations,
)


class InspectionState(str, Enum):
    PENDING = "PENDING"
    CHECKING = "CHECKING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    CHECKPOINT = "CHECKPOINT"
    RATE_LIMITED = "RATE_LIMITED"
    ENDPOINT_CHANGED = "ENDPOINT_CHANGED"
    UNAVAILABLE = "UNAVAILABLE"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


class ProgramStatus(str, Enum):
    ELIGIBLE_NOT_JOINED = "ELIGIBLE_NOT_JOINED"
    ENROLLED = "ENROLLED"
    LINKED = "LINKED"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    PENDING_REVIEW = "PENDING_REVIEW"
    SUSPENDED = "SUSPENDED"
    UNKNOWN = "UNKNOWN"


class PayoutStatus(str, Enum):
    PAYOUT_READY = "PAYOUT_READY"
    PAYOUT_NOT_LINKED = "PAYOUT_NOT_LINKED"
    PAYOUT_PENDING_VERIFICATION = "PAYOUT_PENDING_VERIFICATION"
    PAYOUT_RESTRICTED = "PAYOUT_RESTRICTED"
    PAYOUT_UNKNOWN = "PAYOUT_UNKNOWN"


class SourceGroup(str, Enum):
    IDENTITY = "identity"
    ANALYTICS = "analytics"
    MONETIZATION = "monetization"
    PROGRAMS = "programs"
    PAYOUT = "payout"


@dataclass(frozen=True)
class EndpointRecord:
    """Redacted source record for one observed endpoint."""

    path: str = ""
    status: int = 0
    content_type: str = ""
    group: str = SourceGroup.IDENTITY.value
    payload_keys: tuple = ()
    safe_get: bool = True
    error: str = ""


@dataclass(frozen=True)
class IdentityInfo:
    numeric_user_id: str = ""
    unique_id: str = ""
    nickname: str = ""
    region: str = ""
    verified: Optional[bool] = None
    account_status: str = ""
    followers: Optional[int] = None
    following: Optional[int] = None
    likes: Optional[int] = None
    video_count: Optional[int] = None


@dataclass(frozen=True)
class AnalyticsInfo:
    total_views: Optional[int] = None
    views_30d: Optional[int] = None
    followers_30d: Optional[int] = None
    profile_views_30d: Optional[int] = None
    likes_30d: Optional[int] = None
    videos_30d: Optional[int] = None
    date_range_start: str = ""
    date_range_end: str = ""
    timezone: str = ""
    partial: bool = False


@dataclass(frozen=True)
class MonetizationInfo:
    balance_amount: str = ""
    available_amount: str = ""
    pending_amount: str = ""
    currency: str = ""
    earnings_30d: str = ""
    earnings_current_month: str = ""
    earnings_lifetime: str = ""
    last_updated_at: str = ""


@dataclass(frozen=True)
class ProgramInfo:
    program_id: str = ""
    name: str = ""
    status: ProgramStatus = ProgramStatus.UNKNOWN
    eligible: bool = False
    enrolled: bool = False
    linked: bool = False
    reason_code: str = ""


@dataclass(frozen=True)
class PayoutEntry:
    date: str = ""
    amount: str = ""
    currency: str = ""
    status: str = ""
    masked_transaction_id: str = ""


@dataclass(frozen=True)
class PayoutInfo:
    payout_linked: Optional[bool] = None
    provider: str = ""
    masked_identifier: str = ""
    country: str = ""
    verification_status: str = ""
    payout_status: str = ""
    last_payout_at: str = ""
    history: tuple = ()


@dataclass(frozen=True)
class AccountInspectionResult:
    profile_name: str = ""
    state: InspectionState = InspectionState.PENDING
    checked_at: str = ""
    identity: IdentityInfo = field(default_factory=IdentityInfo)
    analytics: AnalyticsInfo = field(default_factory=AnalyticsInfo)
    monetization: MonetizationInfo = field(default_factory=MonetizationInfo)
    programs: tuple = ()
    payout: PayoutInfo = field(default_factory=PayoutInfo)
    capabilities: AccountCapabilities = field(default_factory=AccountCapabilities)
    sources: tuple = ()
    warnings: tuple = ()
    classification: str = ""
    detail: str = ""

    def display_state(self):
        return {
            InspectionState.PENDING: "Chờ",
            InspectionState.CHECKING: "Đang kiểm tra",
            InspectionState.SUCCESS: "Live",
            InspectionState.PARTIAL: "Partial",
            InspectionState.LOGIN_REQUIRED: "Cần đăng nhập lại",
            InspectionState.CHECKPOINT: "Checkpoint",
            InspectionState.RATE_LIMITED: "Bị giới hạn",
            InspectionState.ENDPOINT_CHANGED: "Endpoint đã thay đổi",
            InspectionState.UNAVAILABLE: "Không khả dụng",
            InspectionState.CANCELLED: "Đã dừng",
            InspectionState.ERROR: "Lỗi",
        }.get(self.state, str(self.state))


def _is_zero_or_none(value):
    if value is None:
        return True
    try:
        return float(value) == 0
    except (TypeError, ValueError):
        return False


# --- Redaction helpers -----------------------------------------------------

_EMAIL_MASK = re.compile(r"([^@\s]{1,2})[^@\s]*@([^@\s]{1,3})[^@\s]*")


def mask_email(value):
    """Mask an email: first 2 chars of local + first 3 chars of domain."""
    text = str(value or "").strip()
    if not text or "@" not in text:
        return ""

    def _repl(match):
        return "{}***@{}***".format(match.group(1), match.group(2))

    return _EMAIL_MASK.sub(_repl, text)


def mask_identifier(value, keep=4):
    """Keep at most ``keep`` trailing characters of an identifier."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]


_REDACTED_QUERY_KEYS = (
    "cookie",
    "token",
    "signature",
    "csrf",
    "sessionid",
    "sid",
    "device_id",
    "msToken",
    "X-Bogus",
)


def redact_query(url):
    """Strip query string when it may carry sensitive params.

    Returns (safe_url, dropped) where dropped lists redacted param names.
    """
    text = str(url or "")
    if "?" not in text:
        return text, ()
    base, query = text.split("?", 1)
    dropped = []
    parts = []
    for chunk in query.split("&"):
        name = chunk.split("=", 1)[0].strip()
        if name in _REDACTED_QUERY_KEYS or name.lower() in _REDACTED_QUERY_KEYS:
            dropped.append(name)
            continue
        parts.append(chunk)
    if parts:
        return base + "?" + "&".join(parts), tuple(dropped)
    return base, tuple(dropped)


def redact_payload_identifier(value, kind="identifier"):
    if not value:
        return ""
    if kind == "email":
        return mask_email(value)
    return mask_identifier(value)


# --- Classification --------------------------------------------------------

def classify_account(result: AccountInspectionResult) -> str:
    """Stable short summary, e.g. 'Live | Analytics OK | Monetized | Payout Ready'."""
    state = result.state
    if state in (
        InspectionState.LOGIN_REQUIRED,
        InspectionState.CHECKPOINT,
        InspectionState.RATE_LIMITED,
        InspectionState.ENDPOINT_CHANGED,
        InspectionState.UNAVAILABLE,
        InspectionState.ERROR,
        InspectionState.CANCELLED,
    ):
        return result.display_state()

    parts = []

    identity = result.identity
    if identity.unique_id or identity.numeric_user_id:
        parts.append("Identity OK")
    else:
        parts.append("Identity thiếu")

    analytics = result.analytics
    if analytics.partial:
        parts.append("Analytics Partial")
    elif analytics.views_30d is not None or analytics.total_views is not None:
        parts.append("Analytics OK")
    else:
        parts.append("Analytics N/A")

    monetization = result.monetization
    if monetization.currency or monetization.balance_amount:
        parts.append("Monetized")
    elif result.programs:
        parts.append("Kiếm tiền: có chương trình")
    else:
        parts.append("Kiếm tiền N/A")

    payout = result.payout
    if payout.payout_linked is True:
        parts.append("Payout Ready")
    elif payout.payout_linked is False:
        parts.append("Payout chưa liên kết")
    elif payout.payout_status:
        parts.append("Payout {}".format(payout.payout_status))
    else:
        parts.append("Payout N/A")

    return " | ".join(parts)


def build_inspection_summary(results):
    counts = {
        "total": len(results),
        "success": 0,
        "partial": 0,
        "login_required": 0,
        "checkpoint": 0,
        "rate_limited": 0,
        "endpoint_changed": 0,
        "unavailable": 0,
        "cancelled": 0,
        "error": 0,
        "pending": 0,
        "checking": 0,
    }
    for result in results:
        state = result.state
        key = {
            InspectionState.SUCCESS: "success",
            InspectionState.PARTIAL: "partial",
            InspectionState.LOGIN_REQUIRED: "login_required",
            InspectionState.CHECKPOINT: "checkpoint",
            InspectionState.RATE_LIMITED: "rate_limited",
            InspectionState.ENDPOINT_CHANGED: "endpoint_changed",
            InspectionState.UNAVAILABLE: "unavailable",
            InspectionState.CANCELLED: "cancelled",
            InspectionState.ERROR: "error",
            InspectionState.CHECKING: "checking",
        }.get(state, "pending")
        counts[key] += 1
    return counts


def analytics_total_views(analytics: AnalyticsInfo) -> Optional[int]:
    if analytics.total_views is not None and not _is_zero_or_none(analytics.total_views):
        return analytics.total_views
    if analytics.views_30d is not None and not _is_zero_or_none(analytics.views_30d):
        return analytics.views_30d
    return None


# --- Payload parsing (browser payloads arrive as mappingproxy) ------------

def to_plain(value):
    """Deep-convert browser mappingproxy/tuples into plain dicts/lists."""
    if isinstance(value, Mapping):
        return {str(key): to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


def _get(payload, path, default=None):
    """Fetch a dotted path from a Mapping, e.g. 'a.b.c' or 'items[0].x'."""
    node = payload
    for part in str(path or "").split("."):
        if not part:
            return default
        if "[" in part:
            name, rest = part.split("[", 1)
            if name and isinstance(node, Mapping):
                node = node.get(name)
            index = int(rest.strip("[]"))
            if isinstance(node, (list, tuple)) and 0 <= index < len(node):
                node = node[index]
            elif name:
                node = None
            continue
        if not isinstance(node, Mapping):
            return default
        node = node.get(part)
    return node


def parse_identity_from_web_user(payload):
    """Extract IdentityInfo from /tiktokstudio/api/web/user payload."""
    if not isinstance(payload, Mapping):
        return IdentityInfo()
    base = payload.get("userBaseInfo") or {}
    profile = base.get("UserProfile") or {}
    user_base = profile.get("UserBase") or {}
    user_status = profile.get("UserStatus") or {}
    region_node = user_base.get("Region") or {}
    cert = user_base.get("CertInfo") or {}

    numeric_id = str(payload.get("userId") or user_base.get("Id") or "").strip()
    region = ""
    try:
        region = str(region_node.get("Region") or "").strip()
    except Exception:
        pass
    verified = None
    try:
        has_cert = cert.get("HasCert")
        if isinstance(has_cert, dict):
            verified = bool(has_cert.get("value") or has_cert.get("Value"))
        elif isinstance(has_cert, bool):
            verified = has_cert
    except Exception:
        verified = None
    return IdentityInfo(
        numeric_user_id=numeric_id,
        unique_id=str(user_base.get("UniqId") or "").strip(),
        nickname=str(user_base.get("NickName") or "").strip(),
        region=region,
        verified=verified,
        account_status=str(user_status.get("UserStatus") or ""),
    )


def parse_identity_from_app_context(payload):
    """Extract IdentityInfo from /node-webapp/api/common-app-context payload."""
    if not isinstance(payload, Mapping):
        return IdentityInfo()
    user = payload.get("user") or {}
    return IdentityInfo(
        numeric_user_id=str(user.get("uid") or "").strip(),
        unique_id=str(user.get("uniqueId") or "").strip(),
        nickname=str(user.get("nickName") or "").strip(),
        region=str(payload.get("region") or "").strip(),
    )


def merge_identity(*candidates):
    """Merge identity candidates, later values filling earlier empties."""
    merged = IdentityInfo()
    for candidate in candidates:
        if not isinstance(candidate, IdentityInfo):
            continue
        merged = IdentityInfo(
            numeric_user_id=merged.numeric_user_id or candidate.numeric_user_id,
            unique_id=merged.unique_id or candidate.unique_id,
            nickname=merged.nickname or candidate.nickname,
            region=merged.region or candidate.region,
            verified=merged.verified if merged.verified is not None else candidate.verified,
            account_status=merged.account_status or candidate.account_status,
            followers=merged.followers if merged.followers is not None else candidate.followers,
            following=merged.following if merged.following is not None else candidate.following,
            likes=merged.likes if merged.likes is not None else candidate.likes,
            video_count=(
                merged.video_count if merged.video_count is not None else candidate.video_count
            ),
        )
    return merged


def parse_monetization_from_reward(payload):
    """Extract MonetizationInfo from reward payloads.

    Returns (info, ok) where ok is False when the payload only carries an
    error/status code (e.g. TikTok's 'Invalid parameters')."""
    if not isinstance(payload, Mapping):
        return MonetizationInfo(), False
    status_code = payload.get("status_code")
    status_msg = str(payload.get("status_msg") or "").strip()
    if isinstance(status_code, int) and status_code != 0:
        return MonetizationInfo(), False
    if not status_msg and not payload.get("log_pb"):
        return MonetizationInfo(), False
    info = MonetizationInfo(
        currency=str(payload.get("currency") or "").strip(),
        balance_amount=_money_str(payload.get("balance") or payload.get("available_balance")),
        available_amount=_money_str(payload.get("available") or payload.get("available_balance")),
        pending_amount=_money_str(payload.get("pending") or payload.get("pending_balance")),
    )
    has_data = bool(info.currency or info.balance_amount or info.available_amount)
    return info, has_data


def _money_str(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return "{:.2f}".format(float(value))
    return str(value).strip()


def build_inspection_result(profile_name, fetched, *, checked_at="", sources=()):
    """Build an AccountInspectionResult from a {path: payload} fetch result.

    ``fetched`` values may be mappingproxy. Sources whose payload only carry a
    TikTok error code are excluded from the identity merge and reported via
    warnings. ``sources`` may carry EndpointRecord diagnostics (e.g. from a
    discovery pass) used to classify identity/analytics/monetization states
    without guessing missing data.
    """
    fetched = fetched or {}
    warnings = list()
    endpoint_records = list(sources or ())

    web_user = to_plain(fetched.get("/tiktokstudio/api/web/user"))
    app_context = to_plain(fetched.get("/node-webapp/api/common-app-context"))
    reward = to_plain(fetched.get("/tiktok/v1/creator/m10n_center/reward_analytics"))

    identity = merge_identity(
        parse_identity_from_web_user(web_user),
        parse_identity_from_app_context(app_context),
    )

    monetization, reward_ok = parse_monetization_from_reward(reward)
    if not reward_ok:
        warnings.append("reward_analytics trả lỗi/không có dữ liệu; bỏ qua số liệu kiếm tiền")

    capability_specs = (
        ("/tiktok/v1/creator/incentives/analytics/dashboard_overview", adapt_dashboard),
        ("/webcast/api/money/creator_earnings/v1/payout_summary", adapt_balance),
        ("/tiktok/v1/creator/incentives/profile", adapt_creative_rewards),
        ("/webcast/api/money/one-wallet/v1/business/rewards", adapt_payout),
        ("/aweme/v2/data/insight/", adapt_traffic),
        ("/webcast/api/compliance/kyc/v1/info/detail", adapt_kyc),
        ("/webcast/api/money/payout_onboarding/v2/onboarding_detail", adapt_payment),
        ("/tiktok/v1/creator/incentives/video/violations", adapt_violations),
    )
    capability_results = []
    for path, adapter in capability_specs:
        if path not in fetched:
            continue
        try:
            capability_results.append(adapter(to_plain(fetched.get(path)), checked_at=checked_at))
        except Exception as error:
            warnings.append("Không parse được capability {} ({})".format(path, type(error).__name__))

    identity_ok = bool(identity.numeric_user_id or identity.unique_id)
    endpoint_changed = False
    identity_unavailable = False
    for record in endpoint_records:
        if record.group == SourceGroup.IDENTITY.value:
            if not record.safe_get:
                if record.status in (401, 403):
                    warnings.append("Endpoint định danh yêu cầu đăng nhập lại")
                elif record.status == 429:
                    warnings.append("Endpoint định danh bị giới hạn request")
                elif record.status:
                    identity_unavailable = True
                else:
                    endpoint_changed = True
            elif not record.payload_keys:
                endpoint_changed = True

    state = InspectionState.SUCCESS
    if endpoint_changed:
        state = InspectionState.ENDPOINT_CHANGED
        warnings.append("Schema endpoint định danh thay đổi; chưa parse được identity")
    elif identity_unavailable:
        state = InspectionState.UNAVAILABLE
        warnings.append("Endpoint định danh không khả dụng")
    elif not identity_ok:
        state = InspectionState.LOGIN_REQUIRED
        warnings.append("Không lấy được định danh tài khoản")
    elif not monetization.currency and not monetization.balance_amount:
        state = InspectionState.PARTIAL
        warnings.append("Chưa có số liệu kiếm tiền/balance")

    return AccountInspectionResult(
        profile_name=profile_name,
        state=state,
        checked_at=checked_at,
        identity=identity,
        monetization=monetization,
        capabilities=AccountCapabilities(tuple(capability_results)),
        sources=tuple(endpoint_records),
        warnings=tuple(warnings),
    )
