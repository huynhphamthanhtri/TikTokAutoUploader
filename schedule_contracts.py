"""
schedule_contracts.py - Hợp đồng dữ liệu chuẩn hóa bất biến cho phân hệ Lên lịch đăng.

Chịu trách nhiệm:
1. Định nghĩa các Dataclass bất biến (frozen=True) cho SchedulePlan, PlannedTask, VideoProbeResult.
2. Bảo vệ tính bất biến sâu (Deep Immutability) đệ quy bằng MappingProxyType và tuple (F08).
3. Hỗ trợ _deep_unfreeze đệ quy an toàn cho to_dict() chống lỗi cannot pickle mappingproxy (Round 3 fix).
4. Hỗ trợ đầy đủ Mapping (dict, MappingProxyType) và validation kiểu dữ liệu (F14).
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

ExecutionMode = Literal["SERVER_SCHEDULE", "LOCAL_QUEUE"]
ResolutionMode = Literal["APPEND", "OVERWRITE", "SKIP"]
OrderMode = Literal["ORIGINAL", "SERIES", "SHUFFLE"]


class ScheduleCommitError(Exception):
    """Lỗi cơ sở khi commit kế hoạch lên lịch."""
    pass


class StalePlanError(ScheduleCommitError):
    """Lỗi do trạng thái dữ liệu đã thay đổi so với thời điểm preview."""
    pass


class InvalidPlanCommitError(ScheduleCommitError):
    """Lỗi kế hoạch không hợp lệ hoặc rỗng."""
    pass


@dataclass(frozen=True)
class ScheduleCommitResult:
    """Kết quả sau khi commit SchedulePlan vào SQLite nguyên tử (P2)."""
    plan_id: str
    created_task_ids: Tuple[str, ...]
    cancelled_task_ids: Tuple[str, ...] = ()
    already_committed: bool = False
    resolution_mode: str = "APPEND"
    committed_at: str = ""

    def __post_init__(self):
        object.__setattr__(self, "created_task_ids", tuple(self.created_task_ids))
        object.__setattr__(self, "cancelled_task_ids", tuple(self.cancelled_task_ids) if self.cancelled_task_ids else ())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "created_task_ids": list(self.created_task_ids),
            "cancelled_task_ids": list(self.cancelled_task_ids),
            "already_committed": self.already_committed,
            "resolution_mode": self.resolution_mode,
            "committed_at": self.committed_at,
        }


def _deep_freeze(obj: Any) -> Any:
    """Đóng băng đệ quy mọi cấu trúc container thành MappingProxyType và tuple bất biến (F08)."""
    if isinstance(obj, Mapping):
        return MappingProxyType({str(k): _deep_freeze(v) for k, v in sorted(obj.items(), key=lambda x: str(x[0]))})
    elif isinstance(obj, (set, frozenset)):
        return tuple(sorted(_deep_freeze(x) for x in obj))
    elif isinstance(obj, (list, tuple)):
        return tuple(_deep_freeze(x) for x in obj)
    elif isinstance(obj, (bytearray, memoryview)):
        return bytes(obj)
    return obj


def _deep_unfreeze(obj: Any) -> Any:
    """Chuyển đổi đệ quy từ MappingProxyType/tuple sang dict/list thông thường có thể pickle/json (F08)."""
    if isinstance(obj, Mapping):
        return {str(k): _deep_unfreeze(v) for k, v in sorted(obj.items(), key=lambda x: str(x[0]))}
    elif isinstance(obj, (list, tuple, set, frozenset)):
        return [_deep_unfreeze(x) for x in obj]
    elif isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    return obj


@dataclass(frozen=True)
class VideoValidationPolicy:
    """Chính sách kiểm tra video hợp lệ."""
    min_duration_seconds: float = 3.0
    max_duration_seconds: float = 600.0
    allowed_codecs: Tuple[str, ...] = ("h264", "hevc", "av1")
    require_audio: bool = False
    max_file_size_bytes: int = 4 * 1024 * 1024 * 1024  # 4GB max


@dataclass(frozen=True)
class VideoProbeResult:
    """Kết quả kiểm tra kỹ thuật và siêu dữ liệu của file video."""
    canonical_path: str
    size_bytes: int
    mtime_ns: int
    duration_seconds: Optional[float]
    video_codec: Optional[str]
    audio_codec: Optional[str]
    width: Optional[int]
    height: Optional[int]
    pixel_format: Optional[str]
    frame_rate: Optional[float]
    is_valid: bool
    issues: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TimelineSlot:
    """Cấu hình một khung giờ đăng trong ngày."""
    slot_id: str
    label: str
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    crosses_midnight: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlannedTask:
    """Một tác vụ xuất bản đã được tính toán đầy đủ mốc thời gian và siêu dữ liệu."""
    slot_id: str
    profile_name: str
    tiktok_account: str
    video_path: str
    canonical_video_path: str
    video_filename: str
    title: str
    profile_timezone: str
    scheduled_epoch: int
    scheduled_time_local: str
    execution_mode: ExecutionMode
    delete_source_after_post: bool
    source_size_bytes: int = 0
    source_mtime_ns: int = 0

    def __post_init__(self):
        if self.execution_mode not in ("SERVER_SCHEDULE", "LOCAL_QUEUE"):
            raise ValueError(
                f"PlannedTask execution_mode không hợp lệ: '{self.execution_mode}'. "
                f"Chỉ chấp nhận 'SERVER_SCHEDULE' hoặc 'LOCAL_QUEUE'."
            )
        if not self.profile_name or not str(self.profile_name).strip():
            raise ValueError("PlannedTask profile_name không được để trống.")
        if self.profile_name != self.profile_name.strip():
            raise ValueError(f"PlannedTask profile_name '{self.profile_name}' chứa khoảng trắng thừa.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "profile_name": self.profile_name,
            "tiktok_account": self.tiktok_account,
            "video_path": self.video_path,
            "canonical_video_path": self.canonical_video_path,
            "video_filename": self.video_filename,
            "title": self.title,
            "profile_timezone": self.profile_timezone,
            "scheduled_time": self.scheduled_time_local,
            "scheduled_epoch": self.scheduled_epoch,
            "golden_hour_slot": self.slot_id,
            "execution_mode": self.execution_mode,
            "status": "PENDING",
            "upload_engine": "DIRECT_API",
            "delete_source_after_post": self.delete_source_after_post,
            "source_size_bytes": self.source_size_bytes,
            "source_mtime_ns": self.source_mtime_ns,
        }


@dataclass(frozen=True)
class UnassignedVideo:
    """Video không thể xếp vào lịch kèm lý do cụ thể."""
    video_path: str
    reason: str
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanWarning:
    """Cảnh báo trong quá trình sinh kế hoạch."""
    code: str
    profile_name: str
    video_path: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConflictSnapshot:
    """Ảnh chụp trạng thái xung đột tại thời điểm lập lịch (Bất biến sâu đệ quy - F08)."""
    conflicted_profiles: Tuple[str, ...]
    snapshot_fingerprint: str
    existing_tasks: Mapping[str, Tuple[Mapping[str, Any], ...]] = field(default_factory=dict)
    can_read_tasks: bool = True

    def __post_init__(self):
        object.__setattr__(self, "conflicted_profiles", tuple(sorted(self.conflicted_profiles)))
        frozen_tasks = _deep_freeze(self.existing_tasks)
        object.__setattr__(self, "existing_tasks", frozen_tasks)

    def to_dict(self) -> Dict[str, Any]:
        # Dùng _deep_unfreeze chống lỗi cannot pickle mappingproxy (F08, Round 3)
        return {
            "conflicted_profiles": list(self.conflicted_profiles),
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "can_read_tasks": self.can_read_tasks,
            "existing_tasks": {
                k: [_deep_unfreeze(t) for t in v]
                for k, v in self.existing_tasks.items()
            },
        }


@dataclass(frozen=True)
class SchedulePlan:
    """Kế hoạch lên lịch hoàn chỉnh, bất biến sâu và tất định (F08)."""
    plan_id: str
    created_at: str
    seed: int
    profile_assignments: Mapping[str, Tuple[PlannedTask, ...]]
    unassigned_videos: Tuple[UnassignedVideo, ...] = ()
    conflicts_identified: Optional[ConflictSnapshot] = None
    resolution_mode: ResolutionMode = "APPEND"
    warnings: Tuple[PlanWarning, ...] = ()
    requested_profile_names: Tuple[str, ...] = ()

    def __post_init__(self):
        frozen_map = _deep_freeze(self.profile_assignments)
        object.__setattr__(self, "profile_assignments", frozen_map)
        object.__setattr__(self, "unassigned_videos", tuple(self.unassigned_videos) if self.unassigned_videos else ())
        object.__setattr__(self, "warnings", tuple(self.warnings) if self.warnings else ())
        norm_profiles = tuple(sorted(set(str(p).strip() for p in self.requested_profile_names if str(p).strip()))) if self.requested_profile_names else ()
        object.__setattr__(self, "requested_profile_names", norm_profiles)

    @property
    def total_planned_tasks(self) -> int:
        return sum(len(tasks) for tasks in self.profile_assignments.values())

    @property
    def total_unassigned(self) -> int:
        return len(self.unassigned_videos)

    def to_dict(self) -> Dict[str, Any]:
        """Tạo dict tách biệt hoàn toàn không chia sẻ tham chiếu."""
        return {
            "plan_id": self.plan_id,
            "created_at": self.created_at,
            "seed": self.seed,
            "profile_assignments": {
                p: [t.to_dict() for t in tasks] for p, tasks in self.profile_assignments.items()
            },
            "unassigned_videos": [u.to_dict() for u in self.unassigned_videos],
            "conflicts_identified": self.conflicts_identified.to_dict() if self.conflicts_identified else None,
            "resolution_mode": self.resolution_mode,
            "warnings": [w.to_dict() for w in self.warnings],
            "requested_profile_names": list(self.requested_profile_names),
            "total_planned_tasks": self.total_planned_tasks,
            "total_unassigned": self.total_unassigned,
        }

    def compute_payload_hash(self) -> str:
        """Tính hash nội dung phục vụ kiểm tra tính bất biến và idempotent commit."""
        dumped = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    def validate_for_commit(self) -> None:
        """Kiểm tra tính hợp lệ toàn vẹn của kế hoạch trước khi commit xuống SQLite (F02)."""
        if not self.plan_id or not str(self.plan_id).strip():
            raise InvalidPlanCommitError("plan_id không được để trống khi commit.")

        if self.resolution_mode not in ("APPEND", "OVERWRITE"):
            raise InvalidPlanCommitError(
                f"resolution_mode không hợp lệ: '{self.resolution_mode}'. Chỉ chấp nhận 'APPEND' hoặc 'OVERWRITE'."
            )

        if self.total_planned_tasks <= 0:
            raise InvalidPlanCommitError(
                f"Kế hoạch {self.resolution_mode} không có bất kỳ bài đăng nào (total_planned_tasks == 0). Không thể commit."
            )

        scope_set = set(str(p).strip() for p in self.requested_profile_names if str(p).strip())

        for p_name, tasks in self.profile_assignments.items():
            norm_p = str(p_name).strip()
            if not norm_p or p_name != norm_p:
                raise InvalidPlanCommitError(f"Tên profile '{p_name}' trong profile_assignments không hợp lệ hoặc chứa khoảng trắng thừa.")
            if scope_set and norm_p not in scope_set:
                raise InvalidPlanCommitError(
                    f"Profile '{norm_p}' được phân bổ trong kế hoạch nhưng không nằm trong requested_profile_names ({scope_set})."
                )
            for t in tasks:
                if str(t.profile_name).strip() != norm_p or t.profile_name != norm_p:
                    raise InvalidPlanCommitError(
                        f"Mismatch dữ liệu: PlannedTask có profile_name='{t.profile_name}' không khớp chính xác với key '{norm_p}'."
                    )
                if not t.video_path or not str(t.video_path).strip():
                    raise InvalidPlanCommitError(f"PlannedTask của profile '{norm_p}' có video_path rỗng.")
                if t.scheduled_epoch <= 0:
                    raise InvalidPlanCommitError(
                        f"PlannedTask của profile '{norm_p}' có scheduled_epoch không hợp lệ: {t.scheduled_epoch}."
                    )
                if t.execution_mode not in ("SERVER_SCHEDULE", "LOCAL_QUEUE"):
                    raise InvalidPlanCommitError(
                        f"PlannedTask của profile '{norm_p}' có execution_mode không hợp lệ: '{t.execution_mode}'."
                    )


@dataclass(frozen=True)
class TimelineRequest:
    """Yêu cầu sinh timeline các mốc thời gian cho 1 profile cụ thể."""
    count: int
    profile_name: str
    timezone_name: str
    start_date_local: Optional[datetime.date] = None
    start_time_local: Optional[datetime.time] = None
    max_daily_posts: int = 2
    selected_slots: Tuple[str, ...] = ("SANG", "TRUA", "CHIEU", "TOI")
    custom_slots: Optional[Tuple[TimelineSlot, ...]] = None
    jitter_minutes: int = 10
    min_lead_time_minutes: int = 20
    execution_mode: ExecutionMode = "SERVER_SCHEDULE"
    existing_occupancy_epochs: Tuple[int, ...] = ()
    random_seed: Optional[int] = None


@dataclass(frozen=True)
class TimelineResult:
    """Kết quả sinh timeline cho 1 profile."""
    scheduled_datetimes: Tuple[datetime.datetime, ...]
    slot_ids: Tuple[str, ...]
    requested_count: int
    allocated_count: int
    unallocated_count: int
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ScheduleRequest:
    """Yêu cầu tạo kế hoạch lên lịch hoàn chỉnh."""
    video_paths: Tuple[str, ...]
    profile_names: Tuple[str, ...]
    profile_configs: Mapping[str, Mapping[str, Any]]
    formula: str
    daily_max: int = 2
    selected_slots: Tuple[str, ...] = ("SANG", "TRUA", "CHIEU", "TOI")
    custom_slots: Optional[Tuple[TimelineSlot, ...]] = None
    jitter_minutes: int = 10
    execution_mode: ExecutionMode = "SERVER_SCHEDULE"
    resolution_mode: ResolutionMode = "APPEND"
    order_mode: OrderMode = "ORIGINAL"
    delete_source_after_post: bool = True
    start_date_local: Optional[datetime.date] = None
    start_time_local: Optional[datetime.time] = None
    random_seed: Optional[int] = None
