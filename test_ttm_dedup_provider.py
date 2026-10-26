# -*- coding: utf-8 -*-
"""
test_ttm_dedup_provider.py - Kiểm thử TtmDedupLogProvider theo quy chuẩn AGENTS.md:
- Đọc an toàn từ SQLite ở chế độ chỉ đọc (read-only URI).
- Bảo toàn ID chuỗi thập phân 64-bit.
- Chia batch an toàn (> 500 IDs).
- Cách ly lỗi DB lock / file không tồn tại / operational error.
- Kiểm thử thực tế trên CSDL thật H:/TTM/profiles.db nếu có trên máy.
"""

import os
import sqlite3
import pytest

from check_features.services.ttm_dedup_provider import TtmDedupLogProvider, TtmDedupRecord


@pytest.fixture
def temp_ttm_db(tmp_path):
    """Tạo database SQLite tạm thời giả lập bảng dedup_log của TTM."""
    db_file = tmp_path / "profiles.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE dedup_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER,
            channel TEXT,
            aweme_id TEXT,
            group_id TEXT,
            desc TEXT,
            create_time INTEGER,
            detected_at INTEGER,
            deleted_at INTEGER,
            action TEXT,
            status TEXT,
            error TEXT
        )
    """)
    # Thêm dữ liệu mẫu với ID 19 chữ số bảo toàn
    cur.execute("""
        INSERT INTO dedup_log (profile_id, channel, aweme_id, group_id, action, status, detected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (10, "user64", "7699321502767893782", "7699321502767893000", "delete", "deleted", 1792600000000))
    cur.execute("""
        INSERT INTO dedup_log (profile_id, channel, aweme_id, group_id, action, status, detected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (10, "user64", "7699077508527967510", "7699077508527967000", "hide", "hidden", 1792600010000))
    conn.commit()
    conn.close()
    return str(db_file)


def test_ttm_dedup_provider_fetches_records_with_precision(temp_ttm_db):
    """Kiểm tra đọc dữ liệu chính xác và bảo toàn 100% ID chuỗi thập phân 64-bit."""
    provider = TtmDedupLogProvider(temp_ttm_db)

    ids = ["7699321502767893782", "7699077508527967510"]
    records, err = provider.get_records(ids)

    assert err is None
    assert len(records) == 2
    assert "7699321502767893782" in records
    assert "7699077508527967510" in records

    rec1 = records["7699321502767893782"]
    assert rec1.aweme_id == "7699321502767893782"
    assert rec1.group_id == "7699321502767893000"
    assert rec1.action == "delete"
    assert rec1.status == "deleted"
    assert rec1.detected_at == 1792600000000
    assert rec1.channel == "user64"


def test_ttm_dedup_provider_missing_db():
    """Kiểm tra xử lý minh bạch khi file database không tồn tại."""
    provider = TtmDedupLogProvider("H:/NonExistentPath/profiles.db")
    records, err = provider.get_records(["7699321502767893782"])

    assert records == {}
    assert err is not None
    assert "không tồn tại" in err


def test_ttm_dedup_provider_invalid_ids_filtered():
    """Kiểm tra tự động bỏ qua các ID không hợp lệ mà không làm crash truy vấn."""
    provider = TtmDedupLogProvider("H:/NonExistentPath/profiles.db")
    records, err = provider.get_records(["not_a_number", "-123", "", None, "7e18"])

    assert records == {}
    assert err is None  # Vì sau khi lọc không còn ID nào hợp lệ nên trả về sớm


def test_ttm_dedup_provider_batching(tmp_path):
    """Kiểm tra chia batch an toàn khi số lượng IDs vượt quá 500."""
    db_file = tmp_path / "large_profiles.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE dedup_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER,
            channel TEXT,
            aweme_id TEXT,
            group_id TEXT,
            desc TEXT,
            create_time INTEGER,
            detected_at INTEGER,
            deleted_at INTEGER,
            action TEXT,
            status TEXT,
            error TEXT
        )
    """)
    # Chèn 600 bản ghi
    data = [
        (1, "chan", f"7699000000000000{i:03d}", f"7699000000000000000", "delete", "deleted", 1792000000000)
        for i in range(600)
    ]
    cur.executemany("""
        INSERT INTO dedup_log (profile_id, channel, aweme_id, group_id, action, status, detected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, data)
    conn.commit()
    conn.close()

    provider = TtmDedupLogProvider(str(db_file))
    query_ids = [f"7699000000000000{i:03d}" for i in range(600)]

    records, err = provider.get_records(query_ids)
    assert err is None
    assert len(records) == 600
    assert records["7699000000000000599"].aweme_id == "7699000000000000599"


def test_ttm_dedup_provider_live_ttm_db_if_exists():
    """Kiểm tra đối chiếu thực tế trên file H:/TTM/profiles.db nếu có trên máy."""
    real_db_path = "H:/TTM/profiles.db"
    if not os.path.exists(real_db_path):
        pytest.skip("File H:/TTM/profiles.db không tồn tại trên máy.")

    provider = TtmDedupLogProvider(real_db_path)
    # 7697300392979205398 là bản ghi thật đã quan sát trong SQLite TTM
    records, err = provider.get_records(["7697300392979205398"])

    assert err is None
    assert "7697300392979205398" in records
    rec = records["7697300392979205398"]
    assert rec.aweme_id == "7697300392979205398"
    assert rec.group_id == "7696480268357553430"
    assert rec.action == "delete"
    assert rec.status == "deleted"
    assert rec.channel == "tenshinoana008"
