import time
from unittest.mock import MagicMock, patch
import unittest
from googleapiclient.errors import HttpError
import httplib2

from youtube_monitor.key_manager import ApiKeyInfo, ApiKeyManager, _next_midnight_utc


class TestKeyManager(unittest.TestCase):
    def test_api_key_info_usability(self):
        now = time.time()
        info = ApiKeyInfo(key="KEY_A", status="ACTIVE")
        self.assertTrue(info.is_usable(now))

        # Quota exceeded before reset
        info.status = "QUOTA_EXCEEDED"
        info.quota_resets_at = now + 3600
        self.assertFalse(info.is_usable(now))

        # Quota exceeded after reset time
        self.assertTrue(info.is_usable(now + 3601))
        self.assertEqual(info.status, "ACTIVE")

        # Rate limited before cooldown
        info.status = "RATE_LIMITED"
        info.retry_after = now + 60
        self.assertFalse(info.is_usable(now))

        # Rate limited after cooldown
        self.assertTrue(info.is_usable(now + 65))
        self.assertEqual(info.status, "ACTIVE")

        # Invalid / Disabled never auto-recovers
        info.status = "INVALID"
        self.assertFalse(info.is_usable(now + 10000))
        info.status = "DISABLED"
        self.assertFalse(info.is_usable(now + 10000))

    def test_round_robin_selection(self):
        mgr = ApiKeyManager(["KEY_1", "KEY_2", "KEY_3"])
        k1 = mgr.get_active_key()
        k2 = mgr.get_active_key()
        k3 = mgr.get_active_key()
        k4 = mgr.get_active_key()
        self.assertEqual([k1, k2, k3, k4], ["KEY_1", "KEY_2", "KEY_3", "KEY_1"])

    def test_error_classification_quota_exceeded(self):
        mgr = ApiKeyManager(["KEY_1", "KEY_2"])
        resp = httplib2.Response({"status": 403})
        content = b'{"error": {"errors": [{"reason": "quotaExceeded", "message": "The request cannot be completed because you have exceeded your quota."}]}}'
        exc = HttpError(resp, content)

        err_type = mgr.handle_api_error("KEY_1", exc)
        self.assertEqual(err_type, "QUOTA_EXCEEDED")

        # KEY_1 should no longer be active, manager should immediately return KEY_2
        next_key = mgr.get_active_key()
        self.assertEqual(next_key, "KEY_2")

    def test_error_classification_rate_limited(self):
        mgr = ApiKeyManager(["KEY_1", "KEY_2"])
        resp = httplib2.Response({"status": 403})
        content = b'{"error": {"errors": [{"reason": "rateLimitExceeded", "message": "User rate limit exceeded."}]}}'
        exc = HttpError(resp, content)

        err_type = mgr.handle_api_error("KEY_1", exc)
        self.assertEqual(err_type, "RATE_LIMITED")
        self.assertEqual(mgr.get_active_key(), "KEY_2")

    def test_error_classification_server_error_preserves_key(self):
        mgr = ApiKeyManager(["KEY_1"])
        resp = httplib2.Response({"status": 503})
        exc = HttpError(resp, b"Backend Error")

        err_type = mgr.handle_api_error("KEY_1", exc)
        self.assertEqual(err_type, "SERVER_ERROR")
        # Should still be usable, not disabled
        self.assertEqual(mgr.get_active_key(), "KEY_1")

    def test_load_and_save_config(self):
        mgr = ApiKeyManager(["KEY_X", "KEY_Y"])
        cfg = {}
        mgr.save_to_config(cfg)
        self.assertEqual(cfg["api_keys"], ["KEY_X", "KEY_Y"])
        self.assertEqual(len(cfg["api_keys_pool"]), 2)

        # Load into new manager
        mgr2 = ApiKeyManager()
        mgr2.load_from_config(cfg)
        self.assertEqual(mgr2.get_keys(), ["KEY_X", "KEY_Y"])

    def test_youtube_client_caching_and_invalidation(self):
        build_calls = []

        def mock_build(service, version, developerKey=None, cache_discovery=False):
            build_calls.append((service, version, developerKey))
            mock_client = MagicMock()
            mock_client.developerKey = developerKey
            return mock_client

        with patch("youtube_monitor.key_manager.build", side_effect=mock_build):
            mgr = ApiKeyManager(["KEY_ALPHA", "KEY_BETA"])
            # First call: builds client
            c1 = mgr.get_youtube_client("KEY_ALPHA")
            self.assertEqual(len(build_calls), 1)
            self.assertEqual(build_calls[0][2], "KEY_ALPHA")

            # Second call for same key: must return CACHED client, no extra build call
            c2 = mgr.get_youtube_client("KEY_ALPHA")
            self.assertIs(c1, c2)
            self.assertEqual(len(build_calls), 1)

            # Call for second key: builds client for KEY_BETA
            c3 = mgr.get_youtube_client("KEY_BETA")
            self.assertEqual(len(build_calls), 2)
            self.assertEqual(build_calls[1][2], "KEY_BETA")

            # Removing KEY_ALPHA purges cache
            mgr.remove_key("KEY_ALPHA")
            self.assertNotIn("KEY_ALPHA", mgr._client_cache)

    def test_non_destructive_config_merge(self):
        """load_from_config không xóa sạch RAM và bảo lưu trạng thái cooldown/lỗi của key."""
        mgr = ApiKeyManager(["KEY_1", "KEY_2"])
        mgr._keys_map["KEY_1"].status = "RATE_LIMITED"
        mgr._keys_map["KEY_1"].retry_after = time.time() + 60.0
        mgr._keys_map["KEY_1"].consecutive_errors = 3
        mgr._keys_map["KEY_1"].last_error_message = "Rate limited"

        # Reload từ config chỉ chứa danh sách tên key phẳng
        cfg = {"api_keys": ["KEY_1", "KEY_2", "KEY_3"]}
        mgr.load_from_config(cfg)

        # KEY_1 vẫn giữ nguyên trạng thái RATE_LIMITED và consecutive_errors = 3
        self.assertEqual(mgr._keys_map["KEY_1"].status, "RATE_LIMITED")
        self.assertEqual(mgr._keys_map["KEY_1"].consecutive_errors, 3)
        self.assertEqual(mgr._keys_map["KEY_1"].last_error_message, "Rate limited")
        # KEY_3 mới được thêm vào trạng thái ACTIVE
        self.assertIn("KEY_3", mgr._keys_map)
        self.assertEqual(mgr._keys_map["KEY_3"].status, "ACTIVE")

    def test_bad_request_does_not_mark_key_invalid(self):
        """Lỗi 400 badRequest (lỗi tham số caller) không bị đánh đồng là INVALID key."""
        mgr = ApiKeyManager(["KEY_PARAM_TEST"])
        resp = httplib2.Response({"status": 400})
        content = b'{"error": {"errors": [{"reason": "badRequest", "message": "Invalid channel id format"}]}}'
        exc = HttpError(resp, content)

        err_type = mgr.handle_api_error("KEY_PARAM_TEST", exc)
        self.assertEqual(err_type, "BAD_REQUEST")
        self.assertEqual(mgr._keys_map["KEY_PARAM_TEST"].status, "ACTIVE")

    def test_execute_with_pool_key_rotation_on_quota(self):
        """execute_with_pool tự động xoay sang key kế tiếp khi key đầu tiên hết quota."""
        mgr = ApiKeyManager(["KEY_QUOTA", "KEY_OK"])

        resp_403 = httplib2.Response({"status": 403})
        content_403 = b'{"error": {"errors": [{"reason": "quotaExceeded", "message": "Daily limit exceeded"}]}}'
        err_quota = HttpError(resp_403, content_403)

        calls = []

        def mock_builder(client):
            mock_req = MagicMock()
            if getattr(client, "key_id", "") == "KEY_QUOTA":
                mock_req.execute.side_effect = err_quota
            else:
                mock_req.execute.return_value = {"items": [{"id": "UC_SUCCESS"}]}
            calls.append(getattr(client, "key_id", ""))
            return mock_req

        def mock_build(service, version, developerKey=None, cache_discovery=False):
            cl = MagicMock()
            cl.key_id = developerKey
            return cl

        with patch("youtube_monitor.key_manager.build", side_effect=mock_build):
            result = mgr.execute_with_pool(mock_builder, action_desc="Test Quota Rotation")
            self.assertEqual(result, {"items": [{"id": "UC_SUCCESS"}]})
            self.assertEqual(calls, ["KEY_QUOTA", "KEY_OK"])
            # KEY_QUOTA phải bị đánh dấu QUOTA_EXCEEDED
            self.assertEqual(mgr._keys_map["KEY_QUOTA"].status, "QUOTA_EXCEEDED")
            # KEY_OK vẫn ACTIVE
            self.assertEqual(mgr._keys_map["KEY_OK"].status, "ACTIVE")

    def test_execute_with_pool_recovers_from_winerror_10054(self):
        """execute_with_pool bắt WinError 10054, hủy client socket cũ và retry thành công trên cùng key."""
        mgr = ApiKeyManager(["KEY_SOCKET_TEST"])
        calls_count = 0

        def mock_builder(client):
            nonlocal calls_count
            calls_count += 1
            mock_req = MagicMock()
            if calls_count == 1:
                # Lần 1: văng ConnectionResetError 10054
                mock_req.execute.side_effect = ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
            else:
                # Lần 2: kết nối lại socket mới thành công
                mock_req.execute.return_value = {"status": "recovered"}
            return mock_req

        with patch("youtube_monitor.key_manager.build", side_effect=lambda *a, **k: MagicMock()):
            res = mgr.execute_with_pool(mock_builder, action_desc="Test Socket Reset Recovery")
            self.assertEqual(res, {"status": "recovered"})
            self.assertEqual(calls_count, 2)
            self.assertEqual(mgr._keys_map["KEY_SOCKET_TEST"].consecutive_errors, 0)



    def test_invalid_api_key_400_bad_request_marks_key_invalid(self):
        """Google trả về 400 badRequest với message 'API key not valid' phải được phân loại là INVALID."""
        from youtube_monitor.key_manager import _redact_key
        mgr = ApiKeyManager(["KEY_2"])
        resp = httplib2.Response({"status": 400})
        content = b'{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "errors": [{"message": "API key not valid. Please pass a valid API key.", "domain": "global", "reason": "badRequest"}]}}'
        exc = HttpError(resp, content)

        err_type = mgr.handle_api_error("KEY_2", exc)
        self.assertEqual(err_type, "INVALID")
        self.assertEqual(mgr._keys_map["KEY_2"].status, "INVALID")
        # Key không còn khả dụng
        self.assertIsNone(mgr.get_active_key())

    def test_execute_with_pool_key_rotation_on_invalid_key(self):
        """execute_with_pool tự động xoay sang key tiếp theo khi gặp key INVALID do 400 badRequest."""
        mgr = ApiKeyManager(["KEY_BOGUS", "KEY_VALID"])
        resp_400 = httplib2.Response({"status": 400})
        content_400 = b'{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "errors": [{"message": "API key not valid. Please pass a valid API key.", "domain": "global", "reason": "badRequest"}]}}'
        err_invalid = HttpError(resp_400, content_400)

        calls = []

        def mock_builder(client):
            mock_req = MagicMock()
            if getattr(client, "key_id", "") == "KEY_BOGUS":
                mock_req.execute.side_effect = err_invalid
            else:
                mock_req.execute.return_value = {"items": [{"id": "CHANNEL_OK"}]}
            calls.append(getattr(client, "key_id", ""))
            return mock_req

        def mock_build(service, version, developerKey=None, cache_discovery=False):
            cl = MagicMock()
            cl.key_id = developerKey
            return cl

        with patch("youtube_monitor.key_manager.build", side_effect=mock_build):
            result = mgr.execute_with_pool(mock_builder, action_desc="Test Invalid Key Rotation")
            self.assertEqual(result, {"items": [{"id": "CHANNEL_OK"}]})
            self.assertEqual(calls, ["KEY_BOGUS", "KEY_VALID"])
            self.assertEqual(mgr._keys_map["KEY_BOGUS"].status, "INVALID")
            self.assertEqual(mgr._keys_map["KEY_VALID"].status, "ACTIVE")

    def test_feed_session_creation_with_retry_and_adapter(self):
        """_get_feed_session khởi tạo Session với Retry và HTTPAdapter kiểm tra đầy đủ tham số cấu hình."""
        import requests
        import youtube_monitor.core as core_mod
        from youtube_monitor.core import _get_feed_session

        with core_mod._feed_session_lock:
            core_mod._feed_session = None

        session = _get_feed_session()
        self.assertIsInstance(session, requests.Session)
        https_adapter = session.get_adapter("https://")
        http_adapter = session.get_adapter("http://")

        self.assertIsNotNone(https_adapter)
        self.assertIsNotNone(http_adapter)
        retries = https_adapter.max_retries
        self.assertEqual(retries.total, 2)
        self.assertEqual(retries.backoff_factor, 0.5)
        self.assertEqual(set(retries.status_forcelist), {500, 502, 503, 504})
        self.assertFalse(retries.raise_on_status)

    def test_key_redaction_helper(self):
        """_redact_key ẩn an toàn cả query params và key định dạng AIzaSy mà không rò rỉ credential."""
        from youtube_monitor.key_manager import _redact_key
        dummy_key = "AIzaSyDummyTestKeyForRedaction1234567"
        raw = f"Error calling https://youtube.googleapis.com/youtube/v3/playlistItems?key={dummy_key}&alt=json"
        redacted = _redact_key(raw, key=dummy_key)
        self.assertNotIn(dummy_key, redacted)
        self.assertIn("key=[REDACTED]", redacted)

        # Test empty and raw key replacement
        raw_msg = f"Failed on key {dummy_key} due to socket reset"
        redacted_msg = _redact_key(raw_msg, key=dummy_key)
        self.assertNotIn(dummy_key, redacted_msg)
        self.assertIn("...4567", redacted_msg)

    def test_parse_google_http_error_defensive(self):
        """_parse_google_http_error xử lý an toàn malformed JSON, non-dict payloads, không ném exception."""
        from youtube_monitor.key_manager import _parse_google_http_error
        resp = httplib2.Response({"status": 400})
        # 1. Non-JSON string
        exc1 = HttpError(resp, b"<html>Not JSON</html>")
        status, reason, reasons, msgs = _parse_google_http_error(exc1)
        self.assertEqual(status, 400)
        self.assertEqual(reason, "unknown")
        self.assertEqual(reasons, [])

        # 2. Non-dict JSON (e.g. array)
        exc2 = HttpError(resp, b'["error1", "error2"]')
        status, reason, reasons, msgs = _parse_google_http_error(exc2)
        self.assertEqual(status, 400)
        self.assertEqual(reason, "unknown")

        # 3. Non-dict inside errors list
        exc3 = HttpError(resp, b'{"error": {"message": "Invalid", "errors": ["not_a_dict"]}}')
        status, reason, reasons, msgs = _parse_google_http_error(exc3)
        self.assertEqual(status, 400)
        self.assertIn("Invalid", msgs)

    def test_test_key_handles_malformed_json_gracefully(self):
        """test_key xử lý an toàn khi Google trả về HttpError với body không phải JSON (không dính UnboundLocalError)."""
        mgr = ApiKeyManager(["KEY_TEST"])
        resp = httplib2.Response({"status": 500})
        bad_exc = HttpError(resp, b"Server Error Non-JSON")

        with patch("youtube_monitor.key_manager.build") as mock_b:
            mock_yt = MagicMock()
            mock_yt.channels().list().execute.side_effect = bad_exc
            mock_b.return_value = mock_yt
            ok, msg = mgr.test_key("KEY_TEST")
            self.assertFalse(ok)
            self.assertIn("500", msg)


if __name__ == "__main__":
    unittest.main()
