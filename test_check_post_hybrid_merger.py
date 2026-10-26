# -*- coding: utf-8 -*-
"""
test_check_post_hybrid_merger.py - Kiểm thử toàn diện cơ chế Hybrid Data Merger & Cookie Formatter cho F2 Check Post.
Tuân thủ AGENTS.md:
- Kiểm chứng logic nghiệp vụ thực tế (Semantic Verification).
- Tái hiện chính xác schema của TikTok Creator Studio & Feed API trên profile NEW 64.
- Đảm bảo nhận diện chính xác 7 video trùng / 25 video như thực tế F3.
- Kiểm tra phân trang và bộ nhớ đệm (cache) khi enrich group_id từ Feed.
- Kiểm tra trạng thái duplicate_status bảo đảm tính chân thực ngữ nghĩa (không gán bừa original khi thiếu metadata).
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from check_features.clients.studio_post_client import (
    TikTokStudioPostClient,
    format_cookie_header,
)
from check_features.models import AwemePost, PenaltyDetails
from check_features.ui.check_post_dialog import CheckPostDialog


class TestTikTokStudioPostClientHybridMerger(unittest.TestCase):
    """Kiểm thử chuyên sâu cho bộ chuyển đổi Cookie và cơ chế Hybrid Data Merger."""

    def test_format_cookie_header_various_formats(self):
        """Kiểm tra format_cookie_header xử lý đúng tất cả định dạng cookie (JSON list, JSON dict, list dict, raw string)."""
        # 1. Raw string thông thường
        raw = "sessionid=abc123xyz; ttwid=def456;"
        self.assertEqual(format_cookie_header(raw), raw)

        # 2. JSON string dạng list dict (định dạng trong configs.json của profile NEW 64)
        json_list_str = json.dumps([
            {"name": "sessionid", "value": "sess_new64_val", "domain": ".tiktok.com"},
            {"name": "ttwid", "value": "ttwid_new64_val", "domain": ".tiktok.com"},
            {"name": "csrf_session_id", "value": "csrf_val", "domain": ".tiktok.com"},
        ])
        formatted = format_cookie_header(json_list_str)
        self.assertIn("sessionid=sess_new64_val", formatted)
        self.assertIn("ttwid=ttwid_new64_val", formatted)
        self.assertIn("csrf_session_id=csrf_val", formatted)
        self.assertTrue(formatted.startswith("sessionid=") or ";" in formatted)

        # 3. List of dicts (Python object)
        py_list = [
            {"name": "passport_csrf_token", "value": "token_123"},
            {"name": "sid_guard", "value": "guard_456"},
        ]
        self.assertEqual(
            format_cookie_header(py_list),
            "passport_csrf_token=token_123; sid_guard=guard_456",
        )

        # 4. JSON string dạng dict key-value
        json_dict_str = json.dumps({"sessionid": "sess_dict", "uid_tt": "uid_val"})
        dict_formatted = format_cookie_header(json_dict_str)
        self.assertIn("sessionid=sess_dict", dict_formatted)
        self.assertIn("uid_tt=uid_val", dict_formatted)

        # 5. Dict (Python object)
        py_dict = {"cookie_a": "val_a", "cookie_b": "val_b"}
        self.assertEqual(format_cookie_header(py_dict), "cookie_a=val_a; cookie_b=val_b")

        # 6. Rỗng hoặc None
        self.assertEqual(format_cookie_header(""), "")
        self.assertEqual(format_cookie_header(None), "")

    def test_hybrid_enrich_group_id_identifies_7_duplicates_of_25_videos(self):
        """
        Kiểm tra cơ chế Hybrid Data Merger:
        Giả lập 25 video từ Creator Studio (Studio item_list API không có group_id),
        sau đó enrich từ feed data có đúng 7 video bị đánh dấu group_id khác chính nó.
        """
        config = {
            "tiktok_id": "@user2513527685201",
            "sec_uid": "MS4wLjABAAAApLcY3iOIQ71ec8IqJ-yO7Ik3HwbnBt3p4UksSAXi-bVI_7YG1fxxwArv7KxU43-V",
            "cookie_str": json.dumps([{"name": "sessionid", "value": "mock_sess"}]),
        }
        client = TikTokStudioPostClient(config)

        # Tạo danh sách 25 AwemePost từ Creator Studio (group_id = "")
        posts = []
        feed_aweme_list = []
        dup_indices = {1, 2, 3, 4, 6, 7, 10}  # 7 bài trùng giống hình thực tế profile NEW 64

        for i in range(25):
            aid = f"74500000000000000{i:02d}"
            # Studio item
            posts.append(
                AwemePost(
                    aweme_id=aid,
                    desc=f"Video test {i}",
                    create_time=1710000000 + i * 3600,
                    data_source="creator_studio",
                    group_id="",  # Ban đầu Studio trả rỗng
                )
            )

            # Feed item tương ứng
            if i in dup_indices:
                gid = f"74499999999999999{i:02d}"  # group_id khác aid => Trùng
            else:
                gid = aid  # group_id trùng aid => Gốc
            feed_aweme_list.append({"aweme_id": aid, "group_id": gid})

        # Mock request get aweme/v1/aweme/post/
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"aweme_list": feed_aweme_list, "status_code": 0, "has_more": 0}

        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            client._enrich_posts_with_feed_group_id(posts, config["sec_uid"], count=25)

        # Kiểm chứng kết quả sau enrich
        dup_posts = [p for p in posts if p.is_dup]
        original_posts = [p for p in posts if not p.is_dup]

        self.assertEqual(len(dup_posts), 7, "Phải nhận diện chính xác đúng 7 video trùng lặp!")
        self.assertEqual(len(original_posts), 18, "Phải nhận diện chính xác 18 video gốc!")

        for p in dup_posts:
            self.assertEqual(p.duplicate_status, "duplicate")
            self.assertEqual(p.data_source, "hybrid")
            self.assertNotEqual(p.group_id, p.aweme_id)
            self.assertTrue(len(p.group_id) > 10)

        for p in original_posts:
            self.assertEqual(p.duplicate_status, "original")
            self.assertEqual(p.data_source, "hybrid")

    def test_enrich_feed_pagination_and_cache(self):
        """
        Kiểm tra cơ chế duyệt phân trang và cache khi enrich group_id:
        - Video ở trang 2 của Studio được đối soát qua trang 2 của Feed.
        - Lần gọi tiếp theo tái sử dụng cache mà không gửi thêm request.
        """
        config = {
            "sec_uid": "MS4wLjABAAAA_test_pagination_sec_uid",
            "cookie_str": "sessionid=test_cache_sess;",
        }
        client = TikTokStudioPostClient(config)

        # 2 posts ở 2 trang khác nhau
        post1 = AwemePost(aweme_id="7100000000000001007", group_id="")
        post2 = AwemePost(aweme_id="7100000000000001008", group_id="")

        # Trang 1 chứa post1, có has_more=1, max_cursor=20
        resp_p1 = MagicMock()
        resp_p1.status_code = 200
        resp_p1.json.return_value = {
            "aweme_list": [{"aweme_id": "7100000000000001007", "group_id": "7100000000000001007"}],
            "has_more": 1,
            "max_cursor": 20,
        }

        # Trang 2 chứa post2, có has_more=0
        resp_p2 = MagicMock()
        resp_p2.status_code = 200
        resp_p2.json.return_value = {
            "aweme_list": [{"aweme_id": "7100000000000001008", "group_id": "7200000000000002002"}],
            "has_more": 0,
            "max_cursor": 0,
        }

        with patch.object(client.session, "get", side_effect=[resp_p1, resp_p2]) as mock_get:
            client._enrich_posts_with_feed_group_id([post1, post2], config["sec_uid"], count=20)

            # Phải gọi 2 lần mạng để lấy hết cả 2 trang
            self.assertEqual(mock_get.call_count, 2)
            self.assertEqual(post1.group_id, "7100000000000001007")
            self.assertEqual(post2.group_id, "7200000000000002002")
            self.assertTrue(post2.is_dup)

            # Lần gọi tiếp theo với post1: kiểm tra dùng cache (không gửi thêm request)
            post1_again = AwemePost(aweme_id="7100000000000001007", group_id="")
            client._enrich_posts_with_feed_group_id([post1_again], config["sec_uid"], count=20)
            self.assertEqual(mock_get.call_count, 2, "Phải sử dụng cache, không gọi thêm mạng!")
            self.assertEqual(post1_again.group_id, "7100000000000001007")

    def test_refresh_clears_negative_cache_and_recovers_newly_available_metadata(self):
        """
        Regression Test (theo phản hồi của Codex Review vòng 5):
        Đảm bảo xóa Negative Cache khi Refresh:
        - Lượt 1: Feed item ban đầu thiếu group_id -> được lưu vào negative cache scanned_ids, trạng thái 'unknown'.
        - Lượt 2: Khi refresh (reset_pagination=True), scanned_ids bị xóa, Feed đã có group_id -> client
          gọi lại mạng và cập nhật thành công group_id cùng trạng thái 'duplicate'.
        """
        client = TikTokStudioPostClient({})
        sec_uid = "MS4wLjABAAAA_test_negative_cache_recovery"
        post = AwemePost(aweme_id="7100000000000001003", group_id="")

        # Lượt 1: Feed chưa có group_id
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "aweme_list": [{"aweme_id": "7100000000000001003"}],
            "has_more": 0,
            "max_cursor": 0,
        }

        with patch.object(client.session, "get", return_value=resp1) as mock_get_1:
            client._enrich_posts_with_feed_group_id([post], sec_uid, count=10)
            self.assertEqual(mock_get_1.call_count, 1)
            self.assertEqual(post.group_id, "")
            self.assertEqual(post.duplicate_status, "unknown")
            self.assertIn("7100000000000001003", client._feed_states[sec_uid]["scanned_ids"])

        # Lượt 2: Refresh với reset_pagination=True, Feed đã cập nhật group_id
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "aweme_list": [{"aweme_id": "7100000000000001003", "group_id": "7200000000000002006"}],
            "has_more": 0,
            "max_cursor": 0,
        }

        with patch.object(client.session, "get", return_value=resp2) as mock_get_2:
            client._enrich_posts_with_feed_group_id([post], sec_uid, count=10, reset_pagination=True)
            self.assertEqual(mock_get_2.call_count, 1, "Phải quét lại mạng sau khi xóa negative cache!")
            self.assertEqual(post.group_id, "7200000000000002006")
            self.assertTrue(post.is_dup)
            self.assertEqual(post.duplicate_status, "duplicate")

    def test_refresh_exhausted_account_allows_enriching_newly_published_post(self):
        """
        Regression Test (theo phản hồi của Codex Review vòng 4):
        Đảm bảo vòng đời làm mới dữ liệu (Refresh Lifecycle):
        - Quét tài khoản A đến khi exhausted=True.
        - Sau đó tài khoản A đăng thêm video mới.
        - Khi người dùng bấm Tải Lại / lượt quét mới (reset_pagination=True), client reset cursor=0
          và tải thành công group_id của video mới, không bị chặn bởi cờ exhausted cũ.
        """
        client = TikTokStudioPostClient({})
        sec_uid = "MS4wLjABAAAA_test_refresh_lifecycle"

        # Lượt 1: Quét đến khi kiệt feed
        post_old = AwemePost(aweme_id="7100000000000001006", group_id="")
        resp_exhaust = MagicMock()
        resp_exhaust.status_code = 200
        resp_exhaust.json.return_value = {
            "aweme_list": [{"aweme_id": "7100000000000001006", "group_id": "7100000000000001006"}],
            "has_more": 0,
            "max_cursor": 0,
        }

        with patch.object(client.session, "get", return_value=resp_exhaust):
            client._enrich_posts_with_feed_group_id([post_old], sec_uid, count=10)
            self.assertTrue(client._feed_states[sec_uid]["exhausted"])
            self.assertEqual(post_old.duplicate_status, "original")

        # Lượt 2: Có video mới vừa đăng "7100000000000001005" (là video trùng)
        post_new = AwemePost(aweme_id="7100000000000001005", group_id="")
        resp_new = MagicMock()
        resp_new.status_code = 200
        resp_new.json.return_value = {
            "aweme_list": [{"aweme_id": "7100000000000001005", "group_id": "7200000000000002005"}],
            "has_more": 0,
            "max_cursor": 0,
        }

        with patch.object(client.session, "get", return_value=resp_new) as mock_get_refresh:
            # Bắt đầu lượt quét mới (reset_pagination=True)
            client._enrich_posts_with_feed_group_id([post_new], sec_uid, count=10, reset_pagination=True)
            
            # Phải gọi mạng từ đầu (max_cursor=0) để lấy bài mới
            self.assertEqual(mock_get_refresh.call_count, 1)
            called_args, called_kwargs = mock_get_refresh.call_args
            self.assertEqual(called_kwargs["params"]["max_cursor"], 0)
            self.assertEqual(post_new.group_id, "7200000000000002005")
            self.assertTrue(post_new.is_dup)
            self.assertEqual(post_new.duplicate_status, "duplicate")

    def test_account_isolation_exhausting_account_a_does_not_affect_account_b(self):
        """
        Regression Test (theo phản hồi của Codex Review vòng 3):
        Đảm bảo tính cô lập tài khoản (Account Isolation):
        - Khi tài khoản A kiệt feed (exhausted=True), tài khoản B vẫn bắt đầu từ max_cursor=0 và fetch bình thường.
        - Cache của tài khoản A không bị rò rỉ sang tài khoản B.
        """
        client = TikTokStudioPostClient({})

        post_a = AwemePost(aweme_id="7100000000000001001", group_id="")
        post_b = AwemePost(aweme_id="7100000000000001002", group_id="")

        sec_a = "MS4wLjABAAAA_acc_A"
        sec_b = "MS4wLjABAAAA_acc_B"

        resp_a = MagicMock()
        resp_a.status_code = 200
        # Tài khoản A kiệt feed ngay từ đầu
        resp_a.json.return_value = {"aweme_list": [], "has_more": 0, "max_cursor": 0}

        resp_b = MagicMock()
        resp_b.status_code = 200
        # Tài khoản B có video trùng
        resp_b.json.return_value = {
            "aweme_list": [{"aweme_id": "7100000000000001002", "group_id": "7200000000000002001"}],
            "has_more": 0,
            "max_cursor": 0,
        }

        with patch.object(client.session, "get", side_effect=[resp_a, resp_b]) as mock_get:
            # 1. Gọi Account A
            client._enrich_posts_with_feed_group_id([post_a], sec_a, count=10)
            self.assertTrue(client._feed_states[sec_a]["exhausted"])

            # 2. Gọi Account B: Phải gọi mạng với cursor 0, không bị exhausted của Account A chặn
            client._enrich_posts_with_feed_group_id([post_b], sec_b, count=10)
            self.assertEqual(mock_get.call_count, 2)
            
            # Kiểm tra tham số gọi cho Account B
            called_args, called_kwargs = mock_get.call_args_list[1]
            self.assertEqual(called_kwargs["params"]["sec_user_id"], sec_b)
            self.assertEqual(called_kwargs["params"]["max_cursor"], 0)
            self.assertEqual(post_b.group_id, "7200000000000002001")
            self.assertTrue(post_b.is_dup)

    def test_enrich_feed_item_without_group_id_remains_unknown_and_cached(self):
        """
        Regression Test (theo phản hồi của Codex Review):
        Khi item trong Feed không có group_id (rỗng/None):
        - post.group_id phải giữ nguyên rỗng, KHÔNG được tự động gán bằng aweme_id.
        - post.duplicate_status phải là 'unknown' (không được báo giả 'original').
        - Client lưu vào _feed_scanned_ids để lần sau không quét lại mạng, nhưng vẫn giữ đúng trạng thái 'unknown'.
        """
        config = {
            "sec_uid": "MS4wLjABAAAA_test_no_gid_sec_uid",
            "cookie_str": "sessionid=test_no_gid_sess;",
        }
        client = TikTokStudioPostClient(config)

        post = AwemePost(aweme_id="7100000000000001011", group_id="")

        resp = MagicMock()
        resp.status_code = 200
        # Feed trả về item nhưng hoàn toàn không có group_id
        resp.json.return_value = {
            "aweme_list": [{"aweme_id": "7100000000000001011", "desc": "No group ID here"}],
            "has_more": 0,
            "max_cursor": 0,
        }

        with patch.object(client.session, "get", return_value=resp) as mock_get:
            client._enrich_posts_with_feed_group_id([post], config["sec_uid"], count=10)

            self.assertEqual(mock_get.call_count, 1)
            self.assertEqual(post.group_id, "")
            self.assertEqual(post.duplicate_status, "unknown")
            self.assertFalse(post.is_dup)
            self.assertEqual(post.data_source, "hybrid")

            # Gọi lại lần 2: phải nhận diện là đã scan (không gọi lại mạng) và vẫn là 'unknown'
            post2 = AwemePost(aweme_id="7100000000000001011", group_id="")
            client._enrich_posts_with_feed_group_id([post2], config["sec_uid"], count=10)
            self.assertEqual(mock_get.call_count, 1, "Phải sử dụng scanned cache, không gọi mạng!")
            self.assertEqual(post2.group_id, "")
            self.assertEqual(post2.duplicate_status, "unknown")

    def test_duplicate_status_semantic_truth_no_false_original(self):
        """
        Kiểm tra duplicate_status tuân thủ nghiêm ngặt tính đúng đắn ngữ nghĩa (Semantic Verification):
        - Có group_id khác aweme_id -> 'duplicate'
        - Có group_id bằng aweme_id -> 'original'
        - Thiếu group_id hoặc '0' -> 'unknown' (nghiêm cấm tự kết luận 'original' khi thiếu metadata)
        """
        # 1. Trùng lặp rõ ràng
        p_dup = AwemePost(aweme_id="7450000000000000001", group_id="7449999999999999999")
        self.assertEqual(p_dup.duplicate_status, "duplicate")
        self.assertTrue(p_dup.is_dup)

        # 2. Khẳng định gốc
        p_orig = AwemePost(aweme_id="7450000000000000001", group_id="7450000000000000001")
        self.assertEqual(p_orig.duplicate_status, "original")
        self.assertFalse(p_orig.is_dup)

        # 3. Thiếu metadata group_id (kể cả nguồn feed hay mobile_api)
        p_missing_gid = AwemePost(aweme_id="7450000000000000001", group_id="", data_source="channel_feed")
        self.assertEqual(p_missing_gid.duplicate_status, "unknown")
        self.assertFalse(p_missing_gid.is_dup)

        p_zero_gid = AwemePost(aweme_id="7450000000000000001", group_id="0", data_source="hybrid")
        self.assertEqual(p_zero_gid.duplicate_status, "unknown")
        self.assertFalse(p_zero_gid.is_dup)

    def test_fetch_posts_graceful_fallback_when_studio_session_expired(self):
        """
        Kiểm tra cơ chế Graceful Fallback khi Creator Studio báo session expired (status_code: 8):
        - Hệ thống tự động fallback qua Public Channel Feed.
        - Giữ nguyên được danh sách video và cờ trùng lặp.
        - Trạng thái shadowban thể hiện 'unknown' với lý do rõ ràng.
        """
        config = {
            "tiktok_id": "user2513527685201",
            "sec_uid": "MS4wLjABAAAApLcY3iOIQ71ec8IqJ-yO7Ik3HwbnBt3p4UksSAXi-bVI_7YG1fxxwArv7KxU43-V",
            "cookie_str": "sessionid=expired_token;",
        }
        client = TikTokStudioPostClient(config)

        # 1. Creator Studio trả status_code: 8
        mock_studio_resp = MagicMock()
        mock_studio_resp.status_code = 200
        mock_studio_resp.json.return_value = {
            "status_code": 8,
            "status_msg": "Login expired",
            "item_list": [],
        }

        # 2. Web Feed & Mobile API trả rỗng hoặc lỗi để test tới nhánh F3 PublicChannelClient
        mock_feed_resp = MagicMock()
        mock_feed_resp.status_code = 200
        mock_feed_resp.json.return_value = {"itemList": []}

        # 3. PublicChannelClient trả về 25 video với 7 bài trùng
        fallback_posts = []
        for i in range(25):
            aid = f"74500000000000000{i:02d}"
            gid = f"74499999999999999{i:02d}" if i < 7 else aid
            fallback_posts.append(
                AwemePost(
                    aweme_id=aid,
                    desc=f"Video FB {i}",
                    group_id=gid,
                    data_source="channel_feed",
                )
            )

        with patch.object(client.session, "post", return_value=mock_studio_resp), \
             patch.object(client.session, "get", return_value=mock_feed_resp), \
             patch("check_features.clients.public_channel_client.TikTokPublicChannelClient.fetch_channel_videos",
                   return_value=(True, fallback_posts, 25, False, "OK")):
            
            ok, posts, cur, has_more, msg = client.fetch_posts(count=25, max_cursor=0)

        self.assertTrue(ok)
        self.assertEqual(len(posts), 25)
        self.assertEqual(sum(1 for p in posts if p.is_dup), 7)
        self.assertIn("Đã lấy 25 bài đăng từ Feed, phát hiện 7 bài trùng", msg)

        # Xác nhận nhãn shadowban khi phiên studio hết hạn
        for p in posts:
            self.assertEqual(p.shadow, "unknown")
            self.assertEqual(p.shadow_reason, "Chưa rõ (Hết phiên Studio)")

    def test_64bit_id_preserved_without_precision_loss(self):
        """Đảm bảo các ID 64-bit của aweme_id và group_id không bao giờ bị chuyển đổi thành float làm mất độ chính xác."""
        large_aid = "7451234567890123456"
        large_gid = "7451234567890123999"

        raw_item = {
            "id": large_aid,
            "group_id": large_gid,
            "desc": "Testing 64-bit ID",
            "stats": {"playCount": 100},
        }

        client = TikTokStudioPostClient({})
        post = client._parse_raw_post(raw_item, username="test_user")

        self.assertEqual(post.aweme_id, large_aid)
        self.assertEqual(post.group_id, large_gid)
        self.assertTrue(post.is_dup)
        self.assertIsInstance(post.aweme_id, str)
        self.assertIsInstance(post.group_id, str)


    def test_parse_raw_post_creator_studio_flat_schema_and_visibility(self):
        """
        Kiểm tra bóc tách toàn diện schema phẳng từ TikTok Creator Studio API:
        - visibility = 1 -> is_private = False (Public)
        - visibility in (3, 4) -> is_private = True (Friends / Only me)
        - cover_url là list các URLs -> bóc tách URL đầu tiên
        - download_urls trong download_info là list -> bóc tách URL đầu tiên
        - play_addr là list -> bóc tách URL đầu tiên
        - duration, post_time, play_count, like_count được chuyển đổi an toàn
        """
        client = TikTokStudioPostClient({})

        # 1. Video công khai (visibility == 1)
        raw_public = {
            "item_id": "7698579332456877334",
            "desc": "Public video test",
            "visibility": 1,
            "post_time": 1792465224,
            "duration": 62443,  # ms
            "play_count": "87",
            "like_count": "15",
            "comment_count": "3",
            "share_count": "2",
            "favorite_count": "5",
            "cover_url": [
                "https://p16-common-sign.tiktokcdn-eu.com/cover1.jpg",
                "https://p19-common-sign.tiktokcdn-eu.com/cover2.jpg",
            ],
            "download_info": {
                "allow_download": True,
                "download_urls": [
                    "https://v19-web-newkey.tiktokcdn.com/download_url1.mp4",
                    "https://v16-web-newkey.tiktokcdn.com/download_url2.mp4",
                ]
            },
            "play_addr": [
                "https://v19-web-newkey.tiktokcdn.com/play1.mp4",
                "https://v16-web-newkey.tiktokcdn.com/play2.mp4",
            ],
        }

        post_pub = client._parse_raw_post(raw_public, username="user2513527685201")
        self.assertEqual(post_pub.aweme_id, "7698579332456877334")
        self.assertFalse(post_pub.is_private, "visibility == 1 bắt buộc phải là công khai (is_private=False)!")
        self.assertEqual(post_pub.duration, 62, "Duration ms > 10000 phải được chuyển thành giây (62s)")
        self.assertEqual(post_pub.play, 87)
        self.assertEqual(post_pub.like, 15)
        self.assertEqual(post_pub.cover, "https://p16-common-sign.tiktokcdn-eu.com/cover1.jpg")
        self.assertEqual(post_pub.download_url, "https://v19-web-newkey.tiktokcdn.com/download_url1.mp4")

        # 2. Video bạn bè (visibility == 3)
        raw_friends = {"item_id": "7698000000000000003", "visibility": 3}
        post_fri = client._parse_raw_post(raw_friends, username="test")
        self.assertTrue(post_fri.is_private, "visibility == 3 phải là riêng tư (Friends)")

        # 3. Video chỉ mình tôi (visibility == 4)
        raw_private = {"item_id": "7698000000000000004", "visibility": 4}
        post_priv = client._parse_raw_post(raw_private, username="test")
        self.assertTrue(post_priv.is_private, "visibility == 4 phải là riêng tư (Only me)")

    def test_enrich_feed_group_id_offline_cache_fallback(self):
        """
        Kiểm tra cơ chế đọc cache offline data/feeds/{uname}_mobile.json:
        Khi nguồn mạng bị ngắt hoặc WAF chặn HTML, hệ thống tự động đối soát từ cache local
        để nhận diện đúng video trùng lặp.
        """
        import tempfile
        import os
        from unittest.mock import patch

        config = {
            "tiktok_id": "user_cache_test",
            "sec_uid": "MS4wLjABAAAA_test_offline_cache",
        }
        client = TikTokStudioPostClient(config)

        post1 = AwemePost(aweme_id="7698285893676829974", group_id="")
        post2 = AwemePost(aweme_id="7698579332456877334", group_id="")

        # Tạo file cache giả lập
        cache_data = {
            "aweme_list": [
                {"aweme_id": "7698285893676829974", "group_id": "7681997048069115149"},  # Duplicate
                {"aweme_id": "7698579332456877334", "group_id": "7698579332456877334"},  # Original
            ]
        }

        # Mock os.path.exists và open để đọc cache_data
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(cache_data))):
            client._enrich_posts_with_feed_group_id([post1, post2], config["sec_uid"], username="user_cache_test")

        self.assertEqual(post1.group_id, "7681997048069115149")
        self.assertTrue(post1.is_dup)
        self.assertEqual(post1.duplicate_status, "duplicate")

        self.assertEqual(post2.group_id, "7698579332456877334")
        self.assertFalse(post2.is_dup)
        self.assertEqual(post2.duplicate_status, "original")

    def test_enrich_feed_group_id_handles_html_bot_challenge_gracefully(self):
        """
        Kiểm tra tính an toàn (Resilience) khi endpoint Mobile Feed bị TikTok chặn trả về HTML:
        - Không ném exception JSONDecodeError làm crash ứng dụng.
        - Dừng phân trang êm ái (graceful break).
        - Giữ nguyên danh sách posts từ Studio.
        """
        client = TikTokStudioPostClient({})
        sec_uid = "MS4wLjABAAAA_test_waf_challenge"
        post = AwemePost(aweme_id="7100000000000001010", group_id="")

        # Mock trả về HTML challenge 200 OK
        html_resp = MagicMock()
        html_resp.status_code = 200
        html_resp.text = "<!doctype html><html><head><title>TikTok Verification</title></head><body>Challenge</body></html>"

        with patch.object(client.session, "get", return_value=html_resp) as mock_get:
            # Không được ném Exception
            client._enrich_posts_with_feed_group_id([post], sec_uid, count=10)

        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(post.group_id, "")
        self.assertEqual(post.duplicate_status, "unknown")

    def test_creator_studio_api_payload_and_csrf_header(self):
        """
        Kiểm tra TikTokStudioPostClient gửi đúng 100% payload TikTok Creator Studio:
        - Payload: cursor, size, query với sort_orders [{'field_name': 'post_time', 'order': 2}], is_recent_posts: False
        - Headers: tt-csrf-token được tự động trích xuất từ tt_csrf_token trong Cookie
        """
        config = {
            "tiktok_id": "test_account",
            "cookie_str": "sessionid=sess_123; tt_csrf_token=csrf_token_abc456; ttwid=ttwid_789;",
        }
        client = TikTokStudioPostClient(config)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 0,
            "status_msg": "",
            "item_list": [{"item_id": "7690000000000000001", "desc": "Test"}],
            "has_more": False,
            "cursor": 25,
        }

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            ok, posts, cur, has_more, msg = client.fetch_posts(count=25, max_cursor=0)

        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 1)
        called_args, called_kwargs = mock_post.call_args

        # Kiểm tra endpoint
        self.assertIn("/tiktok/creator/manage/item_list/v1/", called_args[0])

        # Kiểm tra payload
        payload = called_kwargs.get("json") or {}
        self.assertEqual(payload.get("cursor"), 0)
        self.assertEqual(payload.get("size"), 25)
        query = payload.get("query") or {}
        self.assertEqual(query.get("is_recent_posts"), False)
        sort_orders = query.get("sort_orders") or []
        self.assertEqual(sort_orders, [{"field_name": "post_time", "order": 2}])

        # Kiểm tra header tt-csrf-token
        headers = called_kwargs.get("headers") or {}
        self.assertEqual(headers.get("tt-csrf-token"), "csrf_token_abc456")


    def test_offline_cache_does_not_overwrite_live_feed_group_id(self):
        """
        Regression Test (theo phản hồi của Codex Review):
        Khi một video (Post A) đã có group_id từ live feed, và một video khác (Post B) còn unmatched:
        Nếu file cache offline chứa bản ghi cũ/xung đột của Post A, hệ thống TUYỆT ĐỐI KHÔNG
        được ghi đè group_id của Post A bằng giá trị cũ trong cache.
        Chỉ những video thực sự unmatched mới được nạp từ cache.
        """
        from unittest.mock import patch, mock_open

        config = {
            "tiktok_id": "user_conflict_test",
            "sec_uid": "MS4wLjABAAAA_test_conflict_cache",
        }
        client = TikTokStudioPostClient(config)

        # Post A đã được gán group_id từ live feed
        post_a = AwemePost(aweme_id="7100000000000001004", group_id="7200000000000002003", data_source="hybrid")
        # Đưa vào feed_cache trong session
        client._feed_states[config["sec_uid"]] = {
            "cache": {"7100000000000001004": "7200000000000002003"},
            "scanned_ids": {"7100000000000001004"},
            "cursor": 0,
            "exhausted": False,
        }

        # Post B chưa matched
        post_b = AwemePost(aweme_id="7100000000000001009", group_id="")

        # Cache offline chứa giá trị sai/cũ cho Post A và giá trị đúng cho Post B
        stale_cache_data = {
            "aweme_list": [
                {"aweme_id": "7100000000000001004", "group_id": "stale_corrupted_gid"},
                {"aweme_id": "7100000000000001009", "group_id": "7200000000000002007"},
            ]
        }

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=json.dumps(stale_cache_data))):
            client._enrich_posts_with_feed_group_id([post_a, post_b], config["sec_uid"], username="user_conflict_test")

        # Post A phải giữ nguyên giá trị live feed
        self.assertEqual(post_a.group_id, "7200000000000002003", "Group ID từ live feed không được bị ghi đè bởi cache offline!")
        self.assertEqual(client._feed_states[config["sec_uid"]]["cache"]["7100000000000001004"], "7200000000000002003")

        # Post B được nạp chính xác từ cache
        self.assertEqual(post_b.group_id, "7200000000000002007")
        self.assertEqual(post_b.data_source, "hybrid")

if __name__ == "__main__":
    unittest.main()
