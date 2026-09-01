import time
import unittest
from unittest.mock import MagicMock, patch
import requests

from tiktok_analytics import (
    format_compact_number,
    format_views_follow_badge,
    parse_cookie_input,
    fetch_channel_views_30d,
    fetch_public_profile_stats,
    STUDIO_ITEM_LIST_URL,
)


class TestCompactNumberFormatter(unittest.TestCase):
    def test_format_compact_number(self):
        self.assertEqual(format_compact_number(None), "0")
        self.assertEqual(format_compact_number(""), "0")
        self.assertEqual(format_compact_number(0), "0")
        self.assertEqual(format_compact_number(-50), "0")
        self.assertEqual(format_compact_number(450), "450")
        self.assertEqual(format_compact_number(1000), "1K")
        self.assertEqual(format_compact_number(1500), "1.5K")
        self.assertEqual(format_compact_number(25400), "25.4K")
        self.assertEqual(format_compact_number(1000000), "1M")
        self.assertEqual(format_compact_number(2450000), "2.5M")
        self.assertEqual(format_compact_number(1200000000), "1.2B")
        self.assertEqual(format_compact_number("12500"), "12.5K")


class TestViewsFollowBadge(unittest.TestCase):
    def test_badge_formatting(self):
        self.assertEqual(format_views_follow_badge(None, None), "— | —")
        self.assertEqual(format_views_follow_badge(125400, 15200), "👁️ 125.4K  |  👥 15.2K")
        self.assertEqual(format_views_follow_badge(1500000, None), "👁️ 1.5M  |  👥 —")
        self.assertEqual(format_views_follow_badge(None, 45000), "👁️ —  |  👥 45K")
        self.assertEqual(format_views_follow_badge(0, 0), "👁️ 0  |  👥 0")


class TestCookieParsing(unittest.TestCase):
    def test_parse_cookie_dict(self):
        d = {"sessionid": "abc123xyz", "tt_csrf_token": "token999"}
        cd, cstr, csrf = parse_cookie_input(d)
        self.assertEqual(cd["sessionid"], "abc123xyz")
        self.assertIn("sessionid=abc123xyz", cstr)
        self.assertEqual(csrf, "token999")

    def test_parse_cookie_json_list(self):
        cookie_list = [
            {"name": "sessionid", "value": "sec_sess_1"},
            {"name": "tt_csrf_token", "value": "sec_csrf_1"},
        ]
        cd, cstr, csrf = parse_cookie_input(cookie_list)
        self.assertEqual(cd["sessionid"], "sec_sess_1")
        self.assertEqual(csrf, "sec_csrf_1")

    def test_parse_cookie_raw_string(self):
        raw = "sessionid=raw_sess; store-country-code=US; tt_csrf_token=csrf_raw"
        cd, cstr, csrf = parse_cookie_input(raw)
        self.assertEqual(cd["sessionid"], "raw_sess")
        self.assertEqual(cd["store-country-code"], "US")
        self.assertEqual(csrf, "csrf_raw")


class TestPublicProfileScraping(unittest.TestCase):
    @patch("requests.get")
    def test_scrape_public_rehydration_stats(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <html>
            <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">
            {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": {"uniqueId": "testuser", "secUid": "sec_test_1"}, "stats": {"followerCount": 15400, "videoCount": 42, "heartCount": 89000}}}}}
            </script>
        </html>
        """
        mock_get.return_value = mock_resp

        res = fetch_public_profile_stats("testuser")
        self.assertTrue(res["ok"])
        self.assertEqual(res["follower_count"], 15400)
        self.assertEqual(res["video_count"], 42)
        self.assertEqual(res["heart_count"], 89000)
        self.assertEqual(res["sec_uid"], "sec_test_1")


class TestFetchChannelViews30D(unittest.TestCase):
    def setUp(self):
        self.fixed_now = 1724457600
        self.valid_cookie = "sessionid=valid_sess; tt_csrf_token=valid_token"

    def test_missing_sessionid_without_tiktok_id_returns_error(self):
        res = fetch_channel_views_30d("invalid_cookie_str", now_epoch=self.fixed_now)
        self.assertFalse(res["ok"])
        self.assertEqual(res["views_30d"], 0)
        self.assertIn("Thiếu cookie", res["error"])

    @patch("requests.post")
    def test_single_page_with_early_stop(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "msg": "success",
            "data": {
                "item_list": [
                    {
                        "item_id": "v1",
                        "create_time": self.fixed_now - (2 * 86400),
                        "statistics": {"play_count": 10000, "digg_count": 500, "share_count": 50, "comment_count": 20},
                    },
                    {
                        "item_id": "v2",
                        "create_time": self.fixed_now - (15 * 86400),
                        "statistics": {"play_count": 25000, "digg_count": 1200, "share_count": 100, "comment_count": 40},
                    },
                    {
                        "item_id": "v3",
                        "create_time": self.fixed_now - (40 * 86400),
                        "statistics": {"play_count": 100000, "digg_count": 5000, "share_count": 200, "comment_count": 80},
                    },
                ],
                "has_more": True,
                "cursor": 50,
            },
        }
        mock_post.return_value = mock_response

        res = fetch_channel_views_30d(self.valid_cookie, now_epoch=self.fixed_now)
        self.assertTrue(res["ok"])
        self.assertEqual(res["views_30d"], 35000)
        self.assertEqual(res["videos_30d"], 2)
        self.assertEqual(res["likes_30d"], 1700)
        self.assertEqual(res["views_30d_fmt"], "35K")
        self.assertEqual(mock_post.call_count, 1)

    @patch("requests.post")
    def test_multi_page_pagination(self, mock_post):
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "code": 0,
            "data": {
                "item_list": [
                    {"item_id": "v1", "create_time": self.fixed_now - (1 * 86400), "statistics": {"play_count": 5000, "digg_count": 100}},
                    {"item_id": "v2", "create_time": self.fixed_now - (5 * 86400), "statistics": {"play_count": 15000, "digg_count": 300}},
                ],
                "has_more": True,
                "cursor": 50,
            },
        }

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "code": 0,
            "data": {
                "item_list": [
                    {"item_id": "v3", "create_time": self.fixed_now - (20 * 86400), "statistics": {"play_count": 30000, "digg_count": 600}},
                    {"item_id": "v4", "create_time": self.fixed_now - (35 * 86400), "statistics": {"play_count": 80000, "digg_count": 1000}},
                ],
                "has_more": False,
                "cursor": 100,
            },
        }
        mock_post.side_effect = [resp1, resp2]

        res = fetch_channel_views_30d(self.valid_cookie, now_epoch=self.fixed_now)
        self.assertTrue(res["ok"])
        self.assertEqual(res["views_30d"], 50000)
        self.assertEqual(res["videos_30d"], 3)
        self.assertEqual(res["views_30d_fmt"], "50K")
        self.assertEqual(mock_post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
