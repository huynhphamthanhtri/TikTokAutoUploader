"""
test_tiktok_monetization_client.py - Unit tests for tiktok_monetization_client.py
"""

import unittest
from unittest.mock import MagicMock, patch

from tiktok_monetization_client import (
    HOST_EU,
    HOST_GLOBAL,
    HOST_US,
    TikTokMonetizationClient,
    build_cookie_string,
    fetch_monetization_snapshot,
    mask_sensitive_payment_info,
    resolve_webcast_base_host,
)


class TestTikTokMonetizationClient(unittest.TestCase):
    def test_resolve_webcast_base_host(self):
        """Resolves correct base host based on country code."""
        self.assertEqual(resolve_webcast_base_host("US"), HOST_US)
        self.assertEqual(resolve_webcast_base_host("USA"), HOST_US)
        self.assertEqual(resolve_webcast_base_host("GB"), HOST_EU)
        self.assertEqual(resolve_webcast_base_host("DE"), HOST_EU)
        self.assertEqual(resolve_webcast_base_host("FR"), HOST_EU)
        self.assertEqual(resolve_webcast_base_host("VN"), HOST_GLOBAL)
        self.assertEqual(resolve_webcast_base_host(""), HOST_GLOBAL)

    def test_mask_sensitive_payment_info(self):
        """Masks bank account numbers and email addresses."""
        self.assertEqual(mask_sensitive_payment_info("1234567890"), "***7890")
        self.assertEqual(mask_sensitive_payment_info("johndoe@gmail.com"), "j***@gmail.com")
        self.assertEqual(mask_sensitive_payment_info(""), "")

    def test_build_cookie_string(self):
        """Converts dict, list or string into valid Cookie header."""
        self.assertEqual(build_cookie_string("sessionid=abc; ttwid=123"), "sessionid=abc; ttwid=123")
        self.assertEqual(build_cookie_string({"sessionid": "abc", "ttwid": "123"}), "sessionid=abc; ttwid=123")
        self.assertEqual(
            build_cookie_string([{"name": "sessionid", "value": "abc"}, {"name": "ttwid", "value": "123"}]),
            "sessionid=abc; ttwid=123",
        )

    def test_fetch_monetization_no_auth(self):
        """When cookie_str is missing, returns NO_AUTH status gracefully."""
        cfg = {"use_proxy": False, "cookie_str": ""}
        res = fetch_monetization_snapshot("test_prof", cfg)
        self.assertEqual(res["status"], "NO_AUTH")
        self.assertEqual(res["payout_status"], "NO_AUTH")

    @patch("requests.Session.get")
    def test_fetch_monetization_success_mock(self, mock_get):
        """Parses payout_summary, business/rewards, onboarding_detail, and kyc correctly."""
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "payout_summary" in url:
                mock_resp.json.return_value = {
                    "data": {
                        "total_balance": {"value": "1500.25", "currency": "USD", "currency_symbol": "$"},
                        "available_balance": {"value": "1500.25"},
                        "frozen_balance": {"value": "0.00"},
                        "next_payout_date": 1729036800,
                    }
                }
            elif "rewards" in url:
                mock_resp.json.return_value = {
                    "data": {
                        "summary": {"estimated_amount": {"currency_amount": "$1,500.25"}},
                        "pending_earnings": [
                            {"title": "Jul 2026", "amount": {"currency_amount": "$900.00"}, "bill_id": "999888", "timestamp": 1722470400}
                        ],
                        "payout_breakdown": [{"title": "Qualified Videos", "amount": {"currency_amount": "$1,500.25"}}],
                    }
                }
            elif "payout_onboarding" in url:
                mock_resp.json.return_value = {
                    "data": {
                        "payment_method": "Chase Bank",
                        "account_number": "9876543210",
                    }
                }
            elif "kyc" in url:
                mock_resp.json.return_value = {
                    "data": {
                        "kyc_status": "APPROVED",
                    }
                }
            return mock_resp

        mock_get.side_effect = side_effect

        cfg = {
            "use_proxy": False,
            "cookie_str": "sessionid=valid_token",
            "region": "US",
        }
        res = fetch_monetization_snapshot("US_Profile_01", cfg)

        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["balance"], 1500.25)
        self.assertEqual(res["currency"], "USD")
        self.assertEqual(res["payout_status"], "PAYOUT_READY")
        self.assertIn("Chase Bank", res["payment_method"])
        self.assertIn("***3210", res["payment_method"])
        self.assertEqual(res["kyc_status"], "APPROVED")
        self.assertEqual(len(res["pending_earnings"]), 1)
        self.assertEqual(res["pending_earnings"][0]["bill_id"], "999888")


if __name__ == "__main__":
    unittest.main()
