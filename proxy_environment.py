"""Compare browser-environment continuity across proxy changes.

The browser profile and device fingerprint are only considered stable when
the proxy exit environment keeps the same country, ASN and timezone. An IP
change alone (common for residential/mobile sticky proxies) is safe. A change
in country, ASN or timezone breaks device continuity.

This module is pure and testable: no UI, no filesystem.
"""


# Fields that must stay identical to keep the environment "compatible".
COMPATIBILITY_FIELDS = ("geo_country_code", "geo_asn", "timezone")

# Decisions returned by compare_proxy_environment().
SAME = "same"
COMPATIBLE_CHANGE = "compatible_change"
RISKY_CHANGE = "risky_change"
UNKNOWN = "unknown"

DECISIONS = (SAME, COMPATIBLE_CHANGE, RISKY_CHANGE, UNKNOWN)

LABELS = {
    "geo_country_code": "Country",
    "geo_country": "Quốc gia",
    "geo_asn": "ASN",
    "geo_isp": "ISP",
    "timezone": "Timezone",
    "geo_region": "Region",
    "geo_city": "Thành phố",
}


def proxy_environment_snapshot(fingerprint):
    """Extract the continuity-relevant environment snapshot from a fingerprint."""
    fingerprint = fingerprint or {}
    snapshot = {}
    for key in ("geo_exit_ip", "geo_country_code", "geo_country", "geo_region",
                "geo_city", "geo_asn", "geo_isp", "timezone"):
        snapshot[key] = str(fingerprint.get(key) or "").strip()
    return snapshot


def _clean(value):
    return str(value or "").strip()


def compare_proxy_environment(previous, current):
    """Classify a proxy environment change for continuity decisions.

    ``previous`` and ``current`` may be fingerprint dicts or pre-built
    snapshots (see :func:`proxy_environment_snapshot`).

    Returns a dict::

        {
            "decision": "same" | "compatible_change" | "risky_change" | "unknown",
            "changed_fields": [...],
            "warnings": [...],
            "previous": {...},
            "current": {...},
        }
    """
    previous = proxy_environment_snapshot(previous)
    current = proxy_environment_snapshot(current)

    changed = [
        key
        for key in COMPATIBILITY_FIELDS + ("geo_exit_ip",)
        if _clean(previous.get(key)) != _clean(current.get(key))
    ]

    exit_ip = _clean(current.get("geo_exit_ip"))
    if exit_ip and _clean(previous.get("geo_exit_ip")) == exit_ip:
        return {
            "decision": SAME,
            "changed_fields": [],
            "warnings": [],
            "previous": previous,
            "current": current,
        }

    missing = [
        key
        for key in COMPATIBILITY_FIELDS
        if not _clean(previous.get(key)) or not _clean(current.get(key))
    ]
    if missing:
        warnings = [
            "Thiếu dữ liệu môi trường proxy ({}) nên không thể đánh giá "
            "độ liên tục của browser profile.".format(", ".join(missing))
        ]
        return {
            "decision": UNKNOWN,
            "changed_fields": [key for key in changed if key not in COMPATIBILITY_FIELDS],
            "warnings": warnings,
            "previous": previous,
            "current": current,
        }

    drifted = [
        key
        for key in COMPATIBILITY_FIELDS
        if _clean(previous.get(key)) != _clean(current.get(key))
    ]
    if drifted:
        warnings = []
        for key in drifted:
            label = LABELS.get(key, key)
            warnings.append(
                "{} đã thay đổi: {} -> {}".format(
                    label, _clean(previous.get(key)), _clean(current.get(key))
                )
            )
        return {
            "decision": RISKY_CHANGE,
            "changed_fields": changed,
            "warnings": warnings,
            "previous": previous,
            "current": current,
        }

    warnings = [
        "IP proxy đã thay đổi ({} -> {}) nhưng vẫn cùng Country/ASN/Timezone; "
        "giữ browser profile và fingerprint hiện tại.".format(
            _clean(previous.get("geo_exit_ip")) or "trống",
            exit_ip or "trống",
        )
    ]
    return {
        "decision": COMPATIBLE_CHANGE,
        "changed_fields": changed,
        "warnings": warnings,
        "previous": previous,
        "current": current,
    }


def build_proxy_change_audit(classification, previous, current, warning, now=None):
    """Build a single audit record. Never contains proxy credentials."""
    from datetime import datetime, timezone

    if now is None:
        now = datetime.now(timezone.utc).isoformat()
    return {
        "timestamp": now,
        "classification": classification,
        "warning": str(warning or "")[:300],
        "old_exit_ip": _clean((previous or {}).get("geo_exit_ip")),
        "new_exit_ip": _clean((current or {}).get("geo_exit_ip")),
        "old_country": _clean((previous or {}).get("geo_country_code")),
        "new_country": _clean((current or {}).get("geo_country_code")),
        "old_asn": _clean((previous or {}).get("geo_asn")),
        "new_asn": _clean((current or {}).get("geo_asn")),
        "old_timezone": _clean((previous or {}).get("timezone")),
        "new_timezone": _clean((current or {}).get("timezone")),
    }


def append_proxy_environment_history(config, entry, limit=10):
    """Append an audit entry to ``config['proxy_environment_history']``."""
    history = config.get("proxy_environment_history")
    if not isinstance(history, list):
        history = []
    history.append(dict(entry))
    config["proxy_environment_history"] = history[-limit:]
    return list(config["proxy_environment_history"])


def apply_proxy_environment_warning(config, classification, previous, current, warning, now=None):
    """Record classification, warning and a bounded history entry on a config."""
    from datetime import datetime, timezone

    if now is None:
        now = datetime.now(timezone.utc).isoformat()
    config["proxy_change_classification"] = classification
    config["proxy_environment_warning"] = str(warning or "")[:300]
    config["proxy_environment_changed_at"] = now
    config["proxy_previous_exit_ip"] = _clean((previous or {}).get("geo_exit_ip"))
    entry = build_proxy_change_audit(classification, previous, current, warning, now=now)
    append_proxy_environment_history(config, entry)
    return dict(config)