"""
test_app_ui_contract.py - Unit tests verifying app_ui.build_dashboard contract
"""

import unittest
import customtkinter as ctk

from app_ui import build_dashboard, configure_ttk_styles, classify_log_message


class TestAppUIContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create a hidden test root
        try:
            cls.root = ctk.CTk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root:
            try:
                for child in cls.root.winfo_children():
                    child.destroy()
            except Exception:
                pass

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

    def test_build_dashboard_returns_all_contract_widgets(self):
        """build_dashboard() must return every widget required by main.py."""
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

        # Essential keys checked by main.py
        required_keys = [
            "topbar",
            "manage_frame",
            "control_frame",
            "project_dropdown",
            "tree",
            "important_log_text",
            "failed_uploads_text",
            "status_text",
            "status_count_label",
            "clock_label",
            "youtube_monitor_view",
            "batch_download_view",
            "activity_view",
            "ctx_menu",
            "btn_start_all",
            "btn_stop_all",
            "btn_start_selected",
            "btn_stop_selected",
            "btn_check_cookie",
            "sidebar",
            "project_list_view",
            "log_drawer",
            "switch_workspace",
            "guide_workspace",
            "guide_view",
            "stats_workspace",
            "stats_view",
        ]

        for key in required_keys:
            self.assertIn(key, widgets, f"Missing required widget key: {key}")

        # Check Treeview columns
        tree = widgets["tree"]
        expected_columns = (
            'name', 'tiktok', 'cookie_st', 'activity', 'monetization',
            'proxy_region', 'upload', 'folder', 'last_error'
        )
        self.assertEqual(tree["columns"], expected_columns)

        # Test workspace router switching to statistics and aliases
        switch_ws = widgets["switch_workspace"]
        switch_ws("statistics")
        self.assertTrue(widgets["stats_workspace"].winfo_manager())
        self.assertFalse(widgets["guide_workspace"].winfo_manager())

        switch_ws("stats")
        self.assertTrue(widgets["stats_workspace"].winfo_manager())

        switch_ws("guide")
        self.assertTrue(widgets["guide_workspace"].winfo_manager())
        self.assertFalse(widgets["stats_workspace"].winfo_manager())

    def test_classify_log_message(self):
        """Log classifier returns correct base_tag and important_tag."""
        self.assertEqual(classify_log_message("Upload failed with error 403")[0], "ERROR")
        self.assertEqual(classify_log_message("Proxy ok và sẵn sàng")[1], "INFO")


if __name__ == "__main__":
    unittest.main()
