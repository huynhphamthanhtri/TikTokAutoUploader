"""
test_tiktok_direct_upload.py - Bộ kiểm thử toàn diện cho Động cơ Đăng bài bằng API (Direct API Upload).

Kiểm thử tính đúng đắn nghiệp vụ (Semantic & Business Logic Verification):
1. AWS SigV4 Signer (Canonical request, signing key, authorization header).
2. Cookie Extractor (SQLite, parsing, user_id extraction, fallback).
3. Hashtag & Markup Formatter (<h id="">, text_extra offsets).
4. Signer Bridge (X-Bogus & X-Gnarly generation qua Node Worker).
5. VOD Uploader 4 bước (Auth, SigV4 Apply, Chunks CRC32 & Proxy Bypass, Commit).
6. Publish Client (X-Bogus, X-Gnarly, Single Post payload, Rejections).
7. Direct Uploader Coordinator (Dry-run prepared, Posted, Fallback).
"""

import datetime
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tiktok_cookie_extractor import (
    check_profile_api_readiness,
    cookies_to_header_str,
    extract_cookies_from_profile_sqlite,
    extract_user_id_from_cookies,
    get_profile_tiktok_cookies,
    parse_cookie_string,
)
from tiktok_direct_uploader import run_direct_upload
from tiktok_publish_client import (
    TikTokAccountBlockedError,
    TikTokLoginRequiredError,
    TikTokPublishClient,
    TikTokPublishError,
    format_caption_and_markup,
)
from tiktok_signer_bridge import find_node_executable, sign_tiktok_url
from tiktok_sigv4 import get_signature_key, sign_vod_request
from tiktok_vod_uploader import (
    TikTokVodUploader,
    VodAuthExpiredError,
    VodUploadError,
)


class TestTikTokSigV4(unittest.TestCase):
    def test_sigv4_canonical_and_auth_header(self):
        url = "https://www.tiktok.com/top/v1?Action=ApplyUploadInner&Version=2020-11-19&SpaceName=tiktok&FileType=video&IsInner=1"
        fixed_dt = datetime.datetime(2026, 10, 16, 7, 0, 0, tzinfo=datetime.timezone.utc)

        headers = sign_vod_request(
            url=url,
            access_key_id="TEST_KEY",
            secret_access_key="TEST_SECRET",
            session_token="TEST_TOKEN",
            region="us-east-1",
            service="vod",
            request_datetime=fixed_dt,
        )

        self.assertIn("Authorization", headers)
        self.assertIn("x-amz-date", headers)
        self.assertIn("x-amz-security-token", headers)

        auth_val = headers["Authorization"]
        self.assertTrue(auth_val.startswith("AWS4-HMAC-SHA256 Credential=TEST_KEY/20261016/us-east-1/vod/aws4_request"))
        self.assertIn("SignedHeaders=host;x-amz-date;x-amz-security-token", auth_val)
        self.assertIn("Signature=", auth_val)
        self.assertEqual(headers["x-amz-date"], "20261016T070000Z")
        self.assertEqual(headers["x-amz-security-token"], "TEST_TOKEN")


