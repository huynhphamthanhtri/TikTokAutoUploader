import hashlib
import ipaddress
import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests


GEOIP_URL = "https://ipwho.is/"
PROXY_IP_URL = "https://api.ipify.org"

PRESERVED_ENVIRONMENT_KEYS = {
    "lang",
    "timezone",
    "geolocation",
    "geo_exit_ip",
    "geo_country_code",
    "geo_country",
    "geo_region",
    "geo_city",
    "geo_asn",
    "geo_isp",
    "geo_proxy_hash",
    "geo_resolved_at",
    "geo_source",
}

# Keys describing the proxy-resolved environment. They are recomputed when the
# proxy identity changes and cleared when proxy is disabled.
GEO_ENVIRONMENT_KEYS = (
    "timezone",
    "geolocation",
    "geo_exit_ip",
    "geo_country_code",
    "geo_country",
    "geo_region",
    "geo_city",
    "geo_asn",
    "geo_isp",
    "geo_proxy_hash",
    "geo_resolved_at",
    "geo_source",
)

# Country code -> browser/accept-language locale. Used to keep the browser
# locale consistent with the proxy exit region. Fallback is en-US.
COUNTRY_LOCALES = {
    "JP": "ja-JP",
    "VN": "vi-VN",
    "US": "en-US",
    "CA": "en-CA",
    "GB": "en-GB",
    "KR": "ko-KR",
    "DE": "de-DE",
    "FR": "fr-FR",
    "ES": "es-ES",
    "IT": "it-IT",
    "TW": "zh-TW",
    "HK": "zh-HK",
    "SG": "en-SG",
    "MY": "ms-MY",
    "TH": "th-TH",
    "ID": "id-ID",
    "PH": "en-PH",
    "IN": "en-IN",
    "BR": "pt-BR",
    "MX": "es-MX",
    "AR": "es-AR",
    "CO": "es-CO",
    "AU": "en-AU",
    "NZ": "en-NZ",
    "RU": "ru-RU",
}


def locale_for_country(country_code, fallback="en-US"):
    """Map a two-letter country code to a browser locale."""
    code = str(country_code or "").strip().upper()
    return COUNTRY_LOCALES.get(code, fallback)


def ensure_fingerprint_defaults(fingerprint=None, seed=""):
    source = fingerprint or {}
    fp = {key: source[key] for key in PRESERVED_ENVIRONMENT_KEYS if key in source}
    fp["device_preset"] = "desktop"
    fp.setdefault("lang", "en-US")
    return fp


def proxy_cache_key(proxy_data):
    if not proxy_data:
        return ""
    normalized = "|".join(
        str(proxy_data.get(key, "")).strip()
        for key in ("ip", "port", "user", "pass")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def geo_cache_is_current(fingerprint, proxy_data):
    if not proxy_data or not fingerprint:
        return False
    geo = fingerprint.get("geolocation") or {}
    return bool(
        fingerprint.get("timezone")
        and _valid_coordinates(geo.get("latitude"), geo.get("longitude"))
        and fingerprint.get("geo_proxy_hash") == proxy_cache_key(proxy_data)
    )


def _proxy_url(proxy_data):
    host = str(proxy_data.get("ip", "")).strip()
    port = str(proxy_data.get("port", "")).strip()
    if not host or not port:
        raise ValueError("Proxy thiếu IP hoặc port")
    user = str(proxy_data.get("user", ""))
    password = str(proxy_data.get("pass", ""))
    auth = f"{quote(user, safe='')}:{quote(password, safe='')}@" if user or password else ""
    return f"http://{auth}{host}:{port}"


def _valid_coordinates(latitude, longitude):
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _valid_timezone(value):
    text = str(value or "").strip()
    return bool(text == "UTC" or re.fullmatch(r"[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+", text))


def normalize_geoip_payload(payload, proxy_data):
    if not isinstance(payload, dict) or payload.get("success") is False:
        raise ValueError(str((payload or {}).get("message") or "GeoIP trả dữ liệu không hợp lệ"))
    timezone_data = payload.get("timezone") or {}
    timezone_id = timezone_data.get("id") if isinstance(timezone_data, dict) else timezone_data
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if not _valid_timezone(timezone_id):
        raise ValueError("GeoIP không trả timezone IANA hợp lệ")
    if not _valid_coordinates(latitude, longitude):
        raise ValueError("GeoIP không trả tọa độ hợp lệ")
    connection = payload.get("connection") or {}
    if not isinstance(connection, dict):
        connection = {}
    country_code = str(payload.get("country_code") or "").strip().upper()
    return {
        "timezone": str(timezone_id),
        "geolocation": {
            "latitude": float(latitude),
            "longitude": float(longitude),
            "accuracy": 50,
        },
        "geo_exit_ip": str(payload.get("ip") or ""),
        "geo_country_code": country_code,
        "geo_country": str(payload.get("country") or "").strip(),
        "geo_region": str(payload.get("region") or "").strip(),
        "geo_city": str(payload.get("city") or "").strip(),
        "geo_asn": str(connection.get("asn") or "").strip(),
        "geo_isp": str(connection.get("isp") or connection.get("org") or "").strip(),
        "geo_proxy_hash": proxy_cache_key(proxy_data),
        "geo_resolved_at": datetime.now(timezone.utc).isoformat(),
        "geo_source": "ipwho.is",
    }


def resolve_geoip(proxy_data, timeout=8, request_get=requests.get):
    proxy_url = _proxy_url(proxy_data)
    response = request_get(
        GEOIP_URL,
        proxies={"http": proxy_url, "https": proxy_url},
        headers={"Accept": "application/json", "User-Agent": "TikTokAutoUploader/GeoIP"},
        timeout=timeout,
    )
    response.raise_for_status()
    return normalize_geoip_payload(response.json(), proxy_data)


def verify_proxy_endpoint(proxy_data, timeout=8, request_get=requests.get):
    proxy_url = _proxy_url(proxy_data)
    response = request_get(
        PROXY_IP_URL,
        proxies={"http": proxy_url, "https": proxy_url},
        headers={"Accept": "text/plain", "User-Agent": "TikTokAutoUploader/ProxyCheck"},
        timeout=timeout,
    )
    response.raise_for_status()
    value = str(response.text or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise ValueError("Proxy endpoint không trả IP hợp lệ") from error


def verify_direct_endpoint(timeout=8, request_get=requests.get):
    response = request_get(
        PROXY_IP_URL,
        proxies={"http": "", "https": ""},
        headers={"Accept": "text/plain", "User-Agent": "TikTokAutoUploader/DirectCheck"},
        timeout=timeout,
    )
    response.raise_for_status()
    value = str(response.text or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise ValueError("Direct endpoint không trả IP hợp lệ") from error
