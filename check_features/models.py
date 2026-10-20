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
    shadow: str = "no"  # "no" | "suspect" | "yes"
    shadow_reason: str = ""
    is_top: bool = False
    ai_label: bool = False
    risk_warn: bool = False
    is_paid: bool = False
    is_delete: bool = False
    is_private: bool = False
    penalty_flag: bool = False
    group_id: str = ""
    share_url: str = ""
    download_url: str = ""
    penalty_details: Optional[PenaltyDetails] = None

    @property
    def is_dup(self) -> bool:
        """Video được tính là trùng lặp (repost) khi có group_id và khác aweme_id."""
        return bool(self.group_id and self.group_id != self.aweme_id)


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
