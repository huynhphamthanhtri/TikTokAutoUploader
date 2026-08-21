"""
test_ui_statistics.py - Unit tests cho Tab Thống Kê (ui_statistics.py).

Phân tách rõ ràng:
1. Pure Aggregation Engine Tests (CI-safe, không cần display): Kiểm tra tính đúng đắn 100%
   của việc tổng hợp số liệu (tải OK/Lỗi/Bỏ qua, đăng OK/Lỗi, hôm nay/hôm qua, tỷ lệ %,
   lọc theo mốc thời gian, dự án, từ khóa tìm kiếm, zero-division safety, xuất CSV UTF-8-sig).
2. Desktop Interactive Tests: Kiểm tra khởi tạo widget CTk, Treeviews, sắp xếp cột và chọn tài khoản.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import customtkinter as ctk

from ui_statistics import (
    ALL_OPTION,
    ACCOUNT_COLUMNS,
    ERROR_COLUMNS,
    StatisticsWorkspaceView,
    aggregate_statistics,
    build_statistics_workspace,
    export_statistics_to_csv,
    filter_by_timeframe,
    get_account_recent_errors,
    parse_log_datetime,
)


class TestStatisticsPureEngine(unittest.TestCase):
    """Kiểm tra tầng Pure Data Aggregation Engine (100% CI Testable)."""

    def setUp(self):
        self.ref_now = datetime(2026, 8, 21, 14, 30, 0)
        self.sample_logs = [
            # Event 1: Tải OK cho Profile A (Hôm nay)
            {
                "time": "2026-08-21 10:15:00",
                "type": "youtube_download",
                "video_name": "Video_01",
                "video_url": "https://youtu.be/vid1",
                "profile": "Profile_A",
                "status": "success",
                "detail": "channel=UC123",
                "file_path": "C:/videos/vid1.mp4",
            },
            # Event 2: Tải Lỗi cho Profile A (Hôm nay)
            {
                "time": "2026-08-21 11:20:00",
                "type": "youtube_download",
                "video_name": "Video_02",
                "video_url": "https://youtu.be/vid2",
                "profile": "Profile_A",
                "status": "fail",
                "detail": "Sign in to confirm you are not a bot",
                "file_path": "",
            },
            # Event 3: Đăng OK cho Profile A (Hôm nay)
            {
                "time": "2026-08-21 12:00:00",
                "type": "tiktok_upload",
                "video_name": "Video_01",
                "video_url": "https://youtu.be/vid1",
                "profile": "Profile_A",
                "status": "success",
                "detail": "uploaded",
                "file_path": "C:/videos/vid1.mp4",
            },
            # Event 4: Đăng Lỗi cho Profile A (Hôm nay)
            {
                "time": "2026-08-21 13:00:00",
                "type": "tiktok_upload",
                "video_name": "Video_03",
                "video_url": "https://youtu.be/vid3",
                "profile": "Profile_A",
                "status": "fail",
                "detail": "Account blocked: verification required",
                "file_path": "C:/videos/vid3.mp4",
            },
            # Event 5: Đăng OK cho Profile B (Hôm qua)
            {
                "time": "2026-08-20 15:45:00",
                "type": "tiktok_upload",
                "video_name": "Video_04",
                "video_url": "https://youtu.be/vid4",
                "profile": "Profile_B",
                "status": "success",
                "detail": "uploaded",
                "file_path": "C:/videos/vid4.mp4",
            },
            # Event 6: Tải OK cho Profile B (3 ngày trước)
            {
                "time": "2026-08-18 09:00:00",
                "type": "youtube_download",
                "video_name": "Video_05",
                "video_url": "https://youtu.be/vid5",
                "profile": "Profile_B",
                "status": "success",
                "detail": "channel=UC456",
                "file_path": "C:/videos/vid5.mp4",
            },
            # Event 7: Tải Skipped cho Profile C (15 ngày trước)
            {
                "time": "2026-08-06 08:30:00",
                "type": "youtube_download",
                "video_name": "Video_06",
                "video_url": "https://youtu.be/vid6",
                "profile": "Profile_C",
                "status": "skipped",
                "detail": "already downloaded",
                "file_path": "C:/videos/vid6.mp4",
            },
        ]

        self.sample_profiles = {
            "Profile_A": {
                "uploads_today_count": 1,
                "uploads_yesterday_count": 0,
                "config": {"folder_path": "C:/videos/a"},
            },
            "Profile_B": {
                "uploads_today_count": 0,
                "uploads_yesterday_count": 1,
                "config": {"folder_path": "C:/videos/b"},
            },
            "Profile_C": {
                "uploads_today_count": 0,
                "uploads_yesterday_count": 0,
                "config": {"folder_path": "C:/videos/c"},
            },
            "Profile_Idle": {
                "uploads_today_count": 0,
                "uploads_yesterday_count": 0,
                "config": {"folder_path": "C:/videos/idle"},
            },
        }

        self.sample_projects = {
            "Dự Án Alpha": ["Profile_A", "Profile_B"],
            "Dự Án Beta": ["Profile_C", "Profile_Idle"],
        }

    def test_parse_log_datetime(self):
        self.assertEqual(parse_log_datetime("2026-08-21 14:30:00"), datetime(2026, 8, 21, 14, 30, 0))
        self.assertEqual(parse_log_datetime("2026-08-21 14:30"), datetime(2026, 8, 21, 14, 30, 0))
        self.assertEqual(parse_log_datetime("2026-08-21"), datetime(2026, 8, 21, 0, 0, 0))
        self.assertIsNone(parse_log_datetime(""))
        self.assertIsNone(parse_log_datetime(None))
        self.assertIsNone(parse_log_datetime("invalid_string"))

    def test_filter_by_timeframe(self):
        # All
        all_res = filter_by_timeframe(self.sample_logs, timeframe="all", now_dt=self.ref_now)
        self.assertEqual(len(all_res), 7)

        # Today (Events 1-4)
        today_res = filter_by_timeframe(self.sample_logs, timeframe="today", now_dt=self.ref_now)
        self.assertEqual(len(today_res), 4)
        for r in today_res:
            self.assertTrue(r["time"].startswith("2026-08-21"))

        # Yesterday (Event 5)
        yd_res = filter_by_timeframe(self.sample_logs, timeframe="yesterday", now_dt=self.ref_now)
        self.assertEqual(len(yd_res), 1)
        self.assertEqual(yd_res[0]["video_name"], "Video_04")

        # 7 Days (Events 1-6)
        seven_res = filter_by_timeframe(self.sample_logs, timeframe="7days", now_dt=self.ref_now)
        self.assertEqual(len(seven_res), 6)

        # 30 Days (Events 1-7)
        thirty_res = filter_by_timeframe(self.sample_logs, timeframe="30days", now_dt=self.ref_now)
        self.assertEqual(len(thirty_res), 7)

    def test_get_account_recent_errors(self):
        errors_a = get_account_recent_errors(self.sample_logs, "Profile_A")
        self.assertEqual(len(errors_a), 2)
        # Sắp xếp mới nhất lên đầu (Event 4 trước, Event 2 sau)
        self.assertEqual(errors_a[0]["video_name"], "Video_03")
        self.assertEqual(errors_a[1]["video_name"], "Video_02")

        errors_b = get_account_recent_errors(self.sample_logs, "Profile_B")
        self.assertEqual(len(errors_b), 0)

        errors_empty = get_account_recent_errors(self.sample_logs, "")
        self.assertEqual(len(errors_empty), 0)

    def test_aggregate_statistics_accuracy(self):
        res = aggregate_statistics(
            activity_rows=self.sample_logs,
            profiles_data=self.sample_profiles,
            project_mapping=self.sample_projects,
            project_filter=ALL_OPTION,
            timeframe="all",
            now_dt=self.ref_now,
        )

        summary = res["summary"]
        self.assertEqual(summary["download_success"], 2)  # Event 1 & 6
        self.assertEqual(summary["download_fail"], 1)     # Event 2
        self.assertEqual(summary["download_skipped"], 1)  # Event 7
        self.assertEqual(summary["upload_success"], 2)    # Event 3 & 5
        self.assertEqual(summary["upload_fail"], 1)       # Event 4
        self.assertEqual(summary["uploads_today"], 1)
        self.assertEqual(summary["uploads_yesterday"], 1)
        # Total uploads = 3 (2 success, 1 fail) -> rate = 66.7%
        self.assertEqual(summary["overall_success_rate"], 66.7)
        self.assertEqual(summary["total_accounts"], 4)
        self.assertEqual(summary["active_accounts"], 3)   # A, B, C active; Idle idle

        # Kiểm tra chi tiết tài khoản Profile_A
        accounts_map = {a["name"]: a for a in res["accounts"]}
        acc_a = accounts_map["Profile_A"]
        self.assertEqual(acc_a["dl_ok"], 1)
        self.assertEqual(acc_a["dl_fail"], 1)
        self.assertEqual(acc_a["up_ok"], 1)
        self.assertEqual(acc_a["up_fail"], 1)
        self.assertEqual(acc_a["rate"], 50.0)
        self.assertEqual(acc_a["rate_str"], "50.0%")
        self.assertEqual(acc_a["project"], "Dự Án Alpha")
        self.assertEqual(acc_a["status_tag"], "warn")

        # Kiểm tra chi tiết tài khoản Profile_B
        acc_b = accounts_map["Profile_B"]
        self.assertEqual(acc_b["dl_ok"], 1)
        self.assertEqual(acc_b["dl_fail"], 0)
        self.assertEqual(acc_b["up_ok"], 1)
        self.assertEqual(acc_b["up_fail"], 0)
        self.assertEqual(acc_b["rate"], 100.0)
        self.assertEqual(acc_b["rate_str"], "100.0%")
        self.assertEqual(acc_b["status_tag"], "good")

    def test_aggregate_statistics_zero_division_guard(self):
        """Đảm bảo tài khoản chưa đăng video không bị lỗi ZeroDivisionError."""
        res = aggregate_statistics(
            activity_rows=[],
            profiles_data={"Profile_New": {"uploads_today_count": 0, "uploads_yesterday_count": 0}},
            project_mapping={},
            project_filter=ALL_OPTION,
            timeframe="all",
            now_dt=self.ref_now,
        )
        summary = res["summary"]
        self.assertEqual(summary["overall_success_rate"], 0.0)
        self.assertEqual(summary["overall_success_rate_str"], "-")

        accounts = res["accounts"]
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["rate"], 0.0)
        self.assertEqual(accounts[0]["rate_str"], "-")
        self.assertEqual(accounts[0]["status_tag"], "idle")

    def test_aggregate_statistics_project_filter(self):
        res_alpha = aggregate_statistics(
            activity_rows=self.sample_logs,
            profiles_data=self.sample_profiles,
            project_mapping=self.sample_projects,
            project_filter="Dự Án Alpha",
            timeframe="all",
            now_dt=self.ref_now,
        )
        acc_names = [a["name"] for a in res_alpha["accounts"]]
        self.assertEqual(sorted(acc_names), ["Profile_A", "Profile_B"])

        res_beta = aggregate_statistics(
            activity_rows=self.sample_logs,
            profiles_data=self.sample_profiles,
            project_mapping=self.sample_projects,
            project_filter="Dự Án Beta",
            timeframe="all",
            now_dt=self.ref_now,
        )
        acc_beta_names = [a["name"] for a in res_beta["accounts"]]
        self.assertEqual(sorted(acc_beta_names), ["Profile_C", "Profile_Idle"])

    def test_aggregate_statistics_search_query(self):
        res_search = aggregate_statistics(
            activity_rows=self.sample_logs,
            profiles_data=self.sample_profiles,
            project_mapping=self.sample_projects,
            project_filter=ALL_OPTION,
            search_query="profile_a",
            now_dt=self.ref_now,
        )
        self.assertEqual(len(res_search["accounts"]), 1)
        self.assertEqual(res_search["accounts"][0]["name"], "Profile_A")

    def test_export_statistics_to_csv(self):
        res = aggregate_statistics(
            activity_rows=self.sample_logs,
            profiles_data=self.sample_profiles,
            project_mapping=self.sample_projects,
            now_dt=self.ref_now,
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
            tmp_path = tf.name

        try:
            ok = export_statistics_to_csv(res, tmp_path)
            self.assertTrue(ok)
            self.assertTrue(os.path.exists(tmp_path))

            with open(tmp_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
                self.assertIn("Tên Hồ Sơ", content)
                self.assertIn("Profile_A", content)
                self.assertIn("Dự Án Alpha", content)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


# ==============================================================================
# 2. DESKTOP INTERACTIVE & VIEW CONTRACT TESTS
# ==============================================================================

@unittest.skipIf(os.environ.get("CI", "").lower() == "true", "Interactive Tk rendering is verified on desktop runner")
class TestStatisticsDesktopInteractive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = ctk.CTk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

    def test_build_statistics_workspace_renders_and_initializes_cards(self):
        frame = ctk.CTkFrame(self.root)
        state = {}
        handlers = {
            "get_activity_logs": lambda **_k: [
                {"time": "2026-08-21 10:00:00", "type": "tiktok_upload", "profile": "P1", "status": "success", "video_name": "V1", "detail": "ok", "video_url": "", "file_path": ""}
            ],
            "get_profiles_data": lambda: ({"P1": {"uploads_today_count": 1, "uploads_yesterday_count": 0}}, {"Default": ["P1"]}),
            "get_mtime": lambda: 123456.0,
            "clear_stats": lambda: (True, "OK"),
        }
        view = build_statistics_workspace(frame, state, handlers)
        self.assertIsNotNone(view)
        self.assertIsInstance(view, StatisticsWorkspaceView)

        # Check KPI values
        self.assertIn("1 OK", view.kpi_upload_var.get())
        self.assertEqual(view.kpi_rate_var.get(), "100.0%")

        # Check accounts treeview populated
        children = view.tree_accounts.get_children()
        self.assertEqual(len(children), 1)

        # Check sorting
        view._sort_accounts_by_column("name")
        self.assertTrue(view._sort_reverse)

        # Check selecting account updates errors table
        view.tree_accounts.selection_set(children[0])
        view._on_account_selected()
        self.assertIn("P1", view.error_inspector_title_var.get())

        frame.destroy()


if __name__ == "__main__":
    unittest.main()
