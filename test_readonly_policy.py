import unittest

from readonly_policy import EndpointPolicy, evaluate_request, redacted_audit_record


class ReadOnlyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.spec = EndpointPolicy(
            "test",
            ("www.tiktok.com",),
            "/safe",
            allowed_query_keys=("page",),
            enabled=True,
        )

    def test_allows_exact_https_get(self):
        self.assertTrue(evaluate_request(self.spec, "https://www.tiktok.com/safe?page=1", "GET").allowed)

    def test_rejects_disabled_endpoint(self):
        spec = EndpointPolicy("test", ("www.tiktok.com",), "/safe")
        self.assertEqual(evaluate_request(spec, "https://www.tiktok.com/safe", "GET").reason, "endpoint_disabled")

    def test_rejects_mutating_method_and_body(self):
        self.assertEqual(evaluate_request(self.spec, "https://www.tiktok.com/safe", "POST").reason, "method_not_allowed")
        self.assertEqual(evaluate_request(self.spec, "https://www.tiktok.com/safe", "GET", {}).reason, "request_body_not_allowed")

    def test_rejects_sensitive_and_unknown_query(self):
        self.assertEqual(evaluate_request(self.spec, "https://www.tiktok.com/safe?msToken=x", "GET").reason, "sensitive_query")
        self.assertEqual(evaluate_request(self.spec, "https://www.tiktok.com/safe?days=7", "GET").reason, "query_not_allowed")

    def test_audit_record_never_contains_query(self):
        record = redacted_audit_record(self.spec, "https://www.tiktok.com/safe?page=1&token=x", "GET", 200)
        self.assertEqual(record["path"], "/safe")
        self.assertNotIn("page", str(record))
        self.assertNotIn("token", str(record))

    def test_rejects_userinfo_port_and_fragment(self):
        self.assertEqual(evaluate_request(self.spec, "https://user:pass@www.tiktok.com/safe", "GET").reason, "userinfo_not_allowed")
        self.assertEqual(evaluate_request(self.spec, "https://www.tiktok.com:444/safe", "GET").reason, "port_not_allowed")
        self.assertEqual(evaluate_request(self.spec, "https://www.tiktok.com/safe#x", "GET").reason, "fragment_not_allowed")


if __name__ == "__main__":
    unittest.main()
