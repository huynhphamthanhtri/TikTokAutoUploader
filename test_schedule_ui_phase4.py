# -*- coding: utf-8 -*-
"""
test_schedule_ui_phase4.py - Test suite toàn diện cho Phase 4:
Tab "Lịch đăng" (AutoScheduleView trong schedule_ui.py):
- P4-01: Metric cards cập nhật chính xác theo summary stats từ SQLite.
- P4-02: Bộ lọc trạng thái (PENDING, SERVER_SCHEDULED, SUCCESS, FAILED, CANCELLED, MISSED_RESCHEDULED).
- P4-03: Bộ lọc profile đa chiều & tự động đồng bộ danh sách profile combobox.
- P4-04: Bộ lọc chế độ thực thi (☁️ Hẹn giờ TikTok, 💻 Hàng đợi máy).
- P4-05: Tìm kiếm thời gian thực (video filename, title, profile, task id).
- P4-06: Phân trang linh hoạt (page size 25/50/100/Tất cả, next/prev navigation, clamping).
- P4-07: Thao tác hàng loạt: Bắt đầu hàng đợi (start queue handler).
- P4-08: Thao tác hàng loạt: Tạm dừng hàng đợi (pause queue handler).
- P4-09: Thao tác hàng loạt: Đăng ngay lập tức (post now handler).
- P4-10: Thao tác hàng loạt: Thử lại tác vụ lỗi (retry tasks).
- P4-11: Thao tác hàng loạt: Hủy tác vụ (cancel tasks kèm xác nhận).
- P4-12: Thao tác hàng loạt: Xóa vĩnh viễn bản ghi (delete tasks kèm xác nhận).
- P4-13: Dọn dẹp bản ghi hoàn thành hoặc đã hủy (clear finished tasks).
- P4-14: Double-click xem chi tiết tác vụ & error_message khi có lỗi.
- P4-15: Phòng vệ vòng đời widget bị destroy (defensive destroyed lifecycle).
- P4-16: An toàn tranh chấp & Worker Claim Lease Lock (claim_due_tasks & recover_missed_tasks).
- P4-17: Tuân thủ Quy tắc 14 của AGENTS.md (Zero Live Data Mutation).
"""
import datetime
import math
import os
import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

