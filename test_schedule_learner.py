from datetime import datetime, timezone
import pytest
from youtube_monitor.schedule_learner import (
    PollingWindow,
    ScheduleLearner,
    _time_str_to_minutes,
    _minutes_to_time_str,
    merge_time_ranges,
    is_time_in_windows,
)


def test_time_conversions():
    assert _time_str_to_minutes("18:00") == 18 * 60
    assert _time_str_to_minutes("00:15") == 15
    assert _minutes_to_time_str(18 * 60 + 5) == "18:05"
    assert _minutes_to_time_str(0) == "00:00"


def test_merge_time_ranges():
    ranges = [(1070, 1090), (1085, 1110), (1200, 1220)]
    merged = merge_time_ranges(ranges)
    assert merged == [(1070, 1110), (1200, 1220)]


def test_is_time_in_windows():
    win = PollingWindow(
        id="test_win",
        start_time="17:50",
        end_time="18:10",
        days=[0, 1, 2, 3, 4, 5, 6],
        enabled=True,
    )
    # 18:00 UTC+7 on a Monday (2026-09-07 is Monday)
    dt_inside = datetime.fromisoformat("2026-09-07T18:00:00+07:00")
    assert is_time_in_windows(dt_inside, [win], tz_str="Asia/Ho_Chi_Minh")

    # 17:45 (Before window)
    dt_before = datetime.fromisoformat("2026-09-07T17:45:00+07:00")
    assert not is_time_in_windows(dt_before, [win], tz_str="Asia/Ho_Chi_Minh")

    # 18:15 (After window)
    dt_after = datetime.fromisoformat("2026-09-07T18:15:00+07:00")
    assert not is_time_in_windows(dt_after, [win], tz_str="Asia/Ho_Chi_Minh")


def test_analyze_timestamps_clustering():
    learner = ScheduleLearner(timezone_str="Asia/Ho_Chi_Minh")
    # Simulate 10 videos published around 18:00 (11:00 UTC) and 5 videos around 12:00 (05:00 UTC)
    timestamps = [
        "2026-08-01T11:02:00Z",
        "2026-08-02T10:58:00Z",
        "2026-08-03T11:05:00Z",
        "2026-08-04T11:00:00Z",
        "2026-08-05T10:59:00Z",
        "2026-08-06T11:01:00Z",
        "2026-08-07T11:04:00Z",
        "2026-08-08T11:00:00Z",
        "2026-08-09T10:57:00Z",
        "2026-08-10T11:03:00Z",
        # 12:00 group
        "2026-08-01T05:01:00Z",
        "2026-08-02T04:59:00Z",
        "2026-08-03T05:02:00Z",
        "2026-08-04T05:00:00Z",
    ]

    res = learner.analyze_timestamps(timestamps)
    windows = res["predicted_windows"]
    assert len(windows) == 2
    # Check that 18:00 and 12:00 clusters are detected
    expected_times = {w["expected_time"] for w in windows}
    assert any("18:0" in t for t in expected_times)
    assert any("12:0" in t for t in expected_times)


def test_locked_window_preservation():
    learner = ScheduleLearner(timezone_str="Asia/Ho_Chi_Minh")
    manual_win = PollingWindow(
        id="manual_custom",
        start_time="20:00",
        end_time="20:30",
        days=[0, 1, 2, 3, 4, 5, 6],
        enabled=True,
        locked=True,
        source="MANUAL",
    )

    history = ["2026-08-01T11:00:00Z", "2026-08-02T11:00:00Z"]
    new_ts = "2026-08-03T11:00:00Z"

    updated_history, final_windows, _ = learner.update_history_and_recluster(
        history, new_ts, [manual_win]
    )

    assert new_ts in updated_history
    # manual_win must be kept intact
    assert any(w.id == "manual_custom" and w.locked for w in final_windows)
