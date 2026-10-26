# -*- coding: utf-8 -*-
"""
test_studio_post_client_live_snapshot.py: Kiểm thử Mốc 2 - Thu thập Snapshot Live qua WebSigner và Phân trang an toàn.
Tuân thủ quy chuẩn AGENTS.md:
- Kiểm tra phân trang an toàn: cursor tiến, has_more=False -> completeness: "COMPLETE".
- Kiểm tra phát hiện và ngắt lặp cursor (cursor loop detection) -> completeness: "PARTIAL".
- Kiểm tra phát hiện kẹt cursor (stalled cursor: next_cursor <= cursor khi has_more=True) -> completeness: "PARTIAL".
- Kiểm tra khử trùng lặp ID giữa các trang.
- Kiểm tra phân tách rạch ròi giữa live_studio và cache_offline.
- Kiểm tra bảo toàn ID chuỗi thập phân 64-bit.
"""

from unittest.mock import MagicMock, patch
import pytest

from check_features.clients.studio_post_client import TikTokStudioPostClient
from check_features.models import LivePostSnapshot, AwemePost


@pytest.fixture
def sample_config():
    return {
        "tiktok_id": "test_creator_64",
        "cookie_str": "sessionid=test_sess_123; tt_csrf_token=csrf_token_test456;",
        "use_proxy": False,
    }


def test_fetch_live_snapshot_pagination_complete(sample_config):
    """Kiểm tra phân trang qua 2 trang lấy đủ 28 bài đăng thật và trả về completeness: COMPLETE."""
    client = TikTokStudioPostClient(sample_config)

    # Mock page 1 (20 items, has_more=True, cursor=20)
    page1_items = [{"item_id": f"76990000000000000{i:02d}", "desc": f"Video page 1 - {i}"} for i in range(20)]
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "status_code": 0,
        "item_list": page1_items,
        "has_more": True,
        "cursor": 20,
    }

    # Mock page 2 (8 items, has_more=False, cursor=40)
    page2_items = [{"item_id": f"76990000000000000{i+20:02d}", "desc": f"Video page 2 - {i}"} for i in range(8)]
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {
        "status_code": 0,
        "item_list": page2_items,
        "has_more": False,
        "cursor": 40,
    }

    with patch.object(client.session, "post", side_effect=[resp1, resp2]) as mock_post:
        snapshot = client.fetch_live_snapshot(run_id="test_run_123", max_items=50, max_pages=5)

    assert mock_post.call_count == 2
    assert isinstance(snapshot, LivePostSnapshot)
    assert snapshot.source == "live_studio"
    assert snapshot.completeness == "COMPLETE"
    assert snapshot.run_id == "test_run_123"
    assert snapshot.total_count == 28
    assert len(snapshot.posts) == 28
    assert snapshot.posts[0].aweme_id == "7699000000000000000"
    assert snapshot.posts[27].aweme_id == "7699000000000000027"


def test_fetch_live_snapshot_detects_cursor_loop(sample_config):
    """Kiểm tra cơ chế phát hiện lặp cursor để ngắt vòng lặp vô hạn và đánh dấu completeness: PARTIAL."""
    client = TikTokStudioPostClient(sample_config)

    # Page 1 trả về cursor 20
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "status_code": 0,
        "item_list": [{"item_id": "7699000000000000001", "desc": "Video 1"}],
        "has_more": True,
        "cursor": 20,
    }

    # Page 2 lặp lại cursor 0 (loop!)
    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {
        "status_code": 0,
        "item_list": [{"item_id": "7699000000000000002", "desc": "Video 2"}],
        "has_more": True,
        "cursor": 0,  # Loop về 0
    }

    with patch.object(client.session, "post", side_effect=[resp1, resp2, resp1, resp2]):
        snapshot = client.fetch_live_snapshot(run_id="test_run_loop", max_pages=10)

    # Vòng lặp phải dừng ngay khi gặp lại cursor 0 mà không chạy hết 10 pages
    assert snapshot.completeness == "PARTIAL"
    assert snapshot.source == "live_studio"
    assert snapshot.total_count >= 1


def test_fetch_live_snapshot_detects_stalled_cursor(sample_config):
    """Kiểm tra cơ chế phát hiện cursor không tịnh tiến (stalled cursor) khi has_more=True."""
    client = TikTokStudioPostClient(sample_config)

    resp_stalled = MagicMock()
    resp_stalled.status_code = 200
    resp_stalled.json.return_value = {
        "status_code": 0,
        "item_list": [{"item_id": "7699000000000000001", "desc": "Video 1"}],
        "has_more": True,
        "cursor": 0,  # Không tiến lên
    }

    with patch.object(client.session, "post", return_value=resp_stalled):
        snapshot = client.fetch_live_snapshot(run_id="test_run_stalled", max_pages=5)

    assert snapshot.completeness == "PARTIAL"
    assert snapshot.total_count == 1


def test_fetch_live_snapshot_deduplicates_by_id(sample_config):
    """Kiểm tra khử trùng lặp ID (aweme_id) nếu máy chủ trả về bài đăng trùng giữa các trang."""
    client = TikTokStudioPostClient(sample_config)

    # Cả 2 trang đều trả về cùng một item_id
    dup_id = "7699321502767893782"
    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "status_code": 0,
        "item_list": [{"item_id": dup_id, "desc": "Page 1 dup"}],
        "has_more": True,
        "cursor": 10,
    }

    resp2 = MagicMock()
    resp2.status_code = 200
    resp2.json.return_value = {
        "status_code": 0,
        "item_list": [{"item_id": dup_id, "desc": "Page 2 dup duplicate"}],
        "has_more": False,
        "cursor": 20,
    }

    with patch.object(client.session, "post", side_effect=[resp1, resp2]):
        snapshot = client.fetch_live_snapshot(run_id="test_dedup_id")

    assert snapshot.completeness == "COMPLETE"
    assert snapshot.total_count == 1
    assert snapshot.posts[0].aweme_id == dup_id


