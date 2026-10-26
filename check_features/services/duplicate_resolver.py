# -*- coding: utf-8 -*-
"""
check_features.services.duplicate_resolver - Động cơ đối soát và giải quyết trạng thái trùng lặp tập trung.
Hợp nhất dữ liệu giữa TTM SQLite (dedup_log), Mobile Feed API (aweme_list / group_id) và Creator Studio.
Tuân thủ nghiêm ngặt Hợp đồng 3 trạng thái và 7 mã lý do theo AGENTS.md:
- DUPLICATE (CONFIRMED_DUPLICATE): khi có bằng chứng group_id != aweme_id từ TTM hoặc Feed (và không có source_error).
- CLEAR (MATCHED_ORIGINAL): khi có nguồn đối soát xác nhận rõ ràng group_id == aweme_id (và không có source_error).
- UNKNOWN:
  - SOURCE_ERROR: Khi có lỗi nguồn đối soát (DB lock, mất mạng, lỗi IO). Được ưu tiên xử lý trước mọi kết luận xác định.
  - CONFLICTING_EVIDENCE: Hai nguồn đối soát trả về kết quả mâu thuẫn (một bên DUPLICATE, một bên CLEAR).
  - NO_EVIDENCE: Không tìm thấy bản ghi ở bất kỳ nguồn nào.
  - INVALID_ID: aweme_id không phải chuỗi thập phân hợp lệ.
  - UNVERIFIED_SEMANTICS: Dữ liệu chưa đủ căn cứ xác minh ngữ nghĩa.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..models import AwemePost, DedupResult, validate_decimal_id
from .ttm_dedup_provider import TtmDedupLogProvider, TtmDedupRecord

logger = logging.getLogger("DuplicateResolver")


class DuplicateResolver:
    """Động cơ đối soát trùng lặp 3 trạng thái (DUPLICATE, CLEAR, UNKNOWN)."""

    def __init__(self, ttm_provider: Optional[TtmDedupLogProvider] = None):
        self.ttm_provider = ttm_provider or TtmDedupLogProvider()

    def resolve_single(
        self,
        aweme_id: str,
        feed_group_id: Optional[str] = None,
        ttm_record: Optional[TtmDedupRecord] = None,
        source_error: Optional[str] = None,
    ) -> DedupResult:
        """
        Đối soát và xác định trạng thái cho một video duy nhất theo Bảng Quyết Định 3 Trạng Thái.
        """
        # 1. Kiểm tra tính hợp lệ của aweme_id
        try:
            clean_aid = validate_decimal_id(aweme_id, "aweme_id", allow_empty=False)
        except (TypeError, ValueError):
            return DedupResult(
                aweme_id="",
                state="UNKNOWN",
                reason="INVALID_ID",
                source="none",
                evidence={"raw_aweme_id": aweme_id},
            )

        # 2. Xử lý SOURCE_ERROR ngay lập tức (Codex Finding 1):
        # Khi nguồn đối soát gặp lỗi (TTM DB lock, IO error), tuyệt đối không được bypass sang CLEAR hay DUPLICATE.
        # Phải trả về UNKNOWN / SOURCE_ERROR và bảo lưu toàn bộ bằng chứng từng nguồn để phục vụ chẩn đoán.
        if source_error:
            evidence_data: Dict[str, Any] = {"error": source_error}
            if feed_group_id:
                evidence_data["feed_group_id"] = str(feed_group_id)
            if ttm_record and ttm_record.group_id:
                evidence_data["ttm_group_id"] = str(ttm_record.group_id)
            return DedupResult(
                aweme_id=clean_aid,
                state="UNKNOWN",
                reason="SOURCE_ERROR",
                source="none",
                evidence=evidence_data,
            )

        # 3. Làm sạch feed_group_id nếu có
        clean_feed_gid: Optional[str] = None
        if feed_group_id:
            try:
                clean_feed_gid = validate_decimal_id(feed_group_id, "feed_group_id", allow_empty=True)
            except (TypeError, ValueError):
                clean_feed_gid = None

        # 4. Làm sạch ttm_group_id nếu có
        clean_ttm_gid: Optional[str] = None
        if ttm_record and ttm_record.group_id:
            try:
                clean_ttm_gid = validate_decimal_id(ttm_record.group_id, "ttm_group_id", allow_empty=True)
            except (TypeError, ValueError):
                clean_ttm_gid = None

        # Đánh giá bằng chứng từ từng nguồn
        feed_indicates_dup = bool(clean_feed_gid and clean_feed_gid != "0" and clean_feed_gid != clean_aid)
        feed_indicates_clear = bool(clean_feed_gid and clean_feed_gid != "0" and clean_feed_gid == clean_aid)

        ttm_indicates_dup = bool(clean_ttm_gid and clean_ttm_gid != "0" and clean_ttm_gid != clean_aid)
        ttm_indicates_clear = bool(clean_ttm_gid and clean_ttm_gid != "0" and clean_ttm_gid == clean_aid)

        # 5. Phát hiện mâu thuẫn (CONFLICTING_EVIDENCE)
        if (feed_indicates_dup and ttm_indicates_clear) or (feed_indicates_clear and ttm_indicates_dup):
            return DedupResult(
                aweme_id=clean_aid,
                state="UNKNOWN",
                reason="CONFLICTING_EVIDENCE",
                source="mixed",
                evidence={
                    "feed_group_id": clean_feed_gid,
                    "ttm_group_id": clean_ttm_gid,
                },
            )

        # 6. Xác nhận DUPLICATE (CONFIRMED_DUPLICATE)
        if ttm_indicates_dup and feed_indicates_dup:
            # Cả hai nguồn đều xác nhận trùng
            return DedupResult(
                aweme_id=clean_aid,
                group_id=clean_ttm_gid or clean_feed_gid,
                state="DUPLICATE",
                reason="CONFIRMED_DUPLICATE",
                source="ttm_sqlite+feed",
                detected_at=ttm_record.detected_at if ttm_record else None,
                evidence={
                    "ttm_group_id": clean_ttm_gid,
                    "feed_group_id": clean_feed_gid,
                    "action": ttm_record.action if ttm_record else "",
                    "status": ttm_record.status if ttm_record else "",
                },
            )

        if ttm_indicates_dup:
            # Xác nhận từ TTM dedup_log
            return DedupResult(
                aweme_id=clean_aid,
                group_id=clean_ttm_gid,
                state="DUPLICATE",
                reason="CONFIRMED_DUPLICATE",
                source="ttm_sqlite",
                detected_at=ttm_record.detected_at if ttm_record else None,
                evidence={
                    "ttm_group_id": clean_ttm_gid,
                    "action": ttm_record.action if ttm_record else "",
                    "status": ttm_record.status if ttm_record else "",
                },
            )

        if feed_indicates_dup:
            # Xác nhận từ Mobile Feed API
            return DedupResult(
                aweme_id=clean_aid,
                group_id=clean_feed_gid,
                state="DUPLICATE",
                reason="CONFIRMED_DUPLICATE",
                source="feed_api",
                evidence={"feed_group_id": clean_feed_gid},
            )

        # 7. Xác nhận CLEAR (MATCHED_ORIGINAL)
        # Chỉ khi có nguồn đối soát xác nhận rõ ràng group_id == aweme_id
        if feed_indicates_clear:
            return DedupResult(
                aweme_id=clean_aid,
                group_id=clean_aid,
                state="CLEAR",
                reason="MATCHED_ORIGINAL",
                source="feed_api",
                evidence={"feed_group_id": clean_feed_gid},
            )

        if ttm_indicates_clear:
            return DedupResult(
                aweme_id=clean_aid,
                group_id=clean_aid,
                state="CLEAR",
                reason="MATCHED_ORIGINAL",
                source="ttm_sqlite",
                evidence={"ttm_group_id": clean_ttm_gid},
            )

        # 8. Không tìm thấy bản ghi ở bất kỳ nguồn nào -> UNKNOWN / NO_EVIDENCE
        return DedupResult(
            aweme_id=clean_aid,
            state="UNKNOWN",
            reason="NO_EVIDENCE",
            source="none",
            evidence={},
        )

    def resolve_batch(
        self,
        posts: List[AwemePost],
        query_ttm: bool = True,
    ) -> Dict[str, DedupResult]:
        """
        Đối soát hàng loạt danh sách AwemePost và gán trực tiếp kết quả vào từng post.
        Bảo toàn feed_group_id riêng biệt (Codex Finding 2), không để output TTM ở lần trước
        ghi đè thành bằng chứng Feed ở các lần đối soát lặp lại.
        """
        if not posts:
            return {}

        aweme_ids = [p.aweme_id for p in posts if p.aweme_id]
        ttm_records: Dict[str, TtmDedupRecord] = {}
        ttm_err: Optional[str] = None

        if query_ttm and aweme_ids:
            ttm_records, ttm_err = self.ttm_provider.get_records(aweme_ids)

        results: Dict[str, DedupResult] = {}
        for post in posts:
            if not post.aweme_id:
                continue

            # Codex Finding 2: Tách biệt feed_group_id không để TTM output ghi đè
            feed_gid = getattr(post, "feed_group_id", None)
            if not feed_gid:
                # Nếu chưa có feed_group_id riêng và chưa từng được đối soát (dedup_result is None)
                if post.group_id and post.dedup_result is None:
                    feed_gid = post.group_id
                    post.feed_group_id = feed_gid

            ttm_rec = ttm_records.get(post.aweme_id)

            result = self.resolve_single(
                aweme_id=post.aweme_id,
                feed_group_id=feed_gid,
                ttm_record=ttm_rec,
                source_error=ttm_err,
            )

            # Cập nhật kết quả vào post
            post.dedup_result = result
            if result.state in ("DUPLICATE", "CLEAR") and result.group_id:
                post.group_id = result.group_id

            results[post.aweme_id] = result

        return results
