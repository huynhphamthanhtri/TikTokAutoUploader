"""
schedule_service.py - Tầng dịch vụ điều phối lập lịch (Schedule Application Service).

Chịu trách nhiệm:
1. Điều phối quá trình quét probe video, thu thập snapshot xung đột từ manager.
2. Gọi động cơ lập lịch tất định (schedule_calculator.build_schedule_plan).
3. Thu thập conflict snapshot an toàn, phân biệt rõ ràng giữa không có conflict và lỗi đọc (F09).
4. Tính fingerprint snapshot dựa trên nội dung thực tế của task (profile, id, epoch, status) (F09).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from schedule_contracts import (
    ConflictSnapshot,
    InvalidPlanCommitError,
    PlanWarning,
    ScheduleCommitError,
    ScheduleCommitResult,
    SchedulePlan,
    ScheduleRequest,
    StalePlanError,
    VideoProbeResult,
    VideoValidationPolicy,
)
from schedule_calculator import build_schedule_plan
from video_probe import batch_probe_videos

logger = logging.getLogger("ScheduleService")


def build_conflict_snapshot_from_manager(
    manager: Any,
    profile_names: Sequence[str],
    *,
    raise_on_error: bool = False,
) -> Optional[ConflictSnapshot]:
    """Thu thập ảnh chụp xung đột an toàn từ ScheduleManager mà không khóa ghi DB (F09).
    
    Quy tắc an toàn:
    - Nếu có lỗi xảy ra, ghi nhận rõ ràng, không âm thầm nuốt lỗi nếu raise_on_error = True.
    - Fingerprint được tính từ hash chi tiết các profile và task hiện có (profile, id, epoch, status).
    """
    if manager is None:
        return None

    try:
        if hasattr(manager, "get_conflict_snapshot"):
            return manager.get_conflict_snapshot(profile_names)

        # Tương thích ngược với hàm check_profile_conflicts
        if hasattr(manager, "check_profile_conflicts"):
            raw_conflicts = manager.check_profile_conflicts(list(profile_names))
            conflicted_profiles = tuple(sorted(str(p).strip() for p in raw_conflicts.keys()))

            can_read_tasks = hasattr(manager, "get_active_profile_tasks") or hasattr(manager, "get_all_tasks")
            existing_tasks_map: Dict[str, Tuple[Dict[str, Any], ...]] = {}
            fingerprint_items: List[Dict[str, Any]] = []

            for p in conflicted_profiles:
                p_tasks = []
                if hasattr(manager, "get_active_profile_tasks"):
                    p_tasks = manager.get_active_profile_tasks(p)
                elif hasattr(manager, "get_all_tasks"):
                    p_tasks = [
                        t for t in manager.get_all_tasks()
                        if str(t.get("profile_name") or "").strip() == p
                        and t.get("status") in ("PENDING", "SERVER_SCHEDULED", "QUEUED", "UPLOADING")
                    ]
                existing_tasks_map[p] = tuple(p_tasks)

                for t in p_tasks:
                    fingerprint_items.append({
                        "profile": p,
                        "id": str(t.get("id") or ""),
                        "status": str(t.get("status") or ""),
                        "epoch": int(t.get("scheduled_epoch") or 0),
                    })

            fingerprint_items.sort(key=lambda x: (x["profile"], x["id"], x["epoch"]))
            fingerprint_payload = {
                "conflicted_profiles": list(conflicted_profiles),
                "can_read_tasks": can_read_tasks,
                "tasks": fingerprint_items,
            }
            fingerprint_json = json.dumps(fingerprint_payload, sort_keys=True)
            fingerprint = hashlib.sha256(fingerprint_json.encode("utf-8")).hexdigest()[:16]

            return ConflictSnapshot(
                conflicted_profiles=conflicted_profiles,
                snapshot_fingerprint=fingerprint,
                existing_tasks=existing_tasks_map,
                can_read_tasks=can_read_tasks,
            )
    except Exception as e:
        logger.error(f"Lỗi khi đọc conflict snapshot từ manager: {e}")
        if raise_on_error:
            raise RuntimeError(f"Không thể đọc conflict snapshot từ manager: {e}") from e

    return None


def prepare_schedule_plan(
    request: ScheduleRequest,
    *,
    manager: Optional[Any] = None,
    validation_policy: Optional[VideoValidationPolicy] = None,
    now_utc: Optional[datetime.datetime] = None,
    max_probe_workers: int = 4,
    raise_on_conflict_read_error: bool = False,
) -> SchedulePlan:
    """Điều phối toàn diện quá trình tạo Kế hoạch Lên Lịch (SchedulePlan).
    
    1. Kiểm tra kỹ thuật toàn bộ video thông qua batch probe.
    2. Thu thập snapshot xung đột hiện tại của các profile.
    3. Sinh SchedulePlan tất định, sẵn sàng hiển thị lên Live Preview.
    """
    effective_now = now_utc if now_utc is not None else datetime.datetime.now(datetime.timezone.utc)

    # 1. Batch probe toàn bộ video nguồn
    probes = batch_probe_videos(
        request.video_paths,
        policy=validation_policy,
        max_workers=max_probe_workers,
    )

    # 2. Lấy conflict snapshot từ storage (Read-only)
    snapshot_warnings: List[PlanWarning] = []
    conflict_snapshot: Optional[ConflictSnapshot] = None
    if manager is not None:
        try:
            conflict_snapshot = build_conflict_snapshot_from_manager(
                manager,
                request.profile_names,
                raise_on_error=True,
            )
            if conflict_snapshot and not conflict_snapshot.can_read_tasks and conflict_snapshot.conflicted_profiles:
                snapshot_warnings.append(
                    PlanWarning(
                        code="CANNOT_READ_OCCUPANCY_TASKS",
                        profile_name="*",
                        video_path="*",
                        message="Manager không hỗ trợ API đọc task; không thể đối chiếu occupancy ở chế độ APPEND",
                    )
                )
        except Exception as snap_err:
            if raise_on_conflict_read_error:
                raise
            conflict_snapshot = None
            snapshot_warnings.append(
                PlanWarning(
                    code="CONFLICT_SNAPSHOT_READ_ERROR",
                    profile_name="*",
                    video_path="*",
                    message=str(snap_err),
                )
            )

    # 3. Tính toán SchedulePlan tất định
    plan = build_schedule_plan(
        request=request,
        probes=probes,
        conflicts=conflict_snapshot,
        now_utc=effective_now,
    )

    if snapshot_warnings:
        plan = SchedulePlan(
            plan_id=plan.plan_id,
            created_at=plan.created_at,
            seed=plan.seed,
            profile_assignments=plan.profile_assignments,
            unassigned_videos=plan.unassigned_videos,
            conflicts_identified=plan.conflicts_identified,
            resolution_mode=plan.resolution_mode,
            warnings=tuple(list(plan.warnings) + snapshot_warnings),
            requested_profile_names=plan.requested_profile_names,
        )

    return plan


def commit_schedule_plan(
    plan: SchedulePlan,
    *,
    manager: Any,
    expected_snapshot_fingerprint: Optional[str] = None,
) -> ScheduleCommitResult:
    """Điều phối lưu trữ nguyên tử Kế hoạch Lên Lịch (SchedulePlan) xuống SQLite (P2).
    
    Quy tắc an toàn:
    1. Kiểm tra plan không được None và gọi plan.validate_for_commit() (F02).
    2. Nếu resolution_mode == 'OVERWRITE', bắt buộc phải có expected_snapshot_fingerprint hợp lệ (F03).
    3. Xác định expected_snapshot_fingerprint từ plan nếu không được truyền tường minh.
    4. Ủy quyền cho manager.commit_plan_atomic thực thi trong 1 transaction duy nhất.
    """
    if plan is None:
        raise InvalidPlanCommitError("SchedulePlan không được để None khi commit.")
    if manager is None:
        raise ScheduleCommitError("ScheduleManager bắt buộc phải được cung cấp để commit.")

    # F02: Kiểm tra tính hợp lệ toàn vẹn của kế hoạch
    plan.validate_for_commit()

    fingerprint = expected_snapshot_fingerprint
    if fingerprint is None and plan.conflicts_identified:
        fingerprint = plan.conflicts_identified.snapshot_fingerprint

    # F03: OVERWRITE bắt buộc phải có snapshot fingerprint hợp lệ (chuỗi không rỗng)
    if plan.resolution_mode == "OVERWRITE":
        if not fingerprint or not str(fingerprint).strip():
            raise InvalidPlanCommitError(
                "Kế hoạch OVERWRITE bắt buộc phải có snapshot fingerprint hợp lệ (không được để trống) để chống stale commit."
            )

    if not hasattr(manager, "commit_plan_atomic"):
        raise ScheduleCommitError(f"Manager {manager} không hỗ trợ API commit_plan_atomic của Phase 2.")

    return manager.commit_plan_atomic(
        plan,
        expected_snapshot_fingerprint=fingerprint,
    )


def summarize_schedule_plan(plan: SchedulePlan) -> Dict[str, Any]:
    """Tạo báo cáo tóm tắt kế hoạch hiển thị trên giao diện Preview."""
    tasks_by_date: Dict[str, int] = {}
    tasks_by_profile: Dict[str, int] = {}

    for p_name, task_list in plan.profile_assignments.items():
        tasks_by_profile[p_name] = len(task_list)
        for t in task_list:
            d_str = t.scheduled_time_local[:10]
            tasks_by_date[d_str] = tasks_by_date.get(d_str, 0) + 1

    return {
        "plan_id": plan.plan_id,
        "total_planned": plan.total_planned_tasks,
        "total_unassigned": plan.total_unassigned,
        "profile_counts": tasks_by_profile,
        "date_counts": tasks_by_date,
        "unassigned_reasons": [
            {"path": u.video_path, "reason": u.reason, "details": u.details}
            for u in plan.unassigned_videos
        ],
    }
