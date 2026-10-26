"""
schedule_calculator.py - Động cơ tính toán, phân bổ và bóc tách công thức lên lịch TikTok.

Chịu trách nhiệm:
1. Quét và kiểm tra tính hợp lệ của video nguồn (.mp4, size > 0).
2. Phân bổ video xoay vòng Round-Robin cho danh sách profile (chuẩn hóa deduplicate).
3. Bóc tách token công thức đặt tên chuẩn ({filename}, {n}, {profile}, {account}, {date}, {year}).
4. Tính toán timeline khung giờ vàng, Jitter đa ngày, hỗ trợ Timezone/DST và slot xuyên đêm (F04, F05, F06, F11, F12).
5. Sinh Kế Hoạch Lập Lịch Tất Định (Deterministic SchedulePlan) bất biến, an toàn và chống va chạm ID (F03, F07, F10).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import random
import re
from collections import defaultdict
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from schedule_contracts import (
    ConflictSnapshot,
    ExecutionMode,
    OrderMode,
    PlanWarning,
    PlannedTask,
    ResolutionMode,
    SchedulePlan,
    ScheduleRequest,
    TimelineRequest,
    TimelineResult,
    TimelineSlot,
    UnassignedVideo,
    VideoProbeResult,
)

logger = logging.getLogger("ScheduleCalculator")

COMMON_TZ_OFFSETS: Dict[str, int] = {
    "America/Mexico_City": -6,
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
    "Europe/London": 0,
    "Europe/Paris": 1,
    "Europe/Berlin": 1,
    "Asia/Ho_Chi_Minh": 7,
    "Asia/Bangkok": 7,
    "Asia/Tokyo": 9,
}


def resolve_tz(tz_name: str, strict: bool = False) -> datetime.tzinfo:
    """Chuyển đổi chuỗi tên múi giờ IANA sang đối tượng datetime.tzinfo chuẩn xác."""
    name = str(tz_name or "").strip()
    if not name or name.upper() in ("UTC", "GMT"):
        return datetime.timezone.utc
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(name)
    except Exception as e:
        logger.warning(f"Lỗi tải cơ sở dữ liệu IANA timezone '{name}': {e}")
        if name in COMMON_TZ_OFFSETS:
            return datetime.timezone(datetime.timedelta(hours=COMMON_TZ_OFFSETS[name]))
        if strict:
            raise ValueError(
                f"Không thể tải múi giờ IANA '{name}' để tính toán DST an toàn. "
                f"Vui lòng cài đặt gói tzdata hoặc kiểm tra lại tên múi giờ."
            ) from e
    return datetime.timezone.utc


def _canonicalize_for_hash(obj: Any) -> Any:
    """Chuẩn hóa đệ quy sâu mọi kiểu dữ liệu sang cấu trúc có thứ tự xác định tuyệt đối (F07)."""
    if isinstance(obj, Mapping):
        return {str(k): _canonicalize_for_hash(v) for k, v in sorted(obj.items(), key=lambda x: str(x[0]))}
    elif isinstance(obj, (set, frozenset)):
        return sorted([_canonicalize_for_hash(x) for x in obj], key=lambda x: str(x))
    elif isinstance(obj, (list, tuple)):
        return [_canonicalize_for_hash(x) for x in obj]
    elif isinstance(obj, bytes):
        return obj.hex()
    elif isinstance(obj, (datetime.date, datetime.datetime, datetime.time)):
        return obj.isoformat()
    return obj


def resolve_profile_timezone(cfg: Optional[Mapping[str, Any]]) -> str:
    """Trích xuất tên múi giờ từ cấu hình profile TikTok (hỗ trợ mọi Mapping)."""
    if not isinstance(cfg, Mapping):
        return "America/New_York"
    fp = cfg.get("fingerprint")
    tz = fp.get("timezone") if isinstance(fp, Mapping) else None
    if not tz:
        tz = cfg.get("timezone")
    if isinstance(tz, Mapping):
        return str(tz.get("name") or tz.get("timezone") or tz.get("tz") or "America/New_York").strip()
    if isinstance(tz, str) and tz.strip():
        return tz.strip()
    return "America/New_York"


DEFAULT_GOLDEN_HOUR_SLOTS = {
    "SANG": ((8, 0), (9, 15)),
    "TRUA": ((11, 45), (13, 0)),
    "CHIEU": ((17, 30), (18, 45)),
    "TOI": ((20, 0), (21, 30)),
}


def get_default_timeline_slots() -> Tuple[TimelineSlot, ...]:
    """Trả về danh sách đối tượng TimelineSlot chuẩn mặc định."""
    return (
        TimelineSlot(slot_id="SANG", label="Sáng (08:00 - 09:15)", start_hour=8, start_minute=0, end_hour=9, end_minute=15),
        TimelineSlot(slot_id="TRUA", label="Trưa (11:45 - 13:00)", start_hour=11, start_minute=45, end_hour=13, end_minute=0),
        TimelineSlot(slot_id="CHIEU", label="Chiều (17:30 - 18:45)", start_hour=17, start_minute=30, end_hour=18, end_minute=45),
        TimelineSlot(slot_id="TOI", label="Tối (20:00 - 21:30)", start_hour=20, start_minute=0, end_hour=21, end_minute=30),
    )


def scan_video_files(source: Sequence[str | Path] | str | Path) -> List[Path]:
    """Quét và lọc các file video .mp4 hợp lệ, đảm bảo file tồn tại và size > 0."""
    valid_videos: List[Path] = []
    seen: set = set()

    paths_to_check: List[Path] = []
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_dir():
            for item in sorted(p.iterdir()):
                if item.is_file() and item.suffix.lower() == ".mp4":
                    paths_to_check.append(item)
        elif p.is_file() and p.suffix.lower() == ".mp4":
            paths_to_check.append(p)
    elif isinstance(source, (list, tuple)):
        for item in source:
            p = Path(item)
            if p.is_dir():
                for sub in sorted(p.iterdir()):
                    if sub.is_file() and sub.suffix.lower() == ".mp4":
                        paths_to_check.append(sub)
            elif p.is_file() and p.suffix.lower() == ".mp4":
                paths_to_check.append(p)

    for v in paths_to_check:
        try:
            norm_path = v.resolve()
            if norm_path not in seen and v.is_file() and v.stat().st_size > 0:
                seen.add(norm_path)
                valid_videos.append(v)
        except Exception:
            continue

    return valid_videos


def sanitize_filename_to_title(filename_or_stem: str) -> str:
    """Chuyển đổi tên file thành tiêu đề sạch, loại bỏ dấu gạch dưới và khoảng trắng thừa."""
    stem = Path(filename_or_stem).stem
    clean = stem.replace("_", " ").replace("-", " ")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or stem


def parse_naming_formula(
    formula: str,
    video_path_or_name: str | Path,
    index: int = 1,
    profile_name: str = "",
    account_name: str = "",
    schedule_dt: Optional[datetime.datetime] = None,
) -> str:
    """Bóc tách công thức đặt tiêu đề bài đăng theo bộ biến chuẩn (không dùng spintax)."""
    clean_stem = sanitize_filename_to_title(str(video_path_or_name))
    dt = schedule_dt or datetime.datetime.now()
    clean_account = str(account_name or "").strip()
    if clean_account and not clean_account.startswith("@"):
        clean_account = f"@{clean_account}"

    raw_formula = (formula or "").strip()
    if not raw_formula:
        return clean_stem

    replacements = {
        "{filename}": clean_stem,
        "{n}": str(index),
        "{index}": str(index),
        "{profile}": str(profile_name or "").strip(),
        "{account}": clean_account,
        "{date}": dt.strftime("%d-%m"),
        "{year}": dt.strftime("%Y"),
    }

    result = raw_formula
    for token, val in replacements.items():
        result = re.sub(re.escape(token), val, result, flags=re.IGNORECASE)

    result = re.sub(r"[ \t]+", " ", result).strip()
    return result or clean_stem


def generate_title_preview(
    formula: str,
    sample_filename: str = "top_10_skills.mp4",
    profile_name: str = "Profile_01",
    account_name: str = "user_daily",
) -> str:
    """Tạo tiêu đề mẫu tức thời cho Live Preview trên giao diện."""
    return parse_naming_formula(
        formula=formula,
        video_path_or_name=sample_filename,
        index=1,
        profile_name=profile_name,
        account_name=account_name,
        schedule_dt=datetime.datetime.now(),
    )


def distribute_videos_round_robin(
    videos: Sequence[Path | str],
    profile_names: Sequence[str],
) -> Dict[str, List[Path]]:
    """Phân bổ danh sách video cho các profile theo giải thuật xoay vòng (Round-Robin)."""
    valid_profiles: List[str] = []
    seen_prof: Set[str] = set()
    for p in profile_names:
        norm = str(p or "").strip()
        if norm and norm not in seen_prof:
            seen_prof.add(norm)
            valid_profiles.append(norm)

    if not valid_profiles:
        return {}

    distribution: Dict[str, List[Path]] = {p: [] for p in valid_profiles}
    for idx, v in enumerate(videos):
        p_path = Path(v) if not isinstance(v, Path) else v
        target_profile = valid_profiles[idx % len(valid_profiles)]
        distribution[target_profile].append(p_path)

    return distribution


def round_to_nearest_interval(dt: datetime.datetime, interval_minutes: int = 5) -> datetime.datetime:
    """Làm tròn mốc thời gian về bội số của interval_minutes (khuyến nghị cho TikTok Schedule)."""
    total_minutes = dt.minute + dt.second / 60.0
    rounded_minutes = round(total_minutes / interval_minutes) * interval_minutes
    return dt.replace(second=0, microsecond=0) + datetime.timedelta(minutes=rounded_minutes - dt.minute)


def natural_sort_key(s: str | Path) -> List[Any]:
    """Tạo khóa sắp xếp tự nhiên (Natural Sort), đảm bảo Part 2 đứng trước Part 10."""
    text = str(s)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def order_video_paths(
    paths: Sequence[Path | str],
    mode: OrderMode = "ORIGINAL",
    seed: Optional[int] = None,
) -> List[Path]:
    """Sắp xếp danh sách video theo chế độ được chọn (Original, Series, Shuffle)."""
    p_list = [Path(p) if not isinstance(p, Path) else p for p in paths]
    if mode == "SERIES":
        return sorted(p_list, key=natural_sort_key)
    elif mode == "SHUFFLE":
        shuffled = list(p_list)
        rng = random.Random(seed) if seed is not None else random.Random(42)
        rng.shuffle(shuffled)
        return shuffled
    return list(p_list)


def build_schedule_timeline(
    request: TimelineRequest,
    now_utc: datetime.datetime,
    rng: Optional[random.Random] = None,
) -> TimelineResult:
    """Động cơ tính toán mốc thời gian độc lập, tất định cho 1 profile.
    
    Quy tắc nghiệp vụ:
    - So sánh giới hạn lead time (>= 20 phút) và horizon tuyệt đối qua UTC timestamp.
    - SERVER_SCHEDULE giới hạn tối đa 10 ngày (864,000s) từ now_utc.
    - LÀM TRÒN (rounding) ĐƯỢC THỰC HIỆN TRƯỚC, sau đó mới xác định target_date thực tế và kiểm tra quota/bounds (F04, F11).
    - Ngăn chặn hoàn toàn trùng epoch với các bài đã tồn tại (F04).
    - Duyệt slot theo thứ tự thời gian, không ngắt sớm toàn bộ ngày khi 1 slot vượt horizon (F05).
    - Hỗ trợ start_time_local để bỏ qua các slot đã qua trong ngày bắt đầu (F12).
    - Kết quả luôn sắp xếp tăng dần theo thời gian (F05).
    """
    if request.count <= 0:
        return TimelineResult((), (), request.count, 0, 0, ())

    try:
        tz = resolve_tz(request.timezone_name, strict=True)
    except Exception as tz_err:
        return TimelineResult((), (), request.count, 0, request.count, (str(tz_err),))

    now_in_tz = now_utc.astimezone(tz)
    generator_rng = rng if rng is not None else (
        random.Random(request.random_seed) if request.random_seed is not None else random.Random()
    )

    start_day = request.start_date_local if request.start_date_local is not None else now_in_tz.date()
    start_time_cutoff = request.start_time_local

    # Chuẩn bị slots
    slots: List[TimelineSlot] = []
    if request.custom_slots:
        slots = list(request.custom_slots)
    else:
        default_slots_map = {
            "SANG": TimelineSlot("SANG", "Sáng", 8, 0, 9, 15),
            "TRUA": TimelineSlot("TRUA", "Trưa", 11, 45, 13, 0),
            "CHIEU": TimelineSlot("CHIEU", "Chiều", 17, 30, 18, 45),
            "TOI": TimelineSlot("TOI", "Tối", 20, 0, 21, 30),
        }
        for s_id in request.selected_slots:
            if s_id in default_slots_map:
                slots.append(default_slots_map[s_id])
    if not slots:
        slots = list(get_default_timeline_slots())

    # Sắp xếp slots theo thứ tự thời gian bắt đầu trong ngày (F05)
    def _slot_sort_key(s: TimelineSlot) -> int:
        return s.start_hour * 60 + s.start_minute
    slots.sort(key=_slot_sort_key)

    # Phân tích occupancy theo NGÀY ĐĂNG THỰC TẾ và thu thập tập hợp epoch đã chiếm (F04)
    occupancy_per_date: Dict[datetime.date, int] = defaultdict(int)
    existing_epochs_set: Set[int] = set()
    for ep in request.existing_occupancy_epochs:
        try:
            dt_existing = datetime.datetime.fromtimestamp(ep, tz=tz)
            occupancy_per_date[dt_existing.date()] += 1
            existing_epochs_set.add(int(ep))
        except Exception:
            pass

    now_epoch = int(now_utc.timestamp())
    min_lead_sec = max(20, request.min_lead_time_minutes) * 60  # TikTok an toàn >= 20 phút (F11)
    earliest_epoch = now_epoch + min_lead_sec

    is_server = request.execution_mode == "SERVER_SCHEDULE"
    latest_epoch = now_epoch + 10 * 86400 if is_server else now_epoch + 365 * 86400

    scheduled_candidates: List[Tuple[datetime.datetime, str]] = []
    reasons: List[str] = []

    current_day = start_day
    max_days_guard = max(365, request.count * 10)
    days_checked = 0

    while len(scheduled_candidates) < request.count and days_checked < max_days_guard:
        day_candidates: List[Tuple[datetime.datetime, str, int, datetime.date]] = []

        for slot in slots:
            # Tính mốc giữa khung giờ
            start_mins = slot.start_hour * 60 + slot.start_minute
            end_mins = slot.end_hour * 60 + slot.end_minute
            if end_mins <= start_mins:
                end_mins += 24 * 60

            mid_mins = (start_mins + end_mins) // 2
            half_window = max(5, (end_mins - start_mins) // 4)
            effective_jitter = min(abs(request.jitter_minutes), half_window)

            jitter_delta = generator_rng.randint(-effective_jitter, effective_jitter)
            target_mins = mid_mins + jitter_delta
            target_mins = max(start_mins, min(end_mins, target_mins))

            slot_hour, slot_min = divmod(target_mins % (24 * 60), 60)
            day_offset = target_mins // (24 * 60)
            raw_target_date = current_day + datetime.timedelta(days=day_offset)

            try:
                candidate_dt = datetime.datetime.combine(
                    raw_target_date,
                    datetime.time(hour=slot_hour, minute=slot_min),
                    tzinfo=tz,
                ).replace(fold=0)

                # LÀM TRÒN TRƯỚC (Rounding First) để xác định ngày đăng thực tế và epoch (F04, F11)
                rounded_dt = round_to_nearest_interval(candidate_dt, interval_minutes=5)
                final_date = rounded_dt.date()
                final_epoch = int(rounded_dt.timestamp())

                # Round-trip verification: Bỏ qua các mốc thời gian không tồn tại do nhảy giờ DST Spring-Forward gap
                rt_dt = datetime.datetime.fromtimestamp(final_epoch, tz=tz)
                if (rt_dt.date(), rt_dt.hour, rt_dt.minute) != (rounded_dt.date(), rounded_dt.hour, rounded_dt.minute):
                    continue

                day_candidates.append((rounded_dt, slot.slot_id, final_epoch, final_date))
            except Exception:
                continue

        # SẮP XẾP CHRONOLOGICAL THEO THỜI GIAN THỰC TẾ CỦA MỐC ĐĂNG (giải quyết triệt để lỗi đảo slot xuyên đêm)
        day_candidates.sort(key=lambda item: item[2])

        for rounded_dt, slot_id, final_epoch, final_date in day_candidates:
            if len(scheduled_candidates) >= request.count:
                break

            # Bỏ qua nếu ngày đăng thực tế này đã chạm trần daily_max (F04)
            if occupancy_per_date[final_date] >= request.max_daily_posts:
                continue

            # Kiểm tra start_time_cutoff nếu là ngày bắt đầu đầu tiên (F12)
            if current_day == start_day and start_time_cutoff is not None:
                cutoff_mins = start_time_cutoff.hour * 60 + start_time_cutoff.minute
                rounded_mins = rounded_dt.hour * 60 + rounded_dt.minute
                if rounded_dt.date() == start_day and rounded_mins < cutoff_mins:
                    continue

            # Kiểm tra lead time
            if final_epoch < earliest_epoch:
                continue

            # Kiểm tra trùng epoch chính xác với bài cũ hoặc bài vừa sinh (F04)
            if final_epoch in existing_epochs_set:
                continue

            # Kiểm tra giới hạn 10 ngày server (chỉ bỏ qua candidate này, không ngắt sớm toàn bộ) (F05)
            if is_server and final_epoch > latest_epoch:
                if "Vượt quá giới hạn hẹn giờ 10 ngày của Server TikTok" not in reasons:
                    reasons.append("Vượt quá giới hạn hẹn giờ 10 ngày của Server TikTok")
                continue

            # Chấp nhận mốc này
            scheduled_candidates.append((rounded_dt, slot_id))
            existing_epochs_set.add(final_epoch)
            occupancy_per_date[final_date] += 1

        current_day += datetime.timedelta(days=1)
        days_checked += 1

    # Sắp xếp lại danh sách mốc theo thứ tự thời gian tăng dần tuyệt đối (F05)
    scheduled_candidates.sort(key=lambda item: int(item[0].timestamp()))

    unallocated = request.count - len(scheduled_candidates)
    if unallocated > 0 and not reasons:
        reasons.append(f"Không đủ khung giờ trống trong vòng {days_checked} ngày kiểm tra.")

    final_dts = tuple(c[0] for c in scheduled_candidates)
    final_slots = tuple(c[1] for c in scheduled_candidates)

    return TimelineResult(
        scheduled_datetimes=final_dts,
        slot_ids=final_slots,
        requested_count=request.count,
        allocated_count=len(final_dts),
        unallocated_count=unallocated,
        reasons=tuple(reasons),
    )


def generate_schedule_timeline(
    count: int,
    start_date: Optional[datetime.datetime] = None,
    max_daily_posts: int = 2,
    selected_slots: Optional[Sequence[str]] = None,
    jitter_minutes: int = 10,
    min_lead_time_minutes: int = 20,
    random_seed: Optional[int] = None,
    timezone_name: str = "America/New_York",
) -> List[datetime.datetime]:
    """Hàm wrapper tương thích ngược cho codebase cũ."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    start_local = start_date.date() if isinstance(start_date, datetime.datetime) else None
    start_time = start_date.time() if isinstance(start_date, datetime.datetime) else None

    req = TimelineRequest(
        count=count,
        profile_name="LegacyWrapper",
        timezone_name=timezone_name,
        start_date_local=start_local,
        start_time_local=start_time,
        max_daily_posts=max_daily_posts,
        selected_slots=tuple(selected_slots or ["SANG", "TRUA", "CHIEU", "TOI"]),
        jitter_minutes=jitter_minutes,
        min_lead_time_minutes=min_lead_time_minutes,
        execution_mode="SERVER_SCHEDULE",
        random_seed=random_seed,
    )

    res = build_schedule_timeline(req, now_utc=now_utc)
    return list(res.scheduled_datetimes)


