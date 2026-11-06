# -*- coding: utf-8 -*-
"""
test_schedule_ui_redesign.py - Kiểm thử tái cấu trúc toàn diện tab Lịch Đăng (Phase 3):
1. BulkScheduleModal: Cảnh báo Live Warning và nút [⚡ Khử Lặp] khi phát hiện lặp đúp stem.
2. AutoScheduleView: Bộ lọc Thời Gian (Hôm nay, Ngày mai, 7 ngày tới, Quá hạn).
3. AutoScheduleView: Bộ lọc Phạm Vi (Đang hoạt động vs Đã lưu trữ).
4. AutoScheduleView: Liên kết Dự Án (Project Binding qua get_project_profiles callback).
5. AutoScheduleView: Hiển thị thân thiện 'Chưa liên kết' cho tài khoản TikTok trống.
Tuân thủ Rule 14 AGENTS.md (Zero Live Data Mutation).
"""

import datetime
import os
import shutil
import tempfile
import tkinter as tk
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from schedule_manager import ScheduleManager
from schedule_ui import AutoScheduleView, BulkScheduleModal


class TestScheduleUIRedesign(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_ui_redesign_"))
        self.db_path = self.tmp_dir / "test_redesign.db"
        self.mgr = ScheduleManager(storage_file=self.db_path)

        # Tạo sẵn các video và task mẫu
        self.v1 = self.tmp_dir / "British Food Clash #1392.mp4"
        self.v1.write_bytes(b"\x00" * 1024)

        now_str = datetime.datetime.now().isoformat()
        yesterday_str = (datetime.datetime.now() - datetime.timedelta(days=2)).isoformat()
        tomorrow_str = (datetime.datetime.now() + datetime.timedelta(days=1)).isoformat()

        tasks = [
            # Task hôm nay, profile AUTO 31
            {
                "id": "task_today_31",
                "profile_name": "AUTO 31",
                "tiktok_account": "user_31",
                "video_path": str(self.v1),
                "title": "British Food Clash #1392",
                "scheduled_time": now_str,
                "execution_mode": "SERVER_SCHEDULE",
                "status": "SERVER_SCHEDULED",
            },
            # Task hôm nay, profile AUTO 25, không có tài khoản tiktok
            {
                "id": "task_today_25_no_acc",
                "profile_name": "AUTO 25",
                "tiktok_account": "",
                "video_path": str(self.v1),
                "title": "Review 25",
                "scheduled_time": now_str,
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            },
            # Task ngày mai, profile OTHER
            {
                "id": "task_tomorrow_other",
                "profile_name": "OTHER_PROF",
                "tiktok_account": "other_user",
                "video_path": str(self.v1),
                "title": "Tomorrow Video",
                "scheduled_time": tomorrow_str,
                "execution_mode": "SERVER_SCHEDULE",
                "status": "SERVER_SCHEDULED",
            },
            # Task cũ từ 2 ngày trước, profile AUTO 25
            {
                "id": "task_past_25",
                "profile_name": "AUTO 25",
                "tiktok_account": "user_25",
                "video_path": str(self.v1),
                "title": "Past Video",
                "scheduled_time": yesterday_str,
                "execution_mode": "SERVER_SCHEDULE",
                "status": "SERVER_SCHEDULED",
            },
        ]
        self.mgr.add_tasks(tasks)

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_bulk_schedule_modal_stutter_warning_and_strip(self):
        """BulkScheduleModal hiển thị cảnh báo khi lặp stem và nút Khử lặp hoạt động chuẩn."""
        modal = BulkScheduleModal(
            parent=self.root,
            selected_profiles=["AUTO 31"],
            profile_configs={"AUTO 31": {"tiktok_account": "user_31"}},
            schedule_manager=self.mgr,
        )
        modal.scanned_videos = [self.v1]

        # Nhập công thức bị lặp đúp stem
        modal.formula_var.set("{filename} British Food Clash #1392 #tag1 #tag2")
        self.assertTrue(modal.has_stutter_warning())

        # Bấm khử lặp
        modal._do_strip_stutter()
        self.assertEqual(modal.formula_var.get(), "{filename} #tag1 #tag2")
        self.assertFalse(modal.has_stutter_warning())

        modal.destroy()

    def test_auto_schedule_view_date_and_scope_filter(self):
        """AutoScheduleView hỗ trợ bộ lọc thời gian và phạm vi hoạt động."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # 1. Mặc định phạm vi 'Đang hoạt động' loại bỏ task_past_25 (cũ từ 2 ngày trước)
        view.scope_filter_var.set("Đang hoạt động")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertNotIn("task_past_25", items)
        self.assertIn("task_today_31", items)
        self.assertIn("task_today_25_no_acc", items)

        # 2. Chuyển sang 'Đã lưu trữ' -> chứa task_past_25
        view.scope_filter_var.set("Đã lưu trữ")
        view._refresh_data()
        archived_items = view.tree.get_children()
        self.assertIn("task_past_25", archived_items)

        # 3. Lọc Thời Gian = 'Hôm nay' (với phạm vi Tất cả hoặc Đang hoạt động)
        view.scope_filter_var.set("Tất cả")
        view.date_filter_var.set("Hôm nay")
        view._refresh_data()
        today_items = view.tree.get_children()
        self.assertIn("task_today_31", today_items)
        self.assertIn("task_today_25_no_acc", today_items)
        self.assertNotIn("task_tomorrow_other", today_items)
        self.assertNotIn("task_past_25", today_items)

        view.destroy()

    def test_auto_schedule_view_project_binding(self):
        """AutoScheduleView tự động lọc theo profiles của Dự án đang chọn."""
        # Giả lập callback trả về các profile của dự án THIS OR THAT gồm AUTO 25 và AUTO 31
        mock_proj_profiles = lambda: ["AUTO 31", "AUTO 25"]
        handlers = {"get_project_profiles": mock_proj_profiles}

        view = AutoScheduleView(self.root, schedule_manager=self.mgr, handlers=handlers)
        view.date_filter_var.set("Tất cả thời gian")
        view.scope_filter_var.set("Tất cả")
        view._refresh_data()

        items = view.tree.get_children()
        # OTHER_PROF không nằm trong dự án -> Bị loại bỏ
        self.assertNotIn("task_tomorrow_other", items)
        self.assertIn("task_today_31", items)
        self.assertIn("task_today_25_no_acc", items)
        self.assertIn("task_past_25", items)

        view.destroy()

    def test_auto_schedule_view_displays_chua_lien_ket_fallback(self):
        """AutoScheduleView hiển thị 'Chưa liên kết' cho profile chưa có tài khoản TikTok."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)
        view.scope_filter_var.set("Tất cả")
        view._refresh_data()

        row_vals = view.tree.item("task_today_25_no_acc", "values")
        # Cột thứ 3 (index 2) là Tài khoản TikTok
        self.assertEqual(row_vals[2], "Chưa liên kết")

        row_vals_31 = view.tree.item("task_today_31", "values")
        self.assertEqual(row_vals_31[2], "@user_31")

        view.destroy()
