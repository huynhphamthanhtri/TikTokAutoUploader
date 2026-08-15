import unittest
from decimal import Decimal

from tiktok_capability_models import CapabilityState, KycState, PaymentState
from tiktok_schema_adapters import (
    adapt_balance,
    adapt_creative_rewards,
    adapt_dashboard,
    adapt_kyc,
    adapt_payment,
    adapt_payout,
    adapt_traffic,
    adapt_violations,
    first_present,
)


class TikTokSchemaAdapterTests(unittest.TestCase):
    def test_first_present_keeps_zero_and_false(self):
        self.assertEqual(first_present({"a": 0, "b": 2}, "a", "b"), 0)
        self.assertIs(first_present({"a": False, "b": True}, "a", "b"), False)

    def test_dashboard_keeps_known_zero(self):
        result = adapt_dashboard(
            {
                "currencySymbol": "$",
                "totalAmountCents": 0,
                "estimatedRevenueCents": 0,
                "rpmCents": 0,
                "qualifiedViews": 0,
                "formatted": {"totalAmount": "$0.00", "rpm": "$0.00", "qualifiedViews": "0"},
            }
        )
        self.assertEqual(result.state, CapabilityState.SUCCESS)
        self.assertEqual(result.value.total_amount.minor_units, 0)
        self.assertEqual(result.value.qualified_views, 0)

    def test_balance_zero_is_not_missing(self):
        result = adapt_balance({"balance": 0, "frozen_balance": 0, "payout_threshold": 1000})
        self.assertEqual(result.state, CapabilityState.SUCCESS)
        self.assertEqual(result.value.balance.minor_units, 0)

    def test_creative_rewards_checklist(self):
        result = adapt_creative_rewards(
            {
                "enabled": False,
                "profile": {"profile_status": 1},
                "apply_check_list": [
                    {"key": "age", "status": 1, "desc": "ok"},
                    {"key": "video_view", "status": 2, "desc": "missing"},
                ],
            }
        )
        self.assertFalse(result.value.enabled)
        self.assertFalse(result.value.all_requirements_met)
        self.assertEqual(len(result.value.requirements), 2)

    def test_payout_empty_is_success_empty(self):
        result = adapt_payout(
            {
                "summary": {"currency_amount": 0, "currency_symbol": "$"},
                "pending_earnings": [],
                "payout_breakdown": [],
                "is_flexible_payout_enabled": True,
            }
        )
        self.assertEqual(result.state, CapabilityState.SUCCESS_EMPTY)
        self.assertEqual(result.value.summary.minor_units, 0)

    def test_traffic_keeps_decimal_zero(self):
        result = adapt_traffic(
            [
                {"name": "Search", "percentage": "79.3"},
                {"name": "For You", "percentage": "20.7"},
                {"name": "Sound", "percentage": "0.0"},
            ]
        )
        self.assertEqual(result.state, CapabilityState.SUCCESS)
        self.assertEqual(result.value.sources[-1].percentage, Decimal("0.0"))

    def test_kyc_status_is_redacted(self):
        result = adapt_kyc(
            {
                "kyc_status": {
                    "created": True,
                    "cdd_status": 7,
                    "screen_status": 2,
                    "id_doc_resubmit": False,
                    "poa_doc_resubmit": False,
                    "fail_dynamic_poa": False,
                },
                "last_submitted_data": {"full_name": "secret"},
            }
        )
        self.assertEqual(result.value.state, KycState.VERIFIED)
        self.assertNotIn("secret", repr(result.value))

    def test_payment_confirmed_false_with_method_is_pending(self):
        result = adapt_payment(
            {"data": {"confirmed": False, "masked_instrument_identity": "***1234", "pi_bind_status": 1}}
        )
        self.assertEqual(result.value.state, PaymentState.PENDING)
        self.assertNotIn("1234", repr(result.value))

    def test_violations_distinguishes_empty_from_schema_change(self):
        self.assertEqual(adapt_violations({"video_info_list": []}).state, CapabilityState.SUCCESS_EMPTY)
        self.assertEqual(adapt_violations({}).state, CapabilityState.ENDPOINT_CHANGED)


if __name__ == "__main__":
    unittest.main()
