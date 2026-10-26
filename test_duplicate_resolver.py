# -*- coding: utf-8 -*-
"""
test_duplicate_resolver.py - Kiểm thử Bảng Quyết Định 3 Trạng Thái của DuplicateResolver.
Tuân thủ AGENTS.md:
- Bao phủ đầy đủ 100% các nhánh quyết định (DUPLICATE, CLEAR, UNKNOWN).
- Kiểm tra các lý do: CONFIRMED_DUPLICATE, MATCHED_ORIGINAL, NO_EVIDENCE, SOURCE_ERROR, INVALID_ID, CONFLICTING_EVIDENCE.
- Kiểm tra việc hợp nhất và gắn cờ vào AwemePost (post.is_dup, post.duplicate_status).
"""

from unittest.mock import MagicMock
import pytest

from check_features.models import AwemePost, DedupResult
from check_features.services.duplicate_resolver import DuplicateResolver
from check_features.services.ttm_dedup_provider import TtmDedupLogProvider, TtmDedupRecord


@pytest.fixture
def resolver():
    mock_ttm = MagicMock(spec=TtmDedupLogProvider)
    return DuplicateResolver(ttm_provider=mock_ttm)


def test_resolve_single_ttm_duplicate(resolver):
    """Bằng chứng từ TTM SQLite xác nhận group_id != aweme_id -> DUPLICATE."""
    aid = "7699321502767893782"
    gid = "7699000000000000001"
    ttm_rec = TtmDedupRecord(aweme_id=aid, group_id=gid, action="delete", status="deleted", detected_at=1792000000000)

    res = resolver.resolve_single(aweme_id=aid, feed_group_id=None, ttm_record=ttm_rec)

    assert res.state == "DUPLICATE"
    assert res.reason == "CONFIRMED_DUPLICATE"
    assert res.group_id == gid
    assert res.source == "ttm_sqlite"
    assert res.detected_at == 1792000000000


def test_resolve_single_feed_duplicate(resolver):
    """Bằng chứng từ Mobile Feed API xác nhận feed_group_id != aweme_id -> DUPLICATE."""
    aid = "7699321502767893782"
    feed_gid = "7699000000000000002"

    res = resolver.resolve_single(aweme_id=aid, feed_group_id=feed_gid, ttm_record=None)

    assert res.state == "DUPLICATE"
    assert res.reason == "CONFIRMED_DUPLICATE"
    assert res.group_id == feed_gid
    assert res.source == "feed_api"


def test_resolve_single_both_duplicate(resolver):
    """Cả hai nguồn đều xác nhận DUPLICATE -> source: ttm_sqlite+feed."""
    aid = "7699321502767893782"
    ttm_gid = "7699000000000000001"
    feed_gid = "7699000000000000001"
    ttm_rec = TtmDedupRecord(aweme_id=aid, group_id=ttm_gid, action="hide", status="hidden")

    res = resolver.resolve_single(aweme_id=aid, feed_group_id=feed_gid, ttm_record=ttm_rec)

    assert res.state == "DUPLICATE"
    assert res.reason == "CONFIRMED_DUPLICATE"
    assert res.source == "ttm_sqlite+feed"
    assert res.group_id == ttm_gid


def test_resolve_single_feed_clear(resolver):
    """Feed xác nhận rõ ràng group_id == aweme_id -> CLEAR / MATCHED_ORIGINAL."""
    aid = "7699321502767893782"
    res = resolver.resolve_single(aweme_id=aid, feed_group_id=aid, ttm_record=None)

    assert res.state == "CLEAR"
    assert res.reason == "MATCHED_ORIGINAL"
    assert res.group_id == aid
    assert res.source == "feed_api"


def test_resolve_single_ttm_clear(resolver):
    """TTM xác nhận rõ ràng group_id == aweme_id -> CLEAR / MATCHED_ORIGINAL."""
    aid = "7699321502767893782"
    ttm_rec = TtmDedupRecord(aweme_id=aid, group_id=aid, status="clear")
    res = resolver.resolve_single(aweme_id=aid, feed_group_id=None, ttm_record=ttm_rec)

    assert res.state == "CLEAR"
    assert res.reason == "MATCHED_ORIGINAL"
    assert res.group_id == aid
    assert res.source == "ttm_sqlite"


