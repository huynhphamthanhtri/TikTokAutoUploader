import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
import customtkinter as ctk

from app_ui import build_dashboard
from youtube_monitor.ui import YouTubeMonitorView
from youtube_monitor.batch_view import BatchDownloadView
from youtube_monitor.activity_view import ActivityLogView

_SHARED_TEST_ROOT = None


def get_or_create_test_root():
    global _SHARED_TEST_ROOT
    if _SHARED_TEST_ROOT is not None:
        try:
            if _SHARED_TEST_ROOT.winfo_exists():
                return _SHARED_TEST_ROOT
        except Exception:
            pass
    try:
        # Check if default tkinter root exists
        default_root = getattr(tk, "_default_root", None)
        if default_root is not None and default_root.winfo_exists():
            _SHARED_TEST_ROOT = default_root
            return _SHARED_TEST_ROOT
        root = ctk.CTk()
        root.withdraw()
        _SHARED_TEST_ROOT = root
        return root
    except Exception:
        return None


class TestYouTubeUIContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_or_create_test_root()

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

    def test_youtube_monitor_view_structure(self):
        """Test YouTubeMonitorView 2-column layout, tree columns, context menu, and cards."""
        handlers = {
            "get_profiles": lambda: ["Profile_1", "Profile_2"],
            "get_status": lambda: {
                "running": True,
                "healthy": True,
                "channels": 5,
                "queue": 2,
                "workers": 4,
                "downloaded_today": 12,
                "api_key_set": True,
                "cookies_set": True,
                "callback_verified": True,
            },
            "get_channels": lambda: [
                {
                    "channel_id": "UC12345",
                    "title": "Test Channel",
                    "profile_name": "Profile_1",
                    "active": True,
                    "process_short": True,
                    "seen_count": 8,
                    "folder": "C:/videos/Profile_1",
                }
            ],
            "get_logs": lambda: ["Test log entry"],
            "get_cookies_file": lambda: "C:/path/to/my_cookies.txt",
            "get_max_video_minutes": lambda: 15,
        }

        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)

        # 1. Verify Treeview columns (Refactored 4 columns)
        expected_cols = ("channel_id", "active", "short", "seen")
        self.assertEqual(view.tree["columns"], expected_cols)

        # 2. Verify Cookie filename masking (only filename is displayed, full path in cookie_var)
        view.refresh_data()
        self.assertEqual(view.cookie_var.get(), "C:/path/to/my_cookies.txt")
        self.assertEqual(view.cookie_display_var.get(), "my_cookies.txt")
        self.assertIsNotNone(view.btn_check_cookie)

        # 3. Verify Context menu entries
        self.assertIsNotNone(view.ctx_menu)
        menu_labels = [view.ctx_menu.entrycget(i, "label") for i in range(view.ctx_menu.index("end") + 1) if view.ctx_menu.type(i) == "command"]
        self.assertIn("🌐 Mở kênh trên YouTube", menu_labels)
        self.assertIn("📁 Mở thư mục lưu video", menu_labels)
        self.assertIn("⚡ Bật/Tắt theo dõi kênh", menu_labels)
        self.assertIn("✂️ Bật/Tắt điều chỉnh 40-60s", menu_labels)
        self.assertIn("🔄 Đổi Profile đích...", menu_labels)
        self.assertIn("🗑️ Xóa kênh khỏi danh sách", menu_labels)

        # 4. Verify Responsive layout event
        mock_event_wide = MagicMock()
        mock_event_wide.width = 1200
        view._on_configure(mock_event_wide)
        self.assertFalse(view._is_stacked_layout)

        mock_event_narrow = MagicMock()
        mock_event_narrow.width = 750
        view._on_configure(mock_event_narrow)
        self.assertTrue(view._is_stacked_layout)

        # 5. Verify Video Quality Control
        self.assertIsNotNone(view.quality_menu)
        self.assertIn(view.video_quality_var.get(), ("720p", "1080p"))

        parent.destroy()

    def test_youtube_monitor_table_columns_refactor_regression(self):
        """Regression test for table column refactoring:
        - 3 columns removed (channel, profile, folder)
        - Channel title moved into #0 (Cấu Trúc / Kênh)
        - Safe metadata lookup in _open_folder and _open_profile_picker
        - Grouped vs Flat rendering and 0 seen_count preservation
        - Deleted-column failure path verification (exercising fallback without TclError)
        """
        channels_mock = [
            {
                "channel_id": "UC_TEST_1",
                "title": "あ〜べ〜好きなワイの反応集",
                "profile_name": "Profile_A",
                "active": True,
                "process_short": True,
                "short_min_duration": 30.0,
                "seen_count": 0,  # Critical edge case: 0 must not become empty string
                "folder": "C:/videos/Profile_A",
            },
            {
                "channel_id": "UC_TEST_2",
                "title": "Kênh Tiếng Việt 2",
                "profile_name": "Profile_A",
                "active": False,
                "process_short": False,
                "seen_count": 42,
                "folder": "C:/videos/Profile_A_2",
            },
            {
                "channel_id": "UC_TEST_EMPTY_FOLDER",
                "title": "",  # Empty title tests fallback to tree.item text
                "profile_name": "Profile_A",
                "active": True,
                "process_short": False,
                "seen_count": 5,
                "folder": "",  # Empty folder forces fallback path
            },
        ]
        handlers = {
            "get_profiles": lambda: ["Profile_A"],
            "get_status": lambda: {"running": False},
            "get_channels": lambda: channels_mock,
            "get_logs": lambda: [],
            "get_cookies_file": lambda: "",
            "get_max_video_minutes": lambda: 15,
            "get_profiles_meta": lambda: {"Profile_A": {"project": "Proj1"}},
        }
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)
        view.refresh_data()

        # 1. Check exact 4 columns
        expected_cols = ("channel_id", "active", "short", "seen")
        self.assertEqual(view.tree["columns"], expected_cols)

        # 2. Check #0 heading and attributes
        self.assertEqual(view.tree.heading("#0")["text"], "Cấu Trúc / Kênh")
        self.assertEqual(int(view.tree.column("#0")["width"]), 360)
        self.assertEqual(int(view.tree.column("#0")["minwidth"]), 220)
        self.assertTrue(bool(view.tree.column("#0")["stretch"]))

        # 3. Check column widths and stretch
        self.assertEqual(int(view.tree.column("channel_id")["width"]), 240)
        self.assertTrue(bool(view.tree.column("channel_id")["stretch"]))
        self.assertEqual(int(view.tree.column("active")["width"]), 130)
        self.assertFalse(bool(view.tree.column("active")["stretch"]))
        self.assertEqual(int(view.tree.column("short")["width"]), 160)
        self.assertFalse(bool(view.tree.column("short")["stretch"]))
        self.assertEqual(int(view.tree.column("seen")["width"]), 90)
        self.assertFalse(bool(view.tree.column("seen")["stretch"]))

        # 4. Check Grouped Rendering: Group Node
        root_children = view.tree.get_children("")
        self.assertEqual(len(root_children), 1)
        group_iid = root_children[0]
        group_text = view.tree.item(group_iid, "text")
        self.assertIn("[Profile] Profile_A", group_text)
        self.assertIn("3 kênh", group_text)
        self.assertEqual(tuple(view.tree.item(group_iid, "values")), ("", "", "", ""))

        # 5. Check Grouped Rendering: Child Nodes (Title in #0 text)
        children = view.tree.get_children(group_iid)
        self.assertEqual(len(children), 3)
        
        # Child 1: seen_count == 0 preserved, title in text
        item1 = view.tree.item("UC_TEST_1")
        self.assertEqual(item1["text"], "あ〜べ〜好きなワイの反応集")
        vals1 = tuple(item1["values"])
        self.assertEqual(len(vals1), 4)
        self.assertEqual(vals1[0], "UC_TEST_1")
        self.assertEqual(vals1[1], "● Hoạt động")
        self.assertIn("30–60s", vals1[2])
        self.assertEqual(int(vals1[3]), 0)

        # Child 2:
        item2 = view.tree.item("UC_TEST_2")
        self.assertEqual(item2["text"], "Kênh Tiếng Việt 2")
        vals2 = tuple(item2["values"])
        self.assertEqual(vals2[0], "UC_TEST_2")
        self.assertEqual(vals2[1], "Ⅱ Tạm dừng")
        self.assertEqual(vals2[2], "⚪ Giữ nguyên")
        self.assertEqual(int(vals2[3]), 42)

        # Child 3 (Empty title falls back to CID in display_name):
        item3 = view.tree.item("UC_TEST_EMPTY_FOLDER")
        self.assertEqual(item3["text"], "UC_TEST_EMPTY_FOLDER")

        # 6. Check Flat Mode Rendering
        view._on_view_mode_change("📄 Phẳng")
        self.assertEqual(view.tree.heading("#0")["text"], "Tên Kênh / Handle")
        flat_items = view.tree.get_children("")
        self.assertEqual(len(flat_items), 3)
        flat_item1 = view.tree.item("UC_TEST_1")
        self.assertEqual(flat_item1["text"], "あ〜べ〜好きなワイの反応集")
        self.assertEqual(tuple(flat_item1["values"])[0], "UC_TEST_1")

        # Switch back to grouped
        view._on_view_mode_change("📁 Nhóm")
        self.assertEqual(view.tree.heading("#0")["text"], "Cấu Trúc / Kênh")

        # 7. Safe metadata access: _open_folder with empty/missing folder
        # Tests the fallback block: does NOT raise TclError when 'folder' column is deleted
        with patch.object(view, "append_log") as mock_log:
            view._open_folder("UC_TEST_EMPTY_FOLDER")
            mock_log.assert_called_with("Kênh UC_TEST_EMPTY_FOLDER chưa có thư mục lưu video được cấu hình", error=True)

        with patch("os.path.exists", return_value=True), patch("os.startfile") as mock_startfile:
            view._open_folder("UC_TEST_1")
            mock_startfile.assert_called_once()
        
        # 8. Safe metadata access: _open_profile_picker directly invoked & asserted
        with patch("ui_dialogs.SearchableProfilePickerModal") as mock_picker:
            # Case A: Normal channel with title
            view._open_profile_picker("UC_TEST_1")
            mock_picker.assert_called_once()
            call_kwargs = mock_picker.call_args[1]
            self.assertEqual(call_kwargs["channel_title"], "あ〜べ〜好きなワイの反応集")
            self.assertEqual(call_kwargs["current_profile"], "Profile_A")
            self.assertEqual(call_kwargs["channel_id"], "UC_TEST_1")

        with patch("ui_dialogs.SearchableProfilePickerModal") as mock_picker_fallback:
            # Case B: Empty title channel - extracts from tree_text
            view._open_profile_picker("UC_TEST_EMPTY_FOLDER")
            mock_picker_fallback.assert_called_once()
            call_kwargs_b = mock_picker_fallback.call_args[1]
            self.assertEqual(call_kwargs_b["channel_title"], "UC_TEST_EMPTY_FOLDER")
            self.assertEqual(call_kwargs_b["current_profile"], "Profile_A")

        parent.destroy()

    def test_batch_download_view_structure(self):
        """Test BatchDownloadView elements and state."""
        handlers = {
            "get_profiles": lambda: ["Profile_A"],
            "get_max_video_minutes": lambda: 20,
        }
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, handlers)
        self.assertIsNotNone(view.links_text)
        self.assertIsNotNone(view.btn_start)
        self.assertIsNotNone(view.btn_stop)
        self.assertEqual(view.max_minutes_var.get(), "20")
        parent.destroy()

    def test_activity_view_structure(self):
        """Test ActivityLogView columns and filters."""
        handlers = {
            "get_mtime": lambda: 1234567,
            "get_stats": lambda: {"download_success": 5, "upload_success": 3},
            "get_logs": lambda **kw: [
                {
                    "time": "12:00:00",
                    "type": "youtube_download",
                    "status": "success",
                    "profile": "Profile_1",
                    "video_name": "Test Vid",
                    "video_url": "https://youtube.com/watch?v=123",
                    "detail": "OK",
                    "file_path": "C:/path.mp4",
                }
            ],
        }
        parent = ctk.CTkFrame(self.root)
        view = ActivityLogView(parent, handlers)
        expected_cols = ("time", "type", "status", "profile", "video_name", "video_url", "detail", "file_path")
        self.assertEqual(view.tree["columns"], expected_cols)
        parent.destroy()

    def test_youtube_studio_workspace_subtabs_and_router_aliases(self):
        """Test dashboard router aliases: 'youtube', 'batch', 'history' all route to YouTube Studio with proper sub-tabs."""
        state = {
            "selected_project_var": ctk.StringVar(value="Tất cả"),
            "filter_var": ctk.StringVar(value=""),
            "scale_var": ctk.StringVar(value="100%"),
            "header_total_label": ctk.StringVar(value="0"),
            "header_running_label": ctk.StringVar(value="0"),
            "header_project_label": ctk.StringVar(value="Tất cả"),
        }
        handlers = {
            "create_project": lambda: None,
            "delete_project": lambda: None,
            "add_profile": lambda: None,
            "batch_add_profiles": lambda: None,
            "edit_profile": lambda: None,
            "view_profile_details": lambda: None,
            "export_profiles": lambda: None,
            "delete_profile": lambda: None,
            "rename_profile": lambda: None,
            "assign_to_project": lambda: None,
            "show_statistics_board": lambda: None,
            "open_browser": lambda: None,
            "get_tiktok_cookies": lambda: None,
            "check_cookie_live": lambda: None,
            "inspect_tiktok_account": lambda: None,
            "clean_browser": lambda: None,
            "change_license_key": lambda: None,
            "check_update": lambda: None,
            "clear_failed_uploads_panel": lambda: None,
            "cleanup_failed_videos": lambda: None,
            "start_selected_batch": lambda: None,
            "stop_selected_batch": lambda: None,
            "start_all_in_project": lambda: None,
            "stop_all_in_project": lambda: None,
            "copy_folder_path": lambda: None,
            "copy_channel_link": lambda: None,
            "sort_tree": lambda *a: None,
            "youtube_monitor": {},
            "activity": {},
        }
        widgets = build_dashboard(self.root, state, handlers)

        # 1. Check all 3 views exist in widgets dictionary
        self.assertIn("youtube_monitor_view", widgets)
        self.assertIn("batch_download_view", widgets)
        self.assertIn("activity_view", widgets)
        self.assertIn("switch_youtube_subtab", widgets)

        switch_ws = widgets["switch_workspace"]

        # 2. Test Router Aliases
        # switch to batch alias
        switch_ws("batch")
        # Ensure batch view is alive and preserved
        self.assertIsInstance(widgets["batch_download_view"], BatchDownloadView)

        # switch to history alias
        switch_ws("history")
        self.assertIsInstance(widgets["activity_view"], ActivityLogView)

        # switch to youtube alias
        switch_ws("youtube")
        self.assertIsInstance(widgets["youtube_monitor_view"], YouTubeMonitorView)

        widgets["main_container"].destroy()


if __name__ == "__main__":
    unittest.main()