def build_schedule_plan(
    request: ScheduleRequest,
    probes: Sequence[VideoProbeResult],
    conflicts: Optional[ConflictSnapshot] = None,
    now_utc: Optional[datetime.datetime] = None,
) -> SchedulePlan:
    """Xây dựng Kế hoạch Lên lịch hoàn chỉnh, bất biến sâu và tất định.
    
    Đã khắc phục:
    - F03: Bắt buộc video phải có probe hợp lệ; kiểm tra stat đĩa hiện tại khớp mtime/size probe chống stale probe.
    - F07: Hash đầy đủ toàn bộ tham số đầu vào (cả profile_configs timezone/account và probe fingerprint) trong plan_id.
    - F10: Chuẩn hóa, trim và deduplicate profile names; không để sót video nếu danh sách profile rỗng.
    - F08: Đóng băng sâu cấu trúc trả về qua MappingProxyType.
    - F14: Hỗ trợ mọi kiểu Mapping.
    """
    effective_now_utc = now_utc if now_utc is not None else datetime.datetime.now(datetime.timezone.utc)
    effective_seed = request.random_seed if request.random_seed is not None else 42

    # 1. Chuẩn hóa và deduplicate danh sách profile (F10)
    normalized_profiles: List[str] = []
    seen_prof: Set[str] = set()
    for p in request.profile_names:
        clean_p = str(p or "").strip()
        if clean_p and clean_p not in seen_prof:
            seen_prof.add(clean_p)
            normalized_profiles.append(clean_p)

    probes_seq = probes.values() if isinstance(probes, dict) else probes
    probe_map: Dict[str, VideoProbeResult] = {
        p.canonical_path: p for p in probes_seq
    }

    valid_paths: List[Path] = []
    unassigned_list: List[UnassignedVideo] = []
    warnings: List[PlanWarning] = []

    # 2. Phân loại video dựa trên Probe và đối chiếu stat hiện tại (F03)
    for v_str in request.video_paths:
        v_path = Path(v_str)
        try:
            canon = str(v_path.resolve())
        except Exception:
            canon = str(v_path)

        probe_res = probe_map.get(canon)
        if not v_path.exists() or not v_path.is_file():
            unassigned_list.append(
                UnassignedVideo(
                    video_path=v_str,
                    reason="FILE_NOT_FOUND_ON_DISK",
                    details=f"File không tồn tại trên đường dẫn: {v_str}",
                )
            )
        elif probe_res is None:
            unassigned_list.append(
                UnassignedVideo(
                    video_path=v_str,
                    reason="MISSING_PROBE_RESULT",
                    details="Video chưa được kiểm tra qua probe_video",
                )
            )
        elif not probe_res.is_valid:
            unassigned_list.append(
                UnassignedVideo(
                    video_path=v_str,
                    reason="INVALID_VIDEO_PROBE",
                    details=", ".join(probe_res.issues),
                )
            )
        else:
            # Đối chiếu stat hiện tại với kết quả probe (chống stale probe - F03)
            try:
                curr_stat = v_path.stat()
                curr_size = curr_stat.st_size
                curr_mtime_ns = getattr(curr_stat, "st_mtime_ns", int(curr_stat.st_mtime * 1e9))
                if curr_size != probe_res.size_bytes or curr_mtime_ns != probe_res.mtime_ns:
                    unassigned_list.append(
                        UnassignedVideo(
                            video_path=v_str,
                            reason="STALE_PROBE_FILE_MODIFIED",
                            details="File video bị thay đổi kích thước hoặc thời gian chỉnh sửa sau khi probe",
                        )
                    )
                    continue
            except Exception as stat_err:
                unassigned_list.append(
                    UnassignedVideo(
                        video_path=v_str,
                        reason="STAT_FAILED",
                        details=str(stat_err),
                    )
                )
                continue

            valid_paths.append(v_path)

    # Thu thập cấu hình profile chuẩn hóa phục vụ hash và phân bổ
    cleaned_profile_configs: Dict[str, Dict[str, str]] = {}
    for p_name in normalized_profiles:
        cfg = request.profile_configs.get(p_name, {}) if isinstance(request.profile_configs, Mapping) else {}
        tiktok_acc = str(cfg.get("tiktok_account") or cfg.get("tiktok_id") or "").strip()
        tz_name = resolve_profile_timezone(cfg)
        cleaned_profile_configs[p_name] = {
            "tiktok_account": tiktok_acc,
            "timezone": tz_name,
        }

    # Nếu không có profile hợp lệ nào, chuyển toàn bộ video hợp lệ vào unassigned (F10)
    if not normalized_profiles:
        for v in valid_paths:
            unassigned_list.append(
                UnassignedVideo(
                    video_path=str(v),
                    reason="NO_VALID_PROFILES",
                    details="Danh sách profile rỗng hoặc không có profile hợp lệ",
                )
            )

    # 3. Sắp xếp thứ tự video
    ordered_videos = order_video_paths(valid_paths, mode=request.order_mode, seed=effective_seed)

    # 4. Phân bổ Round-Robin
    distribution = distribute_videos_round_robin(ordered_videos, normalized_profiles)
    profile_assignments: Dict[str, List[PlannedTask]] = {p: [] for p in normalized_profiles}

    conflicted_set = set(conflicts.conflicted_profiles) if conflicts else set()

    # 5. Sinh timeline và tạo PlannedTask cho từng profile
    for p_name in normalized_profiles:
        v_list = distribution.get(p_name, [])
        if not v_list:
            continue

        p_info = cleaned_profile_configs[p_name]
        tiktok_acc = p_info["tiktok_account"]
        tz_name = p_info["timezone"]

        # Xử lý resolution_mode SKIP
        if request.resolution_mode == "SKIP" and p_name in conflicted_set:
            for v_skipped in v_list:
                unassigned_list.append(
                    UnassignedVideo(
                        video_path=str(v_skipped),
                        reason="PROFILE_SKIPPED_DUE_TO_CONFLICT",
                        details=f"Profile '{p_name}' có bài đăng xung đột và chế độ chọn là SKIP",
                    )
                )
            continue

        # Thu thập occupancy epochs nếu là APPEND
        existing_epochs: List[int] = []
        max_existing_epoch = 0
        latest_existing_date: Optional[datetime.date] = None
        if request.resolution_mode == "APPEND" and conflicts and p_name in conflicts.existing_tasks:
            for task_dict in conflicts.existing_tasks[p_name]:
                ep = task_dict.get("scheduled_epoch")
                if isinstance(ep, (int, float)) and ep > 0:
                    int_ep = int(ep)
                    existing_epochs.append(int_ep)
                    if int_ep > max_existing_epoch:
                        max_existing_epoch = int_ep
                    try:
                        t_dt = datetime.datetime.fromtimestamp(int_ep, tz=resolve_tz(tz_name))
                        if latest_existing_date is None or t_dt.date() > latest_existing_date:
                            latest_existing_date = t_dt.date()
                    except Exception:
                        pass

        # Xác định start_date cho profile nếu là APPEND (nối tiếp ngày kế tiếp của lịch cũ)
        profile_start_date = request.start_date_local
        if request.resolution_mode == "APPEND" and latest_existing_date is not None:
            day_after_latest = latest_existing_date + datetime.timedelta(days=1)
            if profile_start_date is None or day_after_latest > profile_start_date:
                profile_start_date = day_after_latest

        # Seed riêng biệt cho từng profile đảm bảo tính ổn định độc lập
        profile_seed_str = f"{effective_seed}:{p_name}"
        profile_seed = int(hashlib.md5(profile_seed_str.encode("utf-8")).hexdigest()[:8], 16)
        profile_rng = random.Random(profile_seed)

        timeline_req = TimelineRequest(
            count=len(v_list),
            profile_name=p_name,
            timezone_name=tz_name,
            start_date_local=profile_start_date,
            start_time_local=request.start_time_local,
            max_daily_posts=request.daily_max,
            selected_slots=request.selected_slots,
            custom_slots=request.custom_slots,
            jitter_minutes=request.jitter_minutes,
            min_lead_time_minutes=20,
            execution_mode=request.execution_mode,
            existing_occupancy_epochs=tuple(existing_epochs),
            random_seed=profile_seed,
        )

        timeline_res = build_schedule_timeline(timeline_req, now_utc=effective_now_utc, rng=profile_rng)

        for idx, sched_dt in enumerate(timeline_res.scheduled_datetimes):
            v_target = v_list[idx]
            canon_v = str(v_target.resolve()) if v_target.exists() else str(v_target)
            p_res = probe_map.get(canon_v)

            task_title = parse_naming_formula(
                formula=request.formula,
                video_path_or_name=v_target,
                index=idx + 1,
                profile_name=p_name,
                account_name=tiktok_acc,
                schedule_dt=sched_dt,
            )

            slot_used = timeline_res.slot_ids[idx] if idx < len(timeline_res.slot_ids) else "AUTO"
            planned_task = PlannedTask(
                slot_id=slot_used,
                profile_name=p_name,
                tiktok_account=tiktok_acc,
                video_path=str(v_target),
                canonical_video_path=canon_v,
                video_filename=v_target.name,
                title=task_title,
                profile_timezone=tz_name,
                scheduled_epoch=int(sched_dt.timestamp()),
                scheduled_time_local=sched_dt.isoformat(),
                execution_mode=request.execution_mode,
                delete_source_after_post=request.delete_source_after_post,
                source_size_bytes=p_res.size_bytes if p_res else (v_target.stat().st_size if v_target.exists() else 0),
                source_mtime_ns=p_res.mtime_ns if p_res else 0,
            )
            profile_assignments[p_name].append(planned_task)

        # Video thừa không đủ slot
        if timeline_res.unallocated_count > 0:
            for unallocated_idx in range(timeline_res.allocated_count, len(v_list)):
                v_overflow = v_list[unallocated_idx]
                reason_str = "; ".join(timeline_res.reasons) or "Sức chứa khung giờ vượt giới hạn"
                unassigned_list.append(
                    UnassignedVideo(
                        video_path=str(v_overflow),
                        reason="SLOT_CAPACITY_EXHAUSTED",
                        details=f"Profile '{p_name}': {reason_str}",
                    )
                )

    # 6. Sinh plan_id tất định từ hash toàn diện mọi thuộc tính (F07)
    custom_slots_serialized = [s.to_dict() for s in request.custom_slots] if request.custom_slots else []
    probe_fingerprints = {
        v_canon: {"size": p.size_bytes, "mtime": p.mtime_ns, "valid": p.is_valid}
        for v_canon, p in probe_map.items()
    }
    unassigned_signatures = [
        {"path": u.video_path, "reason": u.reason}
        for u in unassigned_list
    ]

    # Thu thập disk stat hiện tại của toàn bộ video path trong request
    disk_fingerprints = {}
    for v_str in request.video_paths:
        vp = Path(v_str)
        try:
            st = vp.stat()
            disk_fingerprints[v_str] = {
                "size": st.st_size,
                "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
            }
        except Exception:
            disk_fingerprints[v_str] = {"exists": False}

    # Canonicalize toàn bộ profile_configs trong request (kể cả khi profile_names rỗng)
    canonical_configs = {}
    if isinstance(request.profile_configs, Mapping):
        for k, v in sorted(request.profile_configs.items(), key=lambda x: str(x[0])):
            if isinstance(v, Mapping):
                canonical_configs[str(k)] = {str(ck): str(cv) for ck, cv in sorted(v.items(), key=lambda x: str(x[0]))}
            else:
                canonical_configs[str(k)] = str(v)

    plan_meta = {
        "request_videos": list(request.video_paths),
        "request_profiles": list(request.profile_names),
        "request_profile_configs": canonical_configs,  # Hash toàn bộ configs bất kể profiles rỗng hay không (F07)
        "formula": request.formula,
        "daily_max": request.daily_max,
        "slots": list(request.selected_slots),
        "custom_slots": custom_slots_serialized,
        "jitter_minutes": request.jitter_minutes,
        "mode": request.execution_mode,
        "res_mode": request.resolution_mode,
        "order": request.order_mode,
        "delete_source_after_post": request.delete_source_after_post,
        "start_date": request.start_date_local.isoformat() if request.start_date_local else None,
        "start_time": request.start_time_local.isoformat() if request.start_time_local else None,
        "request_random_seed": request.random_seed,  # Phân biệt None vs int (F07)
        "effective_seed": effective_seed,
        "conflict_fingerprint": str(conflicts.snapshot_fingerprint) if (conflicts and hasattr(conflicts, "snapshot_fingerprint")) else "",
        "probe_fingerprints": probe_fingerprints,  # Bổ sung probe fingerprint (F07)
        "unassigned_signatures": unassigned_signatures,  # Phản ánh thay đổi file và lỗi stale probe (F07)
        "disk_fingerprints": disk_fingerprints,  # Stat thực tế trên đĩa (F07)
    }
    raw_hash = hashlib.sha256(json.dumps(plan_meta, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    plan_id = f"plan_{effective_now_utc.strftime('%Y%m%d_%H%M%S')}_{raw_hash}"

    frozen_assignments = {p: tuple(tasks) for p, tasks in profile_assignments.items()}

    return SchedulePlan(
        plan_id=plan_id,
        created_at=effective_now_utc.isoformat(),
        seed=effective_seed,
        profile_assignments=frozen_assignments,
        unassigned_videos=tuple(unassigned_list),
        conflicts_identified=conflicts,
        resolution_mode=request.resolution_mode,
        warnings=tuple(warnings),
        requested_profile_names=tuple(sorted(request.profile_names)),
    )


def interleave_tasks_by_profile(tasks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sắp xếp danh sách tác vụ theo thuật toán xoay vòng chéo tài khoản (Interleaved Round-Robin)."""
    if not tasks:
        return []

    grouped = defaultdict(list)
    profile_order: List[str] = []

    for t in tasks:
        p_name = str(t.get("profile_name") or "").strip()
        if p_name not in grouped:
            profile_order.append(p_name)
        grouped[p_name].append(t)

    interleaved: List[Dict[str, Any]] = []
    max_len = max(len(lst) for lst in grouped.values()) if grouped else 0

    for i in range(max_len):
        for p_name in profile_order:
            p_tasks = grouped[p_name]
            if i < len(p_tasks):
                interleaved.append(p_tasks[i])

    return interleaved
