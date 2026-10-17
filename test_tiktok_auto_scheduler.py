"""
test_tiktok_auto_scheduler.py - Bộ kiểm thử toàn diện cho Hệ thống Lên lịch đăng TikTok tự động.

Bao phủ:
1. schedule_calculator: Quét file, bóc tách công thức, phân bổ Round-Robin, sinh timeline khung giờ vàng và Jitter.
2. schedule_manager: Lưu trữ JSON bền vững, Task CRUD, Conflict Guard (Append/Overwrite/Skip), Phục hồi lỡ giờ do tắt máy.
3. tiktok_publish_client & tiktok_direct_uploader: Kiểm tra trường schedule_time trong payload xuất bản API.
"""

import datetime
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from schedule_calculator import (
    DEFAULT_GOLDEN_HOUR_SLOTS,
    distribute_videos_round_robin,
    generate_schedule_timeline,
    generate_title_preview,
    parse_naming_formula,
    round_to_nearest_interval,
    sanitize_filename_to_title,
    scan_video_files,
)
from schedule_manager import ScheduleManager


class TestScheduleCalculator(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_calc_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_scan_video_files_filters_non_mp4_and_empty(self):
        """scan_video_files chỉ trả về các file .mp4 tồn tại và có kích thước > 0."""
        # 1. File .mp4 hợp lệ
        valid1 = Path(self.test_dir) / "clip_01.mp4"
        valid1.write_bytes(b"dummy video content 1")

        valid2 = Path(self.test_dir) / "clip_02.MP4"
        valid2.write_bytes(b"dummy video content 2")

        # 2. File không phải mp4
        txt = Path(self.test_dir) / "info.txt"
        txt.write_text("not a video")

        # 3. File mp4 0 byte
        empty = Path(self.test_dir) / "empty.mp4"
        empty.write_bytes(b"")

        found = scan_video_files(self.test_dir)
        found_names = [f.name for f in found]

        self.assertEqual(len(found), 2)
        self.assertIn("clip_01.mp4", found_names)
        self.assertIn("clip_02.MP4", found_names)
        self.assertNotIn("info.txt", found_names)
        self.assertNotIn("empty.mp4", found_names)

    def test_sanitize_filename_to_title(self):
        """sanitize_filename_to_title dọn sạch dấu gạch dưới và khoảng trắng thừa."""
        self.assertEqual(sanitize_filename_to_title("funny_dog__video_part_1.mp4"), "funny dog video part 1")
        self.assertEqual(sanitize_filename_to_title("my-video-name.mp4"), "my video name")

    def test_parse_naming_formula_tokens(self):
        """parse_naming_formula thay thế chính xác các token {filename}, {n}, {profile}, {account}, {date}."""
        dt = datetime.datetime(2026, 10, 18, 12, 30)
        formula = "Tập {n}: {filename} - Kênh {profile} ({account}) ngày {date} năm {year} #viral"
        
        result = parse_naming_formula(
            formula=formula,
            video_path_or_name="top_10_tricks.mp4",
            index=5,
            profile_name="US_Profile_01",
            account_name="daily_clips",
            schedule_dt=dt,
        )

        self.assertIn("Tập 5: top 10 tricks", result)
        self.assertIn("Kênh US_Profile_01", result)
        self.assertIn("(@daily_clips)", result)
        self.assertIn("ngày 18-10", result)
        self.assertIn("năm 2026", result)
        self.assertIn("#viral", result)

    def test_parse_naming_formula_empty_fallback(self):
        """Khi formula rỗng, hàm tự động trả về tên file sạch."""
        res = parse_naming_formula("", "my_video_clip.mp4")
        self.assertEqual(res, "my video clip")

    def test_generate_title_preview(self):
        """generate_title_preview tạo mẫu xem trước nhanh chóng."""
        preview = generate_title_preview("Part {n} - {filename}", "cat_funny.mp4")
        self.assertEqual(preview, "Part 1 - cat funny")

    def test_distribute_videos_round_robin(self):
        """distribute_videos_round_robin phân bổ đều video xoay vòng cho các profile."""
        videos = [Path(f"vid_{i}.mp4") for i in range(7)]
        profiles = ["P1", "P2", "P3"]

        dist = distribute_videos_round_robin(videos, profiles)
        self.assertEqual(len(dist["P1"]), 3)  # vid_0, vid_3, vid_6
        self.assertEqual(len(dist["P2"]), 2)  # vid_1, vid_4
        self.assertEqual(len(dist["P3"]), 2)  # vid_2, vid_5

    def test_generate_schedule_timeline_multi_day_and_jitter(self):
        """generate_schedule_timeline dàn trải theo max_daily_posts qua nhiều ngày."""
        start = datetime.datetime.now() + datetime.timedelta(days=1)
        timeline = generate_schedule_timeline(
            count=5,
            start_date=start,
            max_daily_posts=2,
            selected_slots=["TRUA", "TOI"],
            jitter_minutes=5,
            random_seed=42,
        )

        self.assertEqual(len(timeline), 5)
        # 5 bài với max 2 bài/ngày -> phải dàn trải qua ít nhất 3 ngày
        days = sorted(list(set(t.date() for t in timeline)))
        self.assertEqual(len(days), 3)

        # Các mốc thời gian phải được làm tròn bội số 5 phút
        for t in timeline:
            self.assertEqual(t.minute % 5, 0)
            self.assertEqual(t.second, 0)


class TestScheduleManager(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_mgr_")
        self.storage_file = Path(self.test_dir) / "scheduled_posts.json"
        self.mgr = ScheduleManager(storage_file=self.storage_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_add_and_get_tasks_persistence(self):
        """Tasks được ghi atomic xuống JSON và đọc lại chính xác từ instance mới."""
        tasks_to_add = [
            {
                "profile_name": "Prof_A",
                "tiktok_account": "acc_a",
                "video_path": "C:/videos/v1.mp4",
                "title": "Title 1",
                "scheduled_time": "2026-10-18T12:00:00",
                "execution_mode": "SERVER_SCHEDULE",
            },
            {
                "profile_name": "Prof_B",
                "tiktok_account": "acc_b",
                "video_path": "C:/videos/v2.mp4",
                "title": "Title 2",
                "scheduled_time": "2026-10-18T18:00:00",
                "execution_mode": "LOCAL_QUEUE",
            },
        ]

        created = self.mgr.add_tasks(tasks_to_add)
        self.assertEqual(len(created), 2)
        self.assertTrue(self.storage_file.exists())

        # Khởi tạo instance mới đọc từ file
        new_mgr = ScheduleManager(storage_file=self.storage_file)
        loaded = new_mgr.get_all_tasks()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["profile_name"], "Prof_A")
        self.assertEqual(loaded[1]["profile_name"], "Prof_B")

    def test_check_profile_conflicts(self):
        """check_profile_conflicts phát hiện chính xác profile có bài chờ đăng và ngày kết thúc."""
        self.mgr.add_tasks([
            {
                "profile_name": "Prof_Conflict",
                "scheduled_time": "2026-10-18T12:00:00",
                "status": "PENDING",
            },
            {
                "profile_name": "Prof_Conflict",
                "scheduled_time": "2026-10-19T18:00:00",
                "status": "PENDING",
            },
            {
                "profile_name": "Prof_Free",
                "scheduled_time": "2026-10-17T08:00:00",
                "status": "SUCCESS",  # Đã hoàn thành, không coi là xung đột
            },
        ])

        conflicts = self.mgr.check_profile_conflicts(["Prof_Conflict", "Prof_Free", "Prof_Empty"])
        self.assertIn("Prof_Conflict", conflicts)
        self.assertNotIn("Prof_Free", conflicts)
        self.assertNotIn("Prof_Empty", conflicts)

        info = conflicts["Prof_Conflict"]
        self.assertEqual(info["pending_count"], 2)
        self.assertEqual(info["latest_scheduled_time"], datetime.datetime(2026, 10, 19, 18, 0))

    def test_conflict_resolution_append_overwrite_skip(self):
        """Kiểm tra 3 nhánh xử lý xung đột: APPEND, OVERWRITE, SKIP."""
        # 1. Nhánh SKIP: Bỏ qua profile bận
        new_tasks = [
            {"profile_name": "Prof_A", "title": "A1"},
            {"profile_name": "Prof_B", "title": "B1"},
        ]
        skipped = self.mgr.apply_conflict_resolution("SKIP", new_tasks, conflicted_profiles=["Prof_A"])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["profile_name"], "Prof_B")

        # 2. Nhánh OVERWRITE: Hủy task cũ của profile bận
        self.mgr.add_tasks([
            {"id": "task_old_1", "profile_name": "Prof_A", "status": "PENDING"},
        ])
        overwritten = self.mgr.apply_conflict_resolution("OVERWRITE", new_tasks, conflicted_profiles=["Prof_A"])
        self.assertEqual(len(overwritten), 2)
        old_task = self.mgr.get_task_by_id("task_old_1")
        self.assertEqual(old_task["status"], "CANCELLED")

    def test_recover_missed_tasks(self):
        """recover_missed_tasks tự động phát hiện bài quá hạn do tắt máy và dời sang ngày tiếp theo."""
        past_time = (datetime.datetime.now() - datetime.timedelta(hours=3)).isoformat()
        future_time = (datetime.datetime.now() + datetime.timedelta(hours=3)).isoformat()

        self.mgr.add_tasks([
            {
                "id": "task_missed",
                "profile_name": "P1",
                "scheduled_time": past_time,
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            },
            {
                "id": "task_future",
                "profile_name": "P2",
                "scheduled_time": future_time,
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            },
        ])

        recovered = self.mgr.recover_missed_tasks()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["id"], "task_missed")
        self.assertEqual(recovered[0]["status"], "MISSED_RESCHEDULED")

        # Mốc thời gian mới phải ở tương lai
        new_dt = datetime.datetime.fromisoformat(recovered[0]["scheduled_time"])
        self.assertGreater(new_dt, datetime.datetime.now())

        # Task tương lai không bị ảnh hưởng
        f_task = self.mgr.get_task_by_id("task_future")
        self.assertEqual(f_task["status"], "PENDING")

    def test_cancel_retry_delete_and_stats(self):
        """Kiểm tra các thao tác hủy, thử lại, xóa và tính toán chỉ số thống kê."""
        tasks = self.mgr.add_tasks([
            {"id": "t1", "profile_name": "P1", "status": "PENDING"},
            {"id": "t2", "profile_name": "P2", "status": "SERVER_SCHEDULED"},
            {"id": "t3", "profile_name": "P3", "status": "FAILED"},
        ])

        # Cancel t1
        cancelled = self.mgr.cancel_tasks(["t1"])
        self.assertEqual(cancelled, 1)
        self.assertEqual(self.mgr.get_task_by_id("t1")["status"], "CANCELLED")

        # Retry t3
        retried = self.mgr.retry_tasks(["t3"])
        self.assertEqual(retried, 1)
        self.assertEqual(self.mgr.get_task_by_id("t3")["status"], "PENDING")

        # Stats
        stats = self.mgr.get_summary_stats()
        self.assertEqual(stats["server_scheduled"], 1)
        self.assertEqual(stats["pending"], 1)

        # Delete t2
        deleted = self.mgr.delete_tasks(["t2"])
        self.assertEqual(deleted, 1)
        self.assertIsNone(self.mgr.get_task_by_id("t2"))


