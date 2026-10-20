# -*- coding: utf-8 -*-
"""
test_check_info_service.py - Test suite cho quy trình 3-Tier Waterfall Check Thông Tin.
Tuân thủ nghiêm ngặt AGENTS.md:
- Kiểm tra logic bóc tách danh tính qua Studio API và Setting HTML fallback.
- Kiểm tra đối chiếu UID chống mismatch khi cào trang công khai.
- Kiểm tra phân loại chính xác các trạng thái (SUCCESS, PARTIAL_SUCCESS, COOKIE_EXPIRED, PROXY_ERROR).
"""

import unittest
from unittest.mock import MagicMock, patch
import requests

from check_features.services.check_info_service import TikTokCheckInfoService, CheckInfoResult


class TestTikTokCheckInfoService(unittest.TestCase):
    """Kiểm tra logic nghiệp vụ của TikTokCheckInfoService."""

    def setUp(self):
        self.config = {
            "name": "Profile_Test_01",
            "cookie_str": "sessionid=test_valid_sessionid_abc; store-country-code=us",
            "user_agent": "Mozilla/5.0 TestUA",
        }
        self.service = TikTokCheckInfoService("Profile_Test_01", self.config)

    @patch("requests.Session.get")
    def test_waterfall_step1_studio_success(self, mock_get):
        """Kịch bản 1: Bước 1 Studio API thành công trọn vẹn."""
        # Mock Studio response
        mock_studio = MagicMock()
        mock_studio.status_code = 200
        mock_studio.json.return_value = {
            "user_id": "7111222333444",
            "unique_id": "creator_pro",
            "nickname": "Pro Creator",
            "avatar_url": "https://p16.tiktokcdn.com/avatar1.jpg",
            "region": "US",
            "age_gate_region": "GB",
        }

        # Mock Public Profile response
        mock_pub = MagicMock()
        mock_pub.status_code = 200
        mock_pub.text = """
        <html>
          <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
            {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": {"id": "7111222333444"}, "stats": {"followerCount": 15000, "followingCount": 20, "heartCount": 98000, "videoCount": 45}}}}}
          </script>
        </html>
        """

        mock_get.side_effect = [mock_studio, mock_pub]

        res = self.service.execute()

        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.identity_source, "studio")
        self.assertEqual(res.user["uid"], "7111222333444")
        self.assertEqual(res.user["uniqueId"], "creator_pro")
        self.assertEqual(res.stats["followerCount"], 15000)
        self.assertEqual(res.stats["videoCount"], 45)

    @patch("requests.Session.get")
    def test_waterfall_step2_setting_fallback_success(self, mock_get):
        """Kịch bản 2: Bước 1 lỗi -> Bước 2 /setting fallback thành công."""
        # 1. Studio fail
        mock_studio = MagicMock()
        mock_studio.status_code = 500

        # 2. Setting HTML success
        mock_setting = MagicMock()
        mock_setting.status_code = 200
        mock_setting.url = "https://www.tiktok.com/setting"
        mock_setting.text = """
        <html>
          <script>
            var state = {"uniqueId":"fallback_user","userId":"7222333444555","nickName":"Fallback Nick","avatarUri":["https://p16.tiktokcdn.com/avt2.jpg"],"region":"VN"};
          </script>
        </html>
        """

        # 3. Public Profile success
        mock_pub = MagicMock()
        mock_pub.status_code = 200
        mock_pub.text = """
        <html>
          <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
            {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": {"id": "7222333444555"}, "stats": {"followerCount": 5400, "followingCount": 10, "heartCount": 21000, "videoCount": 12}}}}}
          </script>
        </html>
        """

        mock_get.side_effect = [mock_studio, mock_setting, mock_pub]

        res = self.service.execute()

        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.identity_source, "setting")
        self.assertEqual(res.user["uniqueId"], "fallback_user")
        self.assertEqual(res.user["uid"], "7222333444555")
        self.assertEqual(res.stats["followerCount"], 5400)

    @patch("requests.Session.get")
    def test_anti_mismatch_uid_protection(self, mock_get):
        """Kịch bản 3: UID công khai không khớp với UID đăng nhập (chống redirect nhầm profile)."""
        mock_studio = MagicMock()
        mock_studio.status_code = 200
        mock_studio.json.return_value = {
            "user_id": "111111111",
            "unique_id": "target_user",
        }

        # Public profile trả về user khác có ID 999999999
        mock_pub = MagicMock()
        mock_pub.status_code = 200
        mock_pub.text = """
        <html>
          <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
            {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": {"id": "999999999"}, "stats": {"followerCount": 999999}}}}}
          </script>
        </html>
        """

        mock_get.side_effect = [mock_studio, mock_pub]

        res = self.service.execute()

        # Identity vẫn thành công, nhưng stats không được gán từ user khác
        self.assertTrue(res.success)
        self.assertEqual(res.status, "PARTIAL_SUCCESS")
        self.assertEqual(res.user["uid"], "111111111")
        self.assertEqual(res.stats["followerCount"], 0)

    @patch("requests.Session.get")
    def test_cookie_expired_detection(self, mock_get):
        """Kịch bản 4: Phiên hết hạn -> redirect login -> Báo COOKIE_EXPIRED."""
        mock_studio = MagicMock()
        mock_studio.status_code = 401

        mock_setting = MagicMock()
        mock_setting.status_code = 302
        mock_setting.url = "https://www.tiktok.com/login?redirect_url=/setting"
        mock_setting.text = "Redirecting to login..."

        mock_get.side_effect = [mock_studio, mock_setting]

        res = self.service.execute()

        self.assertFalse(res.success)
        self.assertEqual(res.status, "SESSION_INVALID")
        self.assertEqual(res.auth_state, "EXPIRED")
        self.assertIn("hết hạn", res.error)

    @patch("requests.Session.get")
    def test_session_invalid_user_not_found_20001(self, mock_get):
        """Kịch bản thực tế: Studio API trả StatusCode 20001 (user not found) và /setting đẩy về /foryou."""
        mock_studio = MagicMock()
        mock_studio.status_code = 200
        mock_studio.json.return_value = {
            "userId": "0",
            "statusCode": 0,
            "userBaseInfo": {
                "BaseResp": {
                    "StatusMessage": "user not found",
                    "StatusCode": 20001,
                }
            }
        }

        mock_setting = MagicMock()
        mock_setting.status_code = 200
        mock_setting.url = "https://www.tiktok.com/foryou"
        mock_setting.text = "<html>Foryou page</html>"

        mock_get.side_effect = [mock_studio, mock_setting]

        res = self.service.execute()

        self.assertFalse(res.success)
        self.assertEqual(res.status, "SESSION_INVALID")
        self.assertEqual(res.auth_state, "EXPIRED")
        self.assertIn("20001", res.error)

    @patch("requests.Session.get")
    def test_not_logged_in_no_sessionid(self, mock_get):
        """Kịch bản thực tế: Profile chưa đăng nhập (Cookie SQLite không có sessionid)."""
        cfg_no_session = {
            "name": "BKT TEAM T10 02",
            "cookie_str": "tt_csrf_token=xyz; msToken=123",  # Không có sessionid
            "user_agent": "Mozilla/5.0 TestUA",
        }
        service = TikTokCheckInfoService("BKT TEAM T10 02", cfg_no_session)
        res = service.execute()

        self.assertFalse(res.success)
        self.assertEqual(res.status, "NOT_LOGGED_IN")
        self.assertEqual(res.auth_state, "NOT_LOGGED_IN")
        self.assertIn("Chưa đăng nhập", res.error)
        mock_get.assert_not_called()  # Thoát sớm ở Bước 0, không lãng phí request HTTP

    @patch("requests.Session.get")
    def test_proxy_error_handling(self, mock_get):
        """Kịch bản 5: Lỗi kết nối Proxy."""
        mock_get.side_effect = requests.exceptions.ProxyError("Cannot connect to proxy 1.2.3.4:8080")

        res = self.service.execute()

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PROXY_ERROR")
        self.assertIn("Proxy", res.error)