def test_fetch_live_snapshot_offline_cache_fallback(sample_config):
    """Kiểm tra khi mất mạng hoàn toàn, hệ thống fallback về cache offline và minh bạch source='cache_offline'."""
    client = TikTokStudioPostClient(sample_config)

    cached_mock_posts = [
        AwemePost(aweme_id=f"76980000000000000{i:02d}", desc=f"Cached {i}", data_source="cache_offline")
        for i in range(5)
    ]

    # Giả lập request mạng văng exception
    with patch.object(client.session, "post", side_effect=Exception("Network unreachable")), \
         patch.object(client, "_load_offline_cache_posts", return_value=cached_mock_posts):
        snapshot = client.fetch_live_snapshot(run_id="test_offline_run")

    assert snapshot.source == "cache_offline"
    assert snapshot.completeness == "PARTIAL"
    assert snapshot.total_count == 5
    assert "Mất kết nối" in (snapshot.error or "")
    assert snapshot.posts[0].data_source == "cache_offline"


def test_fetch_live_snapshot_max_pages_reached(sample_config):
    """Codex Finding 1: Kiểm tra khi chạm max_pages mà has_more=True thì completeness phải là PARTIAL."""
    client = TikTokStudioPostClient(sample_config)

    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "status_code": 0,
        "item_list": [{"item_id": "7699000000000000001", "desc": "Video 1"}],
        "has_more": True,
        "cursor": 1,
    }

    with patch.object(client.session, "post", return_value=resp1):
        snapshot = client.fetch_live_snapshot(run_id="test_max_pages", max_pages=1, max_items=100)

    assert snapshot.completeness == "PARTIAL"
    assert snapshot.source == "live_studio"
    assert snapshot.total_count == 1


def test_fetch_live_snapshot_empty_list_with_has_more(sample_config):
    """Codex Finding 2: API trả về list rỗng nhưng báo has_more=True -> completeness: PARTIAL, KHÔNG gọi fallback offline."""
    client = TikTokStudioPostClient(sample_config)

    resp_empty = MagicMock()
    resp_empty.status_code = 200
    resp_empty.json.return_value = {
        "status_code": 0,
        "item_list": [],
        "has_more": True,
        "cursor": 0,
    }

    dummy_cache = [AwemePost(aweme_id="7698999999999999999", desc="Dummy cached post", data_source="cache_offline")]
    with patch.object(client.session, "post", return_value=resp_empty), \
         patch.object(client, "_load_offline_cache_posts", return_value=dummy_cache) as mock_cache:
        snapshot = client.fetch_live_snapshot(run_id="test_empty_has_more")

    # Xác nhận dứt khoát không rơi vào offline cache khi Creator Studio đã phản hồi
    mock_cache.assert_not_called()
    assert snapshot.completeness == "PARTIAL"
    assert snapshot.source == "live_studio"
    assert snapshot.total_count == 0
    assert "has_more=True" in (snapshot.error or "")


def test_fetch_live_snapshot_exception_on_page2_preserves_page1(sample_config):
    """Codex Finding 3: Lỗi ở trang 2 sau khi trang 1 thành công -> giữ posts trang 1, source=live_studio, completeness=PARTIAL."""
    client = TikTokStudioPostClient(sample_config)

    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "status_code": 0,
        "item_list": [{"item_id": "7699000000000000001", "desc": "Video Page 1"}],
        "has_more": True,
        "cursor": 1,
    }

    with patch.object(client.session, "post", side_effect=[resp1, Exception("Timeout on page 2")]):
        snapshot = client.fetch_live_snapshot(run_id="test_page2_err")

    assert snapshot.source == "live_studio"
    assert snapshot.completeness == "PARTIAL"
    assert snapshot.total_count == 1
    assert snapshot.posts[0].aweme_id == "7699000000000000001"
    assert "Lỗi phân trang" in (snapshot.error or "")


def test_fetch_live_snapshot_enrichment_failure_resilience(sample_config):
    """Codex Finding 3b: Lỗi khi enrich feed group_id không làm mất snapshot studio (có sec_uid hợp lệ)."""
    cfg = dict(sample_config)
    cfg["sec_uid"] = "MS4wLjABAAAApLcY3iOIQ71ec8IqJ-yO7Ik3HwbnBt3p4UksSAXi-bVI_7YG1fxxwArv7KxU43-V"
    client = TikTokStudioPostClient(cfg)

    resp1 = MagicMock()
    resp1.status_code = 200
    resp1.json.return_value = {
        "status_code": 0,
        "item_list": [{"item_id": "7699000000000000001", "desc": "Video 1"}],
        "has_more": False,
        "cursor": 1,
    }

    with patch.object(client.session, "post", return_value=resp1), \
         patch.object(client, "_enrich_posts_with_feed_group_id", side_effect=Exception("Enrichment crash")) as mock_enrich:
        snapshot = client.fetch_live_snapshot(run_id="test_enrich_resilience")

    mock_enrich.assert_called_once()
    assert snapshot.source == "live_studio"
    assert snapshot.completeness == "COMPLETE"
    assert snapshot.total_count == 1
