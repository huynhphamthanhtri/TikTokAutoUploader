# -*- coding: utf-8 -*-
"""
check_features.services.post_moderation_evaluator - Động cơ đánh giá kiểm duyệt và phát hiện trùng lặp.
Tuân thủ nghiêm ngặt theo đặc tả F2 (dac_ta_chuc_nang_check_post.md) và phản biện P0/P1 từ Codex Gate 1:
- Chuẩn hóa kiểu dữ liệu an toàn: Phân biệt rõ ràng True, False, None (không biến None/Missing thành False).
- Bậc 6 chỉ kết luận 'no' khi TẤT CẢ 5 cờ bắt buộc đều có mặt hợp lệ và đều âm tính. Bất kỳ trường nào thiếu/null -> 'unknown'.
- Mặc định data_source là 'unknown', chỉ chấp nhận 'creator_studio' khi được chỉ định minh bạch.
- Bảo toàn chuỗi ID 64-bit chống lỗi chuỗi "00", khoảng trắng và làm tròn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import unicodedata
from typing import Any, Dict, List, Optional, Set, Tuple


def parse_safe_bool(val: Any) -> Optional[bool]:
    """
    Chuyển đổi an toàn sang boolean.
    Trả về:
    - True nếu là True, 1, 'true', '1'
    - False nếu là False, 0, 'false', '0'
    - None nếu là None, rỗng, 'null', hoặc giá trị không xác định.
    TUYỆT ĐỐI KHÔNG ép None thành False để tránh biến dữ liệu thiếu thành âm tính giả.
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        if val == 1:
            return True
        if val == 0:
            return False
        return None
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no"):
            return False
    return None


def parse_safe_int(val: Any) -> Optional[int]:
    """Chuyển đổi an toàn sang số nguyên. Trả về None nếu là None, rỗng hoặc không thể parse."""
    if val is None:
        return None
    if isinstance(val, bool):
        return None  # Tránh True -> 1, False -> 0
    try:
        s = str(val).strip()
        if not s:
            return None
        return int(s)
    except (ValueError, TypeError):
        return None


def clean_id_string(val: Any) -> str:
    """Chuẩn hóa chuỗi ID TikTok 64-bit, loại bỏ khoảng trắng và nhận diện chuỗi toàn số 0."""
    if val is None:
        return ""
    s = str(val).strip()
    if not s or set(s) == {"0"}:
        return ""
    return s