if __name__ == "__main__":
    unittest.main()

    @patch("requests.Session.get")
    def test_waterfall_step1_studio_real_nested_schema(self, mock_get):
        """Kịch bản thực tế 100%: Studio trả nested schema theo cấu trúc chuẩn của server TikTok."""
        mock_studio = MagicMock()
        mock_studio.status_code = 200
        mock_studio.json.return_value = {
            "statusCode": 0,
            "userId": "7446698084853384224",
            "userBaseInfo": {
                "UserProfile": {
                    "UserBase": {
                        "Id": "7446698084853384224",
                        "UniqId": "tenshinoana008",
                        "NickName": "ピカッとニュース",
                        "Region": {"Region": "VN", "RegistrationCountry": "gb"},
                        "SecUid": "MS4wLjABAAAA3EHucoweWcK5D6fM7zT6W61uW3eQW0m1uXfGv_uOq4f6i1a8Z8m1uXfGv_uOq4f6",
                        "Avatars": [{"UrlInfo": {"Uri": "https://p16.tiktokcdn.com/avatar_studio.jpg"}}],
                        "CreateTime": 1734000000,
                    }
                }
            }
        }

        mock_pub = MagicMock()
        mock_pub.status_code = 200
        mock_pub.text = """
        <html>
          <script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
            {"__DEFAULT_SCOPE__": {"webapp.user-detail": {"userInfo": {"user": {"id": "7446698084853384224"}, "stats": {"followerCount": 10400, "followingCount": 0, "heartCount": 5137, "videoCount": 78}}}}}
          </script>
        </html>
        """

        mock_get.side_effect = [mock_studio, mock_pub]

        res = self.service.execute()

        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.outcome, "SUCCESS")
        self.assertEqual(res.auth_state, "VALID")
        self.assertEqual(res.identity_source, "studio")
        self.assertEqual(res.user["uid"], "7446698084853384224")
        self.assertEqual(res.user["uniqueId"], "tenshinoana008")
        self.assertEqual(res.user["secUid"], "MS4wLjABAAAA3EHucoweWcK5D6fM7zT6W61uW3eQW0m1uXfGv_uOq4f6i1a8Z8m1uXfGv_uOq4f6")
        self.assertEqual(res.user["region"], "VN")
        self.assertEqual(res.stats["followerCount"], 10400)
        self.assertEqual(res.stats["heartCount"], 5137)
        self.assertEqual(res.stats["videoCount"], 78)

    @patch("requests.Session.get")
    def test_proxy_and_network_error_never_marks_expired(self, mock_get):
        """Kịch bản lỗi proxy/network: auth_state PHẢI là UNKNOWN, TUYỆT ĐỐI không được kết luận COOKIE_EXPIRED."""
        mock_get.side_effect = requests.exceptions.ProxyError("Cannot connect to proxy 108.165.83.187:7150")

        res = self.service.execute()

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PROXY_ERROR")
        self.assertEqual(res.outcome, "FAILED")
        self.assertEqual(res.auth_state, "UNKNOWN")
        self.assertEqual(res.error_code, "PROXY_ERROR")
        self.assertIn("Proxy", res.error)

    def test_format_requests_proxy_rfc_standard(self):
        """Kiểm tra hàm chuẩn hóa proxy format RFC 3986 trong core_helpers."""
        from core_helpers import format_requests_proxy

        # 1. Định dạng 4 phần có xác thực
        p4 = format_requests_proxy("108.165.83.187:7150:rhgwhqac:xwics2j9bztn")
        self.assertIsNotNone(p4)
        self.assertEqual(p4["http"], "http://rhgwhqac:xwics2j9bztn@108.165.83.187:7150")
        self.assertEqual(p4["https"], "http://rhgwhqac:xwics2j9bztn@108.165.83.187:7150")

        # 2. Định dạng 2 phần không user/pass
        p2 = format_requests_proxy("127.0.0.1:8888")
        self.assertIsNotNone(p2)
        self.assertEqual(p2["http"], "http://127.0.0.1:8888")

        # 3. Định dạng SOCKS5
        ps = format_requests_proxy("1.2.3.4:1080:u:p", scheme="socks5")
        self.assertEqual(ps["http"], "socks5://u:p@1.2.3.4:1080")
