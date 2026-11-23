# -*- coding: utf-8 -*-
"""
Bộ kiểm thử cho bản Audit & Tối ưu Lên lịch (Quy mô 800 Acc, Proxy Timezone,
Đăng bù ngày/đêm theo múi giờ Proxy, Đa luồng Proxy Lock và Giao diện thuần Việt).

Tuân thủ 100% Rule 14: Toàn bộ DB & file video dùng tmp_path, không chạm dữ liệu thật.
"""

import datetime
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from schedule_contracts import (
    ScheduleRequest,
    TimelineRequest,
    VideoProbeResult,
)
from schedule_calculator import (
    build_schedule_plan,
    build_schedule_timeline,
    extract_proxy_key,
    resolve_profile_region_label,
    resolve_profile_timezone,
    resolve_tz,
)
from schedule_manager import ScheduleManager
from schedule_service import ProxyAwareScheduleDispatcher


def test_proxy_timezone_priority_and_key_extraction():
    # 1. Proxy GeoIP cache takes highest priority
    cfg_geo = {
        "proxy_string": "103.1.2.3:8080:user1:secretpass",
        "proxy_geo": {"timezone": "America/Los_Angeles", "country_code": "US"},
        "fingerprint": {"timezone": "Europe/London"},
        "timezone": "Asia/Tokyo",
    }
    assert resolve_profile_timezone(cfg_geo) == "America/Los_Angeles"
    assert extract_proxy_key(cfg_geo) == "103.1.2.3:8080"
    assert "🇺🇸" in resolve_profile_region_label(cfg_geo)

    # 2. Fingerprint fallback when proxy_geo absent
    cfg_fp = {"fingerprint": {"timezone": "Asia/Tokyo"}}
    assert resolve_profile_timezone(cfg_fp) == "Asia/Tokyo"
    assert "🇯🇵" in resolve_profile_region_label(cfg_fp)

    # 3. Direct timezone fallback
    cfg_direct = {"timezone": "Europe/Berlin"}
    assert resolve_profile_timezone(cfg_direct) == "Europe/Berlin"

    # 4. Default fallback to local VN timezone
    assert resolve_profile_timezone({}) == "Asia/Ho_Chi_Minh"
    assert extract_proxy_key({"proxy_string": "1.2.3.4:8000", "use_proxy": False}) == ""


def test_randomize_in_slot_uniform_distribution():
    ny_tz = resolve_tz("America/New_York")
    now_utc = datetime.datetime(2026, 11, 20, 6, 0, tzinfo=datetime.timezone.utc)

    minutes_picked = set()
    for seed in range(1, 16):
        req = TimelineRequest(
            count=1,
            profile_name=f"Prof_{seed}",
            timezone_name="America/New_York",
            start_date_local=datetime.date(2026, 11, 20),
            max_daily_posts=1,
            selected_slots=("SANG",),
            jitter_minutes=0,
            randomize_in_slot=True,
            random_seed=seed * 101,
        )
        res = build_schedule_timeline(req, now_utc=now_utc)
        assert res.allocated_count == 1
        dt = res.scheduled_datetimes[0].astimezone(ny_tz)
        total_m = dt.hour * 60 + dt.minute
        # SANG slot is 08:00 (480) to 09:15 (555)
        assert 480 <= total_m <= 555
        minutes_picked.add(total_m)

    # Random across 15 seeds must pick multiple distinct minutes, not hardcoded midpoint
    assert len(minutes_picked) >= 8