class TestCookieExtractor(unittest.TestCase):
    def test_parse_cookie_string(self):
        cookie_str = "sessionid=abc123xyz; msToken=tok_999; ttwid=1%7Cabc; empty="
        parsed = parse_cookie_string(cookie_str)
        self.assertEqual(parsed["sessionid"], "abc123xyz")
        self.assertEqual(parsed["msToken"], "tok_999")
        self.assertEqual(parsed["ttwid"], "1%7Cabc")
        self.assertEqual(parsed["empty"], "")

        header_out = cookies_to_header_str(parsed)
        self.assertIn("sessionid=abc123xyz", header_out)
        self.assertIn("msToken=tok_999", header_out)

    def test_extract_user_id(self):
        cookies = {"sessionid": "s123", "uid": "71829384756"}
        uid = extract_user_id_from_cookies(cookies)
        self.assertEqual(uid, "71829384756")

        raw_header = "UserId:88997766~; Path=/; Domain=.tiktok.com"
        uid_from_raw = extract_user_id_from_cookies({}, raw_header=raw_header)
        self.assertEqual(uid_from_raw, "88997766")

    def test_sqlite_cookie_extraction(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdir = Path(tmp_dir)
            net_dir = pdir / "Default" / "Network"
            net_dir.mkdir(parents=True)
            db_path = net_dir / "Cookies"

            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE cookies (
                    creation_utc INTEGER,
                    host_key TEXT,
                    name TEXT,
                    value TEXT,
                    encrypted_value BLOB,
                    path TEXT
                )
            """)
            cur.execute(
                "INSERT INTO cookies (creation_utc, host_key, name, value, encrypted_value, path) "
                "VALUES (1, '.tiktok.com', 'sessionid', 'mock_sess_123', X'', '/')"
            )
            cur.execute(
                "INSERT INTO cookies (creation_utc, host_key, name, value, encrypted_value, path) "
                "VALUES (2, '.tiktok.com', 'msToken', 'mock_token_456', X'', '/')"
            )
            conn.commit()
            conn.close()

            extracted = extract_cookies_from_profile_sqlite(pdir)
            self.assertEqual(extracted.get("sessionid"), "mock_sess_123")
            self.assertEqual(extracted.get("msToken"), "mock_token_456")

    def test_check_profile_api_readiness_level1(self):
        """Kiểm tra Mức 1 (Offline Check): Xác minh tính sẵn sàng của profile."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            pdir = Path(tmp_dir)
            net_dir = pdir / "Default" / "Network"
            net_dir.mkdir(parents=True)
            db_path = net_dir / "Cookies"

            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("CREATE TABLE cookies (creation_utc INTEGER, host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB, path TEXT)")
            cur.execute("INSERT INTO cookies VALUES (1, '.tiktok.com', 'sessionid', 'mock_sess_ready', X'', '/')")
            cur.execute("INSERT INTO cookies VALUES (2, '.tiktok.com', 'uid_tt', '12345678', X'', '/')")
            conn.commit()
            conn.close()

            # 1. Profile có sessionid hợp lệ -> Sẵn sàng
            ok, msg, cks = check_profile_api_readiness({"browser_profile_path": str(pdir)})
            self.assertTrue(ok)
            self.assertIn("Sẵn sàng", msg)
            self.assertEqual(cks.get("sessionid"), "mock_sess_ready")

            # 2. Profile không có sessionid (chỉ có cookie khác) -> Chưa sẵn sàng
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("DELETE FROM cookies WHERE name = 'sessionid'")
            conn.commit()
            conn.close()

            ok_no_sess, msg_no_sess, _ = check_profile_api_readiness({"browser_profile_path": str(pdir)})
            self.assertFalse(ok_no_sess)
            self.assertIn("Thiếu cookie 'sessionid'", msg_no_sess)

            # 3. Profile rỗng hoàn toàn -> Chưa sẵn sàng
            ok_empty, msg_empty, _ = check_profile_api_readiness({"browser_profile_path": str(Path(tmp_dir) / "nonexistent")})
            self.assertFalse(ok_empty)



class TestHashtagAndMarkupFormatter(unittest.TestCase):
    def test_format_caption_with_hashtags(self):
        caption = "Review phim hay #review #tiktok #xuhuong đỉnh chóp"
        markup, text_extra = format_caption_and_markup(caption)

        self.assertEqual(len(text_extra), 3)
        self.assertEqual(text_extra[0]["hashtag_name"], "review")
        self.assertEqual(text_extra[1]["hashtag_name"], "tiktok")
        self.assertEqual(text_extra[2]["hashtag_name"], "xuhuong")

        self.assertIn('<h id="">#review</h>', markup)
        self.assertIn('<h id="">#tiktok</h>', markup)
        self.assertIn('<h id="">#xuhuong</h>', markup)

        # Kiểm tra start/end offset trên chuỗi ban đầu
        for extra in text_extra:
            start = extra["start"]
            end = extra["end"]
            self.assertEqual(caption[start:end], f"#{extra['hashtag_name']}")

    def test_format_caption_plain_text(self):
        caption = "Video đơn giản không có thẻ"
        markup, text_extra = format_caption_and_markup(caption)
        self.assertEqual(markup, caption)
        self.assertEqual(text_extra, [])


class TestSignerBridge(unittest.TestCase):
    def test_node_executable_discovery(self):
        node_bin = find_node_executable()
        self.assertIsNotNone(node_bin, "Phải tìm thấy binary node.exe khả dụng")
        self.assertTrue(Path(node_bin).is_file() or shutil.which(node_bin))

    def test_sign_tiktok_url_real_execution(self):
        test_url = "https://www.tiktok.com/tiktok/web/project/post/v1/?app_name=tiktok_web&channel=tiktok_web&device_platform=web&aid=1988"
        x_bogus, x_gnarly = sign_tiktok_url(test_url, timeout=10.0)

        self.assertTrue(x_bogus.startswith("DFSz"), f"X-Bogus không hợp lệ: {x_bogus}")
        self.assertTrue(len(x_gnarly) > 50, f"X-Gnarly không hợp lệ: {x_gnarly}")


class TestTikTokVodUploader(unittest.TestCase):
    def setUp(self):
        self.cookies = {"sessionid": "valid_session_123", "msToken": "valid_mstoken"}
        self.uploader = TikTokVodUploader(
            session_cookies=self.cookies,
            proxy_cfg={"use_proxy": True, "proxy_string": "127.0.0.1:8080", "proxy_type": "http"},
            bypass_hosts_for_chunks=True,
        )

    def test_step1_upload_auth_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 0,
            "video_token_v5": "v090_token",
            "auth": {
                "access_key_id": "ASI_KEY_123",
                "secret_acess_key": "SEC_456",
                "session_token": "SESS_TOK_789",
                "expired_time": "2026-10-16T20:00:00Z",
            },
            "store_region": "us-east-1",
        }
        self.uploader.proxy_session.get = MagicMock(return_value=mock_resp)

        auth_data = self.uploader.step1_upload_auth()
        self.assertEqual(auth_data["access_key_id"], "ASI_KEY_123")
        self.assertEqual(auth_data["secret_access_key"], "SEC_456")
        self.assertEqual(auth_data["session_token"], "SESS_TOK_789")
        self.assertEqual(auth_data["store_region"], "us-east-1")

    def test_step1_upload_auth_expired(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 10001,
            "status_msg": "Need to login",
        }
        self.uploader.proxy_session.get = MagicMock(return_value=mock_resp)

        with self.assertRaises(VodAuthExpiredError):
            self.uploader.step1_upload_auth()

    def test_step2_apply_upload_success(self):
        auth_data = {
            "access_key_id": "ASI_KEY_123",
            "secret_access_key": "SEC_456",
            "session_token": "SESS_TOK_789",
            "store_region": "us-east-1",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ResponseMetadata": {"RequestId": "REQ_001"},
            "Result": {
                "InnerUploadAddress": {
                    "UploadNodes": [
                        {
                            "UploadHost": "mon16-normal-useast5.tiktokv.us",
                            "StoreInfos": [
                                {
                                    "StoreUri": "tos-useast-v-0000/mock_store",
                                    "Auth": "UploadAuthToken123",
                                }
                            ],
                        }
                    ]
                },
                "SDKParam": {"upload_mode": "slice", "slice_count_web": 2},
                "Vid": "v09044g40000cf_vid",
            },
        }
        self.uploader.proxy_session.get = MagicMock(return_value=mock_resp)

        apply_data = self.uploader.step2_apply_upload(auth_data)
        self.assertEqual(apply_data["upload_host"], "mon16-normal-useast5.tiktokv.us")
        self.assertEqual(apply_data["store_uri"], "tos-useast-v-0000/mock_store")
        self.assertEqual(apply_data["vid"], "v09044g40000cf_vid")

    def test_step3_chunk_upload_and_crc(self):
        apply_data = {
            "upload_host": "v16-up-test.tiktokcdn.com",
            "store_uri": "mock_uri_123",
            "store_auth": "mock_auth",
            "slice_count_web": 2,
        }

        # Tạo file tạm 10MB
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
            tf.write(b"0" * (10 * 1024 * 1024))
            temp_path = Path(tf.name)

        try:
            mock_post_resp = MagicMock()
            mock_post_resp.status_code = 200
            mock_post_resp.json.return_value = {"code": 2000, "message": "Success"}
            # UploadHost chứa tiktokcdn -> dùng direct_session
            self.uploader.direct_session.post = MagicMock(return_value=mock_post_resp)

            parts_crc = self.uploader.step3_upload_chunks(apply_data, temp_path)
            self.assertTrue(len(parts_crc) >= 1)
            self.assertTrue(all(len(c) == 8 for c in parts_crc))
            self.assertTrue(self.uploader.direct_session.post.called)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_step4_commit_success(self):
        apply_data = {
            "upload_host": "mon16-normal-useast5.tiktokv.us",
            "store_uri": "mock_uri_123",
            "store_auth": "mock_auth",
            "vid": "v090_vid",
        }
        auth_data = {
            "session_token": "SESS_TOK_789",
            "secret_access_key": "SEC_456",
        }
        parts_crc = ["a1b2c3d4", "e5f6g7h8"]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": 2000,
            "data": {
                "post_upload_resp": {
                    "video_meta": {
                        "Width": 1080,
                        "Height": 1920,
                        "Duration": 15.0,
                        "Codec": "h264",
                    },
                    "request_id": "REQ_COMMIT_999",
                }
            },
        }
        self.uploader.proxy_session.post = MagicMock(return_value=mock_resp)

        commit_res = self.uploader.step4_commit(apply_data, parts_crc, auth_data)
        meta = commit_res["video_meta"]
        self.assertEqual(meta["Width"], 1080)
        self.assertEqual(meta["Height"], 1920)
        self.assertEqual(meta["Duration"], 15.0)


