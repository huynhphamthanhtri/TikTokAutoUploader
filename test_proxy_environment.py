import unittest

from proxy_environment import (
    COMPATIBLE_CHANGE,
    RISKY_CHANGE,
    SAME,
    UNKNOWN,
    compare_proxy_environment,
    proxy_environment_snapshot,
)


def fp(**overrides):
    base = {
        "geo_exit_ip": "203.0.113.8",
        "geo_country_code": "JP",
        "geo_country": "Japan",
        "geo_region": "Tokyo",
        "geo_city": "Tokyo",
        "geo_asn": "AS203020",
        "geo_isp": "Example ISP",
        "timezone": "Asia/Tokyo",
    }
    base.update(overrides)
    return base


class ProxyEnvironmentTests(unittest.TestCase):
    def test_same_environment(self):
        result = compare_proxy_environment(fp(), fp())
        self.assertEqual(result["decision"], SAME)
        self.assertEqual(result["changed_fields"], [])
        self.assertEqual(result["warnings"], [])

    def test_ip_change_with_same_country_asn_timezone_is_compatible(self):
        result = compare_proxy_environment(
            fp(), fp(geo_exit_ip="203.0.113.99")
        )
        self.assertEqual(result["decision"], COMPATIBLE_CHANGE)
        self.assertIn("geo_exit_ip", result["changed_fields"])
        self.assertTrue(result["warnings"])

    def test_country_change_is_risky(self):
        result = compare_proxy_environment(
            fp(), fp(geo_exit_ip="203.0.113.99", geo_country_code="US", geo_country="United States")
        )
        self.assertEqual(result["decision"], RISKY_CHANGE)
        self.assertIn("geo_country_code", result["changed_fields"])
        self.assertNotIn("timezone", result["changed_fields"])

    def test_asn_change_is_risky(self):
        result = compare_proxy_environment(
            fp(), fp(geo_exit_ip="203.0.113.99", geo_asn="AS99999")
        )
        self.assertEqual(result["decision"], RISKY_CHANGE)
        self.assertIn("geo_asn", result["changed_fields"])

    def test_timezone_change_is_risky(self):
        result = compare_proxy_environment(
            fp(), fp(geo_exit_ip="203.0.113.99", timezone="America/New_York")
        )
        self.assertEqual(result["decision"], RISKY_CHANGE)
        self.assertIn("timezone", result["changed_fields"])

    def test_missing_data_is_unknown(self):
        result = compare_proxy_environment(
            {"geo_exit_ip": "203.0.113.8"}, fp(geo_exit_ip="203.0.113.99")
        )
        self.assertEqual(result["decision"], UNKNOWN)
        self.assertTrue(result["warnings"])

    def test_same_environment_with_missing_optional_fields_is_same(self):
        previous = fp()
        current = dict(previous)
        previous.pop("geo_region")
        current.pop("geo_city")
        result = compare_proxy_environment(previous, current)
        self.assertEqual(result["decision"], SAME)

    def test_snapshot_only_includes_environment_keys(self):
        fingerprint = dict(fp(), lang="en-US", device_preset="desktop")
        snapshot = proxy_environment_snapshot(fingerprint)
        self.assertIn("geo_exit_ip", snapshot)
        self.assertNotIn("lang", snapshot)
        self.assertNotIn("device_preset", snapshot)

    def test_snapshot_accepts_empty_input(self):
        self.assertEqual(
            proxy_environment_snapshot(None),
            {
                "geo_exit_ip": "",
                "geo_country_code": "",
                "geo_country": "",
                "geo_region": "",
                "geo_city": "",
                "geo_asn": "",
                "geo_isp": "",
                "timezone": "",
            },
        )


if __name__ == "__main__":
    unittest.main()