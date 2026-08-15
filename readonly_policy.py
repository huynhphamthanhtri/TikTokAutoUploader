"""Fail-closed policy for authenticated TikTok read-only requests."""

from dataclasses import dataclass
from typing import Mapping, Tuple
from urllib.parse import parse_qsl, urlsplit


SENSITIVE_QUERY_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "csrf",
        "device_id",
        "mstoken",
        "sessionid",
        "sid",
        "signature",
        "token",
        "x-bogus",
    }
)


@dataclass(frozen=True)
class EndpointPolicy:
    endpoint_id: str
    hosts: Tuple[str, ...]
    path: str
    methods: Tuple[str, ...] = ("GET",)
    allowed_query_keys: Tuple[str, ...] = ()
    allowed_body_keys: Tuple[str, ...] = ()
    content_type: str = ""
    max_response_bytes: int = 512 * 1024
    enabled: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""


def evaluate_request(spec: EndpointPolicy, url: str, method: str, body=None) -> PolicyDecision:
    if not spec.enabled:
        return PolicyDecision(False, "endpoint_disabled")
    parsed = urlsplit(str(url or ""))
    if parsed.scheme.lower() != "https":
        return PolicyDecision(False, "https_required")
    if parsed.username or parsed.password:
        return PolicyDecision(False, "userinfo_not_allowed")
    if parsed.port not in (None, 443):
        return PolicyDecision(False, "port_not_allowed")
    if parsed.fragment:
        return PolicyDecision(False, "fragment_not_allowed")
    if (parsed.hostname or "").lower() not in {host.lower() for host in spec.hosts}:
        return PolicyDecision(False, "host_not_allowed")
    if parsed.path != spec.path:
        return PolicyDecision(False, "path_not_allowed")
    verb = str(method or "").upper()
    if verb not in {item.upper() for item in spec.methods}:
        return PolicyDecision(False, "method_not_allowed")
    if body not in (None, b"", ""):
        if not isinstance(body, Mapping):
            return PolicyDecision(False, "invalid_request_body")
        allowed_body = {item.lower() for item in spec.allowed_body_keys}
        if not allowed_body:
            return PolicyDecision(False, "request_body_not_allowed")
        for key in body:
            lowered = str(key).lower()
            if lowered in SENSITIVE_QUERY_KEYS:
                return PolicyDecision(False, "sensitive_body_key")
            if lowered not in allowed_body:
                return PolicyDecision(False, "body_key_not_allowed")
    allowed_queries = {item.lower() for item in spec.allowed_query_keys}
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in SENSITIVE_QUERY_KEYS:
            return PolicyDecision(False, "sensitive_query")
        if lowered not in allowed_queries:
            return PolicyDecision(False, "query_not_allowed")
    return PolicyDecision(True)


def redacted_audit_record(spec: EndpointPolicy, url: str, method: str, status: int = 0) -> Mapping[str, object]:
    parsed = urlsplit(str(url or ""))
    return {
        "endpoint_id": spec.endpoint_id,
        "method": str(method or "").upper(),
        "host": (parsed.hostname or "").lower(),
        "path": parsed.path,
        "status": int(status or 0),
    }
