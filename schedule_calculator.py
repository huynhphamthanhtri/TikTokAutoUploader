"""
schedule_calculator.py - Động cơ tính toán, phân bổ và bóc tách công thức lên lịch TikTok.

Chịu trách nhiệm:
1. Quét và kiểm tra tính hợp lệ của video nguồn (.mp4, size > 0).
2. Phân bổ video xoay vòng Round-Robin cho danh sách profile.
3. Bóc tách token công thức đặt tên (loại bỏ spintax để chuẩn hóa đa thị trường).
4. Tính toán timeline khung giờ vàng và độ lệch ngẫu nhiên (Jitter) đa ngày.
"""

from __future__ import annotations

import datetime
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 4 Khung Giờ Vàng Chuẩn TikTok (Tên khung, (Giờ bắt đầu, Phút bắt đầu), (Giờ kết thúc, Phút kết thúc))
DEFAULT_GOLDEN_HOUR_SLOTS = {
    "SANG": ((8, 0), (9, 15)),
    "TRUA": ((11, 45), (13, 0)),
    "CHIEU": ((17, 30), (18, 45)),
    "TOI": ((20, 0), (21, 30)),
}


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
    """Bóc tách công thức đặt tiêu đề bài đăng theo bộ biến chuẩn (không dùng spintax).
    
    Tokens hỗ trợ:
    - {filename}: Tên gốc sạch của video (bỏ đuôi .mp4)
    - {n} hoặc {index}: Số thứ tự tăng dần (1, 2, 3...)
    - {profile}: Tên profile
    - {account}: Tên @username TikTok
    - {date}: Ngày đăng dự kiến định dạng DD-MM (ví dụ 18-10)
    - {year}: Năm đăng (ví dụ 2026)
    """
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

    # Dọn dẹp khoảng trắng thừa
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
    videos: Sequence[Path],
    profile_names: Sequence[str],
) -> Dict[str, List[Path]]:
    """Phân bổ danh sách video cho các profile theo giải thuật xoay vòng (Round-Robin)."""
    valid_profiles = [str(p).strip() for p in profile_names if str(p).strip()]
    if not valid_profiles:
        return {}

    distribution: Dict[str, List[Path]] = {p: [] for p in valid_profiles}
    for idx, v in enumerate(videos):
        target_profile = valid_profiles[idx % len(valid_profiles)]
        distribution[target_profile].append(v)

    return distribution


def round_to_nearest_interval(dt: datetime.datetime, interval_minutes: int = 5) -> datetime.datetime:
    """Làm tròn mốc thời gian về bội số của interval_minutes (khuyến nghị cho TikTok Schedule)."""
    total_minutes = dt.minute + dt.second / 60.0
    rounded_minutes = round(total_minutes / interval_minutes) * interval_minutes
    return dt.replace(second=0, microsecond=0) + datetime.timedelta(minutes=rounded_minutes - dt.minute)


def generate_schedule_timeline(
    count: int,
    start_date: Optional[datetime.datetime] = None,
    max_daily_posts: int = 2,
    selected_slots: Optional[Sequence[str]] = None,
    jitter_minutes: int = 10,
    min_lead_time_minutes: int = 20,
    random_seed: Optional[int] = None,
) -> List[datetime.datetime]:
    """Tạo danh sách mốc thời gian hẹn giờ dàn trải đa ngày vào các khung giờ vàng.
    
    Tham số:
    - count: Tổng số mốc thời gian cần sinh
    - start_date: Ngày bắt đầu (mặc định hôm nay)
    - max_daily_posts: Tối đa số bài đăng mỗi ngày (mặc định 2)
    - selected_slots: Danh sách khung giờ được chọn (['SANG', 'TRUA', 'CHIEU', 'TOI'])
    - jitter_minutes: Độ lệch ngẫu nhiên tối đa quanh giờ vàng (mặc định ±10 phút)
    - min_lead_time_minutes: Khoảng thời gian tối thiểu từ bây giờ (TikTok yêu cầu >= 15 phút)
    """
    if count <= 0:
        return []

    rng = random.Random(random_seed) if random_seed is not None else random.Random()
    now = datetime.datetime.now()
    base_start = start_date or now
    if base_start < now:
        base_start = now

    available_slots = list(selected_slots or ["SANG", "TRUA", "CHIEU", "TOI"])
    if not available_slots:
        available_slots = ["SANG", "TRUA", "CHIEU", "TOI"]

    # Thứ tự khung giờ vàng trong ngày
    slot_order = ["SANG", "TRUA", "CHIEU", "TOI"]
    ordered_slots = [s for s in slot_order if s in available_slots]
    if not ordered_slots:
        ordered_slots = slot_order

    timeline: List[datetime.datetime] = []
    current_day = base_start.date()

    # Giới hạn số lần lặp tối đa để tránh vòng lặp vô hạn
    max_days_guard = max(365, count * 5)
    days_checked = 0

    while len(timeline) < count and days_checked < max_days_guard:
        daily_slots_used = 0
        for slot_name in ordered_slots:
            if daily_slots_used >= max_daily_posts or len(timeline) >= count:
                break

            slot_bounds = DEFAULT_GOLDEN_HOUR_SLOTS.get(slot_name)
            if not slot_bounds:
                continue

            (start_h, start_m), (end_h, end_m) = slot_bounds
            # Lấy mốc giữa của khung giờ làm mốc cơ sở
            mid_minutes = ((start_h * 60 + start_m) + (end_h * 60 + end_m)) // 2
            mid_h, mid_m = divmod(mid_minutes, 60)

            # Tạo độ lệch ngẫu nhiên Jitter
            jitter = rng.randint(-abs(jitter_minutes), abs(jitter_minutes))
            raw_target_time = datetime.datetime.combine(
                current_day,
                datetime.time(hour=mid_h, minute=mid_m),
            ) + datetime.timedelta(minutes=jitter)

            # Làm tròn về bội số 5 phút
            target_time = round_to_nearest_interval(raw_target_time, interval_minutes=5)

            # Kiểm tra xem mốc này có đủ khoảng an toàn so với thời điểm hiện tại không
            if target_time < now + datetime.timedelta(minutes=min_lead_time_minutes):
                # Đã qua giờ hoặc quá sát giờ -> Bỏ qua slot này hôm nay
                continue

            timeline.append(target_time)
            daily_slots_used += 1

        current_day += datetime.timedelta(days=1)
        days_checked += 1

    return timeline


def interleave_tasks_by_profile(tasks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sắp xếp danh sách tác vụ theo thuật toán xoay vòng chéo tài khoản (Interleaved Round-Robin).
    
    Ví dụ 10 profile, mỗi profile 3 video:
    - Vòng 1: Profile 1 (Vid 1) -> Profile 2 (Vid 1) -> ... -> Profile 10 (Vid 1)
    - Vòng 2: Profile 1 (Vid 2) -> Profile 2 (Vid 2) -> ... -> Profile 10 (Vid 2)
    - Vòng 3: Profile 1 (Vid 3) -> Profile 2 (Vid 3) -> ... -> Profile 10 (Vid 3)
    
    Giúp tài khoản có khoảng nghỉ tự nhiên rất lớn giữa các lần đăng, chống spam và tránh bóp tương tác.
    """
    if not tasks:
        return []

    from collections import defaultdict
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
