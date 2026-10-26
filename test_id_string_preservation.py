# -*- coding: utf-8 -*-
"""
test_id_string_preservation.py: Kiểm thử bảo toàn chuỗi 64-bit ID và Hợp đồng dữ liệu 3 trạng thái.
Tuân thủ quy chuẩn AGENTS.md:
- Kiểm tra bảo toàn các ID lớn của profile NEW 64 không bị làm tròn số bởi Number/Float.
- Kiểm tra chặn đứng boolean, float, scientific notation ('7e18', '7.69e+18'), số âm, dấu cộng, chuỗi ký tự chữ, chữ số Unicode, số có số 0 ở đầu ('00', '0123').
- Kiểm tra thực thi allow_empty.
- Kiểm tra 3 trạng thái DUPLICATE, CLEAR, UNKNOWN và các Reason Codes.
- Kiểm tra tính tương thích tuyệt đối giữa State và Reason (Hạ về UNKNOWN khi Reason không tương thích).
- Kiểm tra phát hiện và chuẩn hóa bằng chứng mâu thuẫn (Tránh False Original / False Duplicate).
- Kiểm tra ID không hợp lệ tự động vô hiệu hóa kết luận DUPLICATE/CLEAR về UNKNOWN/INVALID_ID.
"""

import json
import pytest
from check_features.models import DedupResult, AwemePost, validate_decimal_id


def test_64bit_id_string_preservation():
    """Kiểm tra các ID 64-bit của NEW 64 không bị mất độ chính xác hay làm tròn."""
    id1 = "7699321502767893782"
    id2 = "7699321502767893783"  # Khác đúng 1 đơn vị ở chữ số cuối
    group_id = "7698240297133804806"

    # Đảm bảo 2 ID khác nhau ở chữ số cuối cùng không bị coi là giống nhau
    assert id1 != id2
    # Đảm bảo float làm mất độ chính xác (IEEE 754 float làm tròn 2 ID này về cùng 1 số!)
    assert float(id1) == float(id2)

    # Khởi tạo DedupResult với id1
    res = DedupResult(
        aweme_id=id1,
        state="DUPLICATE",
        group_id=group_id,
        reason="CONFIRMED_DUPLICATE",
        source="ttm_sqlite"
    )
    assert res.aweme_id == id1
    assert res.group_id == group_id
    assert isinstance(res.aweme_id, str)
    assert isinstance(res.group_id, str)

    # Đảm bảo không bị trùng với id2
    assert res.aweme_id != id2

    # Kiểm tra qua JSON serialization / deserialization
    data = {
        "aweme_id": id1,
        "group_id": group_id,
        "state": "DUPLICATE",
        "reason": "CONFIRMED_DUPLICATE",
    }
    dumped = json.dumps(data)
    loaded = json.loads(dumped)
    assert loaded["aweme_id"] == id1
    assert loaded["group_id"] == group_id


def test_validate_decimal_id_rejects_floats_booleans_and_invalid_chars():
    """Kiểm tra validate_decimal_id từ chối boolean, float, scientific notation, chữ cái, dấu cộng, số âm, Unicode và số 0 ở đầu."""
    # 1. Từ chối boolean (tránh bool là subclass của int)
    with pytest.raises(TypeError) as excinfo:
        validate_decimal_id(True, "aweme_id")
    assert "cannot be a boolean" in str(excinfo.value)

    with pytest.raises(TypeError) as excinfo:
        validate_decimal_id(False, "aweme_id")
    assert "cannot be a boolean" in str(excinfo.value)

    # 2. Từ chối float
    with pytest.raises(TypeError) as excinfo:
        validate_decimal_id(7699321502767893782.0, "aweme_id")
    assert "cannot be a float" in str(excinfo.value)

    # 3. Từ chối chuỗi biểu diễn float hoặc scientific notation
    with pytest.raises(ValueError):
        validate_decimal_id("7.699321502e+18", "aweme_id")

    with pytest.raises(ValueError):
        validate_decimal_id("7e18", "aweme_id")

    with pytest.raises(ValueError):
        validate_decimal_id("7699321502.55", "aweme_id")

    # 4. Từ chối số âm, dấu cộng, chuỗi chữ cái, chữ số Unicode (fullwidth, arabic, superscript)
    with pytest.raises(ValueError):
        validate_decimal_id("-7699321502", "aweme_id")

    with pytest.raises(ValueError):
        validate_decimal_id("+7699321502", "aweme_id")

    with pytest.raises(ValueError):
        validate_decimal_id("abc", "aweme_id")

    with pytest.raises(ValueError):
        validate_decimal_id("１２３", "aweme_id")

    with pytest.raises(ValueError):
        validate_decimal_id("١٢٣", "aweme_id")

    with pytest.raises(ValueError):
        validate_decimal_id("²", "aweme_id")

    # 5. Từ chối số có số 0 ở đầu ('00', '0123')
    with pytest.raises(ValueError) as excinfo:
        validate_decimal_id("00", "aweme_id")
    assert "cannot have leading zeros" in str(excinfo.value)

    with pytest.raises(ValueError) as excinfo:
        validate_decimal_id("0123", "group_id")
    assert "cannot have leading zeros" in str(excinfo.value)

    # 6. Kiểm tra allow_empty=False
    with pytest.raises(ValueError):
        validate_decimal_id("", "aweme_id", allow_empty=False)

    with pytest.raises(ValueError):
        validate_decimal_id(None, "aweme_id", allow_empty=False)

    # 7. Chấp nhận chuỗi số nguyên và rỗng/0 khi allow_empty=True
    assert validate_decimal_id("7699321502767893782", "aweme_id") == "7699321502767893782"
    assert validate_decimal_id(7699321502767893782, "aweme_id") == "7699321502767893782"
    assert validate_decimal_id("0", "aweme_id") == "0"
    assert validate_decimal_id("", "aweme_id", allow_empty=True) == ""
    assert validate_decimal_id(None, "aweme_id", allow_empty=True) == ""


