"""Module học thói quen đăng video của kênh YouTube (Schedule Learner).

Phân tích lịch sử 30-50 video gần nhất để tìm các cụm giờ đăng tập trung,
tính độ lệch chuẩn, nhận diện mẫu ngày trong tuần và sinh các cửa sổ
dự đoán (Predictive Windows) chuẩn giờ ± 10 phút.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
WINDOW_MARGIN_MINUTES = 10  # Cửa sổ quét: giờ dự kiến ± 10 phút (tổng cộng 20 phút)

_TIMEZONE_OFFSETS = {
    "Asia/Ho_Chi_Minh": 7,
    "Asia/Bangkok": 7,
    "Asia/Jakarta": 7,
    "Asia/Tokyo": 9,
    "Asia/Seoul": 9,
    "UTC": 0,
    "GMT": 0,
    "America/New_York": -5,
    "America/Chicago": -6,
    "America/Denver": -7,
    "America/Los_Angeles": -8,
    "Europe/London": 0,
    "Europe/Paris": 1,
    "Europe/Berlin": 1,
}


def resolve_timezone(tz_name: str) -> timezone:
    """Trả về đối tượng datetime.timezone tương thích Windows không cần gói tzdata."""
    name = str(tz_name or "").strip()
    if name in _TIMEZONE_OFFSETS:
        return timezone(timedelta(hours=_TIMEZONE_OFFSETS[name]))
    if name.startswith("+") or name.startswith("-"):
        try:
            parts = name[1:].split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            sign = 1 if name.startswith("+") else -1
            return timezone(sign * timedelta(hours=h, minutes=m))
        except Exception:
            pass
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(name)
    except Exception:
        return timezone(timedelta(hours=7))


@dataclass
class PollingWindow:
    id: str
    start_time: str  # "HH:MM"
    end_time: str  # "HH:MM"
    days: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # 0=T2, 6=CN
    enabled: bool = True
    locked: bool = False  # Nếu True: thuật toán tự học không được tự ý sửa/xóa
    source: str = "LEARNED"  # "LEARNED" hoặc "MANUAL"
    expected_time: str = ""  # "HH:MM"
    std_dev_minutes: float = 0.0
    sample_count: int = 0
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PollingWindow":
        return cls(
            id=str(data.get("id") or ""),
            start_time=str(data.get("start_time") or "00:00"),
            end_time=str(data.get("end_time") or "23:59"),
            days=list(data.get("days") if data.get("days") is not None else [0, 1, 2, 3, 4, 5, 6]),
            enabled=bool(data.get("enabled", True)),
            locked=bool(data.get("locked", False)),
            source=str(data.get("source") or "LEARNED"),
            expected_time=str(data.get("expected_time") or ""),
            std_dev_minutes=float(data.get("std_dev_minutes") or 0.0),
            sample_count=int(data.get("sample_count") or 0),
            confidence=float(data.get("confidence") or 1.0),
        )


def _time_str_to_minutes(t_str: str) -> int:
    parts = t_str.strip().split(":")
    h = int(parts[0]) if parts else 0
    m = int(parts[1]) if len(parts) > 1 else 0
    return (h * 60 + m) % 1440


def _minutes_to_time_str(total_minutes: int) -> str:
    total_minutes = int(total_minutes) % 1440
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{h:02d}:{m:02d}"


def merge_time_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Gộp các khoảng thời gian (phút trong ngày [0..1440]) bị giao hoặc liền kề nhau."""
    if not ranges:
        return []
    # Chuẩn hoá và sắp xếp
    sorted_ranges = sorted(ranges, key=lambda r: r[0])
    merged: List[Tuple[int, int]] = []

    for start, end in sorted_ranges:
        if not merged:
            merged.append((start, end))
            continue
        last_start, last_end = merged[-1]
        if start <= last_end:  # Giao nhau hoặc nối tiếp
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def is_time_in_windows(
    dt: datetime,
    windows: List[PollingWindow],
    tz_str: str = DEFAULT_TIMEZONE,
) -> bool:
    """Kiểm tra thời điểm dt có nằm trong bất kỳ cửa sổ polling đang kích hoạt hay không."""
    tz = resolve_timezone(tz_str)
    local_dt = dt.astimezone(tz)
    weekday = local_dt.weekday()  # 0=Monday, 6=Sunday
    current_minute = local_dt.hour * 60 + local_dt.minute

    # Lọc các cửa sổ áp dụng cho ngày hôm nay
    active_ranges: List[Tuple[int, int]] = []
    for w in windows:
        if not w.enabled:
            continue
        if weekday not in w.days:
            continue
        start_min = _time_str_to_minutes(w.start_time)
        end_min = _time_str_to_minutes(w.end_time)
        if start_min <= end_min:
            active_ranges.append((start_min, end_min))
        else:
            # Cửa sổ vắt qua nửa đêm (vd 23:50 -> 00:10)
            active_ranges.append((start_min, 1440))
            active_ranges.append((0, end_min))

    merged = merge_time_ranges(active_ranges)
    for s, e in merged:
        if s <= current_minute <= e:
            return True
    return False


