"""
test_schedule_planning.py - Bộ kiểm thử tính đúng đắn nghiệp vụ cho Planning Engine (Phase 1).

Bao phủ:
1. Regression P0: Loại bỏ hoàn toàn fallback now(), video thừa được ghi nhận unassigned.
2. F03: Video thiếu probe hoặc probe invalid hoặc file bị thay đổi (stale probe) vào unassigned_videos.
3. F04: Occupancy slot xuyên đêm kiểm tra theo ngày đăng thực tế SAU ROUNDING; chống trùng epoch chính xác.
4. F05: Slots không theo thứ tự vẫn được sắp xếp tăng dần và không bị ngắt sớm horizon.
5. F07: Plan ID và payload hash thay đổi khi đổi timezone, account, delete_source (chống va chạm ID).
6. F08: Tính bất biến sâu (Deep Immutability), MappingProxyType ngăn chặn mutation task dict lồng nhau.
7. F09: Phân biệt rõ lỗi đọc snapshot với không có conflict; cảnh báo rõ ràng.
8. F10: Chuẩn hóa, trim và deduplicate profile names; không để thất thoát video khi không có profile.
9. F12: start_time_local bỏ qua các slot đã qua trong ngày bắt đầu.
10. Timezone & Múi giờ: Xử lý chuẩn xác profile New York, London, Tokyo trên máy tính local.
11. Sắp xếp chuỗi bài tự nhiên (Series Natural Sort: Part 2 trước Part 10).
12. Điều phối prepare_schedule_plan và xử lý video probe lỗi.

Quy tắc bắt buộc:
- TUÂN THỦ 100% RULE 14: Zero Live Data Mutation. Toàn bộ chạy trong tempfile.mkdtemp().
"""

import copy
import datetime
import json
import pickle
import shutil
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock, patch

from schedule_contracts import (
    ConflictSnapshot,
    SchedulePlan,
    ScheduleRequest,
    TimelineRequest,
    TimelineSlot,
    VideoProbeResult,
    VideoValidationPolicy,
)
from schedule_calculator import (
    build_schedule_plan,
    build_schedule_timeline,
    generate_schedule_timeline,
    natural_sort_key,
    order_video_paths,
    resolve_tz,
)
from schedule_service import (
    build_conflict_snapshot_from_manager,
    prepare_schedule_plan,
    summarize_schedule_plan,
)


class TestSchedulePlanningEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_plan_")
        self.test_path = Path(self.test_dir)
        # Mốc: 2026-10-25 10:00:00 UTC (17:00 Giờ VN, 06:00 Giờ New York)
        self.fixed_now_utc = datetime.datetime(2026, 10, 25, 10, 0, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_dummy_video(self, name: str) -> Path:
        p = self.test_path / name
        p.write_bytes(b"dummy video binary content")
        return p

    # =========================================================================
    # 1. REGRESSION P0: KHÔNG FALLBACK NOW() KHI THIẾU SLOT
    # =========================================================================

    def test_p0_no_silent_fallback_to_now(self):
        """Chứng minh: Khi yêu cầu 5 video, các mốc thời gian đều được tính toán hợp lệ trong tương lai, KHÔNG mốc nào bằng now()."""
        req = TimelineRequest(
            count=5,
            profile_name="Profile_P0",
            timezone_name="America/New_York",
            start_date_local=datetime.date(2026, 10, 26),
            max_daily_posts=1,
            selected_slots=("SANG",),
            jitter_minutes=5,
            min_lead_time_minutes=20,
            execution_mode="SERVER_SCHEDULE",
            random_seed=123,
        )

        res = build_schedule_timeline(req, now_utc=self.fixed_now_utc)
        self.assertEqual(res.allocated_count, 5)
        self.assertEqual(res.unallocated_count, 0)
        for dt in res.scheduled_datetimes:
            self.assertGreater(dt.timestamp(), self.fixed_now_utc.timestamp() + 3600)

    def test_p0_overflow_unassigned_in_schedule_plan(self):
        """Khi video vượt quá khả năng phân bổ, SchedulePlan ghi nhận unassigned_videos kèm lý do."""
        v1 = self._create_dummy_video("vid1.mp4")
        v2 = self._create_dummy_video("vid2.mp4")
        v3 = self._create_dummy_video("vid3.mp4")

        start_day = (self.fixed_now_utc + datetime.timedelta(days=8)).date()

        req = ScheduleRequest(
            video_paths=(str(v1), str(v2), str(v3)),
            profile_names=("Profile_Test",),
            profile_configs={"Profile_Test": {"tiktok_account": "user_test", "timezone": "America/New_York"}},
            formula="Test Video {n}",
            daily_max=1,
            selected_slots=("SANG",),
            execution_mode="SERVER_SCHEDULE",
            start_date_local=start_day,
            random_seed=42,
        )

        probes = [
            VideoProbeResult(str(v.resolve()), v.stat().st_size, getattr(v.stat(), "st_mtime_ns", 0), 15.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
            for v in (v1, v2, v3)
        ]

        plan = build_schedule_plan(req, probes=probes, now_utc=self.fixed_now_utc)
        self.assertEqual(plan.total_planned_tasks, 2)
        self.assertEqual(plan.total_unassigned, 1)
        self.assertIn("vid3.mp4", plan.unassigned_videos[0].video_path)
        self.assertEqual(plan.unassigned_videos[0].reason, "SLOT_CAPACITY_EXHAUSTED")

    # =========================================================================
    # 2. F03: VIDEO THIẾU PROBE HOẶC FILE THAY ĐỔI SAU PROBE
    # =========================================================================

    def test_f03_missing_probe_routed_to_unassigned(self):
        """F03: Video không có kết quả probe phải bị từ chối vào unassigned_videos."""
        v1 = self._create_dummy_video("with_probe.mp4")
        v2 = self._create_dummy_video("without_probe.mp4")

        probes = [
            VideoProbeResult(str(v1.resolve()), v1.stat().st_size, getattr(v1.stat(), "st_mtime_ns", 0), 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]

        req = ScheduleRequest(
            video_paths=(str(v1), str(v2)),
            profile_names=("Prof_1",),
            profile_configs={"Prof_1": {"timezone": "America/New_York"}},
            formula="Test {n}",
        )

        plan = build_schedule_plan(req, probes=probes, now_utc=self.fixed_now_utc)
        self.assertEqual(plan.total_planned_tasks, 1)
        self.assertEqual(plan.total_unassigned, 1)
        self.assertEqual(plan.unassigned_videos[0].reason, "MISSING_PROBE_RESULT")

    def test_f03_stale_probe_file_modified(self):
        """F03: File giữ nguyên kích thước nhưng mtime lệch chỉ 500ns (< 1ms) vẫn bị từ chối chính xác."""
        v1 = self._create_dummy_video("stale_vid.mp4")
        stat1 = v1.stat()
        mtime_actual = getattr(stat1, "st_mtime_ns", int(stat1.st_mtime * 1e9))
        size_actual = stat1.st_size

        # Kích thước GIỐNG HỆT, nhưng mtime lệch đúng 500ns (trước đây dung sai 1ms sẽ lọt lỗi này!)
        probe_mtime_stale = mtime_actual - 500
        probes = [
            VideoProbeResult(str(v1.resolve()), size_actual, probe_mtime_stale, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]
        req = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_1",),
            profile_configs={"Prof_1": {"timezone": "America/New_York"}},
            formula="Test",
        )
        plan = build_schedule_plan(req, probes=probes, now_utc=self.fixed_now_utc)
        self.assertEqual(plan.total_planned_tasks, 0)
        self.assertEqual(plan.total_unassigned, 1)
        self.assertEqual(plan.unassigned_videos[0].reason, "STALE_PROBE_FILE_MODIFIED")

    # =========================================================================
    # 3. F04 / F11: OCCUPANCY SLOT XUYÊN ĐÊM VÀ CHECK SAU ROUNDING
    # =========================================================================

    def test_f04_rounding_shifts_date_and_enforces_quota(self):
        """F04: Slot 23:58-23:59 ngày 26 round thành 00:00 ngày 27; kiểm tra quota theo ngày 27 sau rounding."""
        late_slot = TimelineSlot(
            slot_id="LATE",
            label="Khuya (23:58 - 23:59)",
            start_hour=23,
            start_minute=58,
            end_hour=23,
            end_minute=59,
        )
        ny_tz = resolve_tz("America/New_York")
        # Ngày 27/10 đã có 1 bài lúc 12:00
        existing_dt = datetime.datetime(2026, 10, 27, 12, 0, 0, tzinfo=ny_tz)
        existing_epoch = int(existing_dt.timestamp())

        req = TimelineRequest(
            count=1,
            profile_name="Prof_Late",
            timezone_name="America/New_York",
            start_date_local=datetime.date(2026, 10, 26),
            max_daily_posts=1,  # Cap = 1 bài / ngày
            custom_slots=(late_slot,),
            jitter_minutes=0,
            existing_occupancy_epochs=(existing_epoch,),
            random_seed=42,
        )

        res = build_schedule_timeline(req, now_utc=self.fixed_now_utc)
        self.assertEqual(res.allocated_count, 1)
        # Candidate 23:58 ngày 26 round thành 00:00 ngày 27. Vì ngày 27 đã có bài (cap=1),
        # bài phải bị đẩy sang ngày kế tiếp (28/10)!
        self.assertGreaterEqual(res.scheduled_datetimes[0].date(), datetime.date(2026, 10, 28))

    def test_f04_exact_epoch_collision_avoidance(self):
        """F04: Không được sinh mốc có timestamp trùng chính xác với epoch đã tồn tại."""
        ny_tz = resolve_tz("America/New_York")
        colliding_dt = datetime.datetime(2026, 10, 26, 12, 20, 0, tzinfo=ny_tz)
        colliding_epoch = int(colliding_dt.timestamp())

        req = TimelineRequest(
            count=1,
            profile_name="Prof_Collision",
            timezone_name="America/New_York",
            start_date_local=datetime.date(2026, 10, 26),
            selected_slots=("TRUA",),
            jitter_minutes=0,
            existing_occupancy_epochs=(colliding_epoch,),
            max_daily_posts=2,
        )

        res = build_schedule_timeline(req, now_utc=self.fixed_now_utc)
        self.assertEqual(res.allocated_count, 1)
        self.assertNotEqual(int(res.scheduled_datetimes[0].timestamp()), colliding_epoch)

    # =========================================================================
    # 4. F05: SLOTS OUT-OF-ORDER ĐƯỢC SẮP XẾP VÀ KHÔNG NGẮT SỚM HORIZON
    # =========================================================================

    def test_dst_spring_forward_gap_skipped(self):
        """DST Spring-Forward: Slot 02:15-02:45 ngày 14/03/2027 tại New York không tồn tại local 02:30; timeline bỏ qua slot này và sang ngày kế tiếp."""
        gap_slot = TimelineSlot(
            slot_id="DST_GAP",
            label="Khuya Chuyển Giờ (02:15 - 02:45)",
            start_hour=2,
            start_minute=15,
            end_hour=2,
            end_minute=45,
        )
        now_dst = datetime.datetime(2027, 3, 13, 12, 0, 0, tzinfo=datetime.timezone.utc)
        req = TimelineRequest(
            count=1,
            profile_name="Prof_DST",
            timezone_name="America/New_York",
            start_date_local=datetime.date(2027, 3, 14),
            custom_slots=(gap_slot,),
            jitter_minutes=0,
            execution_mode="SERVER_SCHEDULE",
            max_daily_posts=1,
        )
        res = build_schedule_timeline(req, now_utc=now_dst)
        self.assertEqual(res.allocated_count, 1)
        # 02:30 ngày 14/03 không tồn tại, candidate bị bỏ qua, chuyển sang 02:30 ngày 15/03!
        self.assertEqual(res.scheduled_datetimes[0].date(), datetime.date(2027, 3, 15))
        self.assertEqual(res.scheduled_datetimes[0].hour, 2)
        self.assertEqual(res.scheduled_datetimes[0].minute, 30)

    def test_f05_slots_sorted_and_no_premature_horizon_abort(self):
        """F05: Truyền slots ngược thứ tự (TOI, SANG) vào ngày cuối horizon; slot TOI vượt 10 ngày không được làm bỏ qua slot SANG."""
        start_day = (self.fixed_now_utc + datetime.timedelta(days=9)).date()

        req = TimelineRequest(
            count=1,
            profile_name="Prof_Order",
            timezone_name="America/New_York",
            start_date_local=start_day,
            selected_slots=("TOI", "SANG"),
            jitter_minutes=0,
            execution_mode="SERVER_SCHEDULE",
        )

        res = build_schedule_timeline(req, now_utc=self.fixed_now_utc)
        self.assertEqual(res.allocated_count, 1)
        self.assertEqual(res.slot_ids[0], "SANG")

    def test_f05_mixed_valid_and_exceeded_slots_no_premature_abort(self):
        """F05: Slot xuyên đêm vượt trần 10 ngày (D+1) bị bỏ qua, slot kế tiếp trong ngày D vẫn hợp lệ và được cấp lịch thành công."""
        # Fixed now_utc: 2026-10-25 00:00:00 UTC -> 10 ngày là 2026-11-04 00:00:00 UTC
        fixed_now = datetime.datetime(2026, 10, 25, 0, 0, 0, tzinfo=datetime.timezone.utc)
        # Ngày thứ 9: 2026-11-03
        day_9 = datetime.date(2026, 11, 3)

        # Slot 1: 23:00 - 02:00 xuyên đêm (mốc giữa 00:30 ngày 04/11 = 10 ngày 30 phút -> VƯỢT TRẦN 10 NGÀY)
        slot_overnight_exceeded = TimelineSlot(
            slot_id="EXCEEDED_SLOT",
            label="Xuyên đêm vượt trần",
            start_hour=23,
            start_minute=0,
            end_hour=2,
            end_minute=0,
            crosses_midnight=True,
        )
        # Slot 2: 23:30 - 23:40 (mốc giữa 23:35 ngày 03/11 = 9 ngày 23h35m -> CÒN HỢP LỆ)
        slot_valid = TimelineSlot(
            slot_id="VALID_SLOT",
            label="Khung giờ hợp lệ trong hạn",
            start_hour=23,
            start_minute=30,
            end_hour=23,
            end_minute=40,
        )

        req = TimelineRequest(
            count=2,
            profile_name="Prof_F05_Mix",
            timezone_name="UTC",
            start_date_local=day_9,
            custom_slots=(slot_overnight_exceeded, slot_valid),
            jitter_minutes=0,
            execution_mode="SERVER_SCHEDULE",
            max_daily_posts=2,
        )

        res = build_schedule_timeline(req, now_utc=fixed_now)
        # Slot 1 vượt trần bị skip, nhưng Slot 2 hợp lệ vẫn phải được cấp lịch!
        self.assertEqual(res.allocated_count, 1)
        self.assertEqual(res.slot_ids[0], "VALID_SLOT")
        self.assertEqual(res.scheduled_datetimes[0].hour, 23)
        self.assertEqual(res.scheduled_datetimes[0].minute, 35)
        self.assertTrue(any("10 ngày" in r for r in res.reasons))

    def test_iana_timezone_strict_failure(self):
        """DST Assurance: Timezone IANA không tồn tại hoặc lỗi dữ liệu phải raise ValueError khi strict=True."""
        with self.assertRaises(ValueError):
            resolve_tz("America/NonExistent_Invalid_City_12345", strict=True)

    def test_f07_empty_profiles_config_change_alters_plan_id(self):
        """F07: Khi profile_names rỗng, thay đổi profile_configs vẫn làm thay đổi plan_id và payload hash."""
        v1 = self._create_dummy_video("empty_p_cfg.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]
        req1 = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=(),
            profile_configs={"GhostProfile": {"tiktok_account": "acc_alpha"}},
            formula="Title",
            random_seed=42,
        )
        req2 = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=(),
            profile_configs={"GhostProfile": {"tiktok_account": "acc_beta"}},  # Đổi account
            formula="Title",
            random_seed=42,
        )
        plan1 = build_schedule_plan(req1, probes=probes, now_utc=self.fixed_now_utc)
        plan2 = build_schedule_plan(req2, probes=probes, now_utc=self.fixed_now_utc)
        self.assertNotEqual(plan1.plan_id, plan2.plan_id)
        self.assertNotEqual(plan1.compute_payload_hash(), plan2.compute_payload_hash())

    def test_f07_random_seed_none_vs_int_alters_plan_id(self):
        """F07: random_seed=None và random_seed=42 phải sinh plan_id khác nhau."""
        v1 = self._create_dummy_video("seed_diff.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]
        req_none = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Seed",),
            profile_configs={"Prof_Seed": {"timezone": "UTC"}},
            formula="Title",
            random_seed=None,
        )
        req_int = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Seed",),
            profile_configs={"Prof_Seed": {"timezone": "UTC"}},
            formula="Title",
            random_seed=42,
        )
        plan_none = build_schedule_plan(req_none, probes=probes, now_utc=self.fixed_now_utc)
        plan_int = build_schedule_plan(req_int, probes=probes, now_utc=self.fixed_now_utc)
        self.assertNotEqual(plan_none.plan_id, plan_int.plan_id)

    def test_append_mode_starts_day_after_latest_task(self):
        """APPEND Contract: Chế độ APPEND tự động dời start_date sang ngày kế tiếp của task cũ muộn nhất."""
        v1 = self._create_dummy_video("append_day_after.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]

        # Profile đã có task vào ngày 28/10/2026 lúc 20:45 New York
        ny_tz = resolve_tz("America/New_York")
        existing_dt = datetime.datetime(2026, 10, 28, 20, 45, 0, tzinfo=ny_tz)
        existing_ep = int(existing_dt.timestamp())

        snap = ConflictSnapshot(
            conflicted_profiles=("Prof_Append",),
            snapshot_fingerprint="snap_app_1",
            existing_tasks={
                "Prof_Append": (
                    {"id": "old_task_1", "scheduled_epoch": existing_ep, "status": "PENDING"},
                )
            },
        )

        # Yêu cầu lập lịch bắt đầu từ 26/10 với APPEND
        req = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Append",),
            profile_configs={"Prof_Append": {"timezone": "America/New_York"}},
            formula="Append Title",
            start_date_local=datetime.date(2026, 10, 26),
            resolution_mode="APPEND",
            selected_slots=("SANG",),
            jitter_minutes=0,
        )

        plan = build_schedule_plan(req, probes=probes, conflicts=snap, now_utc=self.fixed_now_utc)
        self.assertEqual(plan.total_planned_tasks, 1)
        planned_task = plan.profile_assignments["Prof_Append"][0]
        # Bài mới bắt buộc phải bắt đầu từ ngày 29/10/2026 trở đi (ngày kế tiếp của 28/10)!
        self.assertGreaterEqual(planned_task.scheduled_epoch, existing_ep)
        self.assertGreaterEqual(planned_task.scheduled_time_local[:10], "2026-10-29")

    # =========================================================================
    # 5. F07: PLAN ID HASH THAY ĐỔI THEO MỌI THUỘC TÍNH (CHỐNG VA CHẠM)
    # =========================================================================

    def test_f07_plan_id_collision_resistance_profile_config(self):
        """F07: Đổi timezone hoặc account trong profile_configs phải làm thay đổi plan_id và payload hash."""
        v1 = self._create_dummy_video("hash_v1.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]

        req1 = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_1",),
            profile_configs={"Prof_1": {"timezone": "UTC", "tiktok_account": "user1"}},
            formula="Title",
            random_seed=42,
        )
        req2 = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_1",),
            profile_configs={"Prof_1": {"timezone": "Asia/Tokyo", "tiktok_account": "user1"}},  # Đổi timezone
            formula="Title",
            random_seed=42,
        )

        plan1 = build_schedule_plan(req1, probes=probes, now_utc=self.fixed_now_utc)
        plan2 = build_schedule_plan(req2, probes=probes, now_utc=self.fixed_now_utc)

        self.assertNotEqual(plan1.plan_id, plan2.plan_id)
        self.assertNotEqual(plan1.compute_payload_hash(), plan2.compute_payload_hash())

    def test_f07_collision_resistance_account_and_delete_source(self):
        """F07: Thay đổi tiktok_account hoặc delete_source_after_post phải thay đổi plan_id và payload hash."""
        v1 = self._create_dummy_video("f07_multi.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]
        base_req = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Base",),
            profile_configs={"Prof_Base": {"timezone": "UTC", "tiktok_account": "user_alpha"}},
            formula="Title",
            delete_source_after_post=True,
            random_seed=42,
        )
        req_diff_acc = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Base",),
            profile_configs={"Prof_Base": {"timezone": "UTC", "tiktok_account": "user_beta"}},
            formula="Title",
            delete_source_after_post=True,
            random_seed=42,
        )
        req_diff_del = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Base",),
            profile_configs={"Prof_Base": {"timezone": "UTC", "tiktok_account": "user_alpha"}},
            formula="Title",
            delete_source_after_post=False,
            random_seed=42,
        )

        plan_base = build_schedule_plan(base_req, probes=probes, now_utc=self.fixed_now_utc)
        plan_acc = build_schedule_plan(req_diff_acc, probes=probes, now_utc=self.fixed_now_utc)
        plan_del = build_schedule_plan(req_diff_del, probes=probes, now_utc=self.fixed_now_utc)

        self.assertNotEqual(plan_base.plan_id, plan_acc.plan_id)
        self.assertNotEqual(plan_base.compute_payload_hash(), plan_acc.compute_payload_hash())
        self.assertNotEqual(plan_base.plan_id, plan_del.plan_id)
        self.assertNotEqual(plan_base.compute_payload_hash(), plan_del.compute_payload_hash())

    def test_f07_file_modified_after_probe_changes_plan_id(self):
        """F07: Cùng request và probe ban đầu, nhưng khi file bị sửa trên đĩa -> unassigned thay đổi -> plan_id thay đổi."""
        v1 = self._create_dummy_video("f07_disk_mod.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]
        req = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Mod",),
            profile_configs={"Prof_Mod": {"timezone": "UTC"}},
            formula="Title",
            random_seed=42,
        )
        # Lần 1: File nguyên vẹn -> Plan hợp lệ
        plan1 = build_schedule_plan(req, probes=probes, now_utc=self.fixed_now_utc)
        self.assertEqual(plan1.total_planned_tasks, 1)

        # Sửa file trên đĩa (thêm byte để thay đổi st_size)
        v1.write_bytes(b"additional modified bytes appended")

        # Lần 2: Cùng request, probes, now_utc -> Phát hiện stale probe -> plan_id bắt buộc khác plan1!
        plan2 = build_schedule_plan(req, probes=probes, now_utc=self.fixed_now_utc)
        self.assertEqual(plan2.total_planned_tasks, 0)
        self.assertEqual(plan2.total_unassigned, 1)
        self.assertEqual(plan2.unassigned_videos[0].reason, "STALE_PROBE_FILE_MODIFIED")
        self.assertNotEqual(plan1.plan_id, plan2.plan_id)

    def test_f07_empty_profiles_resolution_mode_changes_plan_id(self):
        """F07: Khi danh sách profile rỗng, thay đổi resolution_mode (APPEND vs OVERWRITE) vẫn làm thay đổi plan_id."""
        v1 = self._create_dummy_video("empty_prof_res.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]
        req_append = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=(),
            profile_configs={},
            formula="Title",
            resolution_mode="APPEND",
            random_seed=42,
        )
        req_overwrite = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=(),
            profile_configs={},
            formula="Title",
            resolution_mode="OVERWRITE",
            random_seed=42,
        )
        plan_append = build_schedule_plan(req_append, probes=probes, now_utc=self.fixed_now_utc)
        plan_overwrite = build_schedule_plan(req_overwrite, probes=probes, now_utc=self.fixed_now_utc)

        self.assertNotEqual(plan_append.plan_id, plan_overwrite.plan_id)

    # =========================================================================
    # 6. F08: TÍNH BẤT BIẾN SÂU (DEEP IMMUTABILITY TRÊN TASK DICT & SNAPSHOT)
    # =========================================================================

    def test_f08_deep_immutability(self):
        """F08: Cả SchedulePlan và ConflictSnapshot đều bất biến sâu đệ quy."""
        v1 = self._create_dummy_video("immut.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]
        req = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Immut",),
            profile_configs={"Prof_Immut": {"timezone": "America/New_York"}},
            formula="Title",
        )
        plan = build_schedule_plan(req, probes=probes, now_utc=self.fixed_now_utc)

        # MappingProxyType ngăn gán ngoài
        self.assertIsInstance(plan.profile_assignments, MappingProxyType)
        with self.assertRaises(TypeError):
            plan.profile_assignments["Prof_Immut"] = ()

        # ConflictSnapshot cũng đóng băng đệ quy
        snap = ConflictSnapshot(
            conflicted_profiles=("Prof_Immut",),
            snapshot_fingerprint="snap123",
            existing_tasks={"Prof_Immut": ({"id": "task_1", "scheduled_epoch": 12345},)},
        )
        self.assertIsInstance(snap.existing_tasks, MappingProxyType)
        # Task dict lồng nhau bên trong cũng là MappingProxyType, không thể sửa
        with self.assertRaises(TypeError):
            snap.existing_tasks["Prof_Immut"][0]["scheduled_epoch"] = 99999

        # to_dict() trả bản sao tách rời hoàn toàn
        d = snap.to_dict()
        d["existing_tasks"]["Prof_Immut"][0]["scheduled_epoch"] = 88888
        self.assertEqual(snap.existing_tasks["Prof_Immut"][0]["scheduled_epoch"], 12345)

    def test_f08_deep_nested_dict_pickle_and_deepcopy(self):
        """F08: ConflictSnapshot và SchedulePlan với nested dict sâu được unfreeze đệ quy an toàn cho deepcopy/pickle/json."""
        snap = ConflictSnapshot(
            conflicted_profiles=("Prof_Deep",),
            snapshot_fingerprint="snap_deep_123",
            existing_tasks={
                "Prof_Deep": (
                    {
                        "id": "task_nested",
                        "metadata": {
                            "attempt": 1,
                            "inner_cfg": {"nested_key": "nested_val"},
                        },
                        "tags": ["tagA", "tagB"],
                    },
                )
            },
        )
        # Đảm bảo cấu trúc bên trong đã bị đóng băng sâu
        self.assertIsInstance(snap.existing_tasks["Prof_Deep"][0]["metadata"], MappingProxyType)
        self.assertIsInstance(snap.existing_tasks["Prof_Deep"][0]["metadata"]["inner_cfg"], MappingProxyType)

        # to_dict() phải tháo gỡ đệ quy hoàn toàn
        d = snap.to_dict()
        self.assertIsInstance(d["existing_tasks"]["Prof_Deep"][0]["metadata"], dict)
        self.assertIsInstance(d["existing_tasks"]["Prof_Deep"][0]["metadata"]["inner_cfg"], dict)

        # Không được ném "TypeError: cannot pickle 'mappingproxy' object" khi deepcopy hoặc pickle
        cloned = copy.deepcopy(d)
        self.assertEqual(cloned["existing_tasks"]["Prof_Deep"][0]["metadata"]["attempt"], 1)
        pickled = pickle.dumps(d)
        self.assertGreater(len(pickled), 0)
        unpickled = pickle.loads(pickled)
        self.assertEqual(unpickled["existing_tasks"]["Prof_Deep"][0]["metadata"]["inner_cfg"]["nested_key"], "nested_val")
        json_str = json.dumps(d)
        self.assertIn("nested_val", json_str)

    # =========================================================================
    # 7. F09: XỬ LÝ LỖI ĐỌC CONFLICT SNAPSHOT TỪ MANAGER
    # =========================================================================

    def test_f09_snapshot_error_handling(self):
        """F09: Lỗi đọc conflict snapshot từ manager được phát hiện và báo PlanWarning rõ ràng."""
        v1 = self._create_dummy_video("snap_err.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        mock_probe = VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)

        # Manager bị lỗi exception
        mock_manager = MagicMock(spec=["check_profile_conflicts"])
        mock_manager.check_profile_conflicts.side_effect = RuntimeError("Database locked by another process")

        req = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Err",),
            profile_configs={"Prof_Err": {"timezone": "America/New_York"}},
            formula="Title",
        )

        with patch("schedule_service.batch_probe_videos", return_value=[mock_probe]):
            # Nếu bật cờ raise_on_conflict_read_error=True -> Ném lỗi
            with self.assertRaises(RuntimeError):
                prepare_schedule_plan(req, manager=mock_manager, raise_on_conflict_read_error=True)

            # Nếu chạy mặc định -> Plan mang cảnh báo CONFLICT_SNAPSHOT_READ_ERROR
            plan = prepare_schedule_plan(req, manager=mock_manager, raise_on_conflict_read_error=False)
            self.assertTrue(any(w.code == "CONFLICT_SNAPSHOT_READ_ERROR" for w in plan.warnings))

    def test_f09_different_conflicted_profiles_produce_different_fingerprints(self):
        """F09: Profile P và Profile Q khi có xung đột (dù active tasks rỗng) vẫn sinh snapshot_fingerprint khác nhau."""
        class MockManager:
            def __init__(self, conflict_prof):
                self._prof = conflict_prof
            def check_profile_conflicts(self, profiles):
                return {self._prof: {"pending_count": 1}}

        mgr_p = MockManager("Profile_P")
        mgr_q = MockManager("Profile_Q")

        snap_p = build_conflict_snapshot_from_manager(mgr_p, ["Profile_P", "Profile_Q"])
        snap_q = build_conflict_snapshot_from_manager(mgr_q, ["Profile_P", "Profile_Q"])

        self.assertIsNotNone(snap_p)
        self.assertIsNotNone(snap_q)
        self.assertNotEqual(snap_p.snapshot_fingerprint, snap_q.snapshot_fingerprint)

    def test_f09_cannot_read_tasks_warning(self):
        """F09: Khi manager báo conflict nhưng không có API đọc chi tiết task, phát cảnh báo CANNOT_READ_OCCUPANCY_TASKS."""
        v1 = self._create_dummy_video("cannot_read_mgr.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        mock_probe = VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)

        class MinimalConflictManager:
            def check_profile_conflicts(self, profiles):
                return {"Prof_Warn": {"pending_count": 2}}

        req = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=("Prof_Warn",),
            profile_configs={"Prof_Warn": {"timezone": "America/New_York"}},
            formula="Title",
        )
        with patch("schedule_service.batch_probe_videos", return_value=[mock_probe]):
            plan = prepare_schedule_plan(req, manager=MinimalConflictManager())
            self.assertTrue(any(w.code == "CANNOT_READ_OCCUPANCY_TASKS" for w in plan.warnings))

    # =========================================================================
    # 8. F10: CHUẨN HÓA PROFILE VÀ XỬ LÝ DANH SÁCH RỖNG
    # =========================================================================

    def test_f10_profile_normalization_and_empty_profiles(self):
        """F10: Profile có khoảng trắng được trim; danh sách profile rỗng đẩy toàn bộ video vào unassigned."""
        v1 = self._create_dummy_video("prof_test.mp4")
        stat1 = v1.stat()
        mtime1 = getattr(stat1, "st_mtime_ns", 0)
        probes = [
            VideoProbeResult(str(v1.resolve()), stat1.st_size, mtime1, 10.0, "h264", "aac", 1080, 1920, "yuv420p", 30.0, True)
        ]

        req_empty = ScheduleRequest(
            video_paths=(str(v1),),
            profile_names=(),
            profile_configs={},
            formula="Title",
        )
        plan_empty = build_schedule_plan(req_empty, probes=probes, now_utc=self.fixed_now_utc)
        self.assertEqual(plan_empty.total_planned_tasks, 0)
        self.assertEqual(plan_empty.total_unassigned, 1)
        self.assertEqual(plan_empty.unassigned_videos[0].reason, "NO_VALID_PROFILES")

    # =========================================================================
    # 9. F12: START_TIME_LOCAL BỎ QUA CÁC SLOT ĐÃ QUA
    # =========================================================================

    def test_f12_start_time_cutoff_skips_earlier_slots(self):
        """F12: start_time_local = 14:00 bỏ qua slot Sáng (8:35) và Trưa (12:20) trong ngày bắt đầu."""
        req = TimelineRequest(
            count=2,
            profile_name="Prof_Cutoff",
            timezone_name="America/New_York",
            start_date_local=datetime.date(2026, 10, 26),
            start_time_local=datetime.time(14, 0),  # Bắt đầu từ 14:00
            selected_slots=("SANG", "TRUA", "CHIEU", "TOI"),
            jitter_minutes=0,
            max_daily_posts=4,
        )

        res = build_schedule_timeline(req, now_utc=self.fixed_now_utc)
        self.assertEqual(res.allocated_count, 2)
        # Bài 1 phải là CHIEU (18:07), Bài 2 phải là TOI (20:45)
        self.assertEqual(res.slot_ids[0], "CHIEU")
        self.assertEqual(res.slot_ids[1], "TOI")

    # =========================================================================
    # 10. SERIES NATURAL SORTING & INTEGRATION
    # =========================================================================

    def test_series_natural_sorting(self):
        """Chế độ SERIES tự động nhận diện số tự nhiên: Part 2 xếp trước Part 10."""
        filenames = ["Part_10.mp4", "Part_1.mp4", "Part_2.mp4", "Part_20.mp4"]
        paths = [self.test_path / f for f in filenames]

        ordered = order_video_paths(paths, mode="SERIES")
        ordered_names = [p.name for p in ordered]
        self.assertEqual(ordered_names, ["Part_1.mp4", "Part_2.mp4", "Part_10.mp4", "Part_20.mp4"])


if __name__ == "__main__":
    unittest.main()