def test_dedup_result_three_states():
    """Kiểm tra tính đúng đắn của 3 trạng thái DUPLICATE, CLEAR, UNKNOWN."""
    id_test = "7699321502767893782"
    group_dup = "7698240297133804806"

    # Case 1: DUPLICATE (group_id != aweme_id)
    r_dup = DedupResult(
        aweme_id=id_test,
        state="DUPLICATE",
        group_id=group_dup,
        reason="CONFIRMED_DUPLICATE",
        source="ttm_sqlite"
    )
    post_dup = AwemePost(aweme_id=id_test, dedup_result=r_dup)
    assert post_dup.is_dup is True
    assert post_dup.duplicate_status == "duplicate"

    # Case 2: CLEAR (group_id == aweme_id trong nguồn đối soát)
    r_clear = DedupResult(
        aweme_id=id_test,
        state="CLEAR",
        group_id=id_test,
        reason="MATCHED_ORIGINAL",
        source="ttm_sqlite"
    )
    post_clear = AwemePost(aweme_id=id_test, dedup_result=r_clear)
    assert post_clear.is_dup is False
    assert post_clear.duplicate_status == "original"

    # Case 3: UNKNOWN (không tìm thấy bằng chứng)
    r_unknown = DedupResult(
        aweme_id=id_test,
        state="UNKNOWN",
        group_id=None,
        reason="NO_EVIDENCE",
        source="none"
    )
    post_unknown = AwemePost(aweme_id=id_test, dedup_result=r_unknown)
    assert post_unknown.is_dup is False
    assert post_unknown.duplicate_status == "unknown"


def test_contradictory_evidence_normalization():
    """Kiểm tra DedupResult tự động phát hiện bằng chứng mâu thuẫn và trả UNKNOWN / CONFLICTING_EVIDENCE."""
    id_test = "7699321502767893782"
    group_dup = "7698240297133804806"

    # Mâu thuẫn 1: Khai báo CLEAR nhưng group_id lại khác aweme_id
    r_bad_clear = DedupResult(
        aweme_id=id_test,
        state="CLEAR",
        group_id=group_dup,
        reason="MATCHED_ORIGINAL"
    )
    assert r_bad_clear.state == "UNKNOWN"
    assert r_bad_clear.reason == "CONFLICTING_EVIDENCE"

    # Mâu thuẫn 2: Khai báo CLEAR nhưng không có group_id
    r_clear_no_group = DedupResult(
        aweme_id=id_test,
        state="CLEAR",
        group_id=None,
        reason="MATCHED_ORIGINAL"
    )
    assert r_clear_no_group.state == "UNKNOWN"
    assert r_clear_no_group.reason == "CONFLICTING_EVIDENCE"

    # Mâu thuẫn 3: Khai báo DUPLICATE nhưng group_id lại bằng aweme_id
    r_bad_dup = DedupResult(
        aweme_id=id_test,
        state="DUPLICATE",
        group_id=id_test,
        reason="CONFIRMED_DUPLICATE"
    )
    assert r_bad_dup.state == "UNKNOWN"
    assert r_bad_dup.reason == "CONFLICTING_EVIDENCE"