def sanitize_appeal_text(text: str) -> str:
    """Chuẩn hóa văn bản kháng nghị, loại bỏ zero-width characters và normalize Unicode NFC."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFC", str(text))
    # Loại bỏ các ký tự vô hình zero-width space
    zero_width = {"\u200b", "\u200c", "\u200d", "\ufeff", "\u2060"}
    for zw in zero_width:
        norm = norm.replace(zw, "")
    import re
    norm = re.sub(r'[ \t]+', ' ', norm)
    return norm.strip()


@dataclass
class ModerationEvaluation:
    """Kết quả đánh giá kiểm duyệt của một bài đăng TikTok."""
    shadow: str = "unknown"  # "no" | "suspect" | "yes" | "unknown"
    shadow_reason: str = ""
    is_duplicate: bool = False
    duplicate_reason: str = ""
    related_video_url: str = ""
    device: str = "unknown"  # "web" | "phone" | "unknown"
    source_label: str = ""   # "CapCut" | "Web Upload" | "Mobile App"
    flags: List[str] = field(default_factory=list)
    penalty_flag: bool = False
    rule_evidence: Dict[str, Any] = field(default_factory=dict)


class PostModerationEvaluator:
    """Động cơ hàm thuần (Pure Evaluator) đánh giá sức khỏe nội dung và trùng lặp."""

    RULESET_VERSION = "2026.10-f2.2"

    @classmethod
    def evaluate(cls, raw: Dict[str, Any], data_source: str = "unknown") -> ModerationEvaluation:
        """
        Đánh giá toàn diện một bản ghi video thô.
        Mặc định data_source là 'unknown'. Chỉ chấp nhận 'creator_studio' khi nguồn đã xác minh.
        """
        if not isinstance(raw, dict) or not raw:
            return ModerationEvaluation(
                shadow="unknown",
                shadow_reason="Dữ liệu bài đăng rỗng hoặc không hợp lệ.",
            )

        # ----------------------------------------------------------------------
        # 1. Trích xuất ID chuỗi an toàn 64-bit
        # ----------------------------------------------------------------------
        aweme_id = clean_id_string(raw.get("aweme_id") or raw.get("id") or raw.get("item_id"))
        group_id = clean_id_string(raw.get("group_id") or raw.get("groupId"))

        video = raw.get("video") if isinstance(raw.get("video"), dict) else {}
        stats = raw.get("statistics") or raw.get("stats") if isinstance(raw.get("statistics") or raw.get("stats"), dict) else {}
        review = raw.get("review_info") or raw.get("review_result") if isinstance(raw.get("review_info") or raw.get("review_result"), dict) else {}
        c2pa = raw.get("c2pa") if isinstance(raw.get("c2pa"), dict) else {}
        aigc = raw.get("aigc_info") or raw.get("aigc_label_type") if isinstance(raw.get("aigc_info") or raw.get("aigc_label_type"), dict) else {}

        # ----------------------------------------------------------------------
        # 2. Bảng Quyết Định 7 Bậc (Shadowban & Hạn Chế Phân Phối)
        # ----------------------------------------------------------------------
        evidence: Dict[str, Any] = {
            "ruleset_version": cls.RULESET_VERSION,
            "data_source": data_source,
            "aweme_id": aweme_id,
            "group_id": group_id,
        }

        # Nếu dữ liệu không đến từ Creator Studio (ví dụ web_feed, mobile_api, unknown),
        # chắc chắn không có các cờ kiểm duyệt chuyên sâu -> Bắt buộc là 'unknown'
        if data_source != "creator_studio":
            shadow = "unknown"
            shadow_reason = f"Dữ liệu từ nguồn {data_source}, chưa xác minh cờ kiểm duyệt Creator Studio."
            penalty_type = None
        else:
            # Bóc tách và chuẩn hóa từng cờ (giữ nguyên None nếu thiếu/không hợp lệ)
            is_proh_val = parse_safe_bool(raw.get("is_prohibited"))
            proh_status_val = parse_safe_int(raw.get("prohibited_status"))
            is_prohibited = (is_proh_val is True) or (proh_status_val == 1)

            self_see_val = parse_safe_bool(raw.get("self_see"))
            priv_status_val = parse_safe_int(raw.get("private_status"))
            self_see = (self_see_val is True) or (priv_status_val == 1)

            is_nff_val = parse_safe_bool(raw.get("is_nff_or_nr"))
            rev_status_val = parse_safe_int(review.get("review_status"))
            is_nff_or_nr = (is_nff_val is True) or (rev_status_val == 2)

            penalty_type = parse_safe_int(raw.get("display_penalty_type"))
            in_reviewing_val = parse_safe_bool(raw.get("in_reviewing"))
            in_reviewing = (in_reviewing_val is True)

            evidence.update({
                "is_prohibited": is_prohibited,
                "self_see": self_see,
                "is_nff_or_nr": is_nff_or_nr,
                "display_penalty_type": penalty_type,
                "in_reviewing": in_reviewing,
            })

            # Phân cấp ưu tiên (Hierarchy of Priorities)
            if is_prohibited:
                shadow = "yes"
                shadow_reason = "Cấm cứng (Bị gỡ vi phạm)"
            elif self_see:
                shadow = "yes"
                shadow_reason = "Chỉ mình thấy (Ép riêng tư)"
            elif is_nff_or_nr:
                shadow = "yes"
                shadow_reason = "Không lên FYP (Hạn chế đề xuất For You)"
            elif penalty_type is not None and penalty_type > 0:
                shadow = "yes"
                shadow_reason = f"Án phạt reach (Loại hình {penalty_type})"
            elif in_reviewing:
                shadow = "suspect"
                shadow_reason = "Đang trong hàng đợi kiểm duyệt"
            else:
                # P0 Fix triệt để: Bậc 6 CHỈ ĐẠT KHI TẤT CẢ 5 cờ bắt buộc đều có mặt hợp lệ và đều False/0.
                # Bất kỳ trường nào bị None, thiếu hoặc không hợp lệ -> Bắt buộc là 'unknown'.
                all_flags_valid = (
                    (is_proh_val is False or proh_status_val == 0) and
                    (self_see_val is False or priv_status_val == 0) and
                    (is_nff_val is False or rev_status_val == 0) and
                    (penalty_type == 0) and
                    (in_reviewing_val is False)
                )
                if all_flags_valid:
                    shadow = "no"
                    shadow_reason = "Không phát hiện cờ hạn chế"
                else:
                    shadow = "unknown"
                    shadow_reason = "Chưa xác định (Không đủ cơ sở xác minh toàn bộ 5 cờ kiểm duyệt)"

        # ----------------------------------------------------------------------
        # 3. Thuật toán Phát hiện Video Trùng Lặp (Duplicate Detection)
        # ----------------------------------------------------------------------
        is_duplicate = False
        duplicate_reason = ""
        related_url = ""

        if group_id and aweme_id and group_id != aweme_id:
            is_duplicate = True
            duplicate_reason = f"Nghi trùng theo Group ID ({group_id})"
            related_url = f"https://www.tiktok.com/@tiktok/video/{group_id}"
            evidence["duplicate_detected"] = True
        else:
            evidence["duplicate_detected"] = False

        # ----------------------------------------------------------------------
        # 4. Phân tích Nguồn Đăng & Thiết Bị (Device Source)
        # ----------------------------------------------------------------------
        device = "unknown"
        source_label = "Upload"

        used_funcs = raw.get("creation_used_functions") or []
        shoot_tab = str(raw.get("shoot_tab") or "").lower()

        if "web" in used_funcs or shoot_tab == "web" or raw.get("source") == "web":
            device = "web"
            source_label = "Web Upload"
        elif c2pa.get("is_capcut") or "capcut" in str(raw).lower():
            device = "phone"
            source_label = "CapCut"
        elif used_funcs or shoot_tab:
            device = "phone"
            source_label = "Điện thoại"

        # ----------------------------------------------------------------------
        # 5. Phân tích Cờ Đặc Biệt (Flags: AI, Ghim, Cảnh báo, Trả phí)
        # ----------------------------------------------------------------------
        flags: List[str] = []
        if (parse_safe_bool(aigc.get("created_by_ai")) is True) or (parse_safe_bool(raw.get("aiLabel")) is True):
            flags.append("AI")
        if (parse_safe_int(raw.get("is_top")) == 1) or (parse_safe_bool(raw.get("isTop")) is True):
            flags.append("Ghim")
        if bool(raw.get("risk_infos") or raw.get("risk_warn") or raw.get("warn_info")):
            flags.append("Cảnh báo")
        if (parse_safe_bool(raw.get("is_paid_content")) is True) or (parse_safe_bool(raw.get("isPaid")) is True):
            flags.append("Trả phí")

        # Cờ phạt tổng quát
        penalty_flag = bool(
            (penalty_type is not None and penalty_type > 0)
            or raw.get("penalty_context")
            or raw.get("penaltyFlag")
            or raw.get("risk_warn")
            or raw.get("risk_infos")
            or raw.get("penalties")
            or is_duplicate
        )

        return ModerationEvaluation(
            shadow=shadow,
            shadow_reason=shadow_reason,
            is_duplicate=is_duplicate,
            duplicate_reason=duplicate_reason,
            related_video_url=related_url,
            device=device,
            source_label=source_label,
            flags=flags,
            penalty_flag=penalty_flag,
            rule_evidence=evidence,
        )