if sys.platform == "win32":
    tcl_dir = os.path.join(sys.prefix, "tcl", "tcl8.6")
    tk_dir = os.path.join(sys.prefix, "tcl", "tk8.6")
    if os.path.exists(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.exists(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir

from schedule_manager import ScheduleManager
from schedule_ui import AutoScheduleView


class TestScheduleUIPhase4(TestCase):
    """Test suite kiểm chứng toàn diện AutoScheduleView và Worker Claim Safety."""

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
        # Thiết lập thư mục tạm cô lập 100% không đụng dữ liệu thật (Rule 14 AGENTS.md)
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_ui_p4_"))
        self.db_path = self.tmp_dir / "test_p4_scheduled_posts.db"
        self.mgr = ScheduleManager(storage_file=self.db_path)

        # Mock các dialog hộp thoại Tkinter
        self.patch_info = patch("tkinter.messagebox.showinfo")
        self.patch_warn = patch("tkinter.messagebox.showwarning")
        self.patch_err = patch("tkinter.messagebox.showerror")
        self.patch_ask = patch("tkinter.messagebox.askyesno")

        self.mock_info = self.patch_info.start()
        self.mock_warn = self.patch_warn.start()
        self.mock_err = self.patch_err.start()
        self.mock_ask = self.patch_ask.start()

        # Nạp dữ liệu mẫu ban đầu gồm 6 task đa dạng trạng thái
        self._seed_initial_tasks()

    def tearDown(self):
        self.patch_info.stop()
        self.patch_warn.stop()
        self.patch_err.stop()
        self.patch_ask.stop()

        for child in self.root.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _seed_initial_tasks(self):
        tasks = [
            {
                "id": "task_alpha_01",
                "profile_name": "Profile_Alpha",
                "tiktok_account": "acc_alpha",
                "video_path": str(self.tmp_dir / "v1.mp4"),
                "video_filename": "v1.mp4",
                "title": "Intro Video Alpha",
                "scheduled_time": "2026-10-26T08:00:00",
                "status": "PENDING",
                "execution_mode": "SERVER_SCHEDULE",
            },
            {
                "id": "task_alpha_02",
                "profile_name": "Profile_Alpha",
                "tiktok_account": "acc_alpha",
                "video_path": str(self.tmp_dir / "v2.mp4"),
                "video_filename": "v2.mp4",
                "title": "Tutorial Alpha",
                "scheduled_time": "2026-10-26T12:00:00",
                "status": "SERVER_SCHEDULED",
                "execution_mode": "SERVER_SCHEDULE",
            },
            {
                "id": "task_beta_01",
                "profile_name": "Profile_Beta",
                "tiktok_account": "acc_beta",
                "video_path": str(self.tmp_dir / "v3.mp4"),
                "video_filename": "v3.mp4",
                "title": "Tips Beta",
                "scheduled_time": "2026-10-26T18:00:00",
                "status": "SUCCESS",
                "execution_mode": "LOCAL_QUEUE",
            },
            {
                "id": "task_beta_02",
                "profile_name": "Profile_Beta",
                "tiktok_account": "acc_beta",
                "video_path": str(self.tmp_dir / "v4.mp4"),
                "video_filename": "v4.mp4",
                "title": "Review Beta",
                "scheduled_time": "2026-10-26T20:00:00",
                "status": "FAILED",
                "execution_mode": "LOCAL_QUEUE",
                "error_message": "Simulated network timeout during upload",
            },
            {
                "id": "task_gamma_01",
                "profile_name": "Profile_Gamma",
                "tiktok_account": "acc_gamma",
                "video_path": str(self.tmp_dir / "v5.mp4"),
                "video_filename": "v5.mp4",
                "title": "Outro Gamma",
                "scheduled_time": "2026-10-27T08:00:00",
                "status": "CANCELLED",
                "execution_mode": "SERVER_SCHEDULE",
            },
            {
                "id": "task_gamma_02",
                "profile_name": "Profile_Gamma",
                "tiktok_account": "acc_gamma",
                "video_path": str(self.tmp_dir / "v6.mp4"),
                "video_filename": "v6.mp4",
                "title": "Daily Vlog Gamma",
                "scheduled_time": "2026-10-27T12:00:00",
                "status": "MISSED_RESCHEDULED",
                "execution_mode": "LOCAL_QUEUE",
            },
        ]
        self.mgr.add_tasks(tasks)

    def test_p4_01_metric_cards_reflect_accurate_summary_stats(self):
        """P4-01: Các ô thống kê (Metric Cards) phản ánh chính xác số liệu từ cơ sở dữ liệu SQLite."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # Đối chiếu 4 biến thống kê thời gian thực
        self.assertEqual(view.stat_pending_var.get(), "2")  # PENDING (1) + MISSED_RESCHEDULED (1)
        self.assertEqual(view.stat_server_var.get(), "1")   # SERVER_SCHEDULED (1)
        self.assertEqual(view.stat_today_var.get(), "1")    # SUCCESS hôm nay (task có ngày mai 26/10)
        self.assertEqual(view.stat_failed_var.get(), "1")   # FAILED (1)

        view.destroy()

    def test_p4_02_status_filter_filters_treeview_rows_accurately(self):
        """P4-02: Bộ lọc trạng thái lọc chính xác các dòng trên Treeview."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # Mặc định "Tất cả": hiển thị đủ 6 task
        self.assertEqual(len(view.tree.get_children()), 6)

        # Lọc "FAILED"
        view.status_filter_var.set("FAILED")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], "task_beta_02")

        # Lọc "SERVER_SCHEDULED"
        view.status_filter_var.set("SERVER_SCHEDULED")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], "task_alpha_02")

        # Lọc "CANCELLED"
        view.status_filter_var.set("CANCELLED")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], "task_gamma_01")

        view.destroy()

    def test_p4_03_profile_filter_and_dynamic_combobox_values(self):
        """P4-03: Bộ lọc profile đồng bộ chính xác danh sách combobox và lọc đúng theo profile."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # Kiểm tra danh sách trong combobox profile
        expected_profiles = ["Tất cả", "Profile_Alpha", "Profile_Beta", "Profile_Gamma"]
        self.assertEqual(view.cb_profile.cget("values"), expected_profiles)

        # Lọc "Profile_Alpha" -> 2 task
        view.profile_filter_var.set("Profile_Alpha")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 2)
        self.assertEqual(set(items), {"task_alpha_01", "task_alpha_02"})

        # Lọc "Profile_Beta" -> 2 task
        view.profile_filter_var.set("Profile_Beta")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 2)
        self.assertEqual(set(items), {"task_beta_01", "task_beta_02"})

        view.destroy()

    def test_p4_04_execution_mode_filter(self):
        """P4-04: Bộ lọc chế độ thực thi (☁️ Hẹn giờ TikTok / 💻 Hàng đợi máy)."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # Lọc "☁️ Hẹn giờ TikTok" (SERVER_SCHEDULE) -> 3 task (alpha_01, alpha_02, gamma_01)
        view.mode_filter_var.set("☁️ Hẹn giờ TikTok")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 3)
        self.assertEqual(set(items), {"task_alpha_01", "task_alpha_02", "task_gamma_01"})

        # Lọc "💻 Hàng đợi máy" (LOCAL_QUEUE) -> 3 task (beta_01, beta_02, gamma_02)
        view.mode_filter_var.set("💻 Hàng đợi máy")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 3)
        self.assertEqual(set(items), {"task_beta_01", "task_beta_02", "task_gamma_02"})

        view.destroy()

    def test_p4_05_search_query_filters_dynamically(self):
        """P4-05: Ô tìm kiếm từ khóa thời gian thực lọc chính xác theo tiêu đề, tên file, profile hoặc ID."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # Tìm theo tiêu đề "Tutorial"
        view.search_var.set("Tutorial")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], "task_alpha_02")

        # Tìm theo tên video "v4.mp4"
        view.search_var.set("v4.mp4")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], "task_beta_02")

        # Tìm theo profile "Gamma"
        view.search_var.set("Gamma")
        view._refresh_data()
        items = view.tree.get_children()
        self.assertEqual(len(items), 2)
        self.assertEqual(set(items), {"task_gamma_01", "task_gamma_02"})

        view.destroy()

    def test_p4_06_pagination_navigation_and_page_size_clamping(self):
        """P4-06: Phân trang linh hoạt: chuyển trang trước/sau, đổi số bản ghi/trang và tự động căn chỉnh trang hợp lệ."""
        # Nạp thêm 60 task để có tổng cộng 66 task
        bulk_tasks = [
            {
                "id": f"bulk_task_{i:03d}",
                "profile_name": "Profile_Bulk",
                "tiktok_account": "acc_bulk",
                "video_path": str(self.tmp_dir / f"bulk_{i}.mp4"),
                "video_filename": f"bulk_{i}.mp4",
                "title": f"Bulk Video {i}",
                "scheduled_time": f"2026-11-01T{10 + (i % 10):02d}:00:00",
                "status": "PENDING",
                "execution_mode": "SERVER_SCHEDULE",
            }
            for i in range(1, 61)
        ]
        self.mgr.add_tasks(bulk_tasks)
        total_seeded = 6 + 60  # 66 tasks

        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # 1. Mặc định page_size = "50"
        self.assertEqual(view.page_size_var.get(), "50")
        self.assertEqual(view.current_page, 1)
        self.assertEqual(view.total_pages, 2)
        self.assertEqual(len(view.tree.get_children()), 50)
        self.assertEqual(view.lbl_page_info.cget("text"), f"Hiển thị 1 - 50 / Tổng {total_seeded} tác vụ")
        self.assertEqual(view.lbl_current_page.cget("text"), "Trang 1 / 2")
        self.assertEqual(view.btn_prev_page.cget("state"), "disabled")
        self.assertEqual(view.btn_next_page.cget("state"), "normal")

        # 2. Bấm Trang sau -> Trang 2
        view._next_page()
        self.assertEqual(view.current_page, 2)
        self.assertEqual(len(view.tree.get_children()), 16)  # 66 - 50 = 16
        self.assertEqual(view.lbl_page_info.cget("text"), f"Hiển thị 51 - 66 / Tổng {total_seeded} tác vụ")
        self.assertEqual(view.lbl_current_page.cget("text"), "Trang 2 / 2")
        self.assertEqual(view.btn_prev_page.cget("state"), "normal")
        self.assertEqual(view.btn_next_page.cget("state"), "disabled")

        # 3. Bấm Trang trước -> Quay lại Trang 1
        view._prev_page()
        self.assertEqual(view.current_page, 1)
        self.assertEqual(len(view.tree.get_children()), 50)
        self.assertEqual(view.btn_prev_page.cget("state"), "disabled")

        # 4. Đổi page_size = "25" -> Tổng trang = ceil(66 / 25) = 3 trang
        view.page_size_var.set("25")
        view._on_page_size_changed()
        self.assertEqual(view.current_page, 1)
        self.assertEqual(view.total_pages, 3)
        self.assertEqual(len(view.tree.get_children()), 25)
        self.assertEqual(view.lbl_page_info.cget("text"), f"Hiển thị 1 - 25 / Tổng {total_seeded} tác vụ")

        # 5. Đổi page_size = "Tất cả" -> Tổng trang = 1, tải trọn vẹn 66 task
        view.page_size_var.set("Tất cả")
        view._on_page_size_changed()
        self.assertEqual(view.current_page, 1)
        self.assertEqual(view.total_pages, 1)
        self.assertEqual(len(view.tree.get_children()), 66)
        self.assertEqual(view.btn_prev_page.cget("state"), "disabled")
        self.assertEqual(view.btn_next_page.cget("state"), "disabled")

        view.destroy()

    def test_p4_07_batch_start_queue(self):
        """P4-07: Thao tác Bắt Đầu Hàng Đợi chuyển tiếp đúng danh sách task đang chọn cho handler."""
        start_queue_mock = MagicMock()
        handlers = {"start_queue_handler": start_queue_mock}
        view = AutoScheduleView(self.root, schedule_manager=self.mgr, handlers=handlers)

        # Chọn task_alpha_01
        view.tree.selection_set("task_alpha_01")
        view._do_start_queue()
        start_queue_mock.assert_called_once_with(["task_alpha_01"])

        # Không có handler: hiển thị thông báo popup an toàn
        view.handlers = {}
        view._do_start_queue()
        self.mock_info.assert_called_with("Thông báo", "Đang khởi động hàng đợi đăng bài tự động.")

        view.destroy()

    def test_p4_08_batch_pause_queue(self):
        """P4-08: Thao tác Tạm Dừng Hàng Đợi gọi pause handler hoặc hiển thị thông báo popup."""
        pause_queue_mock = MagicMock()
        handlers = {"pause_queue_handler": pause_queue_mock}
        view = AutoScheduleView(self.root, schedule_manager=self.mgr, handlers=handlers)

        view._do_pause_queue()
        pause_queue_mock.assert_called_once()

        view.handlers = {}
        view._do_pause_queue()
        self.mock_info.assert_called_with("Thông báo", "Đã tạm dừng hàng đợi đăng bài.")

        view.destroy()

    def test_p4_09_batch_post_now(self):
        """P4-09: Thao tác Đăng Ngay cảnh báo khi chưa chọn task và kích hoạt post_now_handler khi có task."""
        post_now_mock = MagicMock()
        handlers = {"post_now_handler": post_now_mock}
        view = AutoScheduleView(self.root, schedule_manager=self.mgr, handlers=handlers)

        # Chưa chọn task -> cảnh báo
        view.tree.selection_set()
        view._do_post_now()
        self.mock_warn.assert_called_with("Thông báo", "Vui lòng chọn ít nhất một tác vụ để đăng ngay.")
        post_now_mock.assert_not_called()

        # Chọn task_beta_01 -> gọi handler
        view.tree.selection_set("task_beta_01")
        view._do_post_now()
        post_now_mock.assert_called_once_with(["task_beta_01"])

        view.destroy()

    def test_p4_10_batch_retry_error_tasks(self):
        """P4-10: Thao tác Thử Lại Lỗi đưa task FAILED/CANCELLED về hàng đợi PENDING và thông báo cho listener."""
        on_change_mock = MagicMock()
        handlers = {"on_change_handler": on_change_mock}
        view = AutoScheduleView(self.root, schedule_manager=self.mgr, handlers=handlers)

        # Kiểm tra task_beta_02 ban đầu là FAILED
        t_before = self.mgr.get_task_by_id("task_beta_02")
        self.assertEqual(t_before["status"], "FAILED")

        # Chọn task_beta_02 và bấm Retry
        view.tree.selection_set("task_beta_02")
        view._do_retry()

        # Xác nhận trạng thái trong SQLite đã đổi sang PENDING
        t_after = self.mgr.get_task_by_id("task_beta_02")
        self.assertEqual(t_after["status"], "PENDING")
        self.assertEqual(t_after["retry_count"], 0)
        self.assertEqual(t_after["error_message"], "")

        # Xác nhận listener được gọi thông báo thay đổi
        on_change_mock.assert_called_once()
        self.mock_info.assert_called_with("Thành công", "Đã đưa 1 tác vụ về hàng đợi chờ đăng.")

        view.destroy()

    def test_p4_11_batch_cancel_tasks_with_confirmation(self):
        """P4-11: Thao tác Hủy Lịch chuyển trạng thái sang CANCELLED khi người dùng xác nhận 'Yes'."""
        on_change_mock = MagicMock()
        handlers = {"on_change_handler": on_change_mock}
        view = AutoScheduleView(self.root, schedule_manager=self.mgr, handlers=handlers)

        # Người dùng chọn No -> không hủy
        self.mock_ask.return_value = False
        view.tree.selection_set("task_alpha_01")
        view._do_cancel()
        self.assertEqual(self.mgr.get_task_by_id("task_alpha_01")["status"], "PENDING")
        on_change_mock.assert_not_called()

        # Người dùng chọn Yes -> hủy thành công
        self.mock_ask.return_value = True
        view._do_cancel()
        self.assertEqual(self.mgr.get_task_by_id("task_alpha_01")["status"], "CANCELLED")
        on_change_mock.assert_called_once()
        self.mock_info.assert_called_with("Thành công", "Đã hủy thành công 1 tác vụ.")

        view.destroy()

    def test_p4_12_batch_delete_tasks_with_confirmation(self):
        """P4-12: Thao tác Xóa Bản Ghi xóa vĩnh viễn task khỏi SQLite khi người dùng xác nhận 'Yes'."""
        on_change_mock = MagicMock()
        handlers = {"on_change_handler": on_change_mock}
        view = AutoScheduleView(self.root, schedule_manager=self.mgr, handlers=handlers)

        # Người dùng chọn No -> không xóa
        self.mock_ask.return_value = False
        view.tree.selection_set("task_gamma_01")
        view._do_delete()
        self.assertIsNotNone(self.mgr.get_task_by_id("task_gamma_01"))

        # Người dùng chọn Yes -> xóa vĩnh viễn khỏi SQLite
        self.mock_ask.return_value = True
        view._do_delete()
        self.assertIsNone(self.mgr.get_task_by_id("task_gamma_01"))
        on_change_mock.assert_called_once()
        self.mock_info.assert_called_with("Thành công", "Đã xóa 1 bản ghi.")

        view.destroy()

    def test_p4_13_clear_finished_tasks(self):
        """P4-13: Dọn Dẹp Đã Xong xóa sạch các task SUCCESS và CANCELLED, bảo toàn các task PENDING/FAILED."""
        on_change_mock = MagicMock()
        handlers = {"on_change_handler": on_change_mock}
        view = AutoScheduleView(self.root, schedule_manager=self.mgr, handlers=handlers)

        # Trước khi dọn dẹp: có 6 task
        self.assertEqual(len(self.mgr.get_all_tasks()), 6)

        # Dọn dẹp
        view._do_clear_finished()

        # Sau khi dọn dẹp: task_beta_01 (SUCCESS) và task_gamma_01 (CANCELLED) đã bị xóa
        remaining = self.mgr.get_all_tasks()
        remaining_ids = {t["id"] for t in remaining}
        self.assertNotIn("task_beta_01", remaining_ids)
        self.assertNotIn("task_gamma_01", remaining_ids)
        self.assertIn("task_alpha_01", remaining_ids)  # PENDING còn nguyên
        self.assertIn("task_beta_02", remaining_ids)   # FAILED còn nguyên

        on_change_mock.assert_called_once()
        self.mock_info.assert_called_with("Dọn dẹp", "Đã dọn dẹp 2 bản ghi đã hoàn thành hoặc đã hủy.")

        view.destroy()

    def test_p4_14_double_click_row_shows_task_details_and_error(self):
        """P4-14: Nhấp đúp vào dòng hiển thị chi tiết task và chi tiết lỗi error_message khi có lỗi."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # Nhấp đúp vào task_beta_02 (FAILED có error_message)
        view.tree.selection_set("task_beta_02")
        view._on_double_click_row(None)

        self.mock_info.assert_called_once()
        title_arg, msg_arg = self.mock_info.call_args[0]
        self.assertEqual(title_arg, "Chi Tiết Tác Vụ Lên Lịch")
        self.assertIn("task_beta_02", msg_arg)
        self.assertIn("Review Beta", msg_arg)
        self.assertIn("Simulated network timeout during upload", msg_arg)

        view.destroy()

    def test_p4_15_defensive_destroyed_lifecycle(self):
        """P4-15: Khi widget bị destroy, mọi hàm refresh và batch action đều abort an toàn mà không làm văng exception."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)
        view.destroy()

        # Khẳng định winfo_exists() trả về False
        self.assertFalse(view.winfo_exists())

        # Gọi các phương thức sau destroy: không được ném ngoại lệ
        try:
            view._refresh_data()
            view._do_start_queue()
            view._do_pause_queue()
            view._do_post_now()
            view._do_retry()
            view._do_cancel()
            view._do_delete()
            view._do_clear_finished()
            view._on_double_click_row(None)
        except Exception as ex:
            self.fail(f"Hàm ném ngoại lệ khi widget đã bị destroy: {ex}")

    def test_p4_16_concurrency_and_worker_claim_lease_safety(self):
        """P4-16: An toàn tranh chấp khi nhiều Worker cùng nhận việc: cơ chế Lease Lock chỉ cho phép đúng 1 worker nhận task."""
        # 1. Tạo task PENDING có thời gian đến hạn (thời gian quá khứ)
        due_task = {
            "id": "task_due_concurrent_01",
            "profile_name": "Profile_Due",
            "tiktok_account": "acc_due",
            "video_path": str(self.tmp_dir / "due.mp4"),
            "video_filename": "due.mp4",
            "title": "Due Video",
            "scheduled_time": "2026-10-20T08:00:00",
            "status": "PENDING",
            "execution_mode": "LOCAL_QUEUE",
        }
        self.mgr.add_tasks([due_task])

        # 2. Worker 1 nhận việc với lease 300 giây
        claimed_w1 = self.mgr.claim_task_for_execution("task_due_concurrent_01", worker_id="worker_thread_1", lease_seconds=300)
        self.assertTrue(claimed_w1, "Worker 1 phải nhận việc thành công!")

        # 3. Worker 2 cố gắng tranh chấp nhận cùng task đó -> Bắt buộc bị từ chối
        claimed_w2 = self.mgr.claim_task_for_execution("task_due_concurrent_01", worker_id="worker_thread_2", lease_seconds=300)
        self.assertFalse(claimed_w2, "Worker 2 không được phép tranh chấp task đang có lease hiệu lực!")

        # 4. Kiểm tra trạng thái trong SQLite: task đang UPLOADING với worker_1
        t_claimed = self.mgr.get_task_by_id("task_due_concurrent_01")
        self.assertEqual(t_claimed["status"], "UPLOADING")
        self.assertEqual(t_claimed["claim_worker_id"], "worker_thread_1")

        # 5. Tái nhận việc khi lease hết hạn (Worker 1 bị treo/crash, lease_until trôi về quá khứ)
        with self.mgr._get_connection() as conn:
            conn.execute("UPDATE scheduled_tasks SET lease_until = 100 WHERE id = 'task_due_concurrent_01'")

        claimed_w3 = self.mgr.claim_task_for_execution("task_due_concurrent_01", worker_id="worker_thread_3", lease_seconds=300)
        self.assertTrue(claimed_w3, "Worker 3 phải nhận lại việc thành công khi lease cũ đã hết hạn!")
        t_reclaimed = self.mgr.get_task_by_id("task_due_concurrent_01")
        self.assertEqual(t_reclaimed["claim_worker_id"], "worker_thread_3")

        # 6. Kiểm tra phục hồi thông minh cho task PENDING bị lỡ hẹn do tắt máy
        missed_task = {
            "id": "task_missed_shutdown_01",
            "profile_name": "Profile_Missed",
            "tiktok_account": "acc_missed",
            "video_path": str(self.tmp_dir / "missed.mp4"),
            "video_filename": "missed.mp4",
            "title": "Missed Video Shutdown",
            "scheduled_time": "2026-10-20T08:00:00",
            "scheduled_epoch": 1000,
            "status": "PENDING",
            "execution_mode": "LOCAL_QUEUE",
        }
        self.mgr.add_tasks([missed_task])
        recovered = self.mgr.recover_missed_tasks()
        self.assertIn("task_missed_shutdown_01", [t["id"] for t in recovered])
        t_recovered = self.mgr.get_task_by_id("task_missed_shutdown_01")
        self.assertEqual(t_recovered["status"], "MISSED_RESCHEDULED")

    def test_p4_17_zero_live_data_mutation(self):
        """P4-17: Tuân thủ nghiêm ngặt Quy tắc 14 của AGENTS.md: dữ liệu cấu hình live và database thật không bị biến đổi."""
        real_db_path = Path("scheduled_posts.db")
        if real_db_path.exists():
            real_mtime_before = real_db_path.stat().st_mtime_ns
            # Chạy một hành động trên view
            view = AutoScheduleView(self.root, schedule_manager=self.mgr)
            view._refresh_data()
            view.destroy()
            real_mtime_after = real_db_path.stat().st_mtime_ns
            self.assertEqual(real_mtime_before, real_mtime_after, "Database scheduled_posts.db thật tuyệt đối không bị đụng tới!")

    def test_p4_18_get_distinct_profiles_optimized(self):
        """P4-18: Kiểm tra get_distinct_profiles() tối ưu SQL, lọc bỏ rỗng và sắp xếp tăng dần (Codex P2-02)."""
        extra_tasks = [
            {
                "id": "task_dist_1",
                "profile_name": "Profile_Zeta",
                "video_path": str(self.tmp_dir / "z.mp4"),
                "scheduled_time": "2026-10-28T09:00:00",
                "status": "PENDING",
            },
            {
                "id": "task_dist_2",
                "profile_name": "",  # Tên rỗng -> Phải bị loại trừ
                "video_path": str(self.tmp_dir / "empty.mp4"),
                "scheduled_time": "2026-10-28T10:00:00",
                "status": "PENDING",
            },
            {
                "id": "task_dist_3",
                "profile_name": "Profile_Beta",  # Trùng lặp Profile_Beta đã có trong seed
                "video_path": str(self.tmp_dir / "b_dup.mp4"),
                "scheduled_time": "2026-10-28T11:00:00",
                "status": "PENDING",
            },
        ]
        self.mgr.add_tasks(extra_tasks)

        profs = self.mgr.get_distinct_profiles()
        # Seeded có: Profile_Alpha, Profile_Beta, Profile_Gamma. Thêm Profile_Zeta. Rỗng bị loại.
        self.assertEqual(profs, ["Profile_Alpha", "Profile_Beta", "Profile_Gamma", "Profile_Zeta"])
        self.assertNotIn("", profs)

    def test_p4_19_profile_reset_re_query_consistency(self):
        """P4-19: Khi profile đang chọn bị xóa, UI tự động reset về 'Tất cả' TRƯỚC KHI query, không bị trống bảng (Codex P2-01)."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)
        
        # 1. Chọn profile Gamma (có 2 tasks trong seed)
        view.profile_filter_var.set("Profile_Gamma")
        view._refresh_data()
        self.assertEqual(len(view.tree.get_children()), 2)

        # 2. Xóa toàn bộ task của Profile_Gamma
        self.mgr.delete_tasks(["task_gamma_01", "task_gamma_02"])

        # 3. Refresh lại dữ liệu: profile filter phải tự động chuyển về 'Tất cả'
        view._refresh_data()
        self.assertEqual(view.profile_filter_var.get(), "Tất cả")
        
        # Bảng phải hiển thị 4 task còn lại (Profile_Alpha + Profile_Beta), KHÔNG được rỗng!
        remaining_in_tree = view.tree.get_children()
        self.assertEqual(len(remaining_in_tree), 4)
        self.assertNotIn("Profile_Gamma", view.cb_profile.cget("values"))
        view.destroy()

    def test_p4_20_stable_pagination_tie_breaker(self):
        """P4-20: Đảm bảo phân trang có khóa phụ (id ASC) deterministic khi trùng scheduled_time (Codex P2-05)."""
        same_time = "2026-10-31T15:00:00"
        tie_tasks = [
            {"id": "tie_task_03", "profile_name": "TieProf", "video_path": str(self.tmp_dir / "t3.mp4"), "scheduled_time": same_time, "status": "PENDING"},
            {"id": "tie_task_01", "profile_name": "TieProf", "video_path": str(self.tmp_dir / "t1.mp4"), "scheduled_time": same_time, "status": "PENDING"},
            {"id": "tie_task_04", "profile_name": "TieProf", "video_path": str(self.tmp_dir / "t4.mp4"), "scheduled_time": same_time, "status": "PENDING"},
            {"id": "tie_task_02", "profile_name": "TieProf", "video_path": str(self.tmp_dir / "t2.mp4"), "scheduled_time": same_time, "status": "PENDING"},
        ]
        self.mgr.add_tasks(tie_tasks)

        # Query trang 1: limit 2, offset 0
        page1 = self.mgr.get_all_tasks(filter_profile="TieProf", limit=2, offset=0)
        page1_ids = [t["id"] for t in page1]
        self.assertEqual(page1_ids, ["tie_task_01", "tie_task_02"], "Trang 1 phải sắp xếp theo id tăng dần làm tie-breaker!")

        # Query trang 2: limit 2, offset 2
        page2 = self.mgr.get_all_tasks(filter_profile="TieProf", limit=2, offset=2)
        page2_ids = [t["id"] for t in page2]
        self.assertEqual(page2_ids, ["tie_task_03", "tie_task_04"], "Trang 2 phải tiếp nối chính xác không trùng task của trang 1!")

        # Không có phần tử giao nhau giữa các trang
        self.assertEqual(set(page1_ids).intersection(set(page2_ids)), set())

    def test_p4_21_status_combobox_includes_uploading(self):
        """P4-21: Combobox trạng thái phải bao gồm 'UPLOADING' và lọc chính xác (Codex P2-06)."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)
        cb_values = view.cb_status.cget("values")
        self.assertIn("UPLOADING", cb_values)

        # Thêm task có trạng thái UPLOADING
        uploading_task = {
            "id": "task_uploading_test_01",
            "profile_name": "Profile_Alpha",
            "video_path": str(self.tmp_dir / "u.mp4"),
            "video_filename": "u.mp4",
            "title": "Uploading Now",
            "scheduled_time": "2026-10-27T08:00:00",
            "status": "UPLOADING",
        }
        self.mgr.add_tasks([uploading_task])

        # Lọc trạng thái UPLOADING
        view.status_filter_var.set("UPLOADING")
        view._refresh_data()

        rows = view.tree.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], "task_uploading_test_01")
        row_vals = view.tree.item("task_uploading_test_01", "values")
        self.assertEqual(row_vals[6], "⚡ Đang tải...")
        view.destroy()

    def test_p4_22_defensive_page_size_fallback(self):
        """P4-22: Page size parsing phòng thủ an toàn: overflow integer, số âm, rỗng (Codex P2-03)."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # 1. Nhập số nguyên vượt miền SQLite (2**63) -> Fallback về 50 an toàn, không ném OverflowError và hiển thị đúng dữ liệu
        view.page_size_var.set("9223372036854775808")
        view._refresh_data()
        self.assertEqual(view.total_pages, 1)
        self.assertEqual(len(view.tree.get_children()), 6)

        # 2. Nhập chuỗi rác không phải số -> Fallback về 50 an toàn
        view.page_size_var.set("invalid_input_!@#")
        view._refresh_data()
        self.assertEqual(view.total_pages, 1)
        self.assertEqual(len(view.tree.get_children()), 6)

        # 3. Nhập số âm -> Fallback về 50
        view.page_size_var.set("-20")
        view._refresh_data()
        self.assertEqual(view.total_pages, 1)
        self.assertEqual(len(view.tree.get_children()), 6)

        # 4. Nhập chuỗi rỗng -> Fallback về 50
        view.page_size_var.set("   ")
        view._refresh_data()
        self.assertEqual(view.total_pages, 1)
        self.assertEqual(len(view.tree.get_children()), 6)

        # 5. Nhập "Tất cả" -> limit None
        view.page_size_var.set("Tất cả")
        view._refresh_data()
        self.assertEqual(view.total_pages, 1)
        self.assertEqual(len(view.tree.get_children()), 6)
        view.destroy()

    def test_p4_23_root_destroy_defense_all_handlers(self):
        """P4-23: Phòng vệ toàn diện khi widget bị destroy, mọi handler đều return êm ái (Codex P2-04)."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)
        view.destroy()

        # Tất cả các method gọi sau khi view đã destroy không được văng exception
        self.assertFalse(view._is_alive())
        view._prev_page()
        view._next_page()
        view._on_page_size_changed()
        view._notify_change()
        view._refresh_data()
        self.assertEqual(view._get_selected_task_ids(), [])
        view._do_start_queue()
        view._do_pause_queue()
        view._do_post_now()
        view._do_retry()
        view._do_cancel()
        view._do_delete()
        view._do_clear_finished()
        view._on_double_click_row(None)

    def test_p4_24_profile_named_all_exact_match(self):
        """P4-24: Tách biệt Sentinel 'Tất cả' khỏi profile có tên thật là 'all' hoặc 'tat ca' (Codex Review Point 4)."""
        # Thêm task với profile_name là "all" (tên thật của người dùng đặt)
        task_all = {
            "id": "task_named_all_01",
            "profile_name": "all",
            "tiktok_account": "acc_all",
            "video_path": str(self.tmp_dir / "all.mp4"),
            "video_filename": "all.mp4",
            "title": "Profile named all",
            "scheduled_time": "2026-10-29T10:00:00",
            "status": "PENDING",
        }
        self.mgr.add_tasks([task_all])

        view = AutoScheduleView(self.root, schedule_manager=self.mgr)

        # 1. Khi chọn Sentinel "Tất cả", phải hiển thị toàn bộ 7 tasks
        view.profile_filter_var.set("Tất cả")
        view._refresh_data()
        self.assertEqual(len(view.tree.get_children()), 7)

        # 2. Khi chọn chính xác profile "all", CHỈ hiển thị đúng 1 task của profile "all"
        view.profile_filter_var.set("all")
        view._refresh_data()
        rows = view.tree.get_children()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], "task_named_all_01")
        view.destroy()

    def test_p4_25_dialog_closed_or_view_destroyed_during_confirm(self):
        """P4-25: Kiểm tra phòng vệ lifecycle khi view bị destroy trong thời gian modal dialog askyesno đang chờ (Codex P2-04)."""
        view = AutoScheduleView(self.root, schedule_manager=self.mgr)
        view.tree.selection_set(["task_alpha_01"])

        # Giả lập view bị destroy ngay trong lúc dialog đang mở
        def side_effect_destroy(*args, **kwargs):
            view.destroy()
            return True

        self.mock_ask.side_effect = side_effect_destroy

        # Gọi _do_cancel(): sau khi askyesno return True, view đã chết -> không được gọi cancel_tasks
        mock_cancel = patch.object(self.mgr, "cancel_tasks").start()
        try:
            view._do_cancel()
            mock_cancel.assert_not_called()
        finally:
            mock_cancel.stop()

    def test_p4_26_root_teardown_defense(self):
        """P4-26: Phòng vệ khi Tcl interpreter của Tk root thực sự bị destroy (Codex P2-04)."""
        isolated_root = tk.Tk()
        isolated_root.withdraw()
        view = AutoScheduleView(isolated_root, schedule_manager=self.mgr)

        # Hủy hoàn toàn Tcl interpreter root
        isolated_root.destroy()

        # _is_alive() phải bắt TclError/Exception và trả về False
        self.assertFalse(view._is_alive())

        # Mọi lời gọi không được rò rỉ TclError
        view._refresh_data()
        view._do_start_queue()
        view._do_pause_queue()
        view._do_post_now()
        view._do_retry()
        view._do_cancel()
        view._do_delete()
        view._do_clear_finished()

    def test_p4_27_status_tag_and_server_scheduled_cancel_safety(self):
        """P4-27: Kiểm tra tag hiển thị và bảo vệ không hủy task SERVER_SCHEDULED cục bộ để chống ghost post (Codex P2-06 & P4 Review)."""
        # 1. Thêm task SERVER_SCHEDULED có tiktok_item_id
        server_task = {
            "id": "task_server_item_01",
            "profile_name": "Profile_Alpha",
            "tiktok_account": "acc_alpha",
            "tiktok_item_id": "item_tiktok_123456",
            "video_path": str(self.tmp_dir / "server.mp4"),
            "video_filename": "server.mp4",
            "title": "Server Task With ID",
            "scheduled_time": "2026-10-30T10:00:00",
            "status": "SERVER_SCHEDULED",
            "execution_mode": "SERVER_SCHEDULE",
        }
        # Thêm task MISSED_RESCHEDULED để kiểm tra hủy thành công
        missed_task = {
            "id": "task_missed_item_01",
            "profile_name": "Profile_Alpha",
            "tiktok_account": "acc_alpha",
            "video_path": str(self.tmp_dir / "missed2.mp4"),
            "video_filename": "missed2.mp4",
            "title": "Missed Task",
            "scheduled_time": "2026-10-30T12:00:00",
            "status": "MISSED_RESCHEDULED",
            "execution_mode": "LOCAL_QUEUE",
        }
        self.mgr.add_tasks([server_task, missed_task])

        view = AutoScheduleView(self.root, schedule_manager=self.mgr)
        view.status_filter_var.set("SERVER_SCHEDULED")
        view._refresh_data()

        # Kiểm tra tag hiển thị là server_tag
        self.assertIn("task_server_item_01", view.tree.get_children())
        tags = view.tree.item("task_server_item_01", "tags")
        self.assertEqual(tags, ("server_tag",))

        # 2. Thử hủy task SERVER_SCHEDULED: cancel_tasks phải từ chối (bảo vệ lịch đã nằm trên cloud TikTok)
        view.tree.selection_set(["task_server_item_01"])
        self.mock_ask.return_value = True
        view._do_cancel()

        # Trạng thái trên DB phải giữ nguyên SERVER_SCHEDULED và tiktok_item_id được bảo toàn 100%
        t_server_after = self.mgr.get_task_by_id("task_server_item_01")
        self.assertEqual(t_server_after["status"], "SERVER_SCHEDULED")
        self.assertEqual(t_server_after["tiktok_item_id"], "item_tiktok_123456")

        # 3. Hủy task MISSED_RESCHEDULED: chuyển filter về Tất cả để hiển thị và hủy
        view.status_filter_var.set("Tất cả")
        view._refresh_data()
        view.tree.selection_set(["task_missed_item_01"])
        view._do_cancel()
        t_missed_after = self.mgr.get_task_by_id("task_missed_item_01")
        self.assertEqual(t_missed_after["status"], "CANCELLED")
        view.destroy()


if __name__ == "__main__":
    import unittest
    unittest.main()