def test_800_accounts_100_proxies_spreading_and_no_ip_collision(tmp_path):
    """Mô phỏng 800 acc dùng chung 100 proxy (8 acc/proxy), đảm bảo:
    - Tính toán xong nhanh (< 5s).
    - Mỗi acc chung proxy được rải khác khung giờ và cách nhau >= 15 phút.
    """
    num_accounts = 800
    num_proxies = 100
    profile_names = [f"Acc_{i:04d}" for i in range(num_accounts)]
    profile_configs = {
        f"Acc_{i:04d}": {
            "tiktok_account": f"tk_{i:04d}",
            "proxy_string": f"192.168.10.{i % num_proxies}:8000:u:p",
            "proxy_geo": {"timezone": "America/New_York", "country_code": "US"},
        }
        for i in range(num_accounts)
    }

    # Tạo 800 video giả trong tmp_path (mỗi acc 1 video)
    videos = []
    probe_results = []
    for i in range(num_accounts):
        v = tmp_path / f"vid_{i:04d}.mp4"
        v.write_bytes(b"mp4data")
        st = v.stat()
        videos.append(str(v.resolve()))
        probe_results.append(
            VideoProbeResult(
                canonical_path=str(v.resolve()),
                size_bytes=st.st_size,
                mtime_ns=int(st.st_mtime_ns),
                duration_seconds=30.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        )

    req = ScheduleRequest(
        video_paths=tuple(videos),
        profile_names=tuple(profile_names),
        profile_configs=profile_configs,
        formula="{filename}",
        daily_max=1,
        selected_slots=("SANG", "TRUA", "CHIEU", "TOI"),
        jitter_minutes=0,
        execution_mode="LOCAL_QUEUE",
        resolution_mode="APPEND",
        order_mode="ORIGINAL",
        randomize_in_slot=True,
        min_proxy_gap_minutes=15,
        random_seed=777,
    )

    now_utc = datetime.datetime(2026, 11, 20, 5, 0, tzinfo=datetime.timezone.utc)
    t0 = time.perf_counter()
    plan = build_schedule_plan(
        request=req,
        probes=probe_results,
        now_utc=now_utc,
    )
    elapsed = time.perf_counter() - t0

    assert plan.total_planned_tasks == 800
    assert elapsed < 5.0, f"800-account planning took too long: {elapsed:.2f}s"

    # Kiểm tra theo từng proxy: không có 2 bài nào trên cùng proxy trùng hoặc cách nhau < 15 phút
    from collections import defaultdict
    proxy_epochs = defaultdict(list)
    for p_name, tasks in plan.profile_assignments.items():
        p_key = extract_proxy_key(profile_configs[p_name])
        for pt in tasks:
            proxy_epochs[p_key].append(pt.scheduled_epoch)

    assert len(proxy_epochs) == 100
    for p_key, eps in proxy_epochs.items():
        eps.sort()
        assert len(eps) == 8
        for idx in range(1, len(eps)):
            gap = eps[idx] - eps[idx - 1]
            assert gap >= 15 * 60, f"Proxy {p_key} has IP collision gap={gap}s (< 900s)"


def test_recover_missed_tasks_day_vs_night_by_proxy_timezone(tmp_path):
    """Kiểm chứng Phương án B:
    - Lúc 07:00 UTC (= 14:00 VN | 02:00 AM US):
      + Acc VN1 (14:00): đăng bù ngay 1 bài lúc 14:00, bài 2 dời sang giờ vàng kế tiếp.
      + Acc VN2 (14:00, chung Proxy với VN1): đăng bù 1 bài nhưng giãn cách +5 phút (tránh trùng IP).
      + Acc US1 (02:00 AM): ban đêm -> KHÔNG đăng bù ngay, dời sang khung giờ vàng sáng US (>= 08:00 EST).
    """
    db_file = tmp_path / "recover_test.db"
    mgr = ScheduleManager(storage_file=db_file)

    now_utc = datetime.datetime(2026, 11, 23, 7, 0, 0, tzinfo=datetime.timezone.utc)
    past_epoch = int((now_utc - datetime.timedelta(hours=3)).timestamp())

    mgr.add_tasks([
        {
            "id": "vn1_t1", "profile_name": "Acc_VN1", "video_path": "a.mp4",
            "video_filename": "a.mp4", "title": "VN1-1",
            "scheduled_time": "2026-11-23T11:00:00+07:00", "scheduled_epoch": past_epoch,
            "status": "PENDING", "execution_mode": "LOCAL_QUEUE",
        },
        {
            "id": "vn1_t2", "profile_name": "Acc_VN1", "video_path": "b.mp4",
            "video_filename": "b.mp4", "title": "VN1-2",
            "scheduled_time": "2026-11-23T11:30:00+07:00", "scheduled_epoch": past_epoch + 1800,
            "status": "PENDING", "execution_mode": "LOCAL_QUEUE",
        },
        {
            "id": "vn2_t1", "profile_name": "Acc_VN2", "video_path": "c.mp4",
            "video_filename": "c.mp4", "title": "VN2-1",
            "scheduled_time": "2026-11-23T11:00:00+07:00", "scheduled_epoch": past_epoch,
            "status": "PENDING", "execution_mode": "LOCAL_QUEUE",
        },
        {
            "id": "us1_t1", "profile_name": "Acc_US1", "video_path": "d.mp4",
            "video_filename": "d.mp4", "title": "US1-1",
            "scheduled_time": "2026-11-22T23:00:00-05:00", "scheduled_epoch": past_epoch,
            "status": "PENDING", "execution_mode": "LOCAL_QUEUE",
        },
    ])

    profile_configs = {
        "Acc_VN1": {"proxy_string": "10.0.0.1:8080", "proxy_geo": {"timezone": "Asia/Ho_Chi_Minh"}},
        "Acc_VN2": {"proxy_string": "10.0.0.1:8080", "proxy_geo": {"timezone": "Asia/Ho_Chi_Minh"}},
        "Acc_US1": {"proxy_string": "10.0.0.2:8080", "proxy_geo": {"timezone": "America/New_York"}},
    }

    recovered = mgr.recover_missed_tasks(
        max_daily_posts=2,
        profile_configs=profile_configs,
        proxy_cooldown_minutes=5,
        now_utc=now_utc,
    )
    assert len(recovered) == 4
    by_id = {t["id"]: t for t in recovered}

    now_epoch = int(now_utc.timestamp())
    # VN1_t1: đăng bù ngay
    assert by_id["vn1_t1"]["scheduled_epoch"] == now_epoch
    # VN1_t2: dời sang khung giờ vàng tiếp theo (cách >= 2h -> Chiều 17:30+ hoặc Tối)
    assert by_id["vn1_t2"]["scheduled_epoch"] >= now_epoch + 2 * 3600

    # VN2_t1: chung Proxy với VN1 -> bị đẩy giãn cách +5 phút (300s)
    assert by_id["vn2_t1"]["scheduled_epoch"] == now_epoch + 300

    # US1_t1: lúc 02:00 AM New York -> KHÔNG đăng bù ngay, dời sang khung giờ sáng US (08:00-09:15 EST)
    ny_tz = resolve_tz("America/New_York")
    us_dt = datetime.datetime.fromtimestamp(by_id["us1_t1"]["scheduled_epoch"], tz=ny_tz)
    assert us_dt.hour in (8, 9)
    assert "tránh đăng ban đêm" in by_id["us1_t1"]["error_message"]


def test_proxy_aware_dispatcher_parallel_and_proxy_lock(tmp_path):
    """Kiểm chứng Worker Pool: khác Proxy chạy song song, chung Proxy bị khóa không chạy cùng lúc."""
    db_file = tmp_path / "dispatch.db"
    mgr = ScheduleManager(storage_file=db_file)
    past_epoch = int(time.time()) - 60

    mgr.add_tasks([
        {
            "id": "t_p1_a", "profile_name": "P1_A", "video_path": "1.mp4",
            "video_filename": "1.mp4", "title": "T1",
            "scheduled_time": "2026-11-23T08:00:00", "scheduled_epoch": past_epoch,
            "status": "PENDING", "execution_mode": "LOCAL_QUEUE",
        },
        {
            "id": "t_p1_b", "profile_name": "P1_B", "video_path": "2.mp4",
            "video_filename": "2.mp4", "title": "T2",
            "scheduled_time": "2026-11-23T08:00:00", "scheduled_epoch": past_epoch,
            "status": "PENDING", "execution_mode": "LOCAL_QUEUE",
        },
        {
            "id": "t_p2_a", "profile_name": "P2_A", "video_path": "3.mp4",
            "video_filename": "3.mp4", "title": "T3",
            "scheduled_time": "2026-11-23T08:00:00", "scheduled_epoch": past_epoch,
            "status": "PENDING", "execution_mode": "LOCAL_QUEUE",
        },
    ])

    profile_configs = {
        "P1_A": {"proxy_string": "1.1.1.1:8080"},
        "P1_B": {"proxy_string": "1.1.1.1:8080"},  # Chung proxy với P1_A
        "P2_A": {"proxy_string": "2.2.2.2:8080"},  # Proxy khác
    }

    executed = []
    started_event = threading.Event()
    release_event = threading.Event()

    def fake_exec(task):
        executed.append(task["id"])
        if len(executed) >= 2:
            started_event.set()
        release_event.wait(timeout=3.0)

    dispatcher = ProxyAwareScheduleDispatcher(
        manager=mgr,
        execute_fn=fake_exec,
        max_workers=4,
        proxy_cooldown_seconds=60,
    )
    try:
        futs = dispatcher.dispatch_due_tasks(profile_configs=profile_configs)
        # Chỉ nhận tối đa 2 task: 1 từ Proxy 1 (P1_A) và 1 từ Proxy 2 (P2_A), P1_B phải chờ
        assert len(futs) == 2
        started_event.wait(timeout=2.0)
        assert set(executed) == {"t_p1_a", "t_p2_a"}
        assert "t_p1_b" not in executed
    finally:
        release_event.set()
        dispatcher.shutdown(wait=True)


def test_ui_pure_vietnamese_and_no_server_schedule(tmp_path):
    """Kiểm chứng giao diện tối giản: bỏ Jitter, bỏ Mode SERVER_SCHEDULE, thuần Tiếng Việt."""
    import tkinter as tk
    from schedule_ui import BulkScheduleModal, AutoScheduleView

    root = tk.Tk()
    root.withdraw()
    mgr = ScheduleManager(storage_file=tmp_path / "ui_audit.db")

    try:
        modal = BulkScheduleModal(
            parent=root,
            selected_profiles=["Acc_01"],
            profile_configs={"Acc_01": {"tiktok_account": "tk1", "proxy_geo": {"timezone": "Asia/Ho_Chi_Minh"}}},
            schedule_manager=mgr,
        )
        modal.withdraw()

        # Không còn ô nhập Jitter
        assert not hasattr(modal, "jitter_entry")
        assert modal.exec_mode_var.get() == "LOCAL_QUEUE"

        # Combobox thuần Tiếng Việt đồng bộ 2 chiều
        assert modal.order_display_var.get() == "Theo thứ tự file"
        modal.order_display_var.set("Theo bộ tập (1, 2, 3...)")
        assert modal.order_mode_var.get() == "SERIES"

        assert modal.resolution_display_var.get() == "Nối tiếp sau lịch cũ"
        modal.resolution_display_var.set("Ghi đè (Hủy lịch cũ)")
        assert modal.resolution_mode_var.get() == "OVERWRITE"

        modal.destroy()

        # Kiểm chứng bảng quản lý lịch thuần Tiếng Việt & bỏ cột Mode
        view = AutoScheduleView(root, schedule_manager=mgr)
        assert not hasattr(view, "cb_mode")
        cb_vals = list(view.cb_status.cget("values"))
        assert "SERVER_SCHEDULED" not in cb_vals
        assert "Đang chờ đăng" in cb_vals
        assert "Dời lịch (tắt máy)" in cb_vals
        cols = view.tree.cget("columns")
        assert "mode" not in cols
    finally:
        root.destroy()


def test_past_slot_exclusion_and_start_tomorrow_option(tmp_path):
    """Kiểm chứng:
    1. Lên lịch lúc 16:00 chiều: các khung Sáng/Trưa của hôm nay bị bỏ qua 100%, không lên lịch trong quá khứ.
    2. Chọn 'Ngày mai' trên UI: ép lịch bắt đầu từ ngày mai trở đi.
    """
    import tkinter as tk
    from schedule_ui import BulkScheduleModal

    vn_tz = resolve_tz("Asia/Ho_Chi_Minh")
    # 16:00 VN = 09:00 UTC
    now_utc = datetime.datetime(2026, 11, 23, 9, 0, tzinfo=datetime.timezone.utc)
    req_today = TimelineRequest(
        count=2,
        profile_name="Acc_VN",
        timezone_name="Asia/Ho_Chi_Minh",
        max_daily_posts=4,
        selected_slots=("SANG", "TRUA", "CHIEU", "TOI"),
        randomize_in_slot=True,
        random_seed=99,
    )
    res_today = build_schedule_timeline(req_today, now_utc=now_utc)
    assert res_today.allocated_count == 2
    for dt in res_today.scheduled_datetimes:
        assert int(dt.timestamp()) >= int(now_utc.timestamp()) + 20 * 60
        local_dt = dt.astimezone(vn_tz)
        assert local_dt.hour >= 17  # Chỉ nhận Chiều (17h30+) hoặc Tối (20h+) hôm nay

    # Kiểm tra option "Ngày mai" trên BulkScheduleModal
    root = tk.Tk()
    root.withdraw()
    mgr = ScheduleManager(storage_file=tmp_path / "start_tmr.db")
    v1 = tmp_path / "v1.mp4"
    v1.write_bytes(b"mp4")
    try:
        modal = BulkScheduleModal(
            parent=root,
            selected_profiles=["Acc_01"],
            profile_configs={"Acc_01": {"tiktok_account": "tk1", "proxy_geo": {"timezone": "Asia/Ho_Chi_Minh"}}},
            schedule_manager=mgr,
        )
        modal.withdraw()
        modal.scanned_videos = [v1]
        assert modal.start_day_mode_var.get() == "Hôm nay"
        modal.start_day_mode_var.set("Ngày mai")

        captured = {}

        def fake_prepare(request, manager=None):
            captured["start_date_local"] = request.start_date_local
            raise RuntimeError("stop_after_capture")

        with patch("schedule_ui.prepare_schedule_plan", side_effect=fake_prepare), \
             patch("tkinter.messagebox.showerror"):
            modal._generate_preview()

        expected_tmr = datetime.date.today() + datetime.timedelta(days=1)
        assert captured["start_date_local"] == expected_tmr
        modal.destroy()
    finally:
        root.destroy()
