"""Proxy diagnostics & rotation support.

Probes a list of proxy endpoints independently (connectivity, exit IP,
latency, GeoIP detail) without touching browser profiles, then lets the
caller pick a healthy proxy.

Credentials are never echoed back: every public helper returns only the
``ip:port`` label, the exit IP and location metadata.
"""

import time

import requests

from browser_environment import (
    GEOIP_URL,
    _proxy_url,
    resolve_geoip,
    verify_proxy_endpoint,
)
from core_helpers import parse_proxy_string


def proxy_label(proxy_str):
    """Return a credential-free ``ip:port`` label, or '' for invalid input."""
    data = parse_proxy_string(proxy_str or "")
    if not data:
        return ""
    return "{}:{}".format(data.get("ip", ""), data.get("port", ""))


def parse_proxy_lines(text):
    """Normalize a multi-line proxy list into unique, valid proxy strings."""
    seen = set()
    lines = []
    for raw in (text or "").splitlines():
        value = str(raw).strip()
        if not value:
            continue
        data = parse_proxy_string(value)
        if not data:
            continue
        label = "{}:{}".format(data.get("ip", ""), data.get("port", ""))
        if label in seen:
            continue
        seen.add(label)
        lines.append(value)
    return lines


def geo_detail(proxy_data, timeout=8, request_get=None):
    """Best-effort location/provider metadata from the GeoIP endpoint.

    Failures are returned as ``{"error": ...}`` so callers can degrade
    gracefully instead of failing the whole probe."""
    proxy_url = _proxy_url(proxy_data)
    try:
        response = request_get(
            GEOIP_URL,
            proxies={"http": proxy_url, "https": proxy_url},
            headers={
                "Accept": "application/json",
                "User-Agent": "TikTokAutoUploader/ProxyDiagnostics",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        return {"error": str(error)}
    if not isinstance(payload, dict):
        return {"error": "GeoIP payload không hợp lệ"}
    result = {
        "country": str(payload.get("country") or ""),
        "country_code": str(payload.get("country_code") or ""),
        "region": str(payload.get("region") or ""),
        "city": str(payload.get("city") or ""),
    }
    connection = payload.get("connection") or {}
    if isinstance(connection, dict):
        result["org"] = str(connection.get("org") or "")
        result["isp"] = str(connection.get("isp") or "")
        asn = connection.get("asn")
        result["asn"] = str(asn) if asn is not None else ""
    return result


def probe_proxy(proxy_str, timeout=8, request_get=None):
    """Probe a single proxy: connectivity, exit IP, latency and GeoIP detail.

    Returns a dict; ``ok`` is True only when the endpoint authenticated and
    returned a real exit IP. Never includes the proxy password."""
    request_get = request_get or requests.get
    data = parse_proxy_string(proxy_str or "")
    result = {
        "label": proxy_label(proxy_str) or str(proxy_str or "").strip(),
        "ok": False,
        "exit_ip": "",
        "latency_ms": None,
        "geo": {},
        "error": "",
    }
    if not data:
        result["error"] = "Định dạng proxy không hợp lệ (IP:Port:User:Pass)"
        return result

    started = time.perf_counter()
    try:
        exit_ip = verify_proxy_endpoint(data, timeout=timeout, request_get=request_get)
        result["exit_ip"] = exit_ip
    except Exception as error:
        result["error"] = "Không kết nối được proxy: {}".format(type(error).__name__)
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return result

    geo = {}
    try:
        resolved = resolve_geoip(data, timeout=timeout, request_get=request_get)
        geo.update(
            {
                "timezone": resolved.get("timezone", ""),
                "latitude": (resolved.get("geolocation") or {}).get("latitude", ""),
                "longitude": (resolved.get("geolocation") or {}).get("longitude", ""),
            }
        )
    except Exception:
        pass
    try:
        geo.update(geo_detail(data, timeout=timeout, request_get=request_get))
    except Exception:
        pass

    result["ok"] = True
    result["geo"] = geo
    result["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return result


def probe_proxy_list(proxies, timeout=8, request_get=None):
    """Probe a list of proxies sequentially, preserving order."""
    return [
        probe_proxy(proxy_str, timeout=timeout, request_get=request_get)
        for proxy_str in proxies or []
    ]