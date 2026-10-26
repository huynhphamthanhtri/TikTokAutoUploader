# -*- coding: utf-8 -*-
"""
check_features.models - Data Transfer Objects (DTO) cho nhóm Check Thông Tin.
Được chuẩn hoá theo đúng schema thực tế của TikTok API / TikTokManager.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UserInfo:
    """DTO thông tin chi tiết một tài khoản TikTok (F1 - CheckUser)."""
    nickname: str = ""
    username: str = ""
    bio: str = ""
    email: Optional[str] = None
    mobile: Optional[str] = None
    uid: str = ""
    is_email_verified: bool = False
    phone_collected: bool = False
    store_country: str = ""
    user_create_time: int = 0
    nickname_modify_time: int = 0
    unique_id_modify_time: int = 0
    studio_ok: bool = True
    error: Optional[str] = None

    @property
    def nickname_cooldown_days(self) -> int:
        """Số ngày còn lại trước khi được phép đổi nickname (chu kỳ 7 ngày)."""
        import time
        if not self.nickname_modify_time:
            return 0
        now = int(time.time())
        cooldown_period = 7 * 86400
        elapsed = now - self.nickname_modify_time
        if elapsed >= cooldown_period:
            return 0
        import math
        return max(1, math.ceil((cooldown_period - elapsed) / 86400))


@dataclass
class PenaltyDetails:
    """DTO chi tiết án phạt (Strike) của video (F2)."""
    has_penalty: bool = False
    penalty_id: str = ""
    issuer: str = ""
    reason_codes: List[str] = field(default_factory=list)
    appeal_status: int = 0  # 0=chưa, 1=chưa kháng, 2=đang xét, 3=thất bại, 4=hoàn tất
    error: Optional[str] = None


VALID_DEDUP_STATES = {"DUPLICATE", "CLEAR", "UNKNOWN"}
VALID_DEDUP_REASONS = {
    "CONFIRMED_DUPLICATE",
    "MATCHED_ORIGINAL",
    "NO_EVIDENCE",
    "SOURCE_ERROR",
    "INVALID_ID",
    "UNVERIFIED_SEMANTICS",
    "CONFLICTING_EVIDENCE",
}


def validate_decimal_id(val: Any, field_name: str, allow_empty: bool = True) -> str:
    """
    Xác thực nghiêm ngặt ID bảo toàn chuỗi 64-bit:
    - Bắt buộc là chuỗi ký tự số thập phân ASCII (0-9).
    - Từ chối triệt để kiểu boolean (bool là subclass của int trong Python).
    - Từ chối triệt để kiểu float (tránh mất mát độ chính xác IEEE 754).
    - Từ chối các ký tự không phải số: dấu âm, dấu cộng, dấu chấm, scientific notation ('7e18'), ký tự alphabet.
    - Cho phép rỗng/None khi và chỉ khi allow_empty=True. Nếu allow_empty=False mà rỗng thì ném ValueError.
    """
    if isinstance(val, bool):
        raise TypeError(f"{field_name} cannot be a boolean: {val!r}")
    if isinstance(val, float):
        raise TypeError(f"{field_name} cannot be a float (causes 64-bit precision loss). Must be a decimal string.")
    if val is None:
        if allow_empty:
            return ""
        raise ValueError(f"{field_name} cannot be empty or None")

    s = str(val).strip()
    if not s:
        if allow_empty:
            return ""
        raise ValueError(f"{field_name} cannot be empty")

    if not (s.isascii() and s.isdigit()):
        raise ValueError(f"{field_name} must contain only decimal digits (0-9): {val!r}")

    if len(s) > 1 and s.startswith("0"):
        raise ValueError(f"{field_name} cannot have leading zeros: {val!r}")

    return s


@dataclass
class DedupResult:
    """
    DTO kết quả đối soát trùng lặp cho một bài đăng (F2 - CheckPost).
    Tuân thủ Hợp đồng 3 trạng thái phân minh theo AGENTS.md:
    - 'DUPLICATE': 🔴 Có bằng chứng xác nhận group_id != aweme_id.
    - 'CLEAR': ✅ Có bằng chứng xác nhận group_id == aweme_id trong phạm vi nguồn đối soát.
    - 'UNKNOWN': ⚠️ Thiếu dữ liệu, lỗi truy vấn, hoặc ngữ nghĩa chưa được kiểm chứng.
    """
    aweme_id: str = ""
    state: str = "UNKNOWN"  # 'DUPLICATE' | 'CLEAR' | 'UNKNOWN'
    group_id: Optional[str] = None
    reason: str = "NO_EVIDENCE"  # 'CONFIRMED_DUPLICATE' | 'MATCHED_ORIGINAL' | 'NO_EVIDENCE' | 'SOURCE_ERROR' | 'INVALID_ID' | 'UNVERIFIED_SEMANTICS' | 'CONFLICTING_EVIDENCE'
    source: str = "none"  # 'ttm_sqlite' | 'web_detail' | 'none'
    detected_at: Optional[int] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # 1. Xác thực ID 64-bit nghiêm ngặt
        try:
            self.aweme_id = validate_decimal_id(self.aweme_id, "aweme_id", allow_empty=True)
        except (TypeError, ValueError):
            self.state = "UNKNOWN"
            self.reason = "INVALID_ID"
            self.aweme_id = ""

        try:
            if self.group_id is not None:
                self.group_id = validate_decimal_id(self.group_id, "group_id", allow_empty=True)
        except (TypeError, ValueError):
            self.state = "UNKNOWN"
            self.reason = "INVALID_ID"
            self.group_id = None

        # 2. Chuẩn hóa & xác thực State
        if not isinstance(self.state, str) or self.state.upper() not in VALID_DEDUP_STATES:
            self.state = "UNKNOWN"
            self.reason = "UNVERIFIED_SEMANTICS"
        else:
            self.state = self.state.upper()

        # 3. Chuẩn hóa & xác thực Reason
        if not isinstance(self.reason, str) or self.reason.upper() not in VALID_DEDUP_REASONS:
            self.reason = "UNVERIFIED_SEMANTICS"
            self.state = "UNKNOWN"
        else:
            self.reason = self.reason.upper()

        # 4. Đảm bảo tính tương thích tuyệt đối giữa State và Reason (AGENTS.md contract)
        if self.state == "CLEAR":
            if self.reason != "MATCHED_ORIGINAL":
                self.state = "UNKNOWN"
            # CLEAR bắt buộc phải có group_id hợp lệ và bằng chính aweme_id (khác '0' và khác rỗng)
            if not self.group_id or self.group_id == "0" or not self.aweme_id or self.aweme_id == "0" or self.group_id != self.aweme_id:
                self.state = "UNKNOWN"
                self.reason = "CONFLICTING_EVIDENCE"
        elif self.state == "DUPLICATE":
            if self.reason != "CONFIRMED_DUPLICATE":
                self.state = "UNKNOWN"
            # DUPLICATE bắt buộc group_id khác aweme_id và cả hai đều khác rỗng/'0'
            if not self.group_id or self.group_id == "0" or not self.aweme_id or self.aweme_id == "0" or self.group_id == self.aweme_id:
                self.state = "UNKNOWN"
                self.reason = "CONFLICTING_EVIDENCE"

        # 5. Chuẩn hóa reason cuối cùng nếu state là UNKNOWN (chạy sau toàn bộ các nhánh trên)
        if self.state == "UNKNOWN":
            if self.reason in ("CONFIRMED_DUPLICATE", "MATCHED_ORIGINAL"):
                self.reason = "CONFLICTING_EVIDENCE"


@dataclass
class AwemePost:
    """DTO thông tin bài đăng video của tài khoản (F2 - CheckPost)."""
    aweme_id: str = ""
    desc: str = ""
    cover: str = ""
    duration: int = 0  # Seconds
    create_time: int = 0  # Timestamp
    device: str = "web"  # web | phone
    source: str = ""
    play: int = 0
    like: int = 0
    comment: int = 0
    share: int = 0
    save: int = 0
    region: str = ""
    shadow: str = "unknown"  # "no" | "suspect" | "yes" | "unknown"
    shadow_reason: str = ""
    is_top: bool = False
    ai_label: bool = False
    risk_warn: bool = False
    is_paid: bool = False
    is_delete: bool = False
    is_private: bool = False
    penalty_flag: bool = False
    group_id: str = ""
    feed_group_id: str = ""
    duplicate_reason: str = ""
    source_label: str = ""
    data_source: str = "creator_studio"
    share_url: str = ""
    download_url: str = ""
    penalty_details: Optional[PenaltyDetails] = None
    dedup_result: Optional[DedupResult] = None

    def __post_init__(self):
        # Bảo toàn ID là chuỗi thập phân 64-bit
        try:
            self.aweme_id = validate_decimal_id(self.aweme_id, "aweme_id", allow_empty=True)
        except (TypeError, ValueError):
            self.aweme_id = ""

        try:
            self.group_id = validate_decimal_id(self.group_id, "group_id", allow_empty=True)
        except (TypeError, ValueError):
            self.group_id = ""

        try:
            self.feed_group_id = validate_decimal_id(self.feed_group_id, "feed_group_id", allow_empty=True)
        except (TypeError, ValueError):
            self.feed_group_id = ""

    @property
    def flags(self) -> List[str]:
        """Danh sách nhãn đặc biệt dạng chuỗi."""
        res = []
        if self.ai_label:
            res.append("AI")
        if self.is_top:
            res.append("Ghim")
        if self.risk_warn:
            res.append("Cảnh báo")
        if self.is_paid:
            res.append("Trả phí")
        return res

    @property
    def is_dup(self) -> bool:
        """Video được tính là trùng lặp khi có bằng chứng DUPLICATE (hoặc group_id != aweme_id)."""
        if self.dedup_result is not None:
            return self.dedup_result.state == "DUPLICATE"
        gid = str(self.group_id or "").strip()
        aid = str(self.aweme_id or "").strip()
        return bool(gid and gid != "0" and aid and gid != aid)

    @property
    def duplicate_status(self) -> str:
        """
        Xác định rõ ràng 3 trạng thái trùng lặp:
        - 'duplicate': Có bằng chứng DUPLICATE (hoặc group_id hợp lệ != aweme_id).
        - 'original': Có bằng chứng CLEAR (hoặc group_id hợp lệ == aweme_id).
        - 'unknown': Không có bằng chứng hoặc dữ liệu chưa đủ đối soát.
        """
        if self.dedup_result is not None:
            state = self.dedup_result.state.lower()
            if state == "clear":
                return "original"
            return state
        gid = str(self.group_id or "").strip()
        aid = str(self.aweme_id or "").strip()
        if not gid or gid == "0" or not aid:
            return "unknown"
        if gid != aid:
            return "duplicate"
        return "original" 


@dataclass
class ChannelMeta:
    """DTO thông tin kênh TikTok công khai (F3 - CheckDup)."""
    nickname: str = ""
    unique_id: str = ""
    avatar: str = ""
    followers: int = 0
    video_count: int = 0
    sec_uid: str = ""
    error: Optional[str] = None


@dataclass
class VPCSVideo:
    """DTO video trong chương trình Creator Rewards Program / VPCS (F4)."""
    vid: str = ""
    cover_url: str = ""
    description: str = ""
    duration_sec: int = 0
    video_posted_date: int = 0
    eligibility: bool = True
    appeal_status: int = 0  # 0=chưa kháng, 1=chưa kháng, 2=đang xét, 3=thất bại, 4=kháng xịt
    ineligible_reasons: List[Dict[str, Any]] = field(default_factory=list)
    violation_title: str = ""
    violation_description: str = ""
    expiry_date: int = 0
    days_remaining: int = 0

    @property
    def can_appeal(self) -> bool:
        """Quy tắc kiểm tra xem video có thể gửi kháng nghị hay không."""
        if self.eligibility:
            return False
        if self.appeal_status in (0, 1, 3):
            return True
        if self.appeal_status == 4 and bool(self.ineligible_reasons):
            return True
        return False


@dataclass
class LivePostSnapshot:
    """
    DTO Snapshot danh sách bài đăng từ Creator Studio API (F2 - CheckPost).
    Phản ánh tính toàn vẹn của dữ liệu thu thập được theo chuẩn AGENTS.md:
    - posts: Danh sách các bài đăng AwemePost đã khử trùng lặp theo aweme_id.
    - source: 'live_studio' (lấy từ máy chủ) hoặc 'cache_offline' (đọc từ cache local khi mất mạng).
    - fetched_at: Timestamp lúc thu thập snapshot.
    - completeness: 'COMPLETE' (đã duyệt hết phân trang has_more=False) hoặc 'PARTIAL' (bị ngắt giữa chừng do chạm limit, timeout, hoặc lỗi trang).
    - run_id: ID định danh phiên chạy để tránh race condition trên UI.
    - error: Chi tiết lỗi nếu có.
    """
    posts: List[AwemePost] = field(default_factory=list)
    source: str = "live_studio"  # 'live_studio' | 'cache_offline'
    fetched_at: int = 0
    completeness: str = "COMPLETE"  # 'COMPLETE' | 'PARTIAL'
    run_id: str = ""
    error: Optional[str] = None
    next_cursor: int = 0
    has_more: bool = False

    @property
    def total_count(self) -> int:
        return len(self.posts)
