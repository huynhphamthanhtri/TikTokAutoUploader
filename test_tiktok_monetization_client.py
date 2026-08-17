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
        self.assertEqual(
            build_cookie_string('[{"name": "sessionid", "value": "abc"}, {"name": "ttwid", "value": "123"}]'),
            "sessionid=abc; ttwid=123",
        )

    def test_fetch_monetization_no_auth(self):
        """When cookie_str is missing, returns NO_AUTH and 'Chưa có Cookie' status gracefully."""
        cfg = {"use_proxy": False, "cookie_str": ""}
        res = fetch_monetization_snapshot("test_prof", cfg)
        self.assertEqual(res["status"], "NO_AUTH")
        self.assertEqual(res["payout_status"], "Chưa có Cookie")
        self.assertEqual(res["kyc_status"], "Chưa có Cookie")

    @patch("requests.Session.get")
    def test_fetch_monetization_cookie_die_detection(self, mock_get):
        """When TikTok responds with 20003 (not logged in) or HTTP 401, flags as Cookie Die."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 20003,
            "status_message": "session user not logged in",
        }
        mock_get.return_value = mock_resp

        cfg = {"use_proxy": False, "cookie_str": "sessionid=expired_token"}
        res = fetch_monetization_snapshot("Expired_Profile", cfg)

        self.assertEqual(res["status"], "COOKIE_EXPIRED")
        self.assertEqual(res["payout_status"], "Cookie Die")
        self.assertEqual(res["kyc_status"], "Cookie Die")
        self.assertEqual(res["payment_method"], "Cookie die - Không check được")

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
                    "kyc_status": {
                        "cdd_status": 1,
                        "created": True,
                        "user_id": 7447972851385451523,
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
        self.assertEqual(res["tiktok_user_id"], "7447972851385451523")
        self.assertEqual(len(res["pending_earnings"]), 1)
        self.assertEqual(res["pending_earnings"][0]["bill_id"], "999888")

    @patch("requests.Session.get")
    def test_fetch_crp_details_and_dashboard(self, mock_get):
        """Parses CRP profile eligibility, punishment, and dashboard RPM metrics correctly."""
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "payout_summary" in url:
                mock_resp.json.return_value = {"data": {"balance": 0.0}}
            elif "incentives/profile" in url:
                mock_resp.json.return_value = {
                    "status_code": 0,
                    "profile_status": "Disabled",
                    "apply_check_list": [
                        {"key": "follower_count", "amount": 8500, "threshold": 10000, "status": 0},
                        {"key": "video_view", "amount": 65000, "threshold": 100000, "status": 0},
                    ],
                    "raw": {
                        "punishment_infos": [{"title": "Unoriginal Content"}],
                        "profile": {"reapply_starting_date": 1789500000},
                    },
                }
            elif "analytics/dashboard_overview" in url:
                mock_resp.json.return_value = {
                    "status_code": 0,
                    "rpm": 0.85,
                    "qualified_views": 1500000,
                    "estimated_revenue": 1275.0,
                }
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect

        cfg = {"use_proxy": False, "cookie_str": "sessionid=token", "region": "US"}
        res = fetch_monetization_snapshot("CRP_Profile", cfg)

        self.assertEqual(res["crp_status"], "REJECTED")
        self.assertIn("BỊ LOẠI", res["crp_display"])
        self.assertEqual(res["crp_punishment"], "Unoriginal Content")
        self.assertEqual(res["crp_rpm"], 0.85)
        self.assertEqual(res["crp_qualified_views"], 1500000)
        self.assertEqual(res["crp_estimated_revenue"], 1275.0)

    @patch("requests.Session.get")
    def test_fetch_crp_security_reasons_tktbm(self, mock_get):
        """Parses CRP Security reasons penalty (TKTBM) and threshold progress correctly."""
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "incentives/profile" in url:
                mock_resp.json.return_value = {
                    "status_code": 0,
                    "profile_status": 7,
                    "apply_check_list": [
                        {"key": "follower_count", "amount": 10104, "threshold": 10000, "status": 1},
                        {"key": "video_view", "amount": 7090, "threshold": 100000, "status": 2},
                    ],
                    "punishment_infos": [
                        {
                            "title": "Security reasons",
                            "description": "Your account has been associated with multiple high-risk activities...",
                        }
                    ],
                    "profile": {
                        "reapply_starting_date": 1768837633,
                        "could_appeal": False,
                        "could_reapply": True,
                    },
                }
            elif "analytics/dashboard_overview" in url:
                mock_resp.json.return_value = {"status_code": 0, "rpm": 0.0, "qualified_views": 0, "estimated_revenue": 0.0}
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect

        cfg = {"use_proxy": False, "cookie_str": "sessionid=token", "region": "DE"}
        res = fetch_monetization_snapshot("AUTO_18_Mock", cfg)

        self.assertEqual(res["crp_status"], "TKTBM")
        self.assertIn("TKTBM", res["crp_display"])
        self.assertEqual(res["crp_punishment"], "Security reasons")
        self.assertIn("high-risk", res["crp_punishment_desc"])
        self.assertEqual(res["crp_followers"], 10104)
        self.assertEqual(res["crp_views"], 7090)
        self.assertFalse(res["crp_all_met"])
        self.assertTrue(res["crp_can_reapply"])

    @patch("requests.Session.post")
    def test_apply_creative_rewards(self, mock_post):
        """Tests sending application for Creative Rewards Program."""
        from tiktok_monetization_client import apply_creative_rewards_for_profile

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": 0, "status_msg": "Success"}
        mock_post.return_value = mock_resp

        cfg = {"use_proxy": False, "cookie_str": "sessionid=token", "region": "US"}
        res = apply_creative_rewards_for_profile("Eligible_Profile", cfg)

        self.assertTrue(res["success"])
        self.assertIn("thành công", res["message"])

    @patch("requests.Session.get")
    def test_uid_fallback_extraction_via_setting_and_config(self, mock_get):
        """Tests that UID and uniqueId fall back to /setting HTML regex and profile config."""
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "passport" in url:
                # Passport returns empty/failed data
                mock_resp.json.return_value = {"data": {}}
            elif "setting" in url:
                mock_resp.text = '<html><script>{"uid":"7504494015036851222","uniqueId":"creator_test","secUid":"sec_123"}</script></html>'
            elif "payout_summary" in url:
                mock_resp.json.return_value = {"data": {"balance": 0.0}}
            elif "info/detail" in url:
                # KYC returns user_id
                mock_resp.json.return_value = {"kyc_status": {"user_id": 7504494015036851222, "cdd_status": 7, "created": True}}
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect

        cfg = {"use_proxy": False, "cookie_str": "sessionid=token", "region": "DE", "tiktok_account": "@creator_test"}
        res = fetch_monetization_snapshot("Fallback_Profile", cfg)

        self.assertEqual(res["tiktok_user_id"], "7504494015036851222")
        self.assertEqual(res["unique_id"], "creator_test")


if __name__ == "__main__":
    unittest.main()