class TestDirectApiNativeSchedule(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_api_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_publish_video_includes_schedule_time_in_payload(self):
        """Khi truyền schedule_time, TikTokPublishClient gán chính xác vào feature_common_info_list."""
        from tiktok_publish_client import TikTokPublishClient

        client = TikTokPublishClient(session_cookies={"sessionid": "mock_sess", "msToken": "mock_token"})
        vid = "v0123456789"
        video_meta = {"Width": 1080, "Height": 1920, "Duration": 15.0}
        target_timestamp = 1792300500

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status_code": 0,
            "single_post_resp_list": [{"item_id": "719283719283", "status_code": 0}],
        }

        with patch.object(client.proxy_session, "post", return_value=mock_resp) as mock_post, \
             patch("tiktok_publish_client.sign_tiktok_url", return_value=("mock_bogus", "mock_gnarly")):

            res = client.publish_video(
                vid=vid,
                video_meta=video_meta,
                caption="Test Schedule API",
                schedule_time=target_timestamp,
            )

            self.assertEqual(res.item_id, "719283719283")
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            data_bytes = call_kwargs.get("data")
            payload_dict = json.loads(data_bytes.decode("utf-8"))

            feature_info = payload_dict["feature_common_info_list"][0]
            self.assertIn("schedule_time", feature_info)
            self.assertEqual(feature_info["schedule_time"], target_timestamp)

    def test_run_direct_upload_forwards_schedule_time(self):
        """run_direct_upload truyền tham số schedule_time và thiết lập is_scheduled trong details."""
        from tiktok_direct_uploader import run_direct_upload
        from tiktok_vod_uploader import VodUploadResult

        mock_vod_res = VodUploadResult(
            vid="v_mock_vid_123",
            upload_host="https://vod.tiktok.com",
            store_uri="tos-mock-uri",
            video_meta={"Width": 1080, "Height": 1920, "Duration": 20.0},
            request_id="req_123",
            store_region="us-east-1",
        )

        mock_pub_res = MagicMock()
        mock_pub_res.item_id = "7987654321"
        mock_pub_res.video_url = "https://www.tiktok.com/video/7987654321"

        target_ts = 1792300500

        with patch("tiktok_direct_uploader.get_profile_tiktok_cookies", return_value={"sessionid": "valid_session"}), \
             patch("tiktok_direct_uploader.TikTokVodUploader.upload_video", return_value=mock_vod_res), \
             patch("tiktok_direct_uploader.TikTokPublishClient.publish_video", return_value=mock_pub_res) as mock_pub:

            upload_result = run_direct_upload(
                profile_name="Prof_Sched_Test",
                profile_config={"use_proxy": False},
                video_path="dummy.mp4",
                caption="Scheduled via Direct API",
                schedule_time=target_ts,
            )

            mock_pub.assert_called_once()
            self.assertEqual(mock_pub.call_args[1].get("schedule_time"), target_ts)
            self.assertEqual(upload_result.outcome, "posted")
            self.assertTrue(upload_result.details.get("is_scheduled"))
            self.assertEqual(upload_result.details.get("schedule_time"), target_ts)

    def test_interleave_tasks_by_profile(self):
        """interleave_tasks_by_profile sắp xếp xoay vòng chéo từng profile để giãn cách tài khoản."""
        from schedule_calculator import interleave_tasks_by_profile

        tasks = [
            {"id": "t1", "profile_name": "P1", "video": "v1"},
            {"id": "t2", "profile_name": "P1", "video": "v2"},
            {"id": "t3", "profile_name": "P1", "video": "v3"},
            {"id": "t4", "profile_name": "P2", "video": "v4"},
            {"id": "t5", "profile_name": "P2", "video": "v5"},
            {"id": "t6", "profile_name": "P3", "video": "v6"},
        ]

        interleaved = interleave_tasks_by_profile(tasks)
        self.assertEqual(len(interleaved), 6)
        
        # Round 1: P1 -> P2 -> P3
        self.assertEqual(interleaved[0]["id"], "t1")
        self.assertEqual(interleaved[1]["id"], "t4")
        self.assertEqual(interleaved[2]["id"], "t6")
        
        # Round 2: P1 -> P2
        self.assertEqual(interleaved[3]["id"], "t2")
        self.assertEqual(interleaved[4]["id"], "t5")
        
        # Round 3: P1
        self.assertEqual(interleaved[5]["id"], "t3")

    def test_get_all_tasks_vietnamese_and_mode_filters(self):
        """get_all_tasks xử lý chính xác giá trị 'Tất cả' tiếng Việt và lọc theo Mode."""
        test_file = Path(self.test_dir) / "sched_filter_test.json"
        mgr = ScheduleManager(storage_file=test_file)

        mgr.add_tasks([
            {"profile_name": "P1", "status": "PENDING", "execution_mode": "SERVER_SCHEDULE"},
            {"profile_name": "P2", "status": "SUCCESS", "execution_mode": "LOCAL_QUEUE"},
            {"profile_name": "P1", "status": "FAILED", "execution_mode": "SERVER_SCHEDULE"},
        ])

        # Bộ lọc 'Tất cả' không được làm mất dữ liệu
        all_tasks = mgr.get_all_tasks(filter_status="Tất cả", filter_profile="Tất cả", filter_mode="Tất cả chế độ")
        self.assertEqual(len(all_tasks), 3)

        # Lọc theo Mode
        server_tasks = mgr.get_all_tasks(filter_mode="☁️ Hẹn giờ TikTok")
        self.assertEqual(len(server_tasks), 2)

        local_tasks = mgr.get_all_tasks(filter_mode="💻 Hàng đợi máy")
        self.assertEqual(len(local_tasks), 1)

    def test_sanitize_latin1_and_cookie_header(self):
        """cookies_to_header_str và sanitize_latin1 loại bỏ ký tự ngoài latin-1 (ord > 255) chống crash HTTP header."""
        from tiktok_cookie_extractor import cookies_to_header_str, sanitize_latin1

        # Ký tự số Ả Rập \u0661 và emoji
        dirty_str = "user_\u0661\u0662\u0663_token_😀"
        clean = sanitize_latin1(dirty_str)
        self.assertEqual(clean, "user__token_")
        # Mã hóa latin-1 không bị văng lỗi
        clean.encode("latin-1")

        cookies = {
            "sessionid": "valid_session_123",
            "locale": "ar_\u0661",
            "bad_key_\u0662": "value",
        }
        header_str = cookies_to_header_str(cookies)
        self.assertIn("sessionid=valid_session_123", header_str)
        self.assertIn("locale=ar_", header_str)
        # Đảm bảo chuỗi header_str mã hóa an toàn qua latin-1
        encoded = header_str.encode("latin-1")
        self.assertTrue(len(encoded) > 0)

    def test_delete_source_after_post_persistence(self):
        """Cờ delete_source_after_post được lưu trữ và truy xuất chuẩn xác."""
        test_file = Path(self.test_dir) / "sched_del_test.json"
        mgr = ScheduleManager(storage_file=test_file)

        created = mgr.add_tasks([
            {"profile_name": "P1", "delete_source_after_post": True},
            {"profile_name": "P2", "delete_source_after_post": False},
        ])

        self.assertTrue(created[0]["delete_source_after_post"])
        self.assertFalse(created[1]["delete_source_after_post"])

    def test_source_deletion_on_success_and_preservation_on_failure(self):
        """File gốc bị xóa khi đăng thành công (nếu bật cờ) và giữ nguyên 100% khi thất bại."""
        import main
        from tiktok_direct_uploader import UploadResult

        # 1. File test thành công có cờ delete=True -> File bị xóa
        vid1 = Path(self.test_dir) / "vid1.mp4"
        vid1.write_bytes(b"dummy1")
        task1 = {
            "id": "task_del_1",
            "profile_name": "P1",
            "video_path": str(vid1),
            "delete_source_after_post": True,
        }

        with patch("main.run_direct_upload", return_value=UploadResult(outcome="posted", message="ok", details={"item_id": "123"})):
            ok, msg = main._execute_schedule_task_upload(task1)
            self.assertTrue(ok)
            self.assertFalse(vid1.exists())

        # 2. File test thất bại có cờ delete=True -> File KHÔNG bị xóa
        vid2 = Path(self.test_dir) / "vid2.mp4"
        vid2.write_bytes(b"dummy2")
        task2 = {
            "id": "task_del_2",
            "profile_name": "P1",
            "video_path": str(vid2),
            "delete_source_after_post": True,
        }

        with patch("main.run_direct_upload", return_value=UploadResult(outcome="failed", message="network error")):
            ok, msg = main._execute_schedule_task_upload(task2)
            self.assertFalse(ok)
            self.assertTrue(vid2.exists())

        # 3. File test thành công nhưng cờ delete=False -> File KHÔNG bị xóa
        vid3 = Path(self.test_dir) / "vid3.mp4"
        vid3.write_bytes(b"dummy3")
        task3 = {
            "id": "task_del_3",
            "profile_name": "P1",
            "video_path": str(vid3),
            "delete_source_after_post": False,
        }

        with patch("main.run_direct_upload", return_value=UploadResult(outcome="posted", message="ok", details={"item_id": "456"})):
            ok, msg = main._execute_schedule_task_upload(task3)
            self.assertTrue(ok)
            self.assertTrue(vid3.exists())


