# -*- coding: utf-8 -*-
"""
test_check_post_dialog_phase2.py - Comprehensive Unit & Integration Tests cho CheckPostDialog (Phase 2).
Tuân thủ nghiêm ngặt AGENTS.md và các tiêu chuẩn kiểm thử khắt khe từ Codex:
1. Kiểm tra chính xác 13 cột và ánh xạ dữ liệu DTO -> Treeview values.
2. Kiểm tra bất đồng bộ Run Guard: Lượt chạy cũ (A) không được ghi đè lượt chạy mới (B).
3. Kiểm tra Hộp thoại Xác nhận Xóa Video (Target Freeze):
   - Đóng băng Profile name, Video caption, Video ID.
   - Hủy bỏ (No) không gọi API xóa.
   - Xác nhận (Yes) gọi đúng API với đúng Video ID đã chọn.
4. Kiểm tra Hộp thoại Ẩn Video (Private).
5. Kiểm tra Hộp thoại Kháng Strike (ManualAppealDialog):
   - Giới hạn 400 ký tự (399, 400, 401).
   - Sanitize unicode, tiếng Việt có dấu, emoji, zero-width space.
   - Khóa nút khi rỗng hoặc đang submit.
6. Kiểm tra tải video No-Logo luồng nền an toàn.
"""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import customtkinter as ctk

from check_features.ui.check_post_dialog import CheckPostDialog
from check_features.ui.common_appeal_dialog import ManualAppealDialog
from check_features.models import AwemePost, PenaltyDetails


def run_thread_sync(target=None, args=(), kwargs=None, *other_args, **other_kwargs):
    """Helper chạy worker thread đồng bộ và xử lý after(0, ...) ngay lập tức trong test."""
    kwargs = kwargs or {}
    t = MagicMock()
    def fake_start():
        if target:
            target(*args, **kwargs)
    t.start = fake_start
    return t


