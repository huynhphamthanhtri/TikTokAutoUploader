# -*- coding: utf-8 -*-
"""
test_schedule_ui_phase3.py - Bộ kiểm thử Giao diện Ma trận Lên lịch & Quản trị Hàng đợi An Toàn (Phase 3).

Tuân thủ nghiêm ngặt:
- AGENTS.md Quy tắc 14: Zero Live Data Mutation (100% DB và file test nằm trong thư mục tạm mkdtemp).
- AGENTS.md Quy tắc 8: Cấm test tự chứng minh, kiểm thử tính đúng đắn nghiệp vụ đa tầng (UI -> Service -> SQLite Storage).
- AGENTS.md Quy tắc 15: Full 360 call-chain integration verification.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import customtkinter as ctk

import schedule_ui
from schedule_contracts import ConflictSnapshot, SchedulePlan, StalePlanError, VideoProbeResult
from schedule_manager import ScheduleManager
from schedule_ui import BulkScheduleModal


class TestScheduleUIPhase3(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ui_p3_")
        self.db_file = Path(self.test_dir) / "test_p3_storage.db"
        self.patcher = patch("schedule_manager.DEFAULT_DB_FILE", self.db_file)
        self.patcher.start()

        # Patch toàn bộ messagebox để tránh modal dialog hệ thống làm treo test
        self.patch_info = patch("tkinter.messagebox.showinfo")
        self.patch_warn = patch("tkinter.messagebox.showwarning")
        self.patch_err = patch("tkinter.messagebox.showerror")
        self.mock_info = self.patch_info.start()
        self.mock_warn = self.patch_warn.start()
        self.mock_err = self.patch_err.start()

        self.mgr = ScheduleManager(storage_file=self.db_file)

        # Tạo file video giả lập
        self.video_dir = Path(self.test_dir) / "vids"
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.v1 = self.video_dir / "clip_01.mp4"
        self.v2 = self.video_dir / "clip_02.mp4"
        self.v3 = self.video_dir / "clip_03.mp4"
        self.v1.write_bytes(b"\x00" * 2048)
        self.v2.write_bytes(b"\x00" * 2048)
        self.v3.write_bytes(b"\x00" * 2048)

        # Khởi tạo Tk root ẩn cho kiểm thử CustomTkinter
        try:
            self.root = ctk.CTk()
            self.root.withdraw()
        except Exception:
            self.root = None

    def tearDown(self):
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
        self.patch_info.stop()
        self.patch_warn.stop()
        self.patch_err.stop()
        self.patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_modal(self, profiles=None, on_success=None):
        if not self.root:
            self.skipTest("Tkinter display not available")
        p_list = ["Profile_Alpha", "Profile_Beta"] if profiles is None else list(profiles)
        modal = BulkScheduleModal(
            parent=self.root,
            selected_profiles=p_list,
            profile_configs={
                "Profile_Alpha": {"tiktok_account": "acc_alpha", "timezone": "Asia/Ho_Chi_Minh"},
                "Profile_Beta": {"tiktok_account": "acc_beta", "timezone": "Asia/Ho_Chi_Minh"},
            },
            schedule_manager=self.mgr,
            on_success=on_success,
        )
        return modal

    @patch("schedule_service.batch_probe_videos")
    def test_p3_01_preview_generates_valid_plan_and_matrix_rows(self, mock_probe):
        """P3-01: Xem trước (Preview) sinh đúng SchedulePlan, ConflictSnapshot và đổ đầy đủ 100% cột ma trận Treeview."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
            VideoProbeResult(
                canonical_path=str(self.v2.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v2.stat().st_mtime_ns),
                duration_seconds=20.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1, self.v2])

        # Trước khi preview: chưa có plan và nút submit ở trạng thái ban đầu
        self.assertIsNone(modal._current_plan)
        self.assertIsNone(modal._current_snapshot)

        # Bấm tạo xem trước
        modal._generate_preview()

        # 1. Kiểm chứng kế hoạch bất biến và snapshot được gắn kết đầy đủ
        self.assertIsNotNone(modal._current_plan)
        self.assertIsInstance(modal._current_plan, SchedulePlan)
        self.assertEqual(modal._current_plan.total_planned_tasks, 2)
        self.assertEqual(modal._committed_revision, modal._input_revision)

        self.assertIsNotNone(modal._current_snapshot)
        self.assertIsInstance(modal._current_snapshot, ConflictSnapshot)
        self.assertTrue(bool(modal._current_snapshot.snapshot_fingerprint))

        # 2. Đối chiếu 100% chi tiết các dòng trong ma trận Treeview với PlannedTasks trong plan
        tree_children = modal.preview_tree.get_children()
        self.assertEqual(len(tree_children), 2, "Ma trận Treeview phải hiển thị đúng 2 dòng bài đăng dự kiến!")

        all_planned = []
        for p_name, t_list in modal._current_plan.profile_assignments.items():
            all_planned.extend(t_list)
        all_planned.sort(key=lambda x: x.scheduled_epoch)

        for i, item_id in enumerate(tree_children):
            vals = modal.preview_tree.item(item_id)["values"]
            expected_task = all_planned[i]
            # Cột 0: Profile
            self.assertEqual(vals[0], expected_task.profile_name)
            # Cột 1: Local time
            self.assertEqual(vals[1], expected_task.scheduled_time_local)
            # Cột 2: Slot ID
            self.assertEqual(vals[2], expected_task.slot_id)
            # Cột 3: Filename
            self.assertEqual(vals[3], expected_task.video_filename)
            # Cột 4: Title
            self.assertEqual(vals[4], expected_task.title)

        # Nút submit được chuyển sang trạng thái sẵn sàng commit
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")
        self.assertEqual(modal.btn_submit.cget("state"), "normal")
        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_02_modifying_all_input_variables_invalidates_preview(self, mock_probe):
        """P3-02: Bất kỳ thay đổi đầu vào nào (11 biến cấu hình + danh sách video) đều phải vô hiệu hóa preview hiện tại."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1])

        def assert_invalidated_after_action(action_fn, desc):
            modal._generate_preview()
            self.assertIsNotNone(modal._current_plan, f"Chuẩn bị test {desc} thất bại.")
            self.assertIsNotNone(modal._current_snapshot)
            self.assertEqual(modal._committed_revision, modal._input_revision)

            # Thực thi thay đổi
            action_fn()

            # Xác nhận state bị vô hiệu hóa hoàn toàn
            self.assertIsNone(modal._current_plan, f"Khi {desc}, _current_plan phải bị reset về None!")
            self.assertIsNone(modal._current_snapshot, f"Khi {desc}, _current_snapshot phải bị reset về None!")
            self.assertEqual(modal._committed_revision, -1, f"Khi {desc}, _committed_revision phải bị reset về -1!")
            self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")

        # 1. formula_var
        assert_invalidated_after_action(lambda: modal.formula_var.set("{filename} - Part {n}"), "đổi formula_var")
        # 2. resolution_mode_var
        assert_invalidated_after_action(lambda: modal.resolution_mode_var.set("OVERWRITE"), "đổi resolution_mode_var")
        # 3. order_mode_var
        assert_invalidated_after_action(lambda: modal.order_mode_var.set("SHUFFLE"), "đổi order_mode_var")
        # 4. daily_max_var
        assert_invalidated_after_action(lambda: modal.daily_max_var.set("4"), "đổi daily_max_var")
        # 5. jitter_var
        assert_invalidated_after_action(lambda: modal.jitter_var.set("25"), "đổi jitter_var")
        # 6. exec_mode_var
        assert_invalidated_after_action(lambda: modal.exec_mode_var.set("LOCAL_QUEUE"), "đổi exec_mode_var")
        # 7. delete_source_var
        assert_invalidated_after_action(lambda: modal.delete_source_var.set(False), "đổi delete_source_var")
        # 8. slot_sang_var
        assert_invalidated_after_action(lambda: modal.slot_sang_var.set(False), "đổi slot_sang_var")
        # 9. slot_trua_var
        assert_invalidated_after_action(lambda: modal.slot_trua_var.set(False), "đổi slot_trua_var")
        # 10. slot_chieu_var
        assert_invalidated_after_action(lambda: modal.slot_chieu_var.set(False), "đổi slot_chieu_var")
        # 11. slot_toi_var
        assert_invalidated_after_action(lambda: modal.slot_toi_var.set(False), "đổi slot_toi_var")
        # 12. Thay đổi danh sách video sau preview
        assert_invalidated_after_action(lambda: modal._set_scanned_videos([self.v1, self.v2]), "thêm video mới")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_03_commit_persists_atomic_plan_to_sqlite_full_100pct_fields_match(self, mock_probe):
        """P3-03: Commit lưu trữ nguyên tử và đối chiếu 100% tất cả các trường trong DB SQLite với PlannedTasks (kèm fixture trùng tên khác thư mục - Codex Fix)."""
        # Tạo fixture 2 video có CÙNG TÊN FILE 'same_name.mp4' nhưng ở 2 thư mục khác nhau (Codex Round 3 Fix)
        dir_a = self.video_dir / "folder_a"
        dir_b = self.video_dir / "folder_b"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)
        v_a = dir_a / "same_name.mp4"
        v_b = dir_b / "same_name.mp4"
        v_a.write_bytes(b"\x00" * 1024)
        v_b.write_bytes(b"\x00" * 2048)

        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(v_a.resolve()),
                size_bytes=1024,
                mtime_ns=int(v_a.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
            VideoProbeResult(
                canonical_path=str(v_b.resolve()),
                size_bytes=2048,
                mtime_ns=int(v_b.stat().st_mtime_ns),
                duration_seconds=20.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
        ]

        on_success_mock = MagicMock()
        modal = self._create_modal(on_success=on_success_mock)
        modal._set_scanned_videos([v_a, v_b])

        # Preview
        modal._generate_preview()
        plan = modal._current_plan
        plan_id = plan.plan_id

        # Commit
        modal._do_schedule()

        # 1. Kiểm chứng dữ liệu thật trong SQLite khớp 100% tất cả các trường với PlannedTasks
        tasks = self.mgr.get_all_tasks()
        self.assertEqual(len(tasks), 2, "Phải có đúng 2 task được commit vào SQLite!")

        all_planned = []
        for p_name, t_list in plan.profile_assignments.items():
            all_planned.extend(t_list)

        # Dùng canonical_video_path làm khóa đối soát chính xác tuyệt đối (phân biệt 2 video trùng tên ở 2 folder khác nhau)
        planned_by_canon_path = {t.canonical_video_path: t for t in all_planned}
        self.assertEqual(len(planned_by_canon_path), 2, "2 video trùng filename nhưng khác folder phải tạo 2 canonical key rời rạc!")

        # Đối soát song ánh 1-1 giữa tập canonical_video_path trong SQLite và PlannedTask (Codex Round 4 Fix)
        stored_paths = [t["canonical_video_path"] for t in tasks]
        self.assertEqual(len(set(stored_paths)), 2, "Các canonical path lưu trong SQLite không được trùng lặp!")
        self.assertEqual(set(stored_paths), set(planned_by_canon_path), "Tập canonical path trong SQLite phải trùng khớp 100% với plan!")

        # Lấy toàn bộ danh sách cột thực tế của bảng scheduled_tasks trong SQLite qua PRAGMA table_info
        conn = self.mgr._get_connection()
        try:
            columns_info = conn.execute("PRAGMA table_info(scheduled_tasks);").fetchall()
            db_column_names = {r["name"] for r in columns_info}
        finally:
            conn.close()

        # Đối chiếu schema cố định đúng chính xác 36 cột kỳ vọng độc lập (Codex Round 4 Fix)
        EXPECTED_SCHEMA_COLUMNS = {
            'id', 'profile_name', 'tiktok_account', 'video_path', 'video_filename', 'title',
            'scheduled_time', 'scheduled_epoch', 'golden_hour_slot', 'execution_mode', 'status',
            'upload_engine', 'delete_source_after_post', 'source_deleted', 'tiktok_item_id',
            'retry_count', 'max_retries', 'error_message', 'created_at', 'updated_at',
            'completed_at', 'claim_worker_id', 'claimed_at', 'lease_until', 'version',
            'incarnation_id', 'cancelled_by_plan_id', 'plan_id', 'slot_id', 'last_attempt_id',
            'remote_project_id', 'published_at', 'profile_timezone', 'canonical_video_path',
            'source_size_bytes', 'source_mtime_ns'
        }
        self.assertEqual(db_column_names, EXPECTED_SCHEMA_COLUMNS, "PRAGMA table_info phải đúng chính xác 36 cột schema!")

        task_ids = set()
        for t in tasks:
            # Chứng minh 100% tập keys của bản ghi SQLite khớp tuyệt đối với schema PRAGMA
            self.assertEqual(set(t.keys()), db_column_names, "Mỗi bản ghi đọc từ SQLite phải có tập key trùng khớp 100% với PRAGMA table_info!")

            c_path = t["canonical_video_path"]
            self.assertIn(c_path, planned_by_canon_path)
            pt = planned_by_canon_path[c_path]

            # Đối chiếu ID và incarnation_id với định dạng chuẩn regex và tính duy nhất
            self.assertRegex(t["id"], r"^sched_\d{14}_[a-f0-9]{8}$", "Task ID phải theo chuẩn sched_<timestamp>_<hex8>!")
            task_ids.add(t["id"])
            self.assertRegex(t["incarnation_id"], r"^inc_[a-f0-9]{32}$", "Incarnation ID phải theo chuẩn inc_<uuid_hex32>!")

            # Đối chiếu 100% tất cả các cột nghiệp vụ với PlannedTask
            self.assertEqual(t["profile_name"], pt.profile_name)
            self.assertEqual(t["tiktok_account"], pt.tiktok_account)
            self.assertEqual(t["video_path"], pt.video_path)
            self.assertEqual(t["canonical_video_path"], pt.canonical_video_path)
            self.assertEqual(t["video_filename"], pt.video_filename)
            self.assertEqual(t["title"], pt.title)
            self.assertEqual(t["scheduled_time"], pt.scheduled_time_local)
            self.assertEqual(t["scheduled_epoch"], pt.scheduled_epoch)
            self.assertEqual(t["golden_hour_slot"], pt.slot_id)
            self.assertEqual(t["execution_mode"], pt.execution_mode)
            self.assertEqual(t["status"], "PENDING")
            self.assertEqual(t["upload_engine"], "DIRECT_API")
            self.assertEqual(bool(t["delete_source_after_post"]), bool(pt.delete_source_after_post))
            self.assertEqual(bool(t["source_deleted"]), False)
            self.assertEqual(t["retry_count"], 0)
            self.assertEqual(t["max_retries"], 2)
            self.assertEqual(t["plan_id"], plan_id)
            self.assertEqual(t["slot_id"], pt.slot_id)
            self.assertEqual(t["profile_timezone"], pt.profile_timezone)
            self.assertEqual(t["source_size_bytes"], pt.source_size_bytes)
            self.assertEqual(t["source_mtime_ns"], pt.source_mtime_ns)
            self.assertEqual(t["version"], 1)
            self.assertEqual(t["cancelled_by_plan_id"], "")
            self.assertEqual(t["tiktok_item_id"], "")
            self.assertEqual(t["error_message"], "")
            self.assertIsNone(t["completed_at"])
            self.assertEqual(t["claim_worker_id"], "")
            self.assertIsNone(t["claimed_at"])
            self.assertEqual(t["lease_until"], 0)
            self.assertEqual(t["last_attempt_id"], "")
            self.assertEqual(t["remote_project_id"], "")
            self.assertEqual(t["published_at"], 0)
            self.assertRegex(t["created_at"], r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", "created_at phải theo định dạng ISO timestamp!")
            self.assertRegex(t["updated_at"], r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", "updated_at phải theo định dạng ISO timestamp!")

        self.assertEqual(len(task_ids), 2, "Các task ID sinh ra phải phân biệt độc lập hoàn toàn!")

        # 2. Kiểm chứng callback on_success nhận đúng danh sách ID thực tế trong DB
        on_success_mock.assert_called_once()
        cb_ids = on_success_mock.call_args[0][0]
        db_ids = [t["id"] for t in tasks]
        self.assertEqual(set(cb_ids), set(db_ids))
        self.mock_info.assert_called_once()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_04_stale_plan_detected_proves_clear_before_refresh_and_no_stale_retention(self, mock_probe):
        """P3-04: Chứng minh xóa sạch state trước khi refresh trong stale handler; nếu refresh thất bại thì không sót state (Codex Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal.resolution_mode_var.set("OVERWRITE")
        modal._set_scanned_videos([self.v1])

        # Bước 1: Preview tại thời điểm DB rỗng
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)

        # Bước 2: Một tiến trình khác thêm task vào Profile_Alpha trong DB (làm thay đổi fingerprint)
        self.mgr.add_tasks([{
            "id": "task_external_writer_01",
            "profile_name": "Profile_Alpha",
            "video_path": "C:/ext.mp4",
            "scheduled_time": "2026-10-26T10:00:00",
            "status": "PENDING",
        }])

        # Bước 3: Intercept _generate_preview để chứng minh state đã được clear trước khi refresh
        # và giả lập refresh thất bại ngay trong nhánh stale handler (Codex Point 2 Fix)
        state_at_refresh_entry = {}
        orig_gen_preview = modal._generate_preview

        def intercept_refresh():
            state_at_refresh_entry["plan"] = modal._current_plan
            state_at_refresh_entry["snapshot"] = modal._current_snapshot
            state_at_refresh_entry["rev"] = modal._committed_revision
            # Ném lỗi để refresh thất bại
            raise RuntimeError("Simulated refresh network glitch during stale handling")

        with patch.object(modal, "_generate_preview", side_effect=intercept_refresh):
            modal._do_schedule()

        # Kiểm chứng StalePlanError đã kích hoạt cảnh báo
        self.mock_warn.assert_called_once()

        # Kiểm chứng tại thời điểm bắt đầu refresh: state cũ đã bị XÓA SẠCH 100%
        self.assertIsNone(state_at_refresh_entry["plan"], "Trước khi refresh, plan cũ phải bị xóa về None!")
        self.assertIsNone(state_at_refresh_entry["snapshot"], "Trước khi refresh, snapshot cũ phải bị xóa về None!")
        self.assertEqual(state_at_refresh_entry["rev"], -1, "Trước khi refresh, committed_revision phải là -1!")

        # Kiểm chứng sau khi refresh thất bại: state vẫn là None và nút không ở trạng thái Commit (Codex Point 2 Fix)
        self.assertIsNone(modal._current_plan)
        self.assertIsNone(modal._current_snapshot, "Sau khi refresh thất bại, _current_snapshot phải là None!")
        self.assertEqual(modal._committed_revision, -1)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")
        self.assertEqual(modal.btn_submit.cget("state"), "normal")

        # Kiểm chứng trong DB: chỉ có task của external writer, không có task nào bị commit bậy bạ
        current_tasks = self.mgr.get_all_tasks()
        self.assertEqual(len(current_tasks), 1)
        self.assertEqual(current_tasks[0]["id"], "task_external_writer_01")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_04b_stale_plan_real_refresh_success_and_recommit(self, mock_probe):
        """P3-04b: StalePlanError kích hoạt tự động làm mới Preview THẬT, cập nhật snapshot mới và commit thành công (Codex Round 9 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal.resolution_mode_var.set("OVERWRITE")
        modal._set_scanned_videos([self.v1])

        # Bước 1: Preview khi DB rỗng
        modal._generate_preview()
        old_plan = modal._current_plan
        self.assertIsNotNone(old_plan)

        # Bước 2: Một writer khác thêm task vào DB, làm thay đổi snapshot fingerprint
        self.mgr.add_tasks([{
            "id": "task_concurrent_01",
            "profile_name": "Profile_Alpha",
            "video_path": "C:/concurrent.mp4",
            "scheduled_time": "2026-10-26T10:00:00",
            "status": "PENDING",
        }])

        # Bước 3: Người dùng bấm Commit -> StalePlanError xảy ra -> _generate_preview THẬT được gọi sau khi giải phóng cờ _is_committing (Codex Round 9 Fix)
        modal._do_schedule()

        # Cảnh báo Stale được kích hoạt
        self.mock_warn.assert_called_once()

        # Khẳng định sau khi refresh thật: modal có Plan MỚI và nút trở lại trạng thái Commit
        self.assertIsNotNone(modal._current_plan, "Sau khi refresh thật, modal phải có plan mới!")
        self.assertIsNot(modal._current_plan, old_plan, "Plan mới phải khác plan cũ đã bị stale!")
        self.assertEqual(modal._committed_revision, modal._input_revision)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")

        # Khẳng định snapshot mới đã nhận biết task của concurrent writer
        new_snap = modal._current_snapshot
        self.assertIsNotNone(new_snap)
        self.assertIn("Profile_Alpha", new_snap.conflicted_profiles)

        # Bước 4: Người dùng bấm Commit lần 2 với plan đã được refresh -> Commit thành công 100%!
        modal._do_schedule()

        # Trong chế độ OVERWRITE, task cũ bị hủy và task mới được ghi vào DB
        tasks = self.mgr.get_all_tasks()
        old_t = self.mgr.get_task_by_id("task_concurrent_01")
        self.assertEqual(old_t["status"], "CANCELLED")
        new_tasks = [t for t in tasks if t["id"] != "task_concurrent_01"]
        self.assertEqual(len(new_tasks), 1)
        self.assertEqual(new_tasks[0]["status"], "PENDING")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_04c_stale_plan_popup_exception_does_not_abort_refresh_or_recommit(self, mock_probe):
        """P3-04c: Khi popup showwarning gặp sự cố ném Exception, luồng làm mới Preview THẬT vẫn không bị đứt đoạn và recommit thành công (Codex Round 11 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal.resolution_mode_var.set("OVERWRITE")
        modal._set_scanned_videos([self.v1])

        # Bước 1: Preview khi DB rỗng
        modal._generate_preview()
        old_plan = modal._current_plan
        self.assertIsNotNone(old_plan)

        # Bước 2: Một tiến trình ngoại vi thêm task vào DB làm lệch fingerprint
        self.mgr.add_tasks([{
            "id": "task_concurrent_02",
            "profile_name": "Profile_Alpha",
            "video_path": "C:/concurrent2.mp4",
            "scheduled_time": "2026-10-26T11:00:00",
            "status": "PENDING",
        }])

        # Bước 3: Giả lập messagebox.showwarning ném exception (ví dụ window manager / desktop subsystem glitch)
        self.mock_warn.side_effect = RuntimeError("Simulated window manager failure on warning dialog")

        # Bấm Commit -> StalePlanError -> showwarning ném lỗi nhưng được bọc try/except an toàn -> should_refresh_stale vẫn chạy!
        modal._do_schedule()

        # Khẳng định popup showwarning mang side_effect ném lỗi thực sự được gọi (Codex Round 12 Fix)
        self.mock_warn.assert_called_once()

        # Khẳng định: Dù popup lỗi, modal vẫn tự động refresh tạo plan mới thành công
        self.assertIsNotNone(modal._current_plan, "Dù popup lỗi, modal vẫn phải refresh có plan mới!")
        self.assertIsNot(modal._current_plan, old_plan)
        self.assertEqual(modal._committed_revision, modal._input_revision)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")

        # Khẳng định snapshot mới nhận diện xung đột
        self.assertIn("Profile_Alpha", modal._current_snapshot.conflicted_profiles)

        # Bước 4: Reset mock_warn và recommit lần 2 thành công 100%
        self.mock_warn.side_effect = None
        modal._do_schedule()

        old_t = self.mgr.get_task_by_id("task_concurrent_02")
        self.assertEqual(old_t["status"], "CANCELLED")
        active_tasks = [t for t in self.mgr.get_all_tasks() if t["status"] == "PENDING"]
        self.assertEqual(len(active_tasks), 1)

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_05_overwrite_mode_cancels_targets_and_verifies_100pct_record_preservation_of_other_profiles(self, mock_probe):
        """P3-05: Chế độ OVERWRITE hủy task cũ mục tiêu và đối chiếu 100% nguyên vẹn toàn bộ bản ghi Profile_Other (Codex Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        # Tạo trước 2 task cũ đang chờ trong DB của Profile_Alpha
        # Và 1 task của Profile_Other (nằm ngoài phạm vi modal)
        self.mgr.add_tasks([
            {
                "id": "task_old_alpha_01",
                "profile_name": "Profile_Alpha",
                "video_path": "C:/vids/old1.mp4",
                "scheduled_time": "2026-10-26T10:00:00",
                "status": "PENDING",
            },
            {
                "id": "task_old_alpha_02",
                "profile_name": "Profile_Alpha",
                "video_path": "C:/vids/old2.mp4",
                "scheduled_time": "2026-10-26T14:00:00",
                "status": "PENDING",
            },
            {
                "id": "task_preserved_other_99",
                "profile_name": "Profile_Other",
                "video_path": "C:/vids/other.mp4",
                "scheduled_time": "2026-10-26T18:00:00",
                "status": "PENDING",
            },
        ])

        # Ghi lại toàn bộ 100% dictionary của task Profile_Other trước khi commit
        orig_other_task_record = dict(self.mgr.get_task_by_id("task_preserved_other_99"))

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal.resolution_mode_var.set("OVERWRITE")
        modal._set_scanned_videos([self.v1])

        # Preview
        modal._generate_preview()

        # Kiểm tra badge cảnh báo xung đột hiển thị GHI ĐÈ
        badge_text = modal.lbl_conflict_badge.cget("text")
        self.assertIn("GHI ĐÈ (OVERWRITE)", badge_text)
        self.assertIn("HỦY toàn bộ video đang chờ", badge_text)

        # Commit
        modal._do_schedule()

        # 1. Kiểm chứng 2 task của Profile_Alpha bị CANCELLED
        old1 = self.mgr.get_task_by_id("task_old_alpha_01")
        old2 = self.mgr.get_task_by_id("task_old_alpha_02")
        self.assertEqual(old1["status"], "CANCELLED")
        self.assertEqual(old2["status"], "CANCELLED")
        self.assertEqual(old1["version"], 2)
        self.assertEqual(old2["version"], 2)

        # 2. Kiểm chứng task mới của Profile_Alpha được tạo đúng với status PENDING (Codex Fix)
        all_tasks = self.mgr.get_all_tasks()
        new_alpha_tasks = [t for t in all_tasks if t["id"] not in ("task_old_alpha_01", "task_old_alpha_02", "task_preserved_other_99")]
        self.assertEqual(len(new_alpha_tasks), 1)
        self.assertEqual(new_alpha_tasks[0]["profile_name"], "Profile_Alpha")
        self.assertEqual(new_alpha_tasks[0]["status"], "PENDING")

        # 3. Kiểm chứng 100% bản ghi của Profile_Other hoàn toàn NGUYÊN VẸN KHÔNG ĐỔI MỘT BYTE NÀO (Codex Fix)
        actual_other_task_record = dict(self.mgr.get_task_by_id("task_preserved_other_99"))
        self.assertEqual(actual_other_task_record, orig_other_task_record, "Toàn bộ trường của Profile_Other phải khớp 100% ban đầu!")

    @patch("schedule_service.batch_probe_videos")
    def test_p3_06_double_click_commit_guard_on_valid_preview(self, mock_probe):
        """P3-06: Tránh gửi lặp (Debounce Guard) khi commit đang diễn ra sau khi đã có preview hợp lệ (Codex Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1])

        # Tạo preview hợp lệ trước
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)
        self.assertEqual(modal._committed_revision, modal._input_revision)

        # Giả lập thao tác đang commit (_is_committing = True)
        modal._is_committing = True

        with patch("schedule_ui.commit_schedule_plan") as mock_commit:
            modal._do_schedule()
            # Khẳng định commit_schedule_plan không được gọi và không có dữ liệu nào ghi vào SQLite
            mock_commit.assert_not_called()

        tasks = self.mgr.get_all_tasks()
        self.assertEqual(len(tasks), 0, "Không có task nào được lưu khi bị chặn bởi debounce guard!")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_07_render_failure_proves_state_never_published_during_render(self, mock_probe):
        """P3-07: Chứng minh state bị xóa ngay đầu preview 2 dù đã có preview hợp lệ trước đó, nhiều insert thành công và lỗi ở insert sau không làm rò rỉ state (Codex Point 3 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
            VideoProbeResult(
                canonical_path=str(self.v2.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v2.stat().st_mtime_ns),
                duration_seconds=20.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
            VideoProbeResult(
                canonical_path=str(self.v3.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v3.stat().st_mtime_ns),
                duration_seconds=25.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1, self.v2, self.v3])

        # Bước 1: Preview lần 1 thành công hoàn chỉnh -> có valid plan và nút Commit
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan, "Preview lần 1 phải tạo plan hợp lệ!")
        self.assertEqual(modal._committed_revision, modal._input_revision)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")

        # Bước 2: Tạo preview lần 2. Spy Treeview.insert để chứng minh:
        # - State của preview lần 1 đã bị xóa sạch về None/-1 NGAY ĐẦU HÀM _generate_preview()
        # - Cho phép insert 1 và 2 thành công, chỉ ném lỗi ở insert 3
        states_during_inserts = []
        orig_insert = modal.preview_tree.insert
        insert_call_count = 0

        def spy_insert(*args, **kwargs):
            nonlocal insert_call_count
            insert_call_count += 1
            states_during_inserts.append({
                "call": insert_call_count,
                "plan": modal._current_plan,
                "snapshot": modal._current_snapshot,
                "rev": modal._committed_revision,
            })
            if insert_call_count >= 3:
                raise RuntimeError("Simulated GUI render glitch during tree insert 3")
            return orig_insert(*args, **kwargs)

        with patch.object(modal.preview_tree, "insert", side_effect=spy_insert):
            modal._generate_preview()

        # Kiểm chứng: Có ít nhất 3 lượt gọi insert và ở MỌI lượt insert, state cũ từ Preview 1 ĐỀU BỊ XÓA VỀ NONE
        self.assertGreaterEqual(len(states_during_inserts), 3)
        for s in states_during_inserts:
            self.assertIsNone(s["plan"], f"Tại insert #{s['call']}, _current_plan phải là None!")
            self.assertIsNone(s["snapshot"], f"Tại insert #{s['call']}, _current_snapshot phải là None!")
            self.assertEqual(s["rev"], -1, f"Tại insert #{s['call']}, _committed_revision phải là -1!")

        # Khẳng định sau exception ở insert 3: state vẫn là None và nút quay về 1-Click
        self.assertIsNone(modal._current_plan)
        self.assertIsNone(modal._current_snapshot)
        self.assertEqual(modal._committed_revision, -1)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")

        # Khẳng định không thể commit sau lỗi render
        with patch("schedule_ui.commit_schedule_plan") as mock_commit:
            modal._do_schedule()
            mock_commit.assert_not_called()

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_07b_failed_preview_during_do_schedule_preserves_error_message_without_false_success(self, mock_probe):
        """P3-07b: Khi _do_schedule tự động kích hoạt preview do chưa có plan, nếu preview thất bại thì thông báo lỗi được bảo toàn nguyên vẹn, không bị ghi đè thông báo thành công (Codex Round 11 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # Modal chưa từng được preview: _current_plan là None
        self.assertIsNone(modal._current_plan)

        # Mô phỏng lỗi trong quá trình tính toán hoặc render preview (patch schedule_ui.prepare_schedule_plan ném lỗi)
        with patch("schedule_ui.prepare_schedule_plan", side_effect=RuntimeError("Simulated engine calculation failure")):
            modal._do_schedule()

        # Khẳng định:
        # 1. Thông báo lỗi thực tế được bảo toàn nguyên vẹn bao gồm cả chi tiết ngoại lệ (Codex Round 12 Fix)
        current_status = modal.status_info_var.get()
        self.assertIn("❌ Lỗi khi tính toán xem trước", current_status)
        self.assertIn("Simulated engine calculation failure", current_status)
        self.assertNotIn("ℹ️ Đã tạo bản xem trước mới", current_status, "Lỗi preview không được bị ghi đè thành thông báo xem trước mới thành công!")

        # 2. _current_plan vẫn là None
        self.assertIsNone(modal._current_plan)

        # 3. Nút submit không đổi thành commit
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")

        # 4. Không có lệnh commit nào chạy
        self.assertEqual(len(self.mgr.get_all_tasks()), 0)

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_08_callback_exception_does_not_trigger_db_error_popup_nor_double_execute(self, mock_probe):
        """P3-08: Lỗi bên trong callback on_success không làm chạy lại callback lần hai và không báo nhầm lỗi lưu DB (Codex P2 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        callback_call_count = 0

        def buggy_caller_callback(created_ids):
            nonlocal callback_call_count
            callback_call_count += 1
            # Cố tình ném TypeError không liên quan đến signature (ví dụ bug nội bộ của caller)
            raise TypeError("Caller internal bug inside callback!")

        modal = self._create_modal(on_success=buggy_caller_callback)
        modal._set_scanned_videos([self.v1])
        modal._generate_preview()

        # Thực thi commit
        modal._do_schedule()

        # 1. DB phải commit thành công 100%
        tasks = self.mgr.get_all_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "PENDING")

        # 2. Callback chỉ chạy đúng 1 lần duy nhất (không bị khối except TypeError gọi lại lần 2!)
        self.assertEqual(callback_call_count, 1)

        # 3. Popup báo thành công phải xuất hiện, và popup báo lỗi DB không được hiển thị!
        self.mock_info.assert_called_once()
        self.mock_err.assert_not_called()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_09_keyword_only_callback_binding_safety(self, mock_probe):
        """P3-09: Kiểm chứng an toàn callback đa dạng: keyword-only có mặc định, keyword-only bắt buộc, no-arg, positional (Codex Point 5 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        # Case A: Keyword-only có giá trị mặc định (*, refresh=True)
        kw_default_called = False
        def kw_default_callback(*, refresh=True):
            nonlocal kw_default_called
            kw_default_called = True

        modal_a = self._create_modal(on_success=kw_default_callback)
        modal_a._set_scanned_videos([self.v1])
        modal_a._generate_preview()
        modal_a._do_schedule()
        self.assertTrue(kw_default_called, "Keyword-only có default phải được bind an toàn!")

        # Case B: Keyword-only BẮT BUỘC (*, created_tasks) - Đối chiếu 100% ID thực tế (Codex Round 4 Fix)
        ids_before_b = set(t["id"] for t in self.mgr.get_all_tasks())
        kw_tasks_received = None
        def kw_mandatory_tasks_callback(*, created_tasks):
            nonlocal kw_tasks_received
            kw_tasks_received = created_tasks

        modal_b = self._create_modal(on_success=kw_mandatory_tasks_callback)
        modal_b._set_scanned_videos([self.v1])
        modal_b._generate_preview()
        modal_b._do_schedule()
        ids_after_b = set(t["id"] for t in self.mgr.get_all_tasks())
        new_ids_b = ids_after_b - ids_before_b
        self.assertEqual(len(new_ids_b), 1, "Phải sinh ra đúng 1 task ID mới trong SQLite!")
        self.assertEqual(kw_tasks_received, list(new_ids_b), "created_tasks phải nhận đúng 100% ID thực tế vừa được commit trong SQLite!")

        # Case C: Keyword-only BẮT BUỘC (*, created_task_ids) - Đối chiếu 100% ID thực tế (Codex Round 4 Fix)
        ids_before_c = set(t["id"] for t in self.mgr.get_all_tasks())
        kw_ids_received = None
        def kw_mandatory_ids_callback(*, created_task_ids):
            nonlocal kw_ids_received
            kw_ids_received = created_task_ids

        modal_c = self._create_modal(on_success=kw_mandatory_ids_callback)
        modal_c._set_scanned_videos([self.v1])
        modal_c._generate_preview()
        modal_c._do_schedule()
        ids_after_c = set(t["id"] for t in self.mgr.get_all_tasks())
        new_ids_c = ids_after_c - ids_before_c
        self.assertEqual(len(new_ids_c), 1, "Phải sinh ra đúng 1 task ID mới trong SQLite!")
        self.assertEqual(kw_ids_received, list(new_ids_c), "created_task_ids phải nhận đúng 100% ID thực tế vừa được commit trong SQLite!")

        # Case D: Callback không nhận tham số def no_arg_callback()
        no_arg_called = False
        def no_arg_callback():
            nonlocal no_arg_called
            no_arg_called = True

        modal_d = self._create_modal(on_success=no_arg_callback)
        modal_d._set_scanned_videos([self.v1])
        modal_d._generate_preview()
        modal_d._do_schedule()
        self.assertTrue(no_arg_called, "Callback không tham số phải được gọi an toàn!")

        # Không có popup lỗi nào xuất hiện trong cả 4 case
        self.mock_err.assert_not_called()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_10_post_commit_ui_failure_does_not_trigger_db_error_popup(self, mock_probe):
        """P3-10: Lỗi giao diện (popup/đóng modal) sau commit không báo nhầm lỗi DB, và modal còn sống chặn gửi lại commit lặp (Codex Round 5/6 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        cb_received_ids = []
        def track_callback(task_ids):
            cb_received_ids.extend(task_ids)

        modal = self._create_modal(on_success=track_callback)
        modal._set_scanned_videos([self.v1])
        modal._generate_preview()

        # Giả lập lỗi ở cả bước thông báo thành công (messagebox.showinfo) và bước đóng modal (_cancel)
        mock_showinfo = MagicMock(side_effect=RuntimeError("Simulated OS GUI popup display failure"))
        mock_cancel = MagicMock(side_effect=RuntimeError("Simulated modal destroy failure"))

        with patch("tkinter.messagebox.showinfo", mock_showinfo), \
             patch.object(modal, "_cancel", mock_cancel):
            modal._do_schedule()

        # 1. Khẳng định: Cả popup thông báo và lệnh đóng modal đều đã thực sự được gọi (Codex Round 6 Fix)
        mock_showinfo.assert_called_once()
        mock_cancel.assert_called_once()

        # 2. Khẳng định: Dữ liệu SQLite ĐÃ ĐƯỢC LƯU THÀNH CÔNG 100%
        tasks = self.mgr.get_all_tasks()
        self.assertEqual(len(tasks), 1, "Dữ liệu phải được commit thành công vào SQLite!")
        self.assertEqual(tasks[0]["status"], "PENDING")

        # 3. Khẳng định: Tuyệt đối KHÔNG hiển thị popup 'Lỗi Lưu Dữ Liệu' gây hiểu nhầm cho người dùng
        self.mock_err.assert_not_called()

        # 4. Khẳng định: Callback on_success được gọi đúng 1 lần và nhận đúng task ID thực tế
        self.assertEqual(len(cb_received_ids), 1)
        self.assertEqual(cb_received_ids[0], tasks[0]["id"])

        # 5. Khẳng định: Modal còn mở nhưng trạng thái đã đánh dấu commit_succeeded, cả 2 nút Submit và Preview đều bị vô hiệu hóa (Codex Round 7 Fix)
        self.assertTrue(modal._commit_succeeded)
        self.assertEqual(modal.btn_submit.cget("state"), "disabled")
        self.assertEqual(modal.btn_submit.cget("text"), "✅ Đã Lên Lịch Thành Công")
        self.assertEqual(modal.btn_preview.cget("state"), "disabled")

        # 6. Khẳng định: Terminal Latch chặn toàn bộ chuỗi thao tác: đổi input -> Preview -> Commit lại (Codex Round 7 Fix)
        # 6a. Thay đổi input không làm mở lại latch
        modal.formula_var.set("hacked_formula_{filename}")
        self.assertTrue(modal._commit_succeeded, "Latch phải giữ nguyên True khi đổi input sau commit thành công!")
        self.assertIsNone(modal._current_plan, "Không được phép phục hồi plan cũ!")

        # 6b. Bấm Preview bị chặn hoàn toàn
        modal._generate_preview()
        self.assertIsNone(modal._current_plan, "Preview phải bị chặn hoàn toàn khi commit_succeeded đã kích hoạt!")

        # 6c. Người dùng bấm _do_schedule lần nữa khi modal còn mở -> BỊ CHẶN HOÀN TOÀN
        with patch("schedule_ui.commit_schedule_plan") as mock_second_commit:
            modal._do_schedule()
            mock_second_commit.assert_not_called()

        # SQLite không bị thêm task trùng lặp và callback không chạy thêm lần nào
        self.assertEqual(len(self.mgr.get_all_tasks()), 1, "Không được sinh thêm task trùng lặp trong SQLite!")
        self.assertEqual(len(cb_received_ids), 1, "Callback không được gọi lại lần 2!")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_10b_submit_button_error_in_finally_does_not_prevent_preview_button_from_being_disabled(self, mock_probe):
        """P3-10b: Khối finally bọc tách biệt độc lập từng nút: nếu btn_submit.configure ném exception thì btn_preview vẫn được disable 100% (Codex Round 13 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1])
        modal._generate_preview()

        # Giả lập btn_submit.configure ném lỗi khi cập nhật trạng thái disabled trong finally
        orig_submit_config = modal.btn_submit.configure
        def faulty_submit_config(*args, **kwargs):
            if kwargs.get("text") == "✅ Đã Lên Lịch Thành Công":
                raise RuntimeError("Simulated Tkinter submit widget crash during configure")
            return orig_submit_config(*args, **kwargs)

        with patch.object(modal.btn_submit, "configure", side_effect=faulty_submit_config):
            with patch("tkinter.messagebox.showinfo"), patch.object(modal, "_cancel"):
                modal._do_schedule()

        # Khẳng định: Dù btn_submit lỗi, khối try...except độc lập kế tiếp vẫn disable thành công btn_preview
        self.assertTrue(modal._commit_succeeded)
        self.assertEqual(modal.btn_preview.cget("state"), "disabled")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_11_pre_commit_ui_setup_failure_releases_committing_guard(self, mock_probe):
        """P3-11: Lỗi trong giai đoạn thiết lập UI trước commit (update_idletasks) báo đúng 'Lỗi Giao Diện', không đụng vào DB và giải phóng guard (Codex Round 7 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1])
        modal._generate_preview()

        # Giả lập widget update_idletasks ném exception
        with patch.object(modal, "update_idletasks", side_effect=RuntimeError("Simulated GUI loop crash")), \
             patch("schedule_ui.commit_schedule_plan") as mock_commit:
            modal._do_schedule()
            # Khẳng định commit_schedule_plan TUYỆT ĐỐI KHÔNG ĐƯỢC GỌI
            mock_commit.assert_not_called()

        # 1. Khẳng định: Báo đúng phân loại 'Lỗi Giao Diện' (KHÔNG báo nhầm 'Lỗi Lưu Dữ Liệu' - Codex Round 7 Fix)
        self.mock_err.assert_called_once()
        err_title, err_msg = self.mock_err.call_args[0]
        self.assertEqual(err_title, "Lỗi Giao Diện", "Phải báo đúng tiêu đề 'Lỗi Giao Diện'!")
        self.assertIn("Simulated GUI loop crash", err_msg)

        # 2. Khẳng định: Cơ sở dữ liệu SQLite hoàn toàn không có task rác nào
        self.assertEqual(len(self.mgr.get_all_tasks()), 0, "Không có task nào được lưu vào SQLite khi pre-commit UI lỗi!")

        # 3. Khẳng định: Cờ _is_committing bắt buộc phải được giải phóng về False (không bị treo vĩnh viễn)
        self.assertFalse(modal._is_committing, "_is_committing phải luôn được reset về False trong mọi trường hợp!")
        self.assertFalse(modal._commit_succeeded, "Chưa commit thì commit_succeeded phải là False!")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_12_idle_callback_plan_reentrancy_protection(self, mock_probe):
        """P3-12: Kiểm tra Plan Identity Guard chặn tái nhập thay đổi Plan trong lúc update_idletasks (Codex Round 8 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1])
        modal._generate_preview()
        plan_a = modal._current_plan
        self.assertIsNotNone(plan_a)

        # Giả lập một idle callback tinh vi cố gắng thay thế _current_plan bằng một plan B khác
        def sneaky_idle_callback():
            # Tạo giả plan B khác danh tính (different object identity)
            modal._current_plan = MagicMock(name="Plan_B_hijacked")

        with patch.object(modal, "update_idletasks", side_effect=sneaky_idle_callback), \
             patch("schedule_ui.commit_schedule_plan") as mock_commit:
            modal._do_schedule()
            # Khẳng định Plan Identity Guard đã phát hiện sự thay đổi và chặn đứng việc commit plan B
            mock_commit.assert_not_called()

        # 1. Khẳng định: Không có task nào bị ghi vào SQLite
        self.assertEqual(len(self.mgr.get_all_tasks()), 0, "Không được commit plan bị tráo đổi trong idle callback!")

        # 2. Khẳng định: Trạng thái hiển thị cảnh báo kế hoạch bị thay đổi
        self.assertIn("kế hoạch đã bị thay đổi", modal.status_info_var.get())

        # 3. Khẳng định: Cờ _is_committing được giải phóng an toàn
        self.assertFalse(modal._is_committing)
        self.assertFalse(modal._commit_succeeded)

        # 4. Khẳng định: Plan bị tráo đổi đã bị XÓA SẠCH 100% về None, nút quay về 1-Click (Codex Round 9 Fix)
        self.assertIsNone(modal._current_plan, "Plan bị tráo đổi phải bị xóa sạch về None!")
        self.assertIsNone(modal._current_snapshot, "Snapshot cũ phải bị xóa sạch về None!")
        self.assertEqual(modal._committed_revision, -1, "Committed revision phải bị reset về -1!")
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")

        # 5. Khẳng định: Người dùng bấm _do_schedule lần nữa -> Bắt buộc tạo preview mới, tuyệt đối không commit plan B bị tráo (Codex Round 9 Fix)
        with patch("schedule_ui.commit_schedule_plan") as mock_second_commit:
            modal._do_schedule()
            mock_second_commit.assert_not_called()

        self.assertEqual(len(self.mgr.get_all_tasks()), 0, "Lần bấm tiếp theo không được commit plan bị tráo đổi!")

        modal.destroy()

    @patch("schedule_ui.prepare_schedule_plan")
    def test_p3_13_empty_plan_blocks_commit_and_warns_user(self, mock_prep):
        """P3-13: Kế hoạch rỗng (total_planned_tasks == 0) không được bật nút Commit và cảnh báo người dùng (Codex Round 10 Fix)."""
        empty_plan = SchedulePlan(
            plan_id="plan_empty_001",
            created_at="2026-10-25T12:00:00",
            seed=42,
            profile_assignments={"Profile_Alpha": ()},
            unassigned_videos=(),
            conflicts_identified=None,
        )
        mock_prep.return_value = empty_plan

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # Preview
        modal._generate_preview()

        # Khẳng định mock được gọi đúng 1 lần (kiểm chứng độc lập fixture empty_plan được tiêu thụ - Codex Round 11 Fix)
        mock_prep.assert_called_once()

        # 1. Khẳng định: Nút submit KHÔNG ĐƯỢC bật sang trạng thái Xác Nhận Commit
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")

        # 2. Khẳng định: _current_plan vẫn là None (không công bố plan rỗng để commit)
        self.assertIsNone(modal._current_plan)

        # 3. Khẳng định: Cảnh báo kế hoạch rỗng xuất hiện
        self.mock_warn.assert_called_once()
        self.assertIn("Kế Hoạch Rỗng", self.mock_warn.call_args[0][0])
        self.assertIn("Kế hoạch rỗng", modal.status_info_var.get())

        # 4. Bấm _do_schedule không thể commit kế hoạch rỗng
        with patch("schedule_ui.commit_schedule_plan") as mock_commit:
            modal._do_schedule()
            mock_commit.assert_not_called()

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_14_preview_reentrancy_guard_and_safe_release(self, mock_probe):
        """P3-14: Cờ _is_previewing chặn đứng việc tái nhập preview khi đang tính toán và luôn được giải phóng trong finally (Codex Round 11 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # Khởi đầu: _is_previewing là False
        self.assertFalse(modal._is_previewing)

        nested_calls_blocked = 0
        orig_prep = schedule_ui.prepare_schedule_plan

        def spy_prepare_and_reenter(*args, **kwargs):
            nonlocal nested_calls_blocked
            # Kiểm tra: Trong lúc đang tính toán preview, cờ _is_previewing phải là True
            if modal._is_previewing:
                nested_calls_blocked += 1
            # Thử tái nhập gọi lồng _generate_preview()
            modal._generate_preview()
            return orig_prep(*args, **kwargs)

        with patch("schedule_ui.prepare_schedule_plan", side_effect=spy_prepare_and_reenter) as mock_p:
            modal._generate_preview()
            # prepare_schedule_plan chỉ được gọi duy nhất 1 lần (lời gọi lồng bị guard chặn ngay đầu hàm)
            mock_p.assert_called_once()

        self.assertEqual(nested_calls_blocked, 1, "Lời gọi lồng phải thấy _is_previewing là True và bị chặn!")
        self.assertFalse(modal._is_previewing, "Sau khi preview xong, cờ _is_previewing phải được giải phóng về False!")

        # Kịch bản 2: Khi preview ném exception, cờ _is_previewing vẫn bảo đảm được giải phóng về False (finally)
        with patch("schedule_ui.prepare_schedule_plan", side_effect=RuntimeError("Simulated crash in planner")):
            modal._generate_preview()

        self.assertFalse(modal._is_previewing, "Khối finally phải giải phóng _is_previewing về False kể cả khi có ngoại lệ!")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_15_modal_destroyed_during_idle_callbacks_aborts_commit_and_never_mutates_db(self, mock_probe):
        """P3-15: Khi modal bị đóng / destroy bởi idle callback trong lúc update_idletasks(), luồng commit bị hủy bỏ ngay lập tức và không bao giờ ghi DB (Codex Round 11 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # 1. Preview thành công -> có valid plan
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)
        self.assertEqual(modal._committed_revision, modal._input_revision)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")

        # 2. Giả lập một idle callback đóng modal (ví dụ người dùng bấm Cancel hoặc đóng cửa sổ) ngay trong update_idletasks
        def close_modal_during_idle():
            modal._cancel()

        with patch.object(modal, "update_idletasks", side_effect=close_modal_during_idle):
            with patch("schedule_ui.commit_schedule_plan") as mock_commit:
                modal._do_schedule()
                # Khẳng định commit_schedule_plan TUYỆT ĐỐI KHÔNG ĐƯỢC GỌI
                mock_commit.assert_not_called()

        # 3. Khẳng định: modal đã bị đánh dấu destroyed và state đã được xóa sạch
        self.assertTrue(getattr(modal, "_is_destroyed", False))
        self.assertIsNone(modal._current_plan)
        self.assertIsNone(modal._current_snapshot)
        self.assertEqual(modal._committed_revision, -1)

        # 4. Khẳng định: Cơ sở dữ liệu SQLite hoàn toàn sạch, không có bản ghi nào bị ghi bậy
        self.assertEqual(len(self.mgr.get_all_tasks()), 0)

    @patch("schedule_service.batch_probe_videos")
    def test_p3_16_modal_destroyed_during_preview_idle_aborts_preview_and_does_not_mutate_state(self, mock_probe):
        """P3-16: Khi modal bị đóng / destroy bởi idle callback trong lúc update_idletasks() của _generate_preview(), luồng xem trước bị hủy an toàn, không gọi planner và state được dọn sạch về None (Codex Round 12 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # Mô phỏng idle callback đóng modal trong lúc update_idletasks của preview
        def close_modal_during_preview_idle():
            modal._cancel()

        with patch.object(modal, "update_idletasks", side_effect=close_modal_during_preview_idle):
            with patch("schedule_ui.prepare_schedule_plan") as mock_prep:
                with patch.object(modal.preview_tree, "insert") as mock_tree_insert, \
                     patch.object(modal.preview_tree, "delete") as mock_tree_delete, \
                     patch.object(modal.preview_tree, "get_children", return_value=()) as mock_tree_get_children:
                    modal._generate_preview()
                    # Khẳng định prepare_schedule_plan TUYỆT ĐỐI KHÔNG ĐƯỢC GỌI vì modal đã bị destroy
                    mock_prep.assert_not_called()
                    # Khẳng định Treeview TUYỆT ĐỐI KHÔNG BỊ TRUY CẬP (cả get_children, delete, insert) khi đã bị destroy (Codex Round 14 Fix)
                    mock_tree_insert.assert_not_called()
                    mock_tree_delete.assert_not_called()
                    mock_tree_get_children.assert_not_called()

        # Khẳng định modal đã bị đóng và winfo_exists trả về 0 (Codex Round 13 Fix)
        self.assertEqual(modal.winfo_exists(), 0)
        self.assertTrue(getattr(modal, "_is_destroyed", False))
        self.assertIsNone(modal._current_plan)
        self.assertIsNone(modal._current_snapshot)
        self.assertEqual(modal._committed_revision, -1)
        self.assertFalse(modal._is_previewing)

    @patch("schedule_service.batch_probe_videos")
    def test_p3_17_preview_ui_prep_failure_handled_safely_without_leaking_exception(self, mock_probe):
        """P3-17: Khi update_idletasks() trong _generate_preview() ném exception, ngoại lệ được bọc an toàn, _is_previewing được giải phóng và thông báo lỗi UI được thiết lập (Codex Round 12 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # Mô phỏng update_idletasks ném exception
        with patch.object(modal, "update_idletasks", side_effect=RuntimeError("Simulated GUI update glitch in preview")):
            with patch("schedule_ui.prepare_schedule_plan") as mock_prep:
                modal._generate_preview()
                mock_prep.assert_not_called()

        # Khẳng định lỗi được bắt an toàn
        self.mock_err.assert_called_once()
        self.assertIn("Lỗi Giao Diện", self.mock_err.call_args[0][0])
        self.assertIn("Simulated GUI update glitch in preview", modal.status_info_var.get())
        self.assertFalse(modal._is_previewing)
        self.assertIsNone(modal._current_plan)
        self.assertEqual(modal._committed_revision, -1)

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_18_preview_error_reporting_widgets_failure_does_not_leak_exception(self, mock_probe):
        """P3-18: Khi planner lỗi kết hợp với cả status_info_var và messagebox.showerror đều ném exception trong nhánh except Exception as ex, ngoại lệ được bọc an toàn, không rò rỉ ra ngoài và _is_previewing được giải phóng (Codex Round 14 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # Cho phép status chuẩn bị UI chạy bình thường, chỉ ném exception khi ghi thông báo lỗi preview (Codex Round 14 Fix)
        orig_status_set = modal.status_info_var.set
        status_error_call_count = 0

        def faulty_status_set(val):
            nonlocal status_error_call_count
            if str(val).startswith("❌ Lỗi khi tính toán xem trước"):
                status_error_call_count += 1
                raise RuntimeError("Simulated status var failure on preview error reporting")
            return orig_status_set(val)

        self.mock_err.side_effect = RuntimeError("Simulated window manager crash on showerror popup")

        with patch("schedule_ui.prepare_schedule_plan", side_effect=RuntimeError("Simulated engine calculation failure")) as mock_prep:
            with patch.object(modal.status_info_var, "set", side_effect=faulty_status_set):
                # Gọi preview trực tiếp
                modal._generate_preview()

                # Khẳng định:
                # 1. Planner THỰC SỰ ĐÃ ĐƯỢC GỌI
                mock_prep.assert_called_once()
                # 2. Thao tác ghi status lỗi đã thực sự được gọi và kích hoạt ném lỗi
                self.assertEqual(status_error_call_count, 1)
                # 3. Popup showerror thực sự được gọi với đúng tiêu đề "Lỗi Xem Trước"
                self.mock_err.assert_called_once()
                self.assertEqual(self.mock_err.call_args[0][0], "Lỗi Xem Trước")

                # 4. State sạch sẽ, cờ giải phóng an toàn
                self.assertFalse(modal._is_previewing)
                self.assertIsNone(modal._current_plan)
                self.assertIsNone(modal._current_snapshot)
                self.assertEqual(modal._committed_revision, -1)

                # 5. Gọi _do_schedule (sẽ tự động kích hoạt preview) cũng tuyệt đối không bị rò exception
                with patch("schedule_ui.commit_schedule_plan") as mock_commit:
                    modal._do_schedule()
                    mock_commit.assert_not_called()

        self.assertFalse(modal._is_previewing)
        self.assertFalse(modal._is_committing)
        self.assertEqual(len(self.mgr.get_all_tasks()), 0)

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_19_commit_db_error_reporting_widgets_failure_does_not_leak_exception(self, mock_probe):
        """P3-19: Khi commit DB lỗi kết hợp với cả status_info_var và messagebox.showerror đều ném exception trong nhánh except Exception as db_err, ngoại lệ được bọc an toàn, không rò rỉ ra ngoài và _is_committing được giải phóng (Codex Round 14 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # Bước 1: Preview thành công hợp lệ -> có valid plan và nút Commit
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")

        # Bước 2: Thiết lập lỗi kép ở commit:
        # - commit_schedule_plan ném lỗi DB
        # - status_info_var.set ném lỗi khi set "❌ Lỗi commit"
        # - messagebox.showerror ném lỗi khi hiển thị "Lỗi Lưu Dữ Liệu"
        orig_status_set = modal.status_info_var.set
        status_db_error_calls = 0

        def faulty_status_set_commit(val):
            nonlocal status_db_error_calls
            if str(val).startswith("❌ Lỗi commit"):
                status_db_error_calls += 1
                raise RuntimeError("Simulated status var failure on commit DB error reporting")
            return orig_status_set(val)

        self.mock_err.side_effect = RuntimeError("Simulated window manager crash on commit error popup")

        with patch("schedule_ui.commit_schedule_plan", side_effect=RuntimeError("Simulated SQLite write disk failure")) as mock_commit:
            with patch.object(modal.status_info_var, "set", side_effect=faulty_status_set_commit):
                modal._do_schedule()

                # Khẳng định commit thực sự đã được gọi
                mock_commit.assert_called_once()
                # Khẳng định status lỗi commit được gọi
                self.assertEqual(status_db_error_calls, 1)
                # Khẳng định showerror được gọi với tiêu đề "Lỗi Lưu Dữ Liệu"
                self.mock_err.assert_called_once()
                self.assertEqual(self.mock_err.call_args[0][0], "Lỗi Lưu Dữ Liệu")

        # Khẳng định cờ giải phóng an toàn, commit_succeeded không bật, DB không có task
        self.assertFalse(modal._is_committing)
        self.assertFalse(modal._commit_succeeded)
        self.assertEqual(len(self.mgr.get_all_tasks()), 0)

        modal.destroy()



    @patch("schedule_service.batch_probe_videos")
    def test_p3_02b_video_change_invalidates_plan_even_when_label_update_fails(self, mock_probe):
        """P3-02b: Thay đổi danh sách video lập tức vô hiệu hóa plan và revision trước khi cập nhật widget, kể cả khi widget ném exception (Codex Round 15 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # 1. Preview thành công -> có plan và nút Xác Nhận Commit
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)
        self.assertEqual(modal._committed_revision, modal._input_revision)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")

        old_rev = modal._input_revision
        # 2. Giả lập widget lbl_video_count.configure ném exception khi đổi danh sách video
        with patch.object(modal.lbl_video_count, "configure", side_effect=RuntimeError("Simulated label widget crash")) as mock_lbl:
            modal._set_scanned_videos([self.v2])
            mock_lbl.assert_called_once()

        # 3. Khẳng định: Dù widget lỗi, plan cũ và revision cũ ĐÃ BỊ VÔ HIỆU HÓA 100%
        self.assertIsNone(modal._current_plan, "Plan cũ phải bị xóa về None kể cả khi widget label lỗi!")
        self.assertIsNone(modal._current_snapshot, "Snapshot cũ phải bị xóa về None kể cả khi widget label lỗi!")
        self.assertGreater(modal._input_revision, old_rev, "Input revision phải được tăng!")
        self.assertEqual(modal._committed_revision, -1, "Committed revision phải bị reset về -1!")
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")

        # 4. Bấm commit không thể commit plan cũ
        with patch("schedule_ui.commit_schedule_plan") as mock_commit:
            modal._do_schedule()
            mock_commit.assert_not_called()

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_04d_stale_refresh_error_reporter_failure_does_not_leak_exception(self, mock_probe):
        """P3-04d: Khi stale refresh gặp lỗi và setter báo lỗi status cũng ném exception, ngoại lệ được bọc an toàn, không rò rỉ và cờ _is_committing được giải phóng (Codex Round 15 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal.resolution_mode_var.set("OVERWRITE")
        modal._set_scanned_videos([self.v1])

        # Preview thành công
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)

        # Một writer khác thêm task vào DB làm lệch fingerprint (Stale)
        self.mgr.add_tasks([{
            "id": "task_concurrent_03",
            "profile_name": "Profile_Alpha",
            "video_path": "C:/concurrent3.mp4",
            "scheduled_time": "2026-10-26T12:00:00",
            "status": "PENDING",
        }])

        # Giả lập refresh ném exception VÀ status_info_var.set ném exception khi set thông báo lỗi refresh
        orig_status_set = modal.status_info_var.set
        status_stale_err_calls = 0

        def faulty_status_set_stale(val):
            nonlocal status_stale_err_calls
            if str(val).startswith("❌ Lỗi khi làm mới xem trước"):
                status_stale_err_calls += 1
                raise RuntimeError("Simulated status var failure on stale refresh error reporting")
            return orig_status_set(val)

        with patch.object(modal, "_generate_preview", side_effect=RuntimeError("Simulated refresh network glitch")):
            with patch.object(modal.status_info_var, "set", side_effect=faulty_status_set_stale):
                modal._do_schedule()

        # Khẳng định không rò rỉ ngoại lệ, status setter được gọi và cờ giải phóng an toàn
        self.assertEqual(status_stale_err_calls, 1)
        self.assertFalse(modal._is_committing)
        self.assertIsNone(modal._current_plan)
        self.assertEqual(modal._committed_revision, -1)

        modal.destroy()



    @patch("schedule_service.batch_probe_videos")
    def test_p3_02c_formula_change_invalidates_plan_even_when_preview_update_fails(self, mock_probe):
        """P3-02c: Khi đổi công thức tiêu đề, _on_input_changed chạy trước tiên vô hiệu hóa plan và revision kể cả khi _update_preview ném exception (Codex Round 16 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # 1. Preview thành công -> có plan và nút Xác Nhận Commit
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)
        self.assertEqual(modal._committed_revision, modal._input_revision)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")

        old_rev = modal._input_revision

        # 2. Giả lập _update_preview ném exception khi đổi công thức (ví dụ preview_var.set crash)
        # Ghi lại trạng thái quan sát được ngay tại thời điểm setter được gọi để assert bên ngoài (tránh bị nuốt AssertionError - Codex Round 18 Fix)
        state_during_preview_update = {}
        def spy_preview_var_set(*args, **kwargs):
            state_during_preview_update["plan"] = modal._current_plan
            state_during_preview_update["snapshot"] = modal._current_snapshot
            state_during_preview_update["input_rev"] = modal._input_revision
            state_during_preview_update["committed_rev"] = modal._committed_revision
            raise RuntimeError("Simulated preview variable crash")

        with patch.object(modal.preview_var, "set", side_effect=spy_preview_var_set):
            modal.formula_var.set("hacked_{filename}_{n}")

        # Khẳng định setter thực sự được gọi
        self.assertIn("plan", state_during_preview_update, "preview_var.set phải thực sự được gọi!")

        # Khóa chặt bất biến thứ tự: Ngay tại thời điểm preview setter được gọi, _on_input_changed() ĐÃ CHẠY XONG VÀ XÓA STATE
        self.assertIsNone(state_during_preview_update["plan"], "Tại thời điểm preview_var.set chạy, plan cũ đã phải bị xóa về None!")
        self.assertIsNone(state_during_preview_update["snapshot"], "Tại thời điểm preview_var.set chạy, snapshot cũ đã phải bị xóa!")
        self.assertGreater(state_during_preview_update["input_rev"], old_rev, "Tại thời điểm preview_var.set chạy, input revision đã phải tăng!")
        self.assertEqual(state_during_preview_update["committed_rev"], -1, "Tại thời điểm preview_var.set chạy, committed revision phải là -1!")

        # 3. Khẳng định: Dù update preview lỗi, plan cũ và revision cũ ĐÃ BỊ VÔ HIỆU HÓA 100%
        self.assertIsNone(modal._current_plan, "Plan cũ phải bị xóa về None kể cả khi preview_var.set lỗi!")
        self.assertIsNone(modal._current_snapshot, "Snapshot cũ phải bị xóa về None!")
        self.assertGreater(modal._input_revision, old_rev, "Input revision phải được tăng!")
        self.assertEqual(modal._committed_revision, -1, "Committed revision phải bị reset về -1!")
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")

        # 4. Bấm commit không thể commit plan cũ
        with patch("schedule_ui.commit_schedule_plan") as mock_commit:
            modal._do_schedule()
            mock_commit.assert_not_called()

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_07c_do_schedule_preview_success_reporter_failure_does_not_leak_exception(self, mock_probe):
        """P3-07c: Trong _do_schedule, sau khi tự động tạo preview thành công, nếu status_info_var.set ném exception thì ngoại lệ được bọc an toàn, không tự commit DB (Codex Round 16 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal(profiles=["Profile_Alpha"])
        modal._set_scanned_videos([self.v1])

        # Modal chưa preview: _current_plan là None
        self.assertIsNone(modal._current_plan)

        # Giả lập status_info_var.set ném exception khi set thông báo tạo bản xem trước mới thành công
        orig_status_set = modal.status_info_var.set
        status_preview_success_calls = 0

        def faulty_status_set_preview_success(val):
            nonlocal status_preview_success_calls
            if str(val).startswith("ℹ️ Đã tạo bản xem trước mới"):
                status_preview_success_calls += 1
                raise RuntimeError("Simulated status var failure on preview success message")
            return orig_status_set(val)

        with patch.object(modal.status_info_var, "set", side_effect=faulty_status_set_preview_success):
            modal._do_schedule()

        # Khẳng định:
        # 1. Status setter thành công đã thực sự được gọi và kích hoạt ném lỗi
        self.assertEqual(status_preview_success_calls, 1)
        # 2. Preview thành công và plan hợp lệ được tạo
        self.assertIsNotNone(modal._current_plan)
        self.assertEqual(modal._committed_revision, modal._input_revision)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 Xác Nhận Lên Lịch (Commit)")
        self.assertEqual(modal.btn_submit.cget("state"), "normal")
        # 3. Tuyệt đối không tự ý commit vào DB
        self.assertEqual(len(self.mgr.get_all_tasks()), 0)

        modal.destroy()



    def test_p3_02d_missing_input_warning_reporter_failure_does_not_leak_exception(self):
        """P3-02d: Khi không có video hoặc profile, lỗi kép từ cả status_info_var và messagebox.showwarning không rò rỉ ngoại lệ ra ngoài (Codex Round 17/18/19 Fix)."""
        # Kịch bản 1: Thiếu video (scanned_videos rỗng)
        modal1 = self._create_modal(profiles=["Profile_Alpha"])
        modal1.scanned_videos = []

        self.mock_warn.side_effect = RuntimeError("Simulated window manager warning crash")

        status_calls_video = 0
        def faulty_status_video(*args, **kwargs):
            nonlocal status_calls_video
            status_calls_video += 1
            raise RuntimeError("Simulated status var crash on missing video")

        with patch.object(modal1.status_info_var, "set", side_effect=faulty_status_video):
            # 1a. Gọi _generate_preview khi thiếu video
            modal1._generate_preview()
            self.assertEqual(status_calls_video, 1, "Status reporter thiếu video phải được gọi đúng 1 lần sau preview")
            self.assertFalse(modal1._is_previewing)

            # 1b. Gọi _do_schedule khi thiếu video: chứng minh guard thiếu video abort trực tiếp không gọi _generate_preview (Codex Round 19 Fix)
            with patch.object(modal1, "_generate_preview") as mock_gen1:
                modal1._do_schedule()
                mock_gen1.assert_not_called()
                self.assertEqual(status_calls_video, 2, "Status reporter thiếu video phải được gọi tiếp đúng 1 lần sau schedule")
            self.assertFalse(modal1._is_committing)

        self.assertEqual(len(self.mgr.get_all_tasks()), 0)
        modal1.destroy()

        # Kịch bản 2: Có video nhưng thiếu profile (selected_profiles rỗng - Codex Round 18/19 Fix)
        modal2 = self._create_modal(profiles=[])
        modal2.scanned_videos = [self.v1]
        self.assertEqual(len(modal2.selected_profiles), 0)

        status_calls_profile = 0
        def faulty_status_profile(*args, **kwargs):
            nonlocal status_calls_profile
            status_calls_profile += 1
            raise RuntimeError("Simulated status var crash on missing profile")

        with patch.object(modal2.status_info_var, "set", side_effect=faulty_status_profile):
            # 2a. Gọi _generate_preview khi thiếu profile
            modal2._generate_preview()
            self.assertEqual(status_calls_profile, 1, "Status reporter thiếu profile phải được gọi đúng 1 lần sau preview")
            self.assertFalse(modal2._is_previewing)

            # 2b. Gọi _do_schedule khi thiếu profile: chứng minh guard thiếu profile abort trực tiếp không gọi _generate_preview (Codex Round 19 Fix)
            with patch.object(modal2, "_generate_preview") as mock_gen2:
                modal2._do_schedule()
                mock_gen2.assert_not_called()
                self.assertEqual(status_calls_profile, 2, "Status reporter thiếu profile phải được gọi tiếp đúng 1 lần sau schedule")
            self.assertFalse(modal2._is_committing)

        self.assertEqual(len(self.mgr.get_all_tasks()), 0)
        modal2.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_20_input_revision_race_during_preview_aborts_preview(self, mock_probe):
        """P3-20: Đổi cấu hình/input trong lúc engine planner đang tính toán preview lập tức hủy bỏ kết quả tính toán cũ và xóa sạch Treeview không để lại hàng cũ (Codex Round 19/20 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1])

        # Bước 1: Preview lần 1 thành công rực rỡ -> Treeview đã có các dòng hiển thị
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)
        self.assertGreater(len(modal.preview_tree.get_children()), 0, "Preview lần 1 phải đổ dữ liệu vào Treeview!")
        old_items_count = len(modal.preview_tree.get_children())

        # Bước 2: Người dùng sửa đổi cấu hình và bấm Preview lần 2.
        # Trong lúc engine planner đang tính toán, giả lập một race condition thay đổi input revision
        real_prepare = schedule_ui.prepare_schedule_plan

        def side_effect_race(req, manager):
            # Tăng _input_revision và reset state trong lúc planner đang chạy
            modal._on_input_changed()
            return real_prepare(req, manager=manager)

        with patch("schedule_ui.prepare_schedule_plan", side_effect=side_effect_race):
            modal._generate_preview()

        # 1. Khẳng định: Giao diện cảnh báo cấu hình đã thay đổi
        self.assertIn("Cấu hình đã thay đổi trong lúc tính toán", modal.status_info_var.get())

        # 2. Khẳng định: Treeview ma trận BẮT BUỘC BỊ XÓA SẠCH 100%, tuyệt đối không để sót các dòng cũ của Preview lần 1 (Codex Round 20 Fix)
        self.assertEqual(len(modal.preview_tree.get_children()), 0, "Treeview phải bị xóa sạch khi revision race, không được lưu giữ hàng cũ của Preview 1!")

        # 3. Khẳng định: State plan và snapshot bị xóa sạch về None, nút quay về trạng thái 1-Click
        self.assertIsNone(modal._current_plan)
        self.assertIsNone(modal._current_snapshot)
        self.assertEqual(modal._committed_revision, -1)
        self.assertFalse(modal._is_previewing)
        self.assertEqual(modal.btn_submit.cget("text"), "🚀 1-Click Lên Lịch Tự Động")

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_21_input_revision_race_during_commit_idle_aborts_commit(self, mock_probe):
        """P3-21: Đổi input trong lúc update_idletasks của commit: cô lập và kiểm chứng độc lập điều kiện _input_revision != expected_rev (Codex Round 19/20 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            )
        ]

        modal = self._create_modal()
        modal._set_scanned_videos([self.v1])
        modal._generate_preview()
        self.assertIsNotNone(modal._current_plan)
        initial_plan = modal._current_plan
        initial_committed_rev = modal._committed_revision

        def sneaky_idle_only_input_rev_change():
            # GIỮ NGUYÊN TUYỆT ĐỐI identity của _current_plan và _committed_revision!
            # CHỈ tăng duy nhất _input_revision để loại trừ short-circuit và kiểm chứng ĐỘC LẬP nhánh `_input_revision != expected_rev` (Codex Round 20 Fix)
            self.assertIs(modal._current_plan, initial_plan, "Plan identity phải còn nguyên trước khi tăng revision")
            self.assertEqual(modal._committed_revision, initial_committed_rev, "Committed revision phải còn nguyên")
            modal._input_revision += 1

        with patch.object(modal, "update_idletasks", side_effect=sneaky_idle_only_input_rev_change), \
             patch("schedule_ui.commit_schedule_plan") as mock_commit:
            modal._do_schedule()
            # Khẳng định: Chính điều kiện `_input_revision != expected_rev` đã chặn đứng việc commit!
            mock_commit.assert_not_called()

        # 1. Khẳng định không ghi DB
        self.assertEqual(len(self.mgr.get_all_tasks()), 0)

        # 2. Khẳng định trạng thái thông báo kế hoạch thay đổi
        self.assertIn("kế hoạch đã bị thay đổi", modal.status_info_var.get())

        # 3. Khẳng định state được dọn dẹp sạch sẽ
        self.assertIsNone(modal._current_plan)
        self.assertEqual(modal._committed_revision, -1)
        self.assertFalse(modal._is_committing)

        modal.destroy()

    @patch("schedule_service.batch_probe_videos")
    def test_p3_22_real_main_py_on_success_callback_integration(self, mock_probe):
        """P3-22: Tích hợp trực tiếp với callback dùng chung make_bulk_schedule_success_callback của main.py, bao phủ 100% các nhánh switch_workspace và schedule_view (Codex Round 19/20 Fix)."""
        mock_probe.return_value = [
            VideoProbeResult(
                canonical_path=str(self.v1.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v1.stat().st_mtime_ns),
                duration_seconds=15.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
            VideoProbeResult(
                canonical_path=str(self.v2.resolve()),
                size_bytes=2048,
                mtime_ns=int(self.v2.stat().st_mtime_ns),
                duration_seconds=20.0,
                video_codec="h264",
                audio_codec="aac",
                width=1080,
                height=1920,
                pixel_format="yuv420p",
                frame_rate=30.0,
                is_valid=True,
            ),
        ]

        target_profiles = ["Profile_Alpha", "Profile_Beta"]

        # --- Nhánh A: Có switch_workspace_fn (mô phỏng chạy với giao diện chính đầy đủ) ---
        status_log_a = []
        profile_list_called_a = [False]
        workspace_switched_a = []
        tasks_received_a = []

        def mock_status_a(msg): status_log_a.append(msg)
        def mock_profile_list_a(): profile_list_called_a[0] = True
        def mock_switch_ws_a(ws): workspace_switched_a.append(ws)

        # Sử dụng đúng factory hàm make_bulk_schedule_success_callback được dùng trong main.py
        cb_a = schedule_ui.make_bulk_schedule_success_callback(
            target_profiles=target_profiles,
            update_status_fn=mock_status_a,
            update_profile_list_fn=mock_profile_list_a,
            switch_workspace_fn=mock_switch_ws_a,
            schedule_view_ref=None,
        )

        modal_a = self._create_modal(profiles=target_profiles, on_success=cb_a)
        modal_a._set_scanned_videos([self.v1, self.v2])
        modal_a._generate_preview()
        modal_a._do_schedule()

        self.assertTrue(modal_a._commit_succeeded)
        self.assertEqual(status_log_a, ["Đã lên lịch thành công cho 2 profile."])
        self.assertTrue(profile_list_called_a[0])
        self.assertEqual(workspace_switched_a, ["schedule"])

        # Kiểm chứng dữ liệu DB SQLite sau Nhánh A
        db_tasks_a = self.mgr.get_all_tasks()
        self.assertEqual(len(db_tasks_a), 2)
        self.assertEqual(set(t["profile_name"] for t in db_tasks_a), {"Profile_Alpha", "Profile_Beta"})
        self.assertTrue(all(t["status"] == "PENDING" for t in db_tasks_a))
        self.assertTrue(all(t["execution_mode"] == "SERVER_SCHEDULE" for t in db_tasks_a))
        modal_a.destroy()

        # --- Nhánh B: Không có switch_workspace, có schedule_view_ref._refresh_data ---
        status_log_b = []
        profile_list_called_b = [False]
        refresh_called_b = [False]

        def mock_status_b(msg): status_log_b.append(msg)
        def mock_profile_list_b(): profile_list_called_b[0] = True

        class MockScheduleView:
            def _refresh_data(self):
                refresh_called_b[0] = True

        mock_sched_view = MockScheduleView()

        cb_b = schedule_ui.make_bulk_schedule_success_callback(
            target_profiles=["Profile_Alpha"],
            update_status_fn=mock_status_b,
            update_profile_list_fn=mock_profile_list_b,
            switch_workspace_fn=None,
            schedule_view_ref=mock_sched_view,
        )

        # Gọi trực tiếp callback với danh sách created_tasks để kiểm chứng đối soát tham số
        dummy_task_ids = ["task_001", "task_002"]
        cb_b(created_tasks=dummy_task_ids)

        self.assertEqual(status_log_b, ["Đã lên lịch thành công cho 1 profile."])
        self.assertTrue(profile_list_called_b[0])
        self.assertTrue(refresh_called_b[0], "schedule_view._refresh_data() phải được gọi ở nhánh B!")


if __name__ == "__main__":
    unittest.main()
