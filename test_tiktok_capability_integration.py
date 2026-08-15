import unittest

from tiktok_account_inspection import build_inspection_result
from tiktok_capability_models import CapabilityState


class TikTokCapabilityIntegrationTests(unittest.TestCase):
    def test_build_result_adapts_passively_observed_dashboard(self):
        result = build_inspection_result(
            "AUTO 6",
            {
                "/tiktokstudio/api/web/user": {
                    "userId": "1",
                    "userBaseInfo": {"UserProfile": {"UserBase": {"UniqId": "demo"}}},
                },
                "/tiktok/v1/creator/incentives/analytics/dashboard_overview": {
                    "currencySymbol": "$",
                    "totalAmountCents": 0,
                    "rpmCents": 0,
                    "qualifiedViews": 0,
                    "formatted": {"totalAmount": "$0.00", "rpm": "$0.00"},
                },
            },
            checked_at="x",
        )
        dashboard = result.capabilities.get("dashboard")
        self.assertIsNotNone(dashboard)
        self.assertEqual(dashboard.state, CapabilityState.SUCCESS)
        self.assertEqual(dashboard.value.qualified_views, 0)


if __name__ == "__main__":
    unittest.main()
