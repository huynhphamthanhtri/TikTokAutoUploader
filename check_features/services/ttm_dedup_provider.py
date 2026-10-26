# -*- coding: utf-8 -*-
"""
check_features.services.ttm_dedup_provider - Dịch vụ đọc log trùng lặp từ cơ sở dữ liệu SQLite của TTM.
Đọc trực tiếp từ bảng dedup_log trong H:\\TTM\\profiles.db ở chế độ chỉ đọc (read-only mode).
Tuân thủ AGENTS.md:
- An toàn thread, mở DB với uri=True và mode=ro.
- Chia batch an toàn cho truy vấn IN (...).
- Ép kiểu CAST(aweme_id AS TEXT) và CAST(group_id AS TEXT) để bảo toàn ID 64-bit.
- Bắt lỗi OperationalError và trả về lỗi minh bạch, không làm gián đoạn chương trình.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from ..models import validate_decimal_id

logger = logging.getLogger("TtmDedupLogProvider")


@dataclass
class TtmDedupRecord:
    """Bản ghi đối soát từ dedup_log của TTM."""
    aweme_id: str
    group_id: str
    action: str = ""
    status: str = ""
    detected_at: Optional[int] = None
    channel: str = ""


class TtmDedupLogProvider:
    """Provider truy vấn bảng dedup_log từ SQLite của TTM ở chế độ chỉ đọc."""

    DEFAULT_DB_PATH = "H:/TTM/profiles.db"

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or self.DEFAULT_DB_PATH

    def get_records(self, aweme_ids: List[str]) -> Tuple[Dict[str, TtmDedupRecord], Optional[str]]:
        """
        Truy vấn danh sách bản ghi đối soát từ dedup_log theo danh sách aweme_id.
        Trả về (dict {aweme_id: TtmDedupRecord}, error_msg).
        """
        if not aweme_ids:
            return {}, None

        # Lọc và xác thực aweme_id hợp lệ
        valid_ids: List[str] = []
        for aid in aweme_ids:
            try:
                clean_id = validate_decimal_id(aid, "aweme_id", allow_empty=False)
                valid_ids.append(clean_id)
            except (TypeError, ValueError):
                continue

        if not valid_ids:
            return {}, None

        # Kiểm tra file DB tồn tại
        if not os.path.exists(self.db_path):
            err_msg = f"Tập tin cơ sở dữ liệu TTM không tồn tại: {self.db_path}"
            logger.warning(err_msg)
            return {}, err_msg

        records: Dict[str, TtmDedupRecord] = {}
        batch_size = 500

        try:
            # Mở kết nối ở chế độ chỉ đọc (read-only) qua URI
            normalized_path = os.path.abspath(self.db_path).replace("\\", "/")
            uri = f"file:{normalized_path}?mode=ro"

            with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
                cursor = conn.cursor()

                for i in range(0, len(valid_ids), batch_size):
                    batch = valid_ids[i:i + batch_size]
                    placeholders = ",".join("?" for _ in batch)
                    query = f"""
                        SELECT CAST(aweme_id AS TEXT), CAST(group_id AS TEXT), action, status, detected_at, channel
                        FROM dedup_log
                        WHERE aweme_id IN ({placeholders})
                    """
                    cursor.execute(query, batch)
                    rows = cursor.fetchall()

                    for r in rows:
                        r_aid = str(r[0] or "").strip()
                        r_gid = str(r[1] or "").strip()
                        if r_aid:
                            records[r_aid] = TtmDedupRecord(
                                aweme_id=r_aid,
                                group_id=r_gid,
                                action=str(r[2] or ""),
                                status=str(r[3] or ""),
                                detected_at=int(r[4]) if r[4] is not None else None,
                                channel=str(r[5] or ""),
                            )

            return records, None

        except sqlite3.OperationalError as e:
            err_msg = f"Lỗi SQLite khi truy vấn dedup_log (DB lock hoặc bảng không tồn tại): {e}"
            logger.error(err_msg)
            return {}, err_msg
        except Exception as e:
            err_msg = f"Ngoại lệ khi truy vấn TtmDedupLogProvider: {e}"
            logger.error(err_msg)
            return {}, err_msg
