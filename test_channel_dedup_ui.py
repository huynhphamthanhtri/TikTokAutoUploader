import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, r"e:\BK_TOOL_VIBE_AUTO_UPLOAD\VIBE_AUTO_UPLOAD-LP")
sys.stdout.reconfigure(encoding='utf-8')

from tiktok_dedup_engine import (
    DedupConfig,
    DedupDatabase,
    DedupWatchdogWorker,
    ProfileContext,
    TikTokDedupApiClient,
    analyze_tiktok_video,
)
from tiktok_dedup_ui import DedupWatchdogView
import tkinter as tk

class TestChannelDedupUI(unittest.TestCase):
    """Kiểm tra tích hợp tính năng Check Trùng Kênh Bất Kỳ (Subtab 3)."""

    def setUp(self):
        self.db_file = Path(r"e:\BK_TOOL_VIBE_AUTO_UPLOAD\VIBE_AUTO_UPLOAD-LP\scratch\test_channel_ui.db")
        if self.db_file.exists():
            self.db_file.unlink()
        self.db = DedupDatabase(db_path=self.db_file)
        self.config = DedupConfig()
        self.worker = DedupWatchdogWorker(config=self.config, db=self.db, profile_provider=lambda: [])

        # Mock messagebox để tránh popup modal chặn test
        import tkinter.messagebox as mb
        self._orig_showinfo = mb.showinfo
        self._orig_showwarning = mb.showwarning
        self._orig_showerror = mb.showerror
        mb.showinfo = MagicMock()
        mb.showwarning = MagicMock()
        mb.showerror = MagicMock()

        # Khởi tạo Tk root ẩn để test UI components
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        import tkinter.messagebox as mb
        mb.showinfo = self._orig_showinfo
        mb.showwarning = self._orig_showwarning
        mb.showerror = self._orig_showerror
        self.worker.stop()
        self.root.destroy()
        if self.db_file.exists():
            try:
                self.db_file.unlink()
            except Exception:
                pass

    def test_channel_username_extraction(self):
        self.assertEqual(TikTokDedupApiClient.extract_username("@komugi_no_daidokoro"), "komugi_no_daidokoro")
        self.assertEqual(
            TikTokDedupApiClient.extract_username("https://www.tiktok.com/@komugi_no_daidokoro?lang=en"),
            "komugi_no_daidokoro",
        )
        self.assertEqual(TikTokDedupApiClient.extract_username("komugi_no_daidokoro"), "komugi_no_daidokoro")

    def test_channel_fetch_and_populate_ui(self):
        view = DedupWatchdogView(
            parent=self.root,
            db=self.db,
            worker=self.worker,
            get_profiles_func=lambda: [],
        )

        # 1. Chuyển sang subtab channel
        view._switch_subtab("channel")
        self.assertEqual(view._active_subtab, "channel")

        # 2. Đọc payload thực tế datares.txt
        payload_path = Path(r"e:\BK_TOOL_VIBE_AUTO_UPLOAD\VIBE_AUTO_UPLOAD-LP\datares.txt")
        self.assertTrue(payload_path.exists(), "datares.txt phải tồn tại trong workspace")

        with open(payload_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        posts = data.get("aweme_list", [])
        self.assertEqual(len(posts), 9)

        channel_info = {
            "uniqueId": "komugi_no_daidokoro",
            "nickname": "Tiểu Mạch Đồng Học",
            "followers": 10620,
            "videoCount": 38,
        }

        # 3. Đưa vào hàm hiển thị dữ liệu
        view._populate_channel_table(channel_info, posts, "@komugi_no_daidokoro")

        # 4. Kiểm tra Treeview
        children = view.channel_tree.get_children()
        self.assertEqual(len(children), 9, "Treeview phải hiển thị đúng 9 video")

        # Kiểm tra nội dung các dòng trùng
        dup_rows = []
        for row in children:
            vals = view.channel_tree.item(row, "values")
            tags = view.channel_tree.item(row, "tags")
            if "dup" in tags:
                dup_rows.append(vals)

        self.assertEqual(len(dup_rows), 2, "Phải có đúng 2 dòng có tag 'dup'")
        
        # Row 2 (aweme_id 7681997517537594646)
        self.assertEqual(dup_rows[0][1], "7681997517537594646")
        self.assertEqual(dup_rows[0][2], "7681997048069115149")
        self.assertIn("TRÙNG LẶP", dup_rows[0][7])

        # Row 4 (aweme_id 7679460450093845782)
        self.assertEqual(dup_rows[1][1], "7679460450093845782")
        self.assertEqual(dup_rows[1][2], "7679277164268768534")
        self.assertIn("TRÙNG LẶP", dup_rows[1][7])

        # 5. Kiểm tra lưu vào Audit Log qua nút _save_channel_duplicates_to_db
        view._save_channel_duplicates_to_db()
        logs = self.db.get_audit_logs()
        self.assertEqual(len(logs), 2, "Database phải ghi nhận 2 video trùng lặp vào Audit Log")
        
        # 6. Kiểm tra KPI sau khi lưu
        self.assertEqual(view.lbl_detected_val.cget("text"), "2")

if __name__ == "__main__":
    unittest.main()