def test_resolve_single_conflict_feed_dup_ttm_clear(resolver):
    """Mâu thuẫn: Feed bảo trùng nhưng TTM bảo không trùng -> UNKNOWN / CONFLICTING_EVIDENCE."""
    aid = "7699321502767893782"
    feed_gid = "7699000000000000099"  # Dup
    ttm_rec = TtmDedupRecord(aweme_id=aid, group_id=aid)  # Clear

    res = resolver.resolve_single(aweme_id=aid, feed_group_id=feed_gid, ttm_record=ttm_rec)

    assert res.state == "UNKNOWN"
    assert res.reason == "CONFLICTING_EVIDENCE"


def test_resolve_single_conflict_feed_clear_ttm_dup(resolver):
    """Mâu thuẫn: Feed bảo không trùng nhưng TTM bảo trùng -> UNKNOWN / CONFLICTING_EVIDENCE."""
    aid = "7699321502767893782"
    feed_gid = aid  # Clear
    ttm_rec = TtmDedupRecord(aweme_id=aid, group_id="7699000000000000088")  # Dup

    res = resolver.resolve_single(aweme_id=aid, feed_group_id=feed_gid, ttm_record=ttm_rec)

    assert res.state == "UNKNOWN"
    assert res.reason == "CONFLICTING_EVIDENCE"


def test_resolve_single_no_evidence(resolver):
    """Không có bằng chứng ở bất kỳ nguồn nào -> UNKNOWN / NO_EVIDENCE."""
    aid = "7699321502767893782"
    res = resolver.resolve_single(aweme_id=aid, feed_group_id=None, ttm_record=None)

    assert res.state == "UNKNOWN"
    assert res.reason == "NO_EVIDENCE"


def test_resolve_single_source_error(resolver):
    """Lỗi nguồn đối soát (DB lock / network fail) -> UNKNOWN / SOURCE_ERROR."""
    aid = "7699321502767893782"
    res = resolver.resolve_single(aweme_id=aid, feed_group_id=None, ttm_record=None, source_error="DB locked")

    assert res.state == "UNKNOWN"
    assert res.reason == "SOURCE_ERROR"
    assert "DB locked" in res.evidence.get("error", "")


def test_resolve_single_invalid_id(resolver):
    """ID không hợp lệ (chuỗi chữ / âm / số thực) -> UNKNOWN / INVALID_ID."""
    res = resolver.resolve_single(aweme_id="invalid_aweme_id", feed_group_id=None, ttm_record=None)

    assert res.state == "UNKNOWN"
    assert res.reason == "INVALID_ID"
    assert res.aweme_id == ""


def test_resolve_batch_annotates_posts(resolver):
    """Kiểm tra resolve_batch cập nhật chính xác thuộc tính cho từng AwemePost."""
    post1 = AwemePost(aweme_id="7699000000000000001", desc="Post 1 - Dup by TTM")
    post2 = AwemePost(aweme_id="7699000000000000002", desc="Post 2 - Clear by Feed", group_id="7699000000000000002")
    post3 = AwemePost(aweme_id="7699000000000000003", desc="Post 3 - No Evidence")

    # Giả lập TTM tìm thấy post1
    ttm_rec = TtmDedupRecord(aweme_id="7699000000000000001", group_id="7698999999999999999", action="delete")
    resolver.ttm_provider.get_records.return_value = ({"7699000000000000001": ttm_rec}, None)

    results = resolver.resolve_batch([post1, post2, post3], query_ttm=True)

    assert len(results) == 3

    # Post 1: DUPLICATE
    assert post1.dedup_result.state == "DUPLICATE"
    assert post1.dedup_result.reason == "CONFIRMED_DUPLICATE"
    assert post1.group_id == "7698999999999999999"
    assert post1.is_dup is True
    assert post1.duplicate_status == "duplicate"

    # Post 2: CLEAR
    assert post2.dedup_result.state == "CLEAR"
    assert post2.dedup_result.reason == "MATCHED_ORIGINAL"
    assert post2.is_dup is False
    assert post2.duplicate_status == "original"

    # Post 3: UNKNOWN
    assert post3.dedup_result.state == "UNKNOWN"
    assert post3.dedup_result.reason == "NO_EVIDENCE"
    assert post3.is_dup is False
    assert post3.duplicate_status == "unknown"