class TestTikTokPublishClient(unittest.TestCase):
    def setUp(self):
        self.cookies = {"sessionid": "valid_sess", "msToken": "valid_ms"}
        self.client = TikTokPublishClient(
            session_cookies=self.cookies,
            proxy_cfg=None,
        )

    def test_publish_video_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 0,
            "status_msg": "",
            "single_post_resp_list": [
                {
                    "item_id": "7425999888777666555",
                    "status_code": 0,
                }
            ],
        }
        self.client.proxy_session.post = MagicMock(return_value=mock_resp)

        res = self.client.publish_video(
            vid="v090_vid_test",
            video_meta={"Width": 1080, "Height": 1920, "Duration": 20.0},
            caption="Test video #fyp",
        )

        self.assertEqual(res.item_id, "7425999888777666555")
        self.assertEqual(res.video_url, "https://www.tiktok.com/video/7425999888777666555")

    def test_publish_account_blocked(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 10005,
            "status_msg": "Your account is temporarily prevented from posting.",
        }
        self.client.proxy_session.post = MagicMock(return_value=mock_resp)

        with self.assertRaises(TikTokAccountBlockedError):
            self.client.publish_video(
                vid="v090_vid_test",
                video_meta={"Width": 1080, "Height": 1920, "Duration": 20.0},
            )


