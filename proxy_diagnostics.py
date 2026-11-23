"""Proxy diagnostics & rotation support.

Probes a list of proxy endpoints independently (connectivity, exit IP,
latency, GeoIP detail) without touching browser profiles, then lets the
caller pick a healthy proxy.

Credentials are never echoed back: every public helper returns only the
``ip:port`` label, the exit IP and location metadata.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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


def batch_probe_proxies(
    proxies,
    max_workers=5,
    timeout=6,
    request_get=None,
    on_progress=None,
):
    """Probe a collection of proxies concurrently using a bounded ThreadPoolExecutor.

    Parameters:
        proxies: Iterable of proxy strings.
        max_workers: Maximum concurrent probe threads (default 5 to avoid GeoIP rate limits).
        timeout: Timeout in seconds for each probe (default 6).
        request_get: Optional custom requests.get callable for testing/mocking.
        on_progress: Optional callback fn(completed_count, total_count, proxy_str, result).

    Returns:
        Dict[str, dict]: Mapping proxy_str -> probe_result dict.
    """
    clean_proxies = [str(p).strip() for p in (proxies or []) if str(p).strip()]
    if not clean_proxies:
        return {}

    unique_proxies = list(dict.fromkeys(clean_proxies))
    total_count = len(unique_proxies)
    results = {}

    workers = max(1, min(max_workers or 5, total_count))
    completed_count = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(
                probe_proxy,
                p_str,
                timeout=timeout,
                request_get=request_get,
            ): p_str
            for p_str in unique_proxies
        }
        for future in as_completed(future_map):
            p_str = future_map[future]
            try:
                res = future.result()
            except Exception as exc:
                res = {
                    "label": proxy_label(p_str) or p_str,
                    "ok": False,
                    "exit_ip": "",
                    "latency_ms": None,
                    "geo": {},
                    "error": str(exc),
                }
            results[p_str] = res
            completed_count += 1
            if callable(on_progress):
                try:
                    on_progress(completed_count, total_count, p_str, res)
                except Exception:
                    pass

    # Ensure all original clean_proxies have an entry in output mapping
    final_results = {}
    for p in clean_proxies:
        final_results[p] = results.get(p, {})
    return final_results