def test_resolve_single_source_error_with_matching_feed_never_produces_clear(resolver):
    """Codex Finding 1: source_error kết hợp với feed_group_id == aweme_id BẮT BUỘC trả về UNKNOWN/SOURCE_ERROR, KHÔNG bypass sang CLEAR."""
    aid = "7699321502767893782"
    res = resolver.resolve_single(
        aweme_id=aid,
        feed_group_id=aid,  # Feed tưởng chừng là clear
        ttm_record=None,
        source_error="TTM database file is locked by another process",
    )

    assert res.state == "UNKNOWN"
    assert res.reason == "SOURCE_ERROR"
    assert res.source == "none"
    assert "locked" in res.evidence.get("error", "")
    assert res.evidence.get("feed_group_id") == aid


def test_resolve_single_source_error_with_differing_feed_never_produces_duplicate(resolver):
    """Codex Finding 1: source_error kết hợp với feed_group_id != aweme_id BẮT BUỘC trả về UNKNOWN/SOURCE_ERROR, KHÔNG bypass sang DUPLICATE."""
    aid = "7699321502767893782"
    diff_gid = "7699000000000000002"
    res = resolver.resolve_single(
        aweme_id=aid,
        feed_group_id=diff_gid,
        ttm_record=None,
        source_error="Connection refused on TTM SQLite daemon",
    )

    assert res.state == "UNKNOWN"
    assert res.reason == "SOURCE_ERROR"
    assert res.source == "none"
    assert "refused" in res.evidence.get("error", "")
    assert res.evidence.get("feed_group_id") == diff_gid


def test_resolve_single_source_error_preserves_both_feed_and_ttm_evidence(resolver):
    """Codex Suggestion: Khi source_error xảy ra, bảo toàn cả feed_group_id và ttm_group_id trong evidence."""
    aid = "7699321502767893782"
    feed_gid = "7699000000000000002"
    ttm_gid = "7699000000000000003"
    ttm_rec = TtmDedupRecord(aweme_id=aid, group_id=ttm_gid)

    res = resolver.resolve_single(
        aweme_id=aid,
        feed_group_id=feed_gid,
        ttm_record=ttm_rec,
        source_error="Partial network partition during batch fetch",
    )

    assert res.state == "UNKNOWN"
    assert res.reason == "SOURCE_ERROR"
    assert res.evidence.get("error") == "Partial network partition during batch fetch"
    assert res.evidence.get("feed_group_id") == feed_gid
    assert res.evidence.get("ttm_group_id") == ttm_gid


def test_resolve_batch_repeated_resolution_preserves_feed_evidence(resolver):
    """Codex Finding 2: Chạy resolve_batch lặp lại không được lấy output TTM làm bằng chứng Feed mới."""
    # Video 1: Ban đầu không có feed_group_id
    post1 = AwemePost(aweme_id="7699000000000000001", desc="Post 1")
    # Video 2: Ban đầu có feed_group_id == aweme_id (Feed clear)
    post2 = AwemePost(aweme_id="7699000000000000002", desc="Post 2", group_id="7699000000000000002")

    # Lần 1: TTM trả về Post 1 là Duplicate
    ttm_dup_rec = TtmDedupRecord(aweme_id="7699000000000000001", group_id="7698888888888888888", action="delete")
    resolver.ttm_provider.get_records.return_value = ({"7699000000000000001": ttm_dup_rec}, None)

    resolver.resolve_batch([post1, post2], query_ttm=True)

    assert post1.dedup_result.state == "DUPLICATE"
    assert post1.group_id == "7698888888888888888"  # Đã gán group_id từ TTM
    assert post1.feed_group_id == ""  # feed_group_id gốc vẫn rỗng!

    assert post2.dedup_result.state == "CLEAR"
    assert post2.feed_group_id == "7699000000000000002"

    # Lần 2: Người dùng bấm Refresh hoặc TTM xóa bản ghi (bản ghi biến mất)
    resolver.ttm_provider.get_records.return_value = ({}, None)  # TTM không còn record nào

    resolver.resolve_batch([post1, post2], query_ttm=True)

    # Post 1: Phải chuyển về UNKNOWN / NO_EVIDENCE, KHÔNG được biến TTM group_id cũ thành Feed Duplicate giả tạo!
    assert post1.dedup_result.state == "UNKNOWN"
    assert post1.dedup_result.reason == "NO_EVIDENCE"
    assert post1.feed_group_id == ""

    # Post 2: Vẫn bảo toàn feed_group_id == aweme_id và là CLEAR
    assert post2.dedup_result.state == "CLEAR"
    assert post2.dedup_result.reason == "MATCHED_ORIGINAL"
    assert post2.feed_group_id == "7699000000000000002"