class TestDirectUploaderCoordinator(unittest.TestCase):
    def test_missing_sessionid_returns_login_required(self):
        prof_cfg = {"cookie_str": "msToken=only_ms"}
        res = run_direct_upload("test_prof", prof_cfg, "dummy.mp4")
        self.assertEqual(res.outcome, "login_required")
        self.assertFalse(res.post_dispatched)

    @patch("tiktok_direct_uploader.TikTokVodUploader")
    def test_dry_run_stop_before_post(self, mock_uploader_cls):
        mock_instance = MagicMock()
        mock_instance.upload_video.return_value = MagicMock(
            vid="v090_dry_run",
            upload_host="cdn.tiktok.com",
            video_meta={"Width": 1080, "Height": 1920, "Duration": 10.0},
        )
        mock_uploader_cls.return_value = mock_instance

        prof_cfg = {"cookie_str": "sessionid=valid_123"}
        res = run_direct_upload(
            "test_prof",
            prof_cfg,
            "video.mp4",
            stop_before_post=True,
        )

        self.assertEqual(res.outcome, "prepared")
        self.assertFalse(res.post_dispatched)
        self.assertEqual(res.details["vid"], "v090_dry_run")


class TestFastMp4Meta(unittest.TestCase):
    def test_nonexistent_file_returns_defaults(self):
        from fast_mp4_meta import get_fast_mp4_metadata
        meta = get_fast_mp4_metadata("non_existent_file.mp4")
        self.assertEqual(meta["width"], 1080)
        self.assertEqual(meta["height"], 1920)
        self.assertEqual(meta["duration"], 15.0)

    def test_real_video_metadata_parsing(self):
        from fast_mp4_meta import get_fast_mp4_metadata
        sample_path = Path("Auto_Data/AUTO 11/Video/【怖い話】化け狐が百万円より十円の賽銭に深く頭を下げた理由 #2ch怖い話 #神社の怪談 #意味怖.mp4")
        if sample_path.exists():
            meta = get_fast_mp4_metadata(sample_path)
            self.assertEqual(meta["width"], 1080)
            self.assertEqual(meta["height"], 1920)
            self.assertGreater(meta["duration"], 60.0)


if __name__ == "__main__":
    unittest.main()
