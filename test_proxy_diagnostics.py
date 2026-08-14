import unittest

import proxy_diagnostics as diag

GEO_PAYLOAD = {
    "success": True,
    "ip": "1.2.3.4",
    "country": "Japan",
    "country_code": "JP",
    "region": "Tokyo",
    "city": "Tokyo",
    "timezone": {"id": "Asia/Tokyo"},
    "latitude": 35.68,
    "longitude": 139.76,
    "connection": {"org": "Test ISP", "isp": "Test ISP", "asn": 12345},
}


class _FakeResponse:
    def __init__(self, text="", payload=None, status=200):
        self._text = text
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError("HTTP {}".format(self._status))

    @property
    def text(self):
        return self._text

    def json(self):
        return self._payload


class _FakeGet:
    def __init__(self, payload, exit_text="5.6.7.8", fail_urls=()):
        self.calls = []
        self._payload = payload
        self._exit_text = exit_text
        self._fail_urls = set(fail_urls)

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs.get("proxies", {}), kwargs.get("timeout")))
        if url in self._fail_urls:
            raise OSError("boom")
        if url == "https://api.ipify.org":
            return _FakeResponse(text=self._exit_text)
        return _FakeResponse(payload=self._payload)


class ProxyLabelTests(unittest.TestCase):
    def test_label_strips_credentials(self):
        self.assertEqual(diag.proxy_label("1.2.3.4:8080:user:pass"), "1.2.3.4:8080")

    def test_label_simple(self):
        self.assertEqual(diag.proxy_label("1.2.3.4:8080"), "1.2.3.4:8080")

    def test_label_invalid_empty(self):
        self.assertEqual(diag.proxy_label(""), "")
        self.assertEqual(diag.proxy_label("not-a-proxy"), "")


class ParseProxyLinesTests(unittest.TestCase):
    def test_parses_and_dedupes(self):
        lines = diag.parse_proxy_lines(
            "1.2.3.4:1111:u:p\n1.2.3.4:1111:u:p\nhttp://5.6.7.8:2222:a:b\n"
        )
        self.assertEqual(lines, ["1.2.3.4:1111:u:p", "http://5.6.7.8:2222:a:b"])

    def test_skips_invalid_and_blank(self):
        lines = diag.parse_proxy_lines("  \n1.2.3.4:1:2:3\nbad\n")
        self.assertEqual(lines, ["1.2.3.4:1:2:3"])


class ProbeProxyTests(unittest.TestCase):
    def test_probe_ok(self):
        fake = _FakeGet(GEO_PAYLOAD)
        result = diag.probe_proxy("1.2.3.4:8080:user:pass", request_get=fake)
        self.assertTrue(result["ok"])
        self.assertEqual(result["label"], "1.2.3.4:8080")
        self.assertEqual(result["exit_ip"], "5.6.7.8")
        self.assertEqual(result["geo"]["timezone"], "Asia/Tokyo")
        self.assertEqual(result["geo"]["country"], "Japan")
        self.assertEqual(result["geo"]["country_code"], "JP")
        self.assertEqual(result["geo"]["asn"], "12345")
        self.assertIsInstance(result["latency_ms"], int)
        self.assertNotIn("user:pass", str(result))
        self.assertNotIn("secret", str(result))

    def test_probe_bad_format(self):
        result = diag.probe_proxy("not-a-proxy")
        self.assertFalse(result["ok"])
        self.assertIn("không hợp lệ", result["error"])

    def test_probe_connectivity_failure(self):
        fake = _FakeGet(GEO_PAYLOAD, fail_urls={"https://api.ipify.org"})
        result = diag.probe_proxy("1.2.3.4:8080:u:p", request_get=fake)
        self.assertFalse(result["ok"])
        self.assertIn("OSError", result["error"])
        self.assertIsInstance(result["latency_ms"], int)
        self.assertEqual(result["exit_ip"], "")

    def test_probe_geo_failure_still_ok(self):
        fake = _FakeGet(GEO_PAYLOAD, fail_urls={"https://ipwho.is/"})
        result = diag.probe_proxy("1.2.3.4:8080:u:p", request_get=fake)
        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_ip"], "5.6.7.8")
        self.assertEqual(result["geo"].get("error"), "boom")

    def test_probe_credentials_never_leaked(self):
        fake = _FakeGet(GEO_PAYLOAD)
        result = diag.probe_proxy("10.20.30.40:9999:superuser:supersecret", request_get=fake)
        text = repr(result)
        self.assertNotIn("supersecret", text)
        self.assertNotIn("superuser", text)


class ProbeListTests(unittest.TestCase):
    def test_preserves_order(self):
        fake = _FakeGet(GEO_PAYLOAD)
        results = diag.probe_proxy_list(
            ["1.1.1.1:1000:u:p", "2.2.2.2:2000:u:p"], request_get=fake
        )
        self.assertEqual([r["label"] for r in results], ["1.1.1.1:1000", "2.2.2.2:2000"])
        self.assertTrue(all(r["ok"] for r in results))


if __name__ == "__main__":
    unittest.main()