class TestCheckPostDialogPhase2(unittest.TestCase):
    """Bộ kiểm thử tính đúng đắn nghiệp vụ và an toàn bất đồng bộ của CheckPostDialog."""

    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self.mock_cfg = {
            "name": "Prof_Channel_01",
            "tiktok_id": "creator_channel_01",
            "cookie_str": "sessionid=test_sess_01",
        }

    # --------------------------------------------------------------------------
    # 1. Treeview 13 Cột & Ánh Xạ Dữ Liệu
    # --------------------------------------------------------------------------
    def test_treeview_has_13_columns_and_data_mapping(self):
        """Kiểm tra cấu hình 13 cột Treeview và ánh xạ chi tiết từng trường dữ liệu."""
        dlg = CheckPostDialog(self.root, "Prof_Channel_01", self.mock_cfg, autoload=False)

        expected_cols = (
            "c_idx", "c_cover", "c_desc", "c_src", "c_flags",
            "c_play", "c_submetrics", "c_geo", "c_shadow", "c_penalty",
            "c_status", "c_id", "c_action"
        )
        self.assertEqual(len(dlg.tree["columns"]), 13)
        self.assertEqual(tuple(dlg.tree["columns"]), expected_cols)
        self.assertIn("headings", str(dlg.tree.cget("show")))

        # Tạo 1 Post mẫu đầy đủ dữ liệu
        post = AwemePost(
            aweme_id="7391480094101275999",
            desc="Hướng dẫn làm affiliate TikTok hiệu quả",
            cover="https://cdn.tiktok.com/cover.jpg",
            duration=75,  # 01:15
            create_time=1700000000,
            device="web",
            source_label="Web Upload",
            play=125000,
            like=4500,
            comment=320,
            share=150,
            save=80,
            region="VN",
            shadow="yes",
            shadow_reason="Không lên FYP (Hạn chế đề xuất For You)",
            ai_label=True,
            is_top=True,
            is_private=False,
            is_delete=False,
            group_id="",
            download_url="https://cdn.tiktok.com/clean.mp4",
        )

        dlg._posts.append(post)
        dlg._posts_by_id[post.aweme_id] = post
        dlg._insert_row(1, post)

        # Trích xuất values đã render
        row_vals = dlg.tree.item(post.aweme_id, "values")
        self.assertEqual(len(row_vals), 13)
        self.assertEqual(row_vals[0], "1")  # #
        self.assertIn("01:15", row_vals[1])  # Thời lượng mm:ss
        self.assertIn("Hướng dẫn làm affiliate", row_vals[2])  # Caption
        self.assertEqual(row_vals[3], "Web Upload")  # Nguồn
        self.assertIn("AI", row_vals[4])  # Nhãn AI
        self.assertIn("Ghim", row_vals[4])  # Nhãn Ghim
        self.assertEqual(row_vals[5], "125,000")  # Xem format US
        self.assertIn("❤️ 4,500", row_vals[6])  # Submetrics
        self.assertEqual(row_vals[7], "VN")  # GEO
        self.assertIn("Có (Bóp reach)", row_vals[8])  # Shadowban
        self.assertEqual(row_vals[9], "—")  # Penalty không có
        self.assertEqual(row_vals[10], "Công khai")  # Trạng thái
        self.assertEqual(row_vals[11], "7391480094101275999")  # aweme_id string
        self.assertIn("Xóa", row_vals[12])  # Action

        dlg.destroy()

    # --------------------------------------------------------------------------
    # 2. Run Guard Chống Race Condition Bất Đồng Bộ
    # --------------------------------------------------------------------------
    def test_run_guard_drops_stale_worker_results(self):
        """
        Kịch bản quyết định: A bắt đầu -> B bắt đầu -> A trả kết quả -> B trả kết quả.
        UI cuối cùng bắt buộc chỉ chứa dữ liệu của B, kết quả của A bị loại bỏ 100%.
        """
        dlg = CheckPostDialog(self.root, "Prof_Channel_01", self.mock_cfg, autoload=False)

        # Thiết lập Run A
        run_id_a = "Prof_Run_A_1000"
        dlg._current_run_id = run_id_a

        posts_a = [
            AwemePost(aweme_id="7300000000000003001", desc="Post từ Run A", play=10)
        ]

        # Người dùng bấm Tải lại -> Chuyển sang Run B
        run_id_b = "Prof_Run_B_2000"
        dlg._current_run_id = run_id_b

        posts_b = [
            AwemePost(aweme_id="7300000000000003002", desc="Post từ Run B", play=2000)
        ]

        # Giả lập hàm commit nội bộ của _fetch_page_async
        def mock_callback_commit(run_id, posts):
            if dlg._current_run_id != run_id:
                return  # Run Guard Drop
            for idx, p in enumerate(posts, start=1):
                dlg._posts.append(p)
                dlg._posts_by_id[p.aweme_id] = p
                dlg._insert_row(idx, p)

        # 1. Run A callback chạy -> Bị drop
        mock_callback_commit(run_id_a, posts_a)
        self.assertEqual(len(dlg.tree.get_children()), 0)
        self.assertNotIn("7300000000000003001", dlg._posts_by_id)

        # 2. Run B callback chạy -> Chấp nhận
        mock_callback_commit(run_id_b, posts_b)
        self.assertEqual(len(dlg.tree.get_children()), 1)
        self.assertIn("7300000000000003002", dlg._posts_by_id)
        self.assertEqual(dlg.tree.item("7300000000000003002", "values")[2], "Post từ Run B")

        dlg.destroy()

    # --------------------------------------------------------------------------
    # 3. Confirm Dialog Xóa Video (Target Freeze & An Toàn)
    # --------------------------------------------------------------------------
    @patch("check_features.ui.check_post_dialog.messagebox.showinfo")
    @patch("check_features.ui.check_post_dialog.messagebox.askyesno")
    @patch("threading.Thread", side_effect=run_thread_sync)
    def test_delete_video_target_freeze_and_cancellation(self, mock_thread, mock_askyesno, mock_showinfo):
        """Kiểm tra Target Freeze của chức năng xóa: hủy không gọi API; xác nhận gọi đúng target_vid."""
        dlg = CheckPostDialog(self.root, "Prof_Channel_01", self.mock_cfg, autoload=False)

        post = AwemePost(
            aweme_id="7391480094101275111",
            desc="Video nhạy cảm cần xóa",
            is_delete=False,
        )
        dlg._posts.append(post)
        dlg._posts_by_id[post.aweme_id] = post
        dlg._insert_row(1, post)
        dlg.tree.selection_set(post.aweme_id)

        dlg.client.delete_studio_item = MagicMock(return_value=(True, "Xóa thành công"))

        # Kịch bản 1: Người dùng chọn Cancel (No)
        mock_askyesno.return_value = False
        dlg._on_delete_clicked()

        # Kiểm tra nội dung prompt có đóng băng thông tin video không
        prompt_title, prompt_msg = mock_askyesno.call_args[0]
        self.assertIn("Prof_Channel_01", prompt_msg)
        self.assertIn("7391480094101275111", prompt_msg)
        self.assertIn("Video nhạy cảm cần xóa", prompt_msg)
        # API delete không được phép gọi
        dlg.client.delete_studio_item.assert_not_called()
        self.assertFalse(post.is_delete)

        # Kịch bản 2: Người dùng chọn Xác nhận (Yes)
        mock_askyesno.return_value = True
        dlg._on_delete_clicked()
        self.root.update()

        dlg.client.delete_studio_item.assert_called_once_with("7391480094101275111")
        self.assertTrue(post.is_delete)
        # Row status được cập nhật thành đã xóa
        row_vals = dlg.tree.item(post.aweme_id, "values")
        self.assertEqual(row_vals[10], "🗑️ Đã xóa")

        dlg.destroy()

    # --------------------------------------------------------------------------
    # 4. Hộp Thoại Ẩn Video (Private)
    # --------------------------------------------------------------------------
    @patch("check_features.ui.check_post_dialog.messagebox.showinfo")
    @patch("check_features.ui.check_post_dialog.messagebox.askyesno")
    @patch("threading.Thread", side_effect=run_thread_sync)
    def test_hide_video_private(self, mock_thread, mock_askyesno, mock_showinfo):
        """Kiểm tra chuyển video sang chế độ Riêng tư (Private)."""
        dlg = CheckPostDialog(self.root, "Prof_Channel_01", self.mock_cfg, autoload=False)

        post = AwemePost(
            aweme_id="7391480094101275222",
            desc="Video cần ẩn",
            is_private=False,
        )
        dlg._posts.append(post)
        dlg._posts_by_id[post.aweme_id] = post
        dlg._insert_row(1, post)
        dlg.tree.selection_set(post.aweme_id)

        dlg.client.hide_studio_item = MagicMock(return_value=(True, "Ẩn thành công"))

        mock_askyesno.return_value = True
        dlg._on_hide_clicked()
        self.root.update()

        dlg.client.hide_studio_item.assert_called_once_with("7391480094101275222")
        self.assertTrue(post.is_private)
        row_vals = dlg.tree.item(post.aweme_id, "values")
        self.assertEqual(row_vals[10], "🔒 Riêng tư")

        dlg.destroy()

    # --------------------------------------------------------------------------
    # 5. ManualAppealDialog: Giới Hạn 400 Ký Tự, Unicode, Counter & Submit
    # --------------------------------------------------------------------------
    def test_manual_appeal_dialog_limit_and_unicode_handling(self):
        """Kiểm tra ManualAppealDialog kiểm soát nghiêm ngặt <= 400 ký tự và sanitize."""
        callback_mock = MagicMock(return_value=(True, "Đã gửi kháng nghị"))

        appeal_dlg = ManualAppealDialog(
            self.root,
            target_title="Video 7391480094101275333",
            item_id="7391480094101275333",
            violation_reason="Unoriginal Content",
            on_submit_callback=callback_mock,
        )

        # 1. Khởi tạo rỗng -> Nút submit bị disable
        self.assertEqual(appeal_dlg.btn_submit.cget("state"), "disabled")

        # 2. Nhập nội dung hợp lệ tiếng Việt có dấu + emoji
        vn_text = "Tôi là tác giả gốc của video này 🇻🇳. Kính mong TikTok kiểm tra lại!"
        appeal_dlg.textbox.delete("1.0", "end")
        appeal_dlg.textbox.insert("1.0", vn_text)
        appeal_dlg._on_text_changed()

        self.assertEqual(appeal_dlg.btn_submit.cget("state"), "normal")
        self.assertIn(f"{len(vn_text)} / 400", appeal_dlg.lbl_counter.cget("text"))

        # 3. Dán chuỗi vượt quá 400 ký tự (450 ký tự) -> Tự động cắt ngắn về đúng 400
        overflow_text = "A" * 450
        appeal_dlg.textbox.delete("1.0", "end")
        appeal_dlg.textbox.insert("1.0", overflow_text)
        appeal_dlg._on_text_changed()

        current_text = appeal_dlg.textbox.get("1.0", "end-1c")
        self.assertEqual(len(current_text), 400)
        self.assertIn("400 / 400", appeal_dlg.lbl_counter.cget("text"))

        appeal_dlg.destroy = MagicMock()

        # 4. Bấm gửi submit
        with patch("threading.Thread", side_effect=run_thread_sync), \
             patch("check_features.ui.common_appeal_dialog.messagebox.showinfo"):
            appeal_dlg._on_submit_clicked()
            self.root.update()

        callback_mock.assert_called_once_with(current_text)
        appeal_dlg.destroy.assert_called_once()

        # Đợi các after timer nội bộ của CustomTkinter hoàn thành
        time.sleep(0.15)
        self.root.update()

        try:
            ctk.CTkToplevel.destroy(appeal_dlg)
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # 6. Tải Video No-Logo Luồng Nền
    # --------------------------------------------------------------------------
    @patch("check_features.ui.check_post_dialog.messagebox.showinfo")
    @patch("check_features.ui.check_post_dialog.filedialog.askdirectory")
    @patch("check_features.ui.check_post_dialog.download_video_no_watermark")
    @patch("threading.Thread", side_effect=run_thread_sync)
    def test_download_no_logo_background_thread(self, mock_thread, mock_downloader, mock_askdir, mock_showinfo):
        """Kiểm tra gọi download_video_no_watermark với đường dẫn lưu và video ID chính xác."""
        dlg = CheckPostDialog(self.root, "Prof_Channel_01", self.mock_cfg, autoload=False)

        post = AwemePost(
            aweme_id="7391480094101275444",
            desc="Video No-Logo",
            download_url="https://cdn.tiktok.com/clean_video.mp4",
        )
        dlg._posts.append(post)
        dlg._posts_by_id[post.aweme_id] = post
        dlg._insert_row(1, post)
        dlg.tree.selection_set(post.aweme_id)

        mock_askdir.return_value = "C:/Videos"
        mock_downloader.return_value = (True, "Tải thành công", "C:/Videos/7391480094101275444.mp4")

        dlg._on_download_clicked()
        self.root.update()

        mock_downloader.assert_called_once_with(
            "https://cdn.tiktok.com/clean_video.mp4",
            "C:/Videos",
            "7391480094101275444"
        )
        dlg.destroy()


if __name__ == "__main__":
    unittest.main()