def test_state_reason_compatibility_and_downgrade():
    """Kiểm tra DedupResult tự động hạ state về UNKNOWN khi Reason không tương thích hoặc không xác minh."""
    id_test = "7699321502767893782"
    group_dup = "7698240297133804806"

    # 1. DUPLICATE nhưng reason là UNVERIFIED_SEMANTICS -> phải hạ về UNKNOWN
    r1 = DedupResult(
        aweme_id=id_test,
        group_id=group_dup,
        state="DUPLICATE",
        reason="UNVERIFIED_SEMANTICS"
    )
    assert r1.state == "UNKNOWN"
    assert r1.reason == "UNVERIFIED_SEMANTICS"

    # 2. CLEAR nhưng reason là UNVERIFIED_SEMANTICS -> phải hạ về UNKNOWN
    r2 = DedupResult(
        aweme_id=id_test,
        group_id=id_test,
        state="CLEAR",
        reason="UNVERIFIED_SEMANTICS"
    )
    assert r2.state == "UNKNOWN"
    assert r2.reason == "UNVERIFIED_SEMANTICS"

    # 3. DUPLICATE nhưng reason là NO_EVIDENCE -> phải hạ về UNKNOWN
    r3 = DedupResult(
        aweme_id=id_test,
        group_id=group_dup,
        state="DUPLICATE",
        reason="NO_EVIDENCE"
    )
    assert r3.state == "UNKNOWN"
    assert r3.reason == "NO_EVIDENCE"

    # 4. UNKNOWN nhưng reason là CONFIRMED_DUPLICATE -> mâu thuẫn, chuyển reason thành CONFLICTING_EVIDENCE
    r4 = DedupResult(
        aweme_id=id_test,
        group_id=group_dup,
        state="UNKNOWN",
        reason="CONFIRMED_DUPLICATE"
    )
    assert r4.state == "UNKNOWN"
    assert r4.reason == "CONFLICTING_EVIDENCE"

    # 5. CLEAR nhưng reason là CONFIRMED_DUPLICATE (dù ID khớp nhau) -> hạ state về UNKNOWN và chuyển reason thành CONFLICTING_EVIDENCE
    r5 = DedupResult(
        aweme_id="123",
        group_id="123",
        state="CLEAR",
        reason="CONFIRMED_DUPLICATE"
    )
    assert r5.state == "UNKNOWN"
    assert r5.reason == "CONFLICTING_EVIDENCE"

    # 6. DUPLICATE nhưng reason là MATCHED_ORIGINAL (dù ID khác nhau) -> hạ state về UNKNOWN và chuyển reason thành CONFLICTING_EVIDENCE
    r6 = DedupResult(
        aweme_id="123",
        group_id="456",
        state="DUPLICATE",
        reason="MATCHED_ORIGINAL"
    )
    assert r6.state == "UNKNOWN"
    assert r6.reason == "CONFLICTING_EVIDENCE"


def test_invalid_state_and_reason_handling():
    """Kiểm tra State hoặc Reason không hợp lệ bị chuẩn hóa về UNKNOWN / UNVERIFIED_SEMANTICS."""
    r_invalid = DedupResult(
        aweme_id="7699321502767893782",
        state="INVALID_STATE_XYZ",
        reason="ARBITRARY_REASON"
    )
    assert r_invalid.state == "UNKNOWN"
    assert r_invalid.reason == "UNVERIFIED_SEMANTICS"

    # Reason không hợp lệ đi cùng DUPLICATE cũng bị hạ về UNKNOWN
    r_invalid_dup = DedupResult(
        aweme_id="7699321502767893782",
        group_id="7698240297133804806",
        state="DUPLICATE",
        reason="TOTALLY_BOGUS_REASON"
    )
    assert r_invalid_dup.state == "UNKNOWN"
    assert r_invalid_dup.reason == "UNVERIFIED_SEMANTICS"


def test_invalid_id_cannot_produce_definitive_dedup():
    """Kiểm tra ID không hợp lệ ('abc', '7e18', số âm, bool, Unicode '１２３', số 0 ở đầu '00', '0123') tuyệt đối không thể giữ DUPLICATE hay CLEAR."""
    # 1. aweme_id là chuỗi chữ cái
    r1 = DedupResult(aweme_id="abc", group_id="7698240297133804806", state="DUPLICATE")
    assert r1.state == "UNKNOWN"
    assert r1.reason == "INVALID_ID"

    # 2. group_id là số âm
    r2 = DedupResult(aweme_id="7699321502767893782", group_id="-99999", state="DUPLICATE")
    assert r2.state == "UNKNOWN"
    assert r2.reason == "INVALID_ID"

    # 3. group_id là chuỗi chữ cái
    r3 = DedupResult(aweme_id="7699321502767893782", group_id="invalid_gid", state="CLEAR")
    assert r3.state == "UNKNOWN"
    assert r3.reason == "INVALID_ID"

    # 4. aweme_id là chữ số Unicode toàn phần ("１２３")
    r4 = DedupResult(aweme_id="１２３", group_id="123", state="DUPLICATE")
    assert r4.state == "UNKNOWN"
    assert r4.reason == "INVALID_ID"

    # 5. group_id có số 0 ở đầu ("00")
    r5 = DedupResult(aweme_id="123", group_id="00", state="DUPLICATE")
    assert r5.state == "UNKNOWN"
    assert r5.reason == "INVALID_ID"

    # 6. group_id có số 0 ở đầu ("0123")
    r6 = DedupResult(aweme_id="123", group_id="0123", state="DUPLICATE")
    assert r6.state == "UNKNOWN"
    assert r6.reason == "INVALID_ID"

    # 7. AwemePost tích hợp
    post = AwemePost(aweme_id="7699321502767893782", dedup_result=r2)
    assert post.is_dup is False
    assert post.duplicate_status == "unknown"
