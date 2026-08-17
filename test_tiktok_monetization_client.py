"""
test_tiktok_monetization_client.py - Unit tests for tiktok_monetization_client.py
"""

import time
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
    def test_kyc_approved_only_when_cdd_ge_7_and_no_errors(self, mock_get):
        """Verifies real APPROVED state matching live AUTO 18 account (cdd_status=7, created=True)."""
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "kyc" in url:
                mock_resp.json.return_value = {
                    "base_resp": {"status_code": 0},
                    "kyc_status": {
                        "cdd_status": 7,
                        "created": True,
                        "fail_dynamic_poa": False,
                        "id_doc_resubmit": False,
                        "poa_doc_resubmit": False,
                        "user_id": 7447972851385451523,
                    },
                    "last_submitted_data": {
                        "full_name": "Nguyen Van A",
                        "id_type": "ID_CARD",
                        "id_issue_country_region": "VN",
                    }
                }
            elif "payout_onboarding" in url:
                mock_resp.json.return_value = {
                    "data": {
                        "pi_bind_status": 1,
                        "masked_instrument_identity": "test***@gmail.com",
                        "user_tax_status": 1,
                    }
                }
            elif "payout_summary" in url:
                mock_resp.json.return_value = {
                    "data": {"balance": 100.0, "total_balance": {"value": "100.00", "currency": "USD"}}
                }
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect
        cfg = {"use_proxy": False, "cookie_str": "sessionid=valid_token", "region": "US"}
        res = fetch_monetization_snapshot("Live_AUTO_18", cfg)

        self.assertEqual(res["kyc_status"], "APPROVED")
        self.assertEqual(res["kyc_full_name"], "Nguyen Van A")
        self.assertEqual(res["kyc_id_type"], "ID_CARD")
        self.assertEqual(res["payout_status"], "PAYOUT_READY")
        self.assertEqual(res["tax_status"], "TAX_VERIFIED")

    @patch("requests.Session.get")
    def test_kyc_pending_when_cdd_is_1_or_2(self, mock_get):
        """Verifies in-review KYC submissions (cdd_status=1 or 2) are flagged as PENDING, NOT APPROVED."""
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "kyc" in url:
                mock_resp.json.return_value = {
                    "kyc_status": {
                        "cdd_status": 1,
                        "created": True,
                        "fail_dynamic_poa": False,
                        "id_doc_resubmit": False,
                        "poa_doc_resubmit": False,
                    },
                    "last_submitted_data": {
                        "full_name": "Tran Van B",
                    }
                }
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect
        cfg = {"use_proxy": False, "cookie_str": "sessionid=valid_token", "region": "US"}
        res = fetch_monetization_snapshot("Pending_KYC_Profile", cfg)

        self.assertEqual(res["kyc_status"], "PENDING")
        self.assertNotEqual(res["kyc_status"], "APPROVED")

    @patch("requests.Session.get")
    def test_kyc_resubmit_when_poa_or_id_resubmit(self, mock_get):
        """Verifies KYC with resubmit flags (poa_doc_resubmit=True or fail_dynamic_poa=True) are RESUBMIT."""
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "kyc" in url:
                mock_resp.json.return_value = {
                    "kyc_status": {
                        "cdd_status": 7,
                        "created": True,
                        "fail_dynamic_poa": False,
                        "id_doc_resubmit": False,
                        "poa_doc_resubmit": True,  # Needs POA resubmission!
                    },
                    "last_submitted_data": {
                        "full_name": "Le Van C",
                    }
                }
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect
        cfg = {"use_proxy": False, "cookie_str": "sessionid=valid_token", "region": "US"}
        res = fetch_monetization_snapshot("Resubmit_KYC_Profile", cfg)

        self.assertEqual(res["kyc_status"], "RESUBMIT")
        self.assertNotEqual(res["kyc_status"], "APPROVED")

    @patch("requests.Session.get")
    def test_payout_onboarding_pending_and_ready_states(self, mock_get):
        """Verifies pi_bind_status=2 flags as PAYOUT_PENDING (matching BKT TEAM T10 02)."""
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "payout_onboarding" in url:
                mock_resp.json.return_value = {
                    "data": {
                        "confirmed": False,
                        "masked_instrument_identity": "",
                        "pi_bind_status": 2,  # Pending verification!
                        "user_tax_status": 1,
                    }
                }
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect
        cfg = {"use_proxy": False, "cookie_str": "sessionid=valid_token", "region": "US"}
        res = fetch_monetization_snapshot("Pending_PTTT_Profile", cfg)

        self.assertEqual(res["payout_status"], "PAYOUT_PENDING")
        self.assertIn("Đang xác minh", res["payment_method"])

    @patch("requests.Session.get")
    def test_crp_expired_punishment_becomes_eligible(self, mock_get):
        """When punishment reapply date has passed and requirements are met, becomes ELIGIBLE."""
        past_ts = int(time.time()) - 86400 * 5  # 5 days ago
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "incentives/profile" in url:
                mock_resp.json.return_value = {
                    "status_code": 0,
                    "profile_status": "Disabled",
                    "apply_check_list": [
                        {"key": "follower_count", "amount": 15000, "threshold": 10000, "status": 1},
                        {"key": "video_view", "amount": 250000, "threshold": 100000, "status": 1},
                    ],
                    "raw": {
                        "punishment_infos": [{"title": "Unoriginal Content"}],
                        "profile": {"reapply_starting_date": past_ts, "could_reapply": True},
                    },
                }
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect
        cfg = {"use_proxy": False, "cookie_str": "sessionid=token", "region": "US"}
        res = fetch_monetization_snapshot("Expired_Punishment_Profile", cfg)

        self.assertEqual(res["crp_status"], "ELIGIBLE")
        self.assertIn("ĐỦ ĐK", res["crp_display"])
        self.assertIn("Hết hạn phạt", res["crp_display"])
        self.assertTrue(res["crp_can_reapply"])

    @patch("requests.Session.get")
    def test_crp_appeal_state_with_deadline(self, mock_get):
        """When an appeal is submitted and under review, flags as APPEAL."""
        now_ts = int(time.time())
        future_ts = now_ts + 86400 * 7  # 7 days in future
        def side_effect(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            if "incentives/profile" in url:
                mock_resp.json.return_value = {
                    "status_code": 0,
                    "profile_status": "In Review",
                    "raw": {
                        "profile": {
                            "appeal_info": {
                                "appeal_submit_time": now_ts - 3600,
                                "appeal_review_deadline": future_ts,
                            }
                        }
                    }
                }
            else:
                mock_resp.json.return_value = {}
            return mock_resp

        mock_get.side_effect = side_effect
        cfg = {"use_proxy": False, "cookie_str": "sessionid=token", "region": "US"}
        res = fetch_monetization_snapshot("Appealing_Profile", cfg)

        self.assertEqual(res["crp_status"], "APPEAL")
        self.assertIn("ĐANG KHÁNG", res["crp_display"])

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


if __name__ == "__main__":
    unittest.main()
