import unittest

from browser_environment import (
    apply_device_preset,
    build_fingerprint_script,
    chrome_environment_arguments,
    chrome_environment_preferences,
    configure_driver_environment,
    device_override,
    geo_cache_is_current,
    normalize_geoip_payload,
    proxy_cache_key,
    resolve_geoip,
)
from config_store import build_runtime_profiles


class FakeDriver:
    def __init__(self):
        self.commands = []

    def execute_cdp_cmd(self, command, params):
        self.commands.append((command, params))
        return {}


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "success": True,
            "ip": "198.51.100.12",
            "latitude": 1.5,
            "longitude": 2.5,
            "timezone": {"id": "Asia/Ho_Chi_Minh"},
        }


class BrowserEnvironmentTests(unittest.TestCase):
    def test_device_preset_is_coherent(self):
        fingerprint = apply_device_preset({}, "pixel")
        device = device_override(fingerprint)
        self.assertEqual(device["platform"], "Android")
        self.assertTrue(device["mobile"])
        self.assertIn("Android", device["user_agent"])
        self.assertEqual(fingerprint["webrtc_policy"], "controlled")

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
                "latitude": 1.5,
                "longitude": 2.5,
                "timezone": {"id": "Asia/Ho_Chi_Minh"},
            },
            proxy,
        )
        self.assertEqual(result["timezone"], "Asia/Ho_Chi_Minh")
        self.assertEqual(result["geolocation"]["latitude"], 1.5)
        self.assertEqual(result["geo_proxy_hash"], proxy_cache_key(proxy))

    def test_geoip_request_uses_proxy(self):
        calls = []

        def request_get(url, **kwargs):
            calls.append((url, kwargs))
            return FakeResponse()

        proxy = {"ip": "203.0.113.8", "port": "8080", "user": "a@b", "pass": "p:q"}
        resolve_geoip(proxy, timeout=3, request_get=request_get)
        self.assertEqual(calls[0][1]["timeout"], 3)
        self.assertIn("a%40b:p%3Aq@203.0.113.8:8080", calls[0][1]["proxies"]["https"])

    def test_webrtc_policy_has_distinct_options(self):
        controlled = {"webrtc_policy": "controlled"}
        blocked = {"webrtc_policy": "block"}
        self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", chrome_environment_arguments(controlled))
        self.assertNotIn("profile.default_content_setting_values.media_stream_mic", chrome_environment_preferences(controlled))
        self.assertIn("--disable-webrtc", chrome_environment_arguments(blocked))
        self.assertEqual(chrome_environment_preferences(blocked)["profile.default_content_setting_values.media_stream_mic"], 2)

    def test_fingerprint_script_contains_required_hooks_once(self):
        script = build_fingerprint_script({"device_preset": "iphone_x"})
        self.assertIn("HTMLCanvasElement.prototype.toDataURL", script)
        self.assertIn("prototype.readPixels", script)
        self.assertIn("AnalyserNode", script)
        self.assertIn("__privacyFingerprintInstalled", script)
        self.assertNotIn("__CANVAS_SEED__", script)

    def test_driver_environment_applies_ua_timezone_geo_and_script(self):
        driver = FakeDriver()
        fingerprint = apply_device_preset({}, "pixel")
        fingerprint.update({
            "timezone": "Asia/Ho_Chi_Minh",
            "geolocation": {"latitude": 10.75, "longitude": 106.67, "accuracy": 25},
        })
        configure_driver_environment(driver, fingerprint)
        commands = [command for command, _params in driver.commands]
        self.assertIn("Network.setUserAgentOverride", commands)
        self.assertIn("Emulation.setDeviceMetricsOverride", commands)
        self.assertIn("Emulation.setTimezoneOverride", commands)
        self.assertIn("Emulation.setGeolocationOverride", commands)
        self.assertIn("Browser.grantPermissions", commands)
        self.assertIn("Page.addScriptToEvaluateOnNewDocument", commands)

    def test_runtime_profile_migration_adds_environment_defaults(self):
        loaded = {"one": {"cookie_str": "cookie", "fingerprint": {"lang": "en-US"}}}
        runtime = build_runtime_profiles(loaded)
        fingerprint = runtime["one"]["config"]["fingerprint"]
        self.assertEqual(fingerprint["webrtc_policy"], "controlled")
        self.assertIn("canvas_noise_seed", fingerprint)
        self.assertIn("fingerprint", loaded["one"])


if __name__ == "__main__":
    unittest.main()
