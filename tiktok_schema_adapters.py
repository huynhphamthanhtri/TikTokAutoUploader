"""Pure schema adapters for normalized TikTok Insights payloads."""

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Mapping

from tiktok_capability_models import (
    AccountCapabilities,
    BalanceCapability,
    CapabilityResult,
    CapabilityState,
    CreativeRewardsCapability,
    CreativeRewardsRequirement,
    DashboardCapability,
    KycCapability,
    KycState,
    MoneyAmount,
    PaymentCapability,
    PaymentState,
    PayoutCapability,
    TrafficCapability,
    TrafficSource,
    ViolationsCapability,
)


def first_present(mapping, *keys):
    for key in keys:
        if isinstance(mapping, Mapping) and key in mapping:
            value = mapping[key]
            if value is not None and value != "":
                return value
    return None


def schema_hash(payload):
    def shape(value):
        if isinstance(value, Mapping):
            return {str(key): shape(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
        if isinstance(value, (list, tuple)):
            return [shape(value[0])] if value else []
        return type(value).__name__

    encoded = json.dumps(shape(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _minor(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
        except (InvalidOperation, ValueError, OverflowError):
            return None


def _amount(minor, currency="", formatted=""):
    parsed = _minor(minor)
    return None if parsed is None else MoneyAmount(parsed, str(currency or ""), 2, str(formatted or ""))


def adapt_dashboard(payload, checked_at=""):
    payload = payload if isinstance(payload, Mapping) else {}
    formatted = payload.get("formatted") if isinstance(payload.get("formatted"), Mapping) else {}
    currency = str(payload.get("currencySymbol") or payload.get("currency_symbol") or "")
    total = first_present(payload, "totalAmountCents", "total_amount", "totalAmount")
    revenue = first_present(payload, "estimatedRevenueCents", "estimated_revenue", "estimatedRevenue")
    rpm = first_present(payload, "rpmCents", "rpm")
    if total is None and rpm is None and first_present(payload, "qualifiedViews", "qualified_views") is None:
        return CapabilityResult("dashboard", CapabilityState.ENDPOINT_CHANGED, endpoint_id="dashboard_overview", checked_at=checked_at, schema_hash=schema_hash(payload))
    value = DashboardCapability(
        total_amount=_amount(total, currency, formatted.get("totalAmount")),
        estimated_revenue=_amount(revenue, currency, formatted.get("estimatedRevenue")),
        rpm=_amount(rpm, currency, formatted.get("rpm")),
        qualified_views=first_present(payload, "qualifiedViews", "qualified_views"),
    )
    return CapabilityResult("dashboard", CapabilityState.SUCCESS, value, "dashboard_overview", checked_at, schema_hash(payload))


def adapt_balance(payload, checked_at=""):
    payload = payload if isinstance(payload, Mapping) else {}
    if isinstance(payload.get("data"), Mapping):
        payload = payload["data"]
    currency = str(payload.get("currency_symbol") or payload.get("currency") or "")
    balance = first_present(payload, "balance")
    if balance is None:
        return CapabilityResult("balance", CapabilityState.ENDPOINT_CHANGED, endpoint_id="balance_summary", checked_at=checked_at, schema_hash=schema_hash(payload))
    value = BalanceCapability(
        balance=_amount(balance, currency),
        frozen_balance=_amount(first_present(payload, "frozen_balance", "frozenBalance"), currency),
        total_payable=_amount(first_present(payload, "total_payable", "totalPayable"), currency),
        payout_threshold=_amount(first_present(payload, "payout_threshold", "payoutThreshold"), currency),
        next_payout_at=str(first_present(payload, "next_payout_date", "nextPayoutDate") or ""),
    )
    return CapabilityResult("balance", CapabilityState.SUCCESS, value, "balance_summary", checked_at, schema_hash(payload))


def adapt_creative_rewards(payload, checked_at=""):
    payload = payload if isinstance(payload, Mapping) else {}
    profile = payload.get("profile") if isinstance(payload.get("profile"), Mapping) else {}
    checklist = payload.get("apply_check_list") or payload.get("applyCheckList") or ()
    requirements = tuple(
        CreativeRewardsRequirement(str(item.get("key") or ""), item.get("status"), str(item.get("desc") or ""))
        for item in checklist if isinstance(item, Mapping)
    )
    status = profile.get("profile_status") if profile else payload.get("profileStatus")
    enabled = payload.get("enabled")
    if status is None and enabled is None and not requirements:
        return CapabilityResult("creative_rewards", CapabilityState.ENDPOINT_CHANGED, endpoint_id="creative_rewards", checked_at=checked_at, schema_hash=schema_hash(payload))
    all_met = None if not requirements else all(item.status == 1 for item in requirements)
    value = CreativeRewardsCapability(enabled if isinstance(enabled, bool) else None, status, all_met, requirements)
    return CapabilityResult("creative_rewards", CapabilityState.SUCCESS, value, "creative_rewards", checked_at, schema_hash(payload))


def adapt_traffic(payload, checked_at=""):
    if isinstance(payload, Mapping) and isinstance(payload.get("data"), Mapping):
        payload = payload["data"]
    has_container = isinstance(payload, (list, tuple)) or (isinstance(payload, Mapping) and ("sources" in payload or "video_page_percent" in payload))
    if isinstance(payload, (list, tuple)):
        entries = payload
    elif isinstance(payload, Mapping):
        entries = first_present(payload, "sources", "video_page_percent") or ()
    else:
        entries = ()
    sources = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        name = str(first_present(item, "name", "source", "label", "page_name", "traffic_source") or "").strip()
        raw = first_present(item, "percentage", "percent", "value")
        try:
            percent = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
        if name:
            sources.append(TrafficSource(name, percent))
    if not sources and not has_container:
        return CapabilityResult("traffic", CapabilityState.ENDPOINT_CHANGED, TrafficCapability(), "traffic_source", checked_at, schema_hash(payload))
    if not sources:
        return CapabilityResult("traffic", CapabilityState.SUCCESS_EMPTY, TrafficCapability(), "traffic_source", checked_at, schema_hash(payload))
    total = sum((item.percentage for item in sources), Decimal("0"))
    warnings = () if Decimal("99") <= total <= Decimal("101") else ("Tổng tỷ lệ traffic nằm ngoài 99-101%",)
    return CapabilityResult("traffic", CapabilityState.PARTIAL if warnings else CapabilityState.SUCCESS, TrafficCapability(tuple(sources)), "traffic_source", checked_at, schema_hash(payload), warnings=warnings)


def adapt_kyc(payload, checked_at=""):
    payload = payload if isinstance(payload, Mapping) else {}
    status = payload.get("kyc_status") if isinstance(payload.get("kyc_status"), Mapping) else payload
    created = status.get("created") if isinstance(status, Mapping) else None
    cdd = status.get("cdd_status") if isinstance(status, Mapping) else None
    screen = status.get("screen_status") if isinstance(status, Mapping) else None
    resubmit_keys = ("fail_dynamic_poa", "id_doc_resubmit", "poa_doc_resubmit")
    has_resubmit = isinstance(status, Mapping) and any(key in status for key in resubmit_keys)
    resubmit = any(bool(status.get(key)) for key in resubmit_keys) if has_resubmit else None
    if created is None and cdd is None and screen is None and not has_resubmit:
        return CapabilityResult("kyc", CapabilityState.ENDPOINT_CHANGED, endpoint_id="kyc_status", checked_at=checked_at, schema_hash=schema_hash(payload))
    if created is None and cdd is None:
        state = KycState.UNKNOWN
    elif resubmit:
        state = KycState.ACTION_REQUIRED
    elif created and isinstance(cdd, int) and cdd >= 7:
        state = KycState.VERIFIED
    elif created:
        state = KycState.PENDING
    else:
        state = KycState.NOT_STARTED
    value = KycCapability(state, created if isinstance(created, bool) else None, cdd, screen, resubmit)
    return CapabilityResult("kyc", CapabilityState.SUCCESS, value, "kyc_status", checked_at, schema_hash(payload))


def adapt_payment(payload, checked_at=""):
    payload = payload if isinstance(payload, Mapping) else {}
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    recognized = {"confirmed", "masked_instrument_identity", "pi_bind_status", "kyc_status"}
    if not isinstance(data, Mapping) or not any(key in data for key in recognized):
        return CapabilityResult("payment", CapabilityState.ENDPOINT_CHANGED, endpoint_id="payment_method", checked_at=checked_at, schema_hash=schema_hash(payload))
    confirmed = data.get("confirmed")
    masked = str(data.get("masked_instrument_identity") or "").strip()
    bind = data.get("pi_bind_status")
    if confirmed is True:
        state = PaymentState.LINKED
    elif masked and confirmed is False:
        state = PaymentState.PENDING
    elif confirmed is False:
        state = PaymentState.NOT_LINKED
    else:
        state = PaymentState.UNKNOWN
    value = PaymentCapability(state, confirmed if isinstance(confirmed, bool) else None, bool(masked) if masked else None, bind, data.get("kyc_status"))
    return CapabilityResult("payment", CapabilityState.SUCCESS, value, "payment_method", checked_at, schema_hash(payload))


def adapt_payout(payload, checked_at=""):
    payload = payload if isinstance(payload, Mapping) else {}
    if isinstance(payload.get("data"), Mapping):
        payload = payload["data"]
    pending = payload.get("pending_earnings") or ()
    breakdown = payload.get("payout_breakdown") or ()
    summary = payload.get("summary")
    flexible = payload.get("is_flexible_payout_enabled")
    if summary is None and flexible is None and not pending and not breakdown:
        return CapabilityResult("payout", CapabilityState.ENDPOINT_CHANGED, endpoint_id="payout_rewards", checked_at=checked_at, schema_hash=schema_hash(payload))
    amount = None
    if isinstance(summary, Mapping):
        amount = _amount(first_present(summary, "currency_amount", "value"), str(summary.get("currency_symbol") or ""))
    value = PayoutCapability(amount, flexible if isinstance(flexible, bool) else None, len(pending), len(breakdown))
    state = CapabilityState.SUCCESS_EMPTY if not pending and not breakdown else CapabilityState.SUCCESS
    return CapabilityResult("payout", state, value, "payout_rewards", checked_at, schema_hash(payload))


def adapt_violations(payload, checked_at=""):
    payload = payload if isinstance(payload, Mapping) else {}
    if "video_info_list" not in payload:
        return CapabilityResult("violations", CapabilityState.ENDPOINT_CHANGED, endpoint_id="video_violations", checked_at=checked_at, schema_hash=schema_hash(payload))
    items = payload.get("video_info_list") or ()
    value = ViolationsCapability(len(items))
    state = CapabilityState.SUCCESS_EMPTY if not items else CapabilityState.SUCCESS
    return CapabilityResult("violations", state, value, "video_violations", checked_at, schema_hash(payload))


def build_capability_results(requests, transport_result, checked_at=""):
    adapters = {
        "dashboard": adapt_dashboard,
        "balance": adapt_balance,
        "creative_rewards": adapt_creative_rewards,
        "payout": adapt_payout,
        "traffic": adapt_traffic,
        "kyc": adapt_kyc,
        "payment": adapt_payment,
        "violations": adapt_violations,
    }
    rows = dict((transport_result or {}).get("results") or {})
    errors = {
        str(item.get("capability") or ""): item
        for item in ((transport_result or {}).get("errors") or ())
        if isinstance(item, Mapping)
    }
    results = []
    for request in requests or ():
        capability = request.capability
        row = rows.get(capability)
        if row is not None:
            adapter = adapters.get(capability)
            if adapter is None:
                results.append(CapabilityResult(capability, CapabilityState.UNAVAILABLE, endpoint_id=request.endpoint_id, checked_at=checked_at, warnings=("Chưa có adapter",)))
                continue
            try:
                adapted = adapter(row.get("payload"), checked_at=checked_at)
                if adapted.state == CapabilityState.ENDPOINT_CHANGED and row.get("payload_keys"):
                    adapted = CapabilityResult(
                        adapted.capability,
                        adapted.state,
                        adapted.value,
                        adapted.endpoint_id,
                        adapted.checked_at,
                        adapted.schema_hash,
                        adapted.adapter_version,
                        tuple(adapted.warnings) + ("keys=" + ",".join(row.get("payload_keys")[:30]),),
                    )
                results.append(adapted)
            except Exception as error:
                results.append(CapabilityResult(capability, CapabilityState.ERROR, endpoint_id=request.endpoint_id, checked_at=checked_at, warnings=(type(error).__name__,)))
            continue
        error = errors.get(capability, {})
        status = int(error.get("status") or 0)
        reason = str(error.get("reason") or "unavailable")
        if status in (401, 403):
            state = CapabilityState.LOGIN_REQUIRED
        elif status == 429:
            state = CapabilityState.RATE_LIMITED
        elif reason == "endpoint_disabled":
            state = CapabilityState.UNAVAILABLE
        elif reason in ("invalid_json", "response_too_large") or (status == 200 and reason):
            state = CapabilityState.ENDPOINT_CHANGED
        else:
            state = CapabilityState.UNAVAILABLE
        content_type = str(error.get("content_type") or "").split(";", 1)[0]
        warning = "{};status={};content_type={}".format(reason, status, content_type)
        results.append(CapabilityResult(capability, state, endpoint_id=request.endpoint_id, checked_at=checked_at, warnings=(warning,)))
    return AccountCapabilities(tuple(results))
