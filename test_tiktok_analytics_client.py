"""Regression tests for owned-browser TikTok Studio analytics."""

import unittest
from unittest.mock import MagicMock, patch

from browser_patchright_glue import ProfileBusyError
from tiktok_analytics_client import fetch_profile_analytics


class TestTikTokAnalyticsClient(unittest.TestCase):
    def setUp(self):
        self.token = MagicMock()
        self.open_session = patch("browser_patchright_glue.open_session", return_value=self.token)
        self.close_session = patch("browser_patchright_glue.close_session")
        self.fetch_studio = patch("browser_patchright_glue.fetch_tiktok_studio_analytics")
        self.import_cookies = patch("browser_patchright_glue.import_cookies")
        self.parse_cookie = patch("browser_patchright_glue.parse_cookie", return_value=[{"name": "sessionid", "value": "redacted"}])
        self.mock_open = self.open_session.start()
        self.mock_close = self.close_session.start()
        self.mock_fetch = self.fetch_studio.start()
        self.mock_import = self.import_cookies.start()
        self.mock_parse = self.parse_cookie.start()

    def tearDown(self):
        self.open_session.stop()
        self.close_session.stop()
        self.fetch_studio.stop()
        self.import_cookies.stop()
        self.parse_cookie.stop()

    @patch("requests.Session.get")
    def test_browser_studio_success_aggregates_raw_metrics(self, mock_get):
        self.mock_fetch.return_value = {
            "state": "SUCCESS",
            "totals": {"views": 125000, "likes": 8000, "comments": 200, "shares": 500, "videos": 15},
            "observed_request_shapes": [{"path": "/tiktok/creator/manage/item_list/v1/"}],
        }
        mock_get.return_value = MagicMock(status_code=200, text='{"followerCount":18500}')

        result = fetch_profile_analytics("Profile", {"browser_profile_path": "C:/Profile", "tiktok_id": "example_user"})

        self.assertEqual(result["state"], "SUCCESS")
        self.assertEqual(result["calculation_source"], "BROWSER_STUDIO_ITEM_LIST")
        self.assertEqual(result["views_30d"], 125000)
        self.assertEqual(result["engagement_rate"], 6.96)
        self.mock_open.assert_called_once()
        self.mock_close.assert_called_once_with(self.token.handle, timeout=10)
        self.mock_import.assert_not_called()
        self.assertEqual(result["studio_observed_request_shapes"], [{"path": "/tiktok/creator/manage/item_list/v1/"}])

    @patch("requests.Session.get")
    def test_api_8_imports_saved_cookie_once_then_retries(self, mock_get):
        self.mock_fetch.side_effect = [
            {"state": "STUDIO_API_REJECTED", "apiCode": 8},
            {"state": "SUCCESS", "totals": {"views": 1, "likes": 0, "comments": 0, "shares": 0, "videos": 1}},
        ]
        mock_get.return_value = MagicMock(status_code=200, text='{"followerCount":10}')

        result = fetch_profile_analytics("Profile", {"cookie_str": "redacted", "tiktok_id": "example_user"})

        self.assertEqual(result["state"], "SUCCESS")
        self.assertEqual(result["auth_source"], "COOKIE_FALLBACK")
        self.assertEqual(self.mock_fetch.call_count, 2)
        self.mock_import.assert_called_once()

    @patch("requests.Session.get")
    def test_api_8_after_cookie_retry_requires_login_without_raw_zero(self, mock_get):
        self.mock_fetch.side_effect = [
            {"state": "STUDIO_API_REJECTED", "apiCode": 8},
            {"state": "STUDIO_API_REJECTED", "apiCode": 8},
        ]
        mock_get.return_value = MagicMock(status_code=200, text='{"followerCount":10}')

        result = fetch_profile_analytics("Profile", {"cookie_str": "redacted", "tiktok_id": "example_user"})

        self.assertEqual(result["primary_state"], "LOGIN_REQUIRED")
        self.assertEqual(result["error_code"], "LOGIN_REQUIRED_API_8")
        self.assertIsNone(result["views_30d"])
        self.mock_import.assert_called_once()

    @patch("requests.Session.get")
    def test_profile_in_use_does_not_open_or_close_a_second_session(self, mock_get):
        self.mock_open.side_effect = ProfileBusyError("profile is already in use")
        mock_get.return_value = MagicMock(status_code=200, text='{"followerCount":10}')

        result = fetch_profile_analytics("Profile", {"tiktok_id": "example_user"})

        self.assertEqual(result["primary_state"], "PROFILE_IN_USE")
        self.assertEqual(result["state"], "PARTIAL")
        self.assertEqual(result["fallback_state"], "DISABLED")
        self.mock_close.assert_not_called()

    @patch("requests.Session.get")
    def test_invalid_studio_json_never_creates_a_zero_metric(self, mock_get):
        self.mock_fetch.return_value = {"state": "STUDIO_INVALID_JSON"}
        mock_get.return_value = MagicMock(status_code=200, text='{"followerCount":10}')

        result = fetch_profile_analytics("Profile", {"tiktok_id": "example_user"})

        self.assertEqual(result["primary_state"], "STUDIO_INVALID_JSON")
        self.assertIsNone(result["views_30d"])
        self.assertEqual(result["fallback_state"], "DISABLED")

    @patch("requests.Session.get")
    def test_studio_api_code_is_preserved_without_response_payload(self, mock_get):
        self.mock_fetch.return_value = {"state": "STUDIO_API_REJECTED", "apiCode": 5}
        mock_get.return_value = MagicMock(status_code=200, text='{"followerCount":10}')

        result = fetch_profile_analytics("Profile", {"tiktok_id": "example_user"})

        self.assertEqual(result["error_code"], "STUDIO_API_REJECTED_API_5")

    @patch("requests.Session.get")
    def test_api_5_never_publishes_raw_zero(self, mock_get):
        self.mock_fetch.return_value = {"state": "STUDIO_API_REJECTED", "apiCode": 5}
        mock_get.return_value = MagicMock(status_code=200, text='{"followerCount":10}')

        result = fetch_profile_analytics("Profile", {"tiktok_id": "example_user"})

        self.assertEqual(result["primary_state"], "STUDIO_API_REJECTED")
        self.assertIsNone(result["views_30d"])


if __name__ == "__main__":
    unittest.main()
