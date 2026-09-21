import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tkinter as tk

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))
sys.stdout.reconfigure(encoding='utf-8')

from tiktok_dedup_engine import (
    DedupConfig,
    DedupDatabase,
    DedupWatchdogWorker,
    ProfileContext,
    TikTokDedupApiClient,
    analyze_tiktok_video,
)
from tiktok_dedup_ui import CheckPostDialog


@unittest.skipIf(os.environ.get("CI", "").lower() == "true", "Interactive Tk rendering is verified on desktop runner")
class TestCheckPostDialog(unittest.TestCase):
    """Kiểm tra toàn diện Hộp thoại Kiểm Tra Bài Đăng (CheckPostDialog)."""

    def setUp(self):
        self.db_file = ROOT_DIR / "temp_test_checkpost.db"
        if self.db_file.exists():
            self.db_file.unlink()
        self.db = DedupDatabase(db_path=self.db_file)

        # Mock messagebox để tránh popup modal chặn test tự động
        import tkinter.messagebox as mb
        import tiktok_dedup_ui
        self._orig_showinfo = mb.showinfo
        self._orig_showwarning = mb.showwarning
        self._orig_showerror = mb.showerror
        self._orig_askyesno = mb.askyesno
        self._orig_ui_mb = tiktok_dedup_ui.messagebox
        mb.showinfo = MagicMock()
        mb.showwarning = MagicMock()
        mb.showerror = MagicMock()
        mb.askyesno = MagicMock(return_value=True)
        tiktok_dedup_ui.messagebox = MagicMock()

        self.root = tk.Tk()
        self.root.withdraw()

        # Tạo profile test AUTO 6 tương tự trong profiles.db
        self.profile = ProfileContext(
            profile_id="AUTO 6",
            tiktok_account="user61172346518401",
            sec_uid="MS4wLjABAAAAlnDxX7XhRytdsm9Hi-I8X_BjaLwNG4c2ZCVz9SoXQzGj1i6bk9WFVrZlDAAyoFQ_",
            cookies="sessionid=fake123; tt-csrf-token=dummy456",
            proxy_string="45.61.124.46:6375",
        )

    def tearDown(self):
        import tkinter.messagebox as mb
        import tiktok_dedup_ui
        mb.showinfo = self._orig_showinfo
        mb.showwarning = self._orig_showwarning
        mb.showerror = self._orig_showerror
        mb.askyesno = self._orig_askyesno
        tiktok_dedup_ui.messagebox = self._orig_ui_mb
        self.root.destroy()
        if self.db_file.exists():
            try:
                self.db_file.unlink()
            except Exception:
                pass

    def test_checkpost_dialog_population_and_duplicate_detection(self):
        """Kiểm tra dialog hiển thị đúng 9 video với 2 video trùng từ payload TikTokManager."""
        dialog = CheckPostDialog(self.root, profile=self.profile, db=self.db, autoload=False)

        # Đọc trực tiếp datares.txt
        payload_path = ROOT_DIR / "datares.txt"
        self.assertTrue(payload_path.exists())
        with open(payload_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        posts = raw.get("aweme_list", [])

        # Kích hoạt cập nhật UI với posts thực tế
        dialog._on_posts_loaded(posts)

        # Kiểm tra số dòng trên bảng Treeview
        children = dialog.tree.get_children()
        self.assertEqual(len(children), 9, "Bảng phải hiển thị đầy đủ 9 video")

        # Kiểm tra chi tiết 2 video trùng lặp
        dup_rows = []
        clean_rows = []
        for row in children:
            vals = dialog.tree.item(row, "values")
            tags = dialog.tree.item(row, "tags")
            if "dup" in tags:
                dup_rows.append(vals)
            elif "clean" in tags:
                clean_rows.append(vals)

        self.assertEqual(len(dup_rows), 2, "Phải phát hiện chính xác 2 video trùng lặp")
        self.assertEqual(len(clean_rows), 7, "Phải có đúng 7 video gốc")

        # Video trùng 1: aweme_id 7681997517537594646
        self.assertEqual(dup_rows[0][1], "7681997517537594646")
        self.assertEqual(dup_rows[0][2], "7681997048069115149")
        self.assertIn("TRÙNG LẶP", dup_rows[0][8])

        # Video trùng 2: aweme_id 7679460450093845782
        self.assertEqual(dup_rows[1][1], "7679460450093845782")
        self.assertEqual(dup_rows[1][2], "7679277164268768534")
        self.assertIn("TRÙNG LẶP", dup_rows[1][8])

        # Kiểm tra nhãn cảnh báo tổng kết
        self.assertIn("Phát hiện 2 bài viết trùng lặp", dialog.lbl_status_summary.cget("text"))

        # Test lưu video trùng vào Audit Log
        dialog._save_duplicates_to_audit()
        logs = self.db.get_audit_logs(profile_id="AUTO 6")
        self.assertEqual(len(logs), 2, "Database phải lưu chính xác 2 video trùng vào dedup_log")

    @patch("webbrowser.open")
    def test_checkpost_dialog_actions(self, mock_web_open):
        """Kiểm tra các nút thao tác mở link video gốc và bài đăng."""
        dialog = CheckPostDialog(self.root, profile=self.profile, db=self.db, autoload=False)
        payload_path = ROOT_DIR / "datares.txt"
        with open(payload_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        dialog._on_posts_loaded(raw.get("aweme_list", []))

        children = dialog.tree.get_children()

        # Chọn video thứ 2 (là video trùng aweme_id 7681997517537594646)
        dialog.tree.selection_set(children[1])

        # Thử mở video gốc
        dialog._open_selected_original()
        mock_web_open.assert_called_with("https://www.tiktok.com/@tiktok/video/7681997048069115149")

        # Thử mở video hiện tại
        dialog._open_selected_current()
        self.assertIn("7681997517537594646", mock_web_open.call_args[0][0])

        # Thử sao chép ID
        dialog._copy_selected_id()
        clipboard_val = self.root.clipboard_get()
        self.assertEqual(clipboard_val, "7681997517537594646")


if __name__ == "__main__":
    unittest.main()
