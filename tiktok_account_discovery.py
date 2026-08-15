"""Read-only network discovery for TikTok account inspection.

Pure guard/allowlist helpers live here so unit tests can verify them without
a browser. The async collector returned by ``build_discovery_operation`` runs
inside the browser runtime thread (via glue.run_operation) and only observes
GET JSON traffic on allowlisted TikTok hosts; it never issues state-changing
requests and never persists raw payloads.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import json

from tiktok_account_inspection import EndpointRecord, SourceGroup, redact_query


# --- Allowlist -------------------------------------------------------------

ALLOWED_HOSTS = (
    "www.tiktok.com",
    "tiktok.com",
    "webcast.tiktok.com",
    "aggr16-normal.tiktokv.us",
    "webcast16-normal-no1a.tiktokv.eu",
)

# Path prefixes that are safe to observe. The upload/auth path is excluded
# because its query carries tokens; everything else under these prefixes is a
# read-only creator API.
ALLOWED_PATH_PREFIXES = (
    "/tiktokstudio/api/web/user",
    "/tiktok/v1/creator/",
    "/tiktokstudio/api/",
    "/node-webapp/api/common-app-context",
    "/aweme/v2/data/insight/",
    "/webcast/api/money/creator_earnings/v1/payout_summary",
    "/webcast/api/money/one-wallet/v1/business/rewards",
    "/webcast/api/compliance/kyc/v1/info/detail",
    "/webcast/api/money/payout_onboarding/v2/onboarding_detail",
)

# Candidate read-only pages for discovery. No clicks are performed.
DISCOVERY_PAGES = (
    "https://www.tiktok.com/tiktokstudio/upload?from=creator_center&tab=video",
    "https://www.tiktok.com/tiktokstudio",
    "https://www.tiktok.com/tiktokstudio/analytics",
    "https://www.tiktok.com/tiktokstudio/content",
    "https://www.tiktok.com/tiktokstudio/monetization",
)

# Seed endpoints with observed evidence in request_traces.
SEED_ENDPOINTS = (
    "/tiktokstudio/api/web/user",
    "/tiktok/v1/creator/m10n_center/reward_analytics",
    "/node-webapp/api/common-app-context",
    "/tiktok/v1/creator/publish_setting/",
)

SENSITIVE_QUERY_KEYS = (
    "cookie",
    "token",
    "signature",
    "csrf",
    "sessionid",
    "sid",
    "device_id",
    "mstoken",
    "x-bogus",
    "sec-ch-ua",
)

# Content types we consider JSON.
JSON_CONTENT_TYPES = (
    "application/json",
    "text/json",
    "application/problem+json",
)


def normalized_host(url: str) -> str:
    text = str(url or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0]
    return text.strip().lower()


def request_path(url: str) -> str:
    text = str(url or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    if "/" in text:
        text = text.split("/", 1)[1]
    text = text.split("?", 1)[0]
    return "/" + text.lstrip("/")


def is_allowed_host(url: str) -> bool:
    return normalized_host(url) in ALLOWED_HOSTS


def is_allowed_path(url: str) -> bool:
    path = request_path(url)
    for prefix in ALLOWED_PATH_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def is_sensitive_query(url: str) -> bool:
    text = str(url or "")
    if "?" not in text:
        return False
    for chunk in text.split("?", 1)[1].split("&"):
        name = chunk.split("=", 1)[0].strip().lower()
        if name in SENSITIVE_QUERY_KEYS:
            return True
    return False


def is_readonly_allowed(url: str, method: str) -> bool:
    """A request may be observed only if it is a GET on an allowlisted path."""
    if str(method or "").upper() != "GET":
        return False
    if not is_allowed_host(url):
        return False
    if not is_allowed_path(url):
        return False
    if is_sensitive_query(url):
        return False
    return True


def is_json_content_type(content_type: str) -> bool:
    ctype = str(content_type or "").strip().lower()
    for candidate in JSON_CONTENT_TYPES:
        if ctype.startswith(candidate):
            return True
    return False


def _candidate_group(path: str) -> str:
    if "/m10n_center/" in path or "/reward" in path or "/balance" in path:
        return SourceGroup.MONETIZATION.value
    if "/program" in path or "/partnership" in path:
        return SourceGroup.PROGRAMS.value
    if "/payout" in path or "/withdraw" in path or "/paypal" in path:
        return SourceGroup.PAYOUT.value
    if "/analytics" in path or "/stat" in path or "/insight" in path:
        return SourceGroup.ANALYTICS.value
    return SourceGroup.IDENTITY.value


def payload_shape(payload: Any, depth: int = 2) -> Any:
    """Structural fingerprint of a JSON payload (keys + types, no values).

    Scalars are replaced by their type name; nested dicts keep their keys up to
    ``depth``; lists show the shape of their first element (or ``"array"``).
    Works with any Mapping/Sequence (including browser mappingproxy values).
    """
    if isinstance(payload, Mapping):
        if depth <= 0:
            return "object"
        out: Dict[str, Any] = {}
        for key, value in payload.items():
            out[str(key)] = payload_shape(value, depth - 1)
        return out
    if isinstance(payload, (list, tuple)):
        if not payload:
            return "array"
        return [payload_shape(payload[0], depth - 1)]
    if isinstance(payload, bool):
        return "bool"
    if isinstance(payload, int):
        return "int"
    if isinstance(payload, float):
        return "float"
    if payload is None:
        return "null"
    return "str"


def extract_payload_keys(payload: Any, max_depth: int = 3) -> Tuple[str, ...]:
    """Flat tuple of dotted key paths present in a JSON payload."""
    keys: List[str] = []

    def _walk(node, prefix):
        if len(keys) > 400:
            return
        if isinstance(node, Mapping):
            for key, value in node.items():
                path = "{}.{}".format(prefix, key) if prefix else str(key)
                if path.count(".") > max_depth:
                    keys.append(path + ".<nested>")
                    continue
                if isinstance(value, Mapping):
                    _walk(value, path)
                elif isinstance(value, (list, tuple)):
                    if value and isinstance(value[0], Mapping):
                        _walk(value[0], path + "[]")
                    else:
                        keys.append(path + "[]")
                else:
                    keys.append(path)
        elif isinstance(node, (list, tuple)):
            if node and isinstance(node[0], Mapping):
                _walk(node[0], prefix + "[]")

    _walk(payload, "")
    return tuple(sorted(set(keys)))


@dataclass(frozen=True)
class DiscoveryResult:
    profile_name: str = ""
    checked_at: str = ""
    session_state: str = "unknown"
    pages_visited: tuple = ()
    endpoints: tuple = ()
    observed_urls: tuple = ()
    observed_payloads: tuple = ()
    blocked_requests: tuple = ()
    warnings: tuple = ()
    network_error: str = ""


def classify_endpoint(record: EndpointRecord) -> Optional[EndpointRecord]:
    """Downgrade an observed endpoint when it failed or changed.

    Returns None when the record is healthy; otherwise returns a record whose
    status/error reflect the problem so callers never parse a login page or
    challenge as real data.
    """
    if record.status == 0:
        return EndpointRecord(
            path=record.path,
            status=0,
            group=record.group,
            safe_get=False,
            error=record.error or "Không nhận được response",
        )
    if record.status in (401, 403):
        return EndpointRecord(
            path=record.path,
            status=record.status,
            group=record.group,
            safe_get=False,
            error="Cần đăng nhập lại hoặc bị chặn",
        )
    if record.status in (429, 503):
        return EndpointRecord(
            path=record.path,
            status=record.status,
            group=record.group,
            safe_get=False,
            error="Bị giới hạn request",
        )
    if record.status >= 500:
        return EndpointRecord(
            path=record.path,
            status=record.status,
            group=record.group,
            safe_get=False,
            error="Lỗi server TikTok",
        )
    if not record.content_type or not is_json_content_type(record.content_type):
        return EndpointRecord(
            path=record.path,
            status=record.status,
            group=record.group,
            safe_get=False,
            error="Response không phải JSON",
        )
    if not record.payload_keys:
        return EndpointRecord(
            path=record.path,
            status=record.status,
            content_type=record.content_type,
            group=record.group,
            safe_get=False,
            error="Payload rỗng hoặc không có key",
        )
    return None


def build_discovery_operation(
    pages: Sequence[str] = DISCOVERY_PAGES,
    settle_seconds: float = 2.0,
    max_response_bytes: int = 512 * 1024,
) -> Callable[[Any], Any]:
    """Build the async page-operation used by glue.discover_tiktok_readonly_endpoints.

    The returned callable runs on the browser runtime thread with ``page`` as
    its only argument (the glue run_operation contract). It navigates each page,
    collects allowlisted GET JSON responses and returns a DiscoveryResult.
    """
    import asyncio
    import time as _time

    async def _collect(page):
        observed: List[Dict[str, Any]] = []
        blocked: List[str] = []
        visited = []
        observed_urls: Dict[str, str] = {}
        observed_payloads: Dict[str, Any] = {}
        try:
            pages_to_visit = list(pages) if pages else list(DISCOVERY_PAGES)
        except Exception:
            pages_to_visit = list(DISCOVERY_PAGES)

        async def _fetch_seed_endpoints(page):
            """Read-only in-page GET of seed endpoints; returns {path: payload}."""
            results = {}
            script = """async (url) => {
                const res = await fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}});
                const text = await res.text();
                let parsed = null;
                try { parsed = JSON.parse(text); } catch (e) {}
                return {status: res.status, body: parsed};
            }"""
            for path in SEED_ENDPOINTS:
                url = "https://www.tiktok.com" + path
                if not is_readonly_allowed(url, "GET"):
                    continue
                try:
                    info = await page.evaluate(script, url)
                except Exception:
                    continue
                info_dict = dict(info) if isinstance(info, Mapping) else {}
                if info_dict.get("status") == 200 and isinstance(info_dict.get("body"), Mapping):
                    results[path] = info_dict["body"]
            return results

        async def _read_body(response, record):
            """Async body read scheduled from the sync response callback."""
            try:
                body = await response.body()
            except Exception as error:
                record["safe_get"] = False
                record["error"] = "Không đọc được body ({})".format(type(error).__name__)
                return
            if len(body) > max_response_bytes:
                return
            try:
                text = body.decode("utf-8")
            except Exception:
                return
            try:
                payload = json.loads(text)
            except Exception:
                return
            if isinstance(payload, Mapping):
                record["payload_keys"] = extract_payload_keys(payload)
                observed_payloads[record["path"]] = payload

        pending_reads: "set[Any]" = set()

        def _on_response(response):
            try:
                request = response.request
                method = str(request.method or "GET")
                url = str(request.url or "")
                status = int(response.status or 0)
                content_type = ""
                try:
                    headers = response.headers or {}
                    content_type = str(headers.get("content-type", ""))
                except Exception:
                    pass
                if not is_readonly_allowed(url, method):
                    if method != "GET":
                        blocked.append("{} {}".format(method, request_path(url)))
                    return
                path = request_path(url)
                observed_urls[path] = url
                record = {
                    "path": path,
                    "status": status,
                    "content_type": content_type,
                    "group": _candidate_group(path),
                    "payload_keys": (),
                    "safe_get": True,
                    "error": "",
                }
                if status == 200 and is_json_content_type(content_type):
                    try:
                        task = asyncio.create_task(_read_body(response, record))
                        pending_reads.add(task)
                        task.add_done_callback(pending_reads.discard)
                    except Exception:
                        pass
                observed.append(record)
            except Exception:
                pass

        try:
            page.on("response", _on_response)
        except Exception:
            pass

        try:
            for url in pages_to_visit:
                safe_url, _dropped = redact_query(url)
                visited.append(safe_url)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                except Exception as error:
                    blocked.append("goto {} ({})".format(request_path(url), type(error).__name__))
                await asyncio.sleep(settle_seconds)
        finally:
            try:
                page.remove_listener("response", _on_response)
            except Exception:
                pass

        if pending_reads:
            await asyncio.gather(*list(pending_reads), return_exceptions=True)

        seed_payloads = await _fetch_seed_endpoints(page)
        for path, payload in seed_payloads.items():
            existing = next((item for item in observed if item["path"] == path), None)
            record = {
                "path": path,
                "status": 200,
                "content_type": "application/json",
                "group": _candidate_group(path),
                "payload_keys": extract_payload_keys(payload),
                "safe_get": True,
                "error": "",
            }
            if existing is not None:
                if record["payload_keys"]:
                    existing.update(record)
            else:
                observed.append(record)

        seen = {}
        for record in observed:
            path = record["path"]
            if path not in seen:
                seen[path] = record
            elif record["payload_keys"] and not seen[path].get("payload_keys"):
                seen[path] = record
        endpoint_records = tuple(
            EndpointRecord(
                path=item["path"],
                status=item["status"],
                content_type=item["content_type"],
                group=item["group"],
                payload_keys=item["payload_keys"],
                safe_get=item["safe_get"],
                error=item["error"],
            )
            for item in sorted(seen.values(), key=lambda r: r["path"])
        )
        return DiscoveryResult(
            checked_at=_time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            pages_visited=tuple(visited),
            endpoints=endpoint_records,
            observed_urls=tuple(sorted(observed_urls.items())),
            observed_payloads=tuple(sorted(observed_payloads.items())),
            blocked_requests=tuple(blocked),
            session_state="authenticated",
        )

    return _collect
