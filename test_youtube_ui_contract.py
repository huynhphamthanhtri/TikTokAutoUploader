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

        # 1. Verify Treeview columns
        expected_cols = ("channel", "channel_id", "profile", "active", "short", "seen", "folder")
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