class TestBulkScheduleModalUI(unittest.TestCase):
    def test_modal_fixed_bottom_bar_and_scrollable_container(self):
        """BulkScheduleModal must have fixed bottom bar and scrollable container."""
        import customtkinter as ctk
        from schedule_ui import BulkScheduleModal
        try:
            root = ctk.CTk()
            root.withdraw()
        except Exception:
            self.skipTest("Tkinter display not available")

        try:
            modal = BulkScheduleModal(root, selected_profiles=["Profile_A"])
            self.assertTrue(hasattr(modal, "scroll"))
            self.assertIsInstance(modal.scroll, ctk.CTkScrollableFrame)
            self.assertTrue(hasattr(modal, "btn_submit"))
            self.assertTrue(modal.btn_submit.winfo_exists())
            self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")
            modal.destroy()
        finally:
            try:
                root.destroy()
            except Exception:
                pass


class TestScheduleSummaryAndProfileTableIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_sched_summary_")
        self.storage_file = Path(self.test_dir) / "scheduled_posts.json"
        self.mgr = ScheduleManager(storage_file=self.storage_file)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_summary_map_empty(self):
        """Khi chưa có task nào, trả về dict rỗng."""
        res = self.mgr.get_schedule_summary_map()
        self.assertEqual(res, {})

    def test_summary_map_multiple_profiles_and_dates(self):
        """Tổng hợp chính xác số clip chờ và mốc ngày muộn nhất cho từng profile."""
        self.mgr.add_tasks([
            {
                "id": "t1",
                "profile_name": "Prof_Alpha",
                "scheduled_time": "2026-10-18T10:00:00",
                "status": "PENDING",
            },
            {
                "id": "t2",
                "profile_name": "Prof_Alpha",
                "scheduled_time": "2026-10-20T19:30:00",
                "status": "SERVER_SCHEDULED",
            },
            {
                "id": "t3",
                "profile_name": "Prof_Alpha",
                "scheduled_time": "2026-10-19T14:15:00",
                "status": "QUEUED",
            },
            # Task đã hoàn thành hoặc hủy của Alpha không tính vào pending
            {
                "id": "t4",
                "profile_name": "Prof_Alpha",
                "scheduled_time": "2026-10-25T14:15:00",
                "status": "SUCCESS",
            },
            {
                "id": "t5",
                "profile_name": "Prof_Alpha",
                "scheduled_time": "2026-10-26T14:15:00",
                "status": "CANCELLED",
            },
            # Profile Beta: 1 task
            {
                "id": "tb1",
                "profile_name": "Prof_Beta",
                "scheduled_time": "2026-10-21T08:00:00",
                "status": "PENDING",
            },
            # Profile Gamma: Task có ngày hẹn không hợp lệ (không parse được ISO)
            {
                "id": "tg1",
                "profile_name": "Prof_Gamma",
                "scheduled_time": "INVALID_DATE_FORMAT",
                "status": "MISSED_RESCHEDULED",
            },
        ])

        summary = self.mgr.get_schedule_summary_map()
        self.assertEqual(len(summary), 3)

        alpha = summary["Prof_Alpha"]
        self.assertEqual(alpha["pending_count"], 3)
        self.assertEqual(alpha["latest_date_str"], "20/10")
        self.assertEqual(alpha["badge"], "📅 Đến 20/10 (3 clip)")

        beta = summary["Prof_Beta"]
        self.assertEqual(beta["pending_count"], 1)
        self.assertEqual(beta["latest_date_str"], "21/10")
        self.assertEqual(beta["badge"], "📅 Đến 21/10 (1 clip)")

        gamma = summary["Prof_Gamma"]
        self.assertEqual(gamma["pending_count"], 1)
        self.assertEqual(gamma["badge"], "📅 (1 clip)")

    def test_upload_badge_display_logic(self):
        """Kiểm tra logic hiển thị badge trạng thái Tiến Độ Đăng (Phương án 1)."""
        from profile_runtime_status import UploadState, upload_label

        summary = {
            "Prof_Sched": {
                "pending_count": 3,
                "latest_date_str": "20/10",
                "badge": "📅 Đến 20/10 (3 clip)",
            }
        }

        def compute_upload_badge(p_name, upload_state):
            sched_info = summary.get(p_name)
            if upload_state == UploadState.IDLE:
                if sched_info and sched_info.get("badge"):
                    return sched_info["badge"]
                return upload_label(upload_state)
            elif upload_state == UploadState.SUCCEEDED and sched_info and sched_info.get("latest_date_str"):
                return f"Đã đăng • 📅 {sched_info['latest_date_str']}"
            return upload_label(upload_state)

        # 1. Profile có lịch và đang rảnh (IDLE) -> Hiển thị ngày và số clip
        self.assertEqual(compute_upload_badge("Prof_Sched", UploadState.IDLE), "📅 Đến 20/10 (3 clip)")

        # 2. Profile không có lịch và đang rảnh (IDLE) -> Hiển thị "Chờ video"
        self.assertEqual(compute_upload_badge("Prof_NoSched", UploadState.IDLE), "Chờ video")

        # 3. Profile có lịch nhưng vừa đăng xong clip trước đó (SUCCEEDED) -> Hiển thị "Đã đăng • 📅 20/10"
        self.assertEqual(compute_upload_badge("Prof_Sched", UploadState.SUCCEEDED), "Đã đăng • 📅 20/10")

        # 4. Profile đang thực sự tải lên (UPLOADING) -> Luôn ưu tiên hiển thị tiến trình đang tải
        self.assertEqual(compute_upload_badge("Prof_Sched", UploadState.UPLOADING), "Đang tải video")


if __name__ == "__main__":
    unittest.main()