class ScheduleLearner:
    """Thuật toán phân tích lịch sử đăng video và cập nhật lịch học."""

    def __init__(self, timezone_str: str = DEFAULT_TIMEZONE) -> None:
        self.timezone_str = timezone_str
        self.tz = resolve_timezone(timezone_str)

    def parse_iso_datetime(self, iso_str: str) -> Optional[datetime]:
        try:
            val = iso_str.strip()
            if val.endswith("Z"):
                val = val[:-1] + "+00:00"
            return datetime.fromisoformat(val).astimezone(self.tz)
        except Exception:
            return None

    def analyze_timestamps(
        self,
        published_iso_list: List[str],
        cluster_tolerance_minutes: int = 35,
        min_cluster_size: int = 2,
    ) -> Dict[str, Any]:
        """Phân tích danh sách thời điểm đăng video để tìm các khung giờ dự đoán."""
        valid_dts: List[datetime] = []
        for s in published_iso_list:
            dt = self.parse_iso_datetime(s)
            if dt:
                valid_dts.append(dt)

        if not valid_dts:
            return {
                "timezone": self.timezone_str,
                "confidence_score": 0.0,
                "predicted_windows": [],
                "analyzed_count": 0,
            }

        # Đổi thành phút trong ngày [0..1440]
        entries = [(dt.hour * 60 + dt.minute, dt.weekday(), dt) for dt in valid_dts]
        entries.sort(key=lambda x: x[0])

        # Gom cụm 1D (Greedy clustering theo khoảng cách phút)
        clusters: List[List[Tuple[int, int, datetime]]] = []
        for item in entries:
            minute = item[0]
            assigned = False
            for c in clusters:
                # Kiểm tra khoảng cách với điểm trung bình của cụm
                c_mean = sum(x[0] for x in c) / len(c)
                diff = abs(minute - c_mean)
                # Xử lý vòng quanh nửa đêm (0h vs 24h)
                diff = min(diff, 1440 - diff)
                if diff <= cluster_tolerance_minutes:
                    c.append(item)
                    assigned = True
                    break
            if not assigned:
                clusters.append([item])

        # Lọc cụm đạt kích thước tối thiểu
        predicted_windows: List[PollingWindow] = []
        total_samples = len(valid_dts)

        for idx, cluster in enumerate(clusters):
            if len(cluster) < min_cluster_size and total_samples >= 5:
                continue

            c_minutes = [x[0] for x in cluster]
            mean_minute = sum(c_minutes) / len(c_minutes)

            # Tính độ lệch chuẩn
            variance = sum((m - mean_minute) ** 2 for m in c_minutes) / len(c_minutes)
            std_dev = math.sqrt(variance)

            # Các thứ trong tuần xuất hiện
            weekdays = sorted(list({x[1] for x in cluster}))
            # Nếu cụm xuất hiện trên >= 4 ngày khác nhau -> xem như áp dụng cả tuần
            if len(weekdays) >= 4:
                weekdays = [0, 1, 2, 3, 4, 5, 6]

            expected_time_str = _minutes_to_time_str(int(round(mean_minute)))
            start_min = (int(round(mean_minute)) - WINDOW_MARGIN_MINUTES) % 1440
            end_min = (int(round(mean_minute)) + WINDOW_MARGIN_MINUTES) % 1440

            sample_ratio = len(cluster) / total_samples
            # Độ tin cậy cao nếu mẫu nhiều và độ lệch chuẩn thấp
            confidence = min(1.0, (sample_ratio * 1.5) * max(0.5, 1.0 - (std_dev / 60.0)))

            pw = PollingWindow(
                id=f"pred_{expected_time_str.replace(':', '')}_{idx}",
                expected_time=expected_time_str,
                start_time=_minutes_to_time_str(start_min),
                end_time=_minutes_to_time_str(end_min),
                days=weekdays,
                enabled=True,
                locked=False,
                source="LEARNED",
                std_dev_minutes=round(std_dev, 1),
                sample_count=len(cluster),
                confidence=round(confidence, 2),
            )
            predicted_windows.append(pw)

        # Tính tổng điểm tin cậy
        overall_confidence = (
            sum(w.confidence * w.sample_count for w in predicted_windows) / total_samples
            if total_samples > 0 and predicted_windows
            else 0.0
        )

        return {
            "timezone": self.timezone_str,
            "confidence_score": round(min(1.0, overall_confidence), 2),
            "predicted_windows": [w.to_dict() for w in predicted_windows],
            "analyzed_count": total_samples,
            "last_analyzed_at": datetime.now(self.tz).isoformat(),
        }

    def update_history_and_recluster(
        self,
        history_iso_list: List[str],
        new_published_iso: str,
        existing_windows: List[PollingWindow],
        max_history: int = 50,
    ) -> Tuple[List[str], List[PollingWindow], bool]:
        """Bổ sung video mới vào lịch sử, tính toán lại và bảo lưu các khung giờ LOCKED."""
        history = list(history_iso_list or [])
        if new_published_iso not in history:
            history.append(new_published_iso)
            history.sort()
            if len(history) > max_history:
                history = history[-max_history:]

        # Phân tích lại trên lịch sử mới
        analysis = self.analyze_timestamps(history)
        new_learned = [PollingWindow.from_dict(w) for w in analysis.get("predicted_windows", [])]

        # Tách các khung giờ LOCKED hoặc MANUAL cũ để bảo toàn
        locked_or_manual = [w for w in existing_windows if w.locked or w.source == "MANUAL"]

        # Hợp nhất: giữ nguyên locked/manual, thêm learned mới nếu không bị khóa đè
        final_windows = list(locked_or_manual)
        has_new_window = False

        for nlw in new_learned:
            # Kiểm tra xem có trùng lặp với window đã bị locked hay không
            overlap = False
            for lw in locked_or_manual:
                diff = abs(_time_str_to_minutes(nlw.start_time) - _time_str_to_minutes(lw.start_time))
                if min(diff, 1440 - diff) <= 15:
                    overlap = True
                    break
            if not overlap:
                final_windows.append(nlw)
                has_new_window = True

        return history, final_windows, has_new_window
