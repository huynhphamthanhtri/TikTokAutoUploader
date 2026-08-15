import unittest

from readonly_policy import evaluate_request
from tiktok_capability_requests import ACTIVE_CAPABILITIES, build_capability_requests
from tiktok_endpoint_catalog import endpoint_policy
from tiktok_schema_adapters import build_capability_results
from tiktok_capability_models import CapabilityState


class TikTokCapabilityRequestTests(unittest.TestCase):
    def test_default_build_only_returns_active_verified_capabilities(self):
        requests = build_capability_requests()
        self.assertEqual(
            {request.capability for request in requests},
            set(ACTIVE_CAPABILITIES),
        )

    def test_every_built_request_passes_policy(self):
        for request in build_capability_requests():
            spec = endpoint_policy(request.endpoint_id)
            decision = evaluate_request(spec, request.url, request.method, request.body)
            self.assertTrue(decision.allowed, (request.capability, decision.reason))
            self.assertTrue(spec.enabled, (request.capability, "endpoint must be enabled"))

    def test_disabled_capabilities_never_built_by_default(self):
        capabilities = {request.capability for request in build_capability_requests()}
        for name in ("dashboard", "creative_rewards", "traffic", "video_rank"):
            self.assertNotIn(name, capabilities)

    def test_explicit_selection_can_request_any_catalog_endpoint(self):
        for request in build_capability_requests(capabilities=("balance",)):
            self.assertEqual(request.capability, "balance")

    def test_transport_errors_become_capability_states(self):
        requests = build_capability_requests()
        transport = {
            "errors": [
                {"capability": request.capability, "endpoint_id": request.endpoint_id, "status": 429, "reason": "bad_response"}
                for request in requests
            ]
        }
        results = build_capability_results(requests, transport)
        self.assertTrue(results.results)
        self.assertTrue(all(item.state == CapabilityState.RATE_LIMITED for item in results.results))


if __name__ == "__main__":
    unittest.main()
