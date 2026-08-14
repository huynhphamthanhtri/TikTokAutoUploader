import unittest

from browser_environment import (
    GEO_ENVIRONMENT_KEYS,
    ensure_fingerprint_defaults,
    geo_cache_is_current,
    locale_for_country,
    normalize_geoip_payload,
    proxy_cache_key,
    resolve_geoip,
    verify_direct_endpoint,
    verify_proxy_endpoint,
)
from config_store import build_runtime_profiles


class FakeResponse:
    text = "198.51.100.12"

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "success": True,
            "ip": "198.51.100.12",
            "country_code": "VN",
            "country": "Vietnam",
            "region": "Ho Chi Minh City",
            "city": "Ho Chi Minh City",
            "latitude": 1.5,
            "longitude": 2.5,
            "connection": {"asn": 140353, "isp": "VNPT", "org": "VNPT"},
            "timezone": {"id": "Asia/Ho_Chi_Minh"},
        }


class BrowserEnvironmentTests(unittest.TestCase):
    def test_legacy_mobile_and_custom_profiles_migrate_to_native_desktop(self):
        for preset in ("pixel", "iphone_x", "custom"):
            fingerprint = ensure_fingerprint_defaults({
                "device_preset": preset,
                "user_agent": "fabricated",
                "user_agent_metadata": {"mobile": True},
                "platform": "Android",
                "mobile": True,
                "touch_points": 5,
                "hardware_concurrency": 8,
                "canvas_noise": 0.001,
                "window_width": 412,
                "window_height": 915,
                "webrtc_policy": "block",
            })
            self.assertEqual(fingerprint, {"device_preset": "desktop", "lang": "en-US"})

    def test_native_migration_preserves_only_locale_and_geo_cache(self):
        original = {
            "lang": "en-GB",
            "timezone": "Europe/London",
            "geolocation": {"latitude": 51.5, "longitude": -0.12, "accuracy": 50},
            "geo_exit_ip": "198.51.100.12",
            "geo_proxy_hash": "cache-key",
            "geo_resolved_at": "2026-08-13T00:00:00+00:00",
            "geo_source": "ipwho.is",
        }
        fingerprint = ensure_fingerprint_defaults(original)
        self.assertEqual(fingerprint["device_preset"], "desktop")
        for key, value in original.items():
            self.assertEqual(fingerprint[key], value)
        self.assertNotIn("profile_note", ensure_fingerprint_defaults({"profile_note": "drop"}))

    def test_proxy_cache_key_does_not_expose_proxy_value(self):
        proxy = {"ip": "203.0.113.8", "port": "8080", "user": "u", "pass": "p"}
        key = proxy_cache_key(proxy)
        self.assertEqual(len(key), 64)
        self.assertNotIn("203.0.113.8", key)
        self.assertTrue(geo_cache_is_current({"timezone": "UTC", "geolocation": {"latitude": 1, "longitude": 2}, "geo_proxy_hash": key}, proxy))

    def test_geoip_payload_is_normalized(self):
        proxy = {"ip": "203.0.113.8", "port": "8080"}
        result = normalize_geoip_payload(
            {
                "success": True,
                "ip": "198.51.100.12",
                "country_code": "VN",
                "country": "Vietnam",
                "region": "Ho Chi Minh City",
                "city": "Ho Chi Minh City",
                "latitude": 1.5,
                "longitude": 2.5,
                "connection": {"asn": 140353, "isp": "VNPT"},
                "timezone": {"id": "Asia/Ho_Chi_Minh"},
            },
            proxy,
        )
        self.assertEqual(result["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(result["geolocation"]["latitude"], 1.5)
        self.assertEqual(result["geo_proxy_hash"], proxy_cache_key(proxy))
        self.assertEqual(result["geo_country_code"], "VN")
        self.assertEqual(result["geo_country"], "Vietnam")
        self.assertEqual(result["geo_region"], "Ho Chi Minh City")
        self.assertEqual(result["geo_city"], "Ho Chi Minh City")
        self.assertEqual(result["geo_asn"], "140353")
        self.assertEqual(result["geo_isp"], "VNPT")

    def test_geoip_payload_tolerates_missing_connection_and_country(self):
        proxy = {"ip": "203.0.113.8", "port": "8080"}
        result = normalize_geoip_payload(
            {
                "success": True,
                "ip": "198.51.100.12",
                "latitude": 1.5,
                "longitude": 2.5,
                "timezone": {"id": "Asia/Tokyo"},
            },
            proxy,
        )
        self.assertEqual(result["geo_country_code"], "")
        self.assertEqual(result["geo_asn"], "")
        self.assertEqual(result["geo_isp"], "")
        self.assertEqual(result["timezone"], "Asia/Tokyo")

    def test_country_locale_mapping_and_fallback(self):
        self.assertEqual(locale_for_country("JP"), "ja-JP")
        self.assertEqual(locale_for_country("vn"), "vi-VN")
        self.assertEqual(locale_for_country("US"), "en-US")
        self.assertEqual(locale_for_country("ZZ"), "en-US")

    def test_geo_environment_keys_present_in_normalized_payload(self):
        proxy = {"ip": "203.0.113.8", "port": "8080"}
        result = normalize_geoip_payload(
            {
                "success": True,
                "ip": "198.51.100.12",
                "latitude": 1.5,
                "longitude": 2.5,
                "timezone": {"id": "Asia/Ho_Chi_Minh"},
            },
            proxy,
        )
        for key in GEO_ENVIRONMENT_KEYS:
            self.assertIn(key, result)

    def test_geoip_request_uses_proxy(self):
        calls = []

        def request_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        proxy = {"ip": "203.0.113.8", "port": "8080", "user": "a@b", "pass": "p:q"}
        resolve_geoip(proxy, timeout=3, request_get=request_get)
        self.assertEqual(calls[0][1]["timeout"], 3)
        self.assertIn("a%40b:p%3Aq@203.0.113.8:8080", calls[0][1]["proxies"]["https"])

    def test_proxy_endpoint_preflight_uses_encoded_credentials(self):
        calls = []

        def request_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        proxy = {"ip": "203.0.113.8", "port": "8080", "user": "a@b", "pass": "p:q"}
        current_ip = verify_proxy_endpoint(proxy, timeout=4, request_get=request_get)
        self.assertEqual(current_ip, "198.51.100.12")
        self.assertEqual(calls[0][1]["timeout"], 4)
        self.assertIn("a%40b:p%3Aq@203.0.113.8:8080", calls[0][1]["proxies"]["https"])

    def test_direct_endpoint_explicitly_bypasses_environment_proxy(self):
        calls = []

        def request_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        self.assertEqual(verify_direct_endpoint(request_get=request_get), "198.51.100.12")
        self.assertEqual(calls[0][1]["proxies"], {"http": "", "https": ""})

    def test_old_spoof_keys_are_tolerated_but_removed(self):
        obsolete = {
            "fingerprint_protection": True,
            "hardware_concurrency": 8,
            "canvas_noise_seed": 1,
            "webgl_noise_seed": 2,
            "audio_noise_seed": 3,
            "audio_noise": 0.1,
            "webgl_vendor": "old vendor",
            "webgl_renderer": "old renderer",
        }
        fingerprint = ensure_fingerprint_defaults(obsolete)
        self.assertTrue(obsolete.keys().isdisjoint(fingerprint))

    def test_runtime_profile_migration_adds_environment_defaults(self):
        loaded = {"one": {"cookie_str": "cookie", "fingerprint": {"lang": "en-US"}}}
        runtime = build_runtime_profiles(loaded)
        fingerprint = runtime["one"]["config"]["fingerprint"]
        self.assertEqual(fingerprint["device_preset"], "desktop")
        self.assertEqual(fingerprint["lang"], "en-US")
        self.assertNotIn("canvas_noise_seed", fingerprint)
        self.assertIn("fingerprint", loaded["one"])


if __name__ == "__main__":
    unittest.main()
