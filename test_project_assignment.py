"""
test_project_assignment.py - Unit & Regression Tests for Bulk Project Assignment & UI Modals
"""

import os
import sys
if sys.platform == "win32":
    tcl_dir = os.path.join(sys.prefix, "tcl", "tcl8.6")
    tk_dir = os.path.join(sys.prefix, "tcl", "tk8.6")
    if os.path.exists(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.exists(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
import tkinter as tk
import customtkinter as ctk

# Safe import of main for test environment
try:
    with patch("customtkinter.CTk.mainloop"):
        import main
except Exception:
    import main


class TestAssignProfilesCentralLogic(unittest.TestCase):
    def setUp(self):
        self.orig_profiles = main.profiles
        self.orig_projects = main.projects
        self.orig_selected_project = main.selected_project_var.get()
        main.profiles = {
            "Profile_A": {
                "project": "Project_1",
                "config": {"project_name": "Project_1", "email": "a@test.com"},
                "running": False,
                "uploads_today_count": 0,
                "uploads_yesterday_count": 0,
                "uploads_today_date": datetime.now().strftime("%Y-%m-%d"),
            },
            "Profile_B": {
                "project": "Project_1",
                "config": {"project_name": "Project_1", "email": "b@test.com"},
                "running": False,
                "uploads_today_count": 0,
                "uploads_yesterday_count": 0,
                "uploads_today_date": datetime.now().strftime("%Y-%m-%d"),
            },
            "Profile_C": {
                "project": "Project_2",
                "config": {"project_name": "Project_2", "email": "c@test.com"},
                "running": False,
                "uploads_today_count": 0,
                "uploads_yesterday_count": 0,
                "uploads_today_date": datetime.now().strftime("%Y-%m-%d"),
            },
        }
        main.projects = {
            "Mặc định": set(),
            "Project_1": {"Profile_A", "Profile_B"},
            "Project_2": {"Profile_C"},
            "Project_Target": set(),
        }

    def tearDown(self):
        import main
        main.profiles = self.orig_profiles
        main.projects = self.orig_projects
        main.selected_project_var.set(self.orig_selected_project)

    def test_single_profile_assignment(self):
        import main
        with patch.object(main, "save_configs") as mock_save, \
             patch.object(main, "update_profile_list") as mock_upl, \
             patch.object(main, "update_project_dropdown") as mock_upd, \
             patch.object(main, "_refresh_status_bar") as mock_rsb, \
             patch.object(main, "update_status") as mock_us:
            changed, skipped, msg = main._assign_profiles_to_project(["Profile_A"], "Project_Target")

        self.assertEqual(changed, ["Profile_A"])
        self.assertEqual(skipped, [])
        self.assertEqual(main.profiles["Profile_A"]["project"], "Project_Target")
        self.assertEqual(main.profiles["Profile_A"]["config"]["project_name"], "Project_Target")
        self.assertIn("Profile_A", main.projects["Project_Target"])
        self.assertNotIn("Profile_A", main.projects["Project_1"])
        mock_save.assert_called_once()
        mock_upl.assert_called_once()
        mock_upd.assert_called_once()
        mock_rsb.assert_called_once()

    def test_bulk_multi_profile_assignment(self):
        import main
        with patch.object(main, "save_configs") as mock_save, \
             patch.object(main, "update_profile_list"), \
             patch.object(main, "update_project_dropdown"), \
             patch.object(main, "_refresh_status_bar"), \
             patch.object(main, "update_status"):
            changed, skipped, msg = main._assign_profiles_to_project(["Profile_A", "Profile_C"], "Project_Target")

        self.assertEqual(set(changed), {"Profile_A", "Profile_C"})
        self.assertEqual(skipped, [])

        # Invariant checks for Profile_A
        self.assertEqual(main.profiles["Profile_A"]["project"], "Project_Target")
        self.assertEqual(main.profiles["Profile_A"]["config"]["project_name"], "Project_Target")
        self.assertIn("Profile_A", main.projects["Project_Target"])
        self.assertNotIn("Profile_A", main.projects["Project_1"])

        # Invariant checks for Profile_C
        self.assertEqual(main.profiles["Profile_C"]["project"], "Project_Target")
        self.assertEqual(main.profiles["Profile_C"]["config"]["project_name"], "Project_Target")
        self.assertIn("Profile_C", main.projects["Project_Target"])
        self.assertNotIn("Profile_C", main.projects["Project_2"])

        mock_save.assert_called_once()

    def test_repair_duplicate_stale_membership(self):
        import main
        # Corrupt state: Profile_A is accidentally in both Project_1 and Project_2
        main.projects["Project_2"].add("Profile_A")

        with patch.object(main, "save_configs"), \
             patch.object(main, "update_profile_list"), \
             patch.object(main, "update_project_dropdown"), \
             patch.object(main, "_refresh_status_bar"), \
             patch.object(main, "update_status"):
            changed, skipped, msg = main._assign_profiles_to_project(["Profile_A"], "Project_Target")

        self.assertIn("Profile_A", main.projects["Project_Target"])
        self.assertNotIn("Profile_A", main.projects["Project_1"])
        self.assertNotIn("Profile_A", main.projects["Project_2"])

    def test_repair_stale_membership_already_at_target_persists(self):
        import main
        main.profiles["Profile_A"]["project"] = "Project_Target"
        main.profiles["Profile_A"]["config"]["project_name"] = "Project_Target"
        main.projects["Project_1"].discard("Profile_A")
        main.projects["Project_Target"].add("Profile_A")
        main.projects["Project_2"].add("Profile_A")

        with patch.object(main, "save_configs") as mock_save, \
             patch.object(main, "update_profile_list"), \
             patch.object(main, "update_project_dropdown"), \
             patch.object(main, "_refresh_status_bar"), \
             patch.object(main, "update_status"):
            changed, skipped, _msg = main._assign_profiles_to_project(["Profile_A"], "Project_Target")

        self.assertEqual(changed, ["Profile_A"])
        self.assertEqual(skipped, [])
        self.assertNotIn("Profile_A", main.projects["Project_2"])
        mock_save.assert_called_once()

    def test_idempotent_reassignment_does_not_save_unnecessarily(self):
        import main
        with patch.object(main, "save_configs") as mock_save:
            changed, skipped, msg = main._assign_profiles_to_project(["Profile_A"], "Project_1")

        self.assertEqual(changed, [])
        self.assertEqual(skipped, [])
        self.assertIn("Tất cả hồ sơ đã thuộc", msg)
        mock_save.assert_not_called()

    def test_invalid_target_project_rejected(self):
        import main
        with patch.object(main, "save_configs") as mock_save:
            changed, skipped, msg = main._assign_profiles_to_project(["Profile_A"], "NonExistentProject")

        self.assertEqual(changed, [])
        self.assertEqual(skipped, ["Profile_A"])
        self.assertIn("không hợp lệ", msg)
        mock_save.assert_not_called()

    def test_stale_deleted_profile_skipped_safely(self):
        import main
        with patch.object(main, "save_configs") as mock_save, \
             patch.object(main, "update_profile_list"), \
             patch.object(main, "update_project_dropdown"), \
             patch.object(main, "_refresh_status_bar"), \
             patch.object(main, "update_status"):
            changed, skipped, msg = main._assign_profiles_to_project(["Profile_A", "GhostProfile"], "Project_Target")

        self.assertEqual(changed, ["Profile_A"])
        self.assertEqual(skipped, ["GhostProfile"])
        mock_save.assert_called_once()

    def test_profile_project_reader_fallback(self):
        import main
        # 1. Standard synced profile
        self.assertEqual(main._profile_project({"project": "P1", "config": {"project_name": "P1"}}), "P1")
        # 2. Root project only
        self.assertEqual(main._profile_project({"project": "P2", "config": {}}), "P2")
        # 3. Legacy config only
        self.assertEqual(main._profile_project({"config": {"project_name": "P3"}}), "P3")
        # 4. Default fallback
        self.assertEqual(main._profile_project({}), "Mặc định")
        self.assertEqual(main._profile_project(None), "Mặc định")


class TestDeleteProjectMigration(unittest.TestCase):
    def setUp(self):
        import main
        self.orig_profiles = main.profiles
        self.orig_projects = main.projects
        self.orig_selected_project = main.selected_project_var.get()
        main.profiles = {
            "Prof_1": {
                "project": "ToDel", "config": {"project_name": "ToDel"}, "running": False,
                "uploads_today_count": 0, "uploads_yesterday_count": 0,
                "uploads_today_date": datetime.now().strftime("%Y-%m-%d"),
            },
            "Prof_2": {
                "project": "ToDel", "config": {"project_name": "ToDel"}, "running": False,
                "uploads_today_count": 0, "uploads_yesterday_count": 0,
                "uploads_today_date": datetime.now().strftime("%Y-%m-%d"),
            },
        }
        main.projects = {
            "Mặc định": set(),
            "ToDel": {"Prof_1", "Prof_2"},
        }
        main.selected_project_var.set("ToDel")

    def tearDown(self):
        import main
        main.profiles = self.orig_profiles
        main.projects = self.orig_projects
        main.selected_project_var.set(self.orig_selected_project)

    def test_delete_project_migrates_all_representations_to_default(self):
        import main
        with patch.object(main, "_license_guard", return_value=True), \
             patch("tkinter.messagebox.askyesno", return_value=True), \
             patch.object(main, "save_configs") as mock_save, \
             patch.object(main, "update_profile_list"), \
             patch.object(main, "update_project_dropdown"), \
             patch.object(main, "_refresh_status_bar"), \
             patch.object(main, "update_status"):
            main.delete_project()

        self.assertNotIn("ToDel", main.projects)
        self.assertEqual(main.profiles["Prof_1"]["project"], "Mặc định")
        self.assertEqual(main.profiles["Prof_1"]["config"]["project_name"], "Mặc định")
        self.assertEqual(main.profiles["Prof_2"]["project"], "Mặc định")
        self.assertEqual(main.profiles["Prof_2"]["config"]["project_name"], "Mặc định")
        self.assertIn("Prof_1", main.projects["Mặc định"])
        self.assertIn("Prof_2", main.projects["Mặc định"])
        self.assertEqual(main.selected_project_var.get(), main.ALL_OPTION)
        mock_save.assert_called_once()

    def test_delete_project_migrates_profile_missing_from_membership_set(self):
        import main
        main.projects["ToDel"].discard("Prof_2")

        with patch.object(main, "_license_guard", return_value=True), \
             patch("tkinter.messagebox.askyesno", return_value=True), \
             patch.object(main, "save_configs"), \
             patch.object(main, "update_profile_list"), \
             patch.object(main, "update_project_dropdown"), \
             patch.object(main, "_refresh_status_bar"), \
             patch.object(main, "update_status"):
            main.delete_project()

        self.assertEqual(main.profiles["Prof_2"]["project"], "Mặc định")
        self.assertEqual(main.profiles["Prof_2"]["config"]["project_name"], "Mặc định")
        self.assertIn("Prof_2", main.projects["Mặc định"])


class TestAssignProjectModalUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("CI", "").lower() == "true":
            cls.root = None
            return
        try:
            cls.root = ctk.CTk()
            cls.root.withdraw()
        except Exception:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def test_modal_initial_state_and_inline_creation(self):
        if not self.root:
            self.skipTest("Tkinter display not available")
        from ui_dialogs import AssignProjectModal

        created_projects = []
        assignments = []

        def mock_create(name):
            created_projects.append(name)
            return True, ""

        def mock_assign(profs, target):
            assignments.append((profs, target))
            return True, ""

        modal = AssignProjectModal(
            parent=self.root,
            selected_profiles=["Profile_A", "Profile_B"],
            profile_projects={"Profile_A": "P1", "Profile_B": "P2"},
            project_counts={"Mặc định": 0, "P1": 1, "P2": 1},
            on_create_project=mock_create,
            on_assign=mock_assign,
        )

        # 1. Mixed selection shows placeholder or valid selection
        self.assertIsNotNone(modal.project_menu)
        self.assertIsNotNone(modal.btn_save)

        # 2. Test inline project creation
        modal.new_proj_name_var.set("New_Project_99")
        modal._handle_create_new_project()
        self.assertIn("New_Project_99", created_projects)
        self.assertEqual(modal.selected_target_proj.get(), "New_Project_99")

        # 3. Test Save assignment
        modal._handle_save_assignment()
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0][0], ["Profile_A", "Profile_B"])
        self.assertEqual(assignments[0][1], "New_Project_99")

        try:
            modal.destroy()
        except Exception:
            pass

    def test_selection_action_bar_count_and_assign_handler(self):
        if not self.root:
            self.skipTest("Tkinter display not available")
        from ui_components import SelectionActionBar

        assign_handler = MagicMock()
        bar = SelectionActionBar(
            self.root,
            handlers={
                "start_selected": MagicMock(),
                "stop_selected": MagicMock(),
                "check_cookie": MagicMock(),
                "assign_to_project": assign_handler,
            },
        )
        try:
            bar.set_selection_count(3)
            self.assertEqual(bar.count_var.get(), "Đã chọn: 3")
            assign_button = next(
                child for child in bar.winfo_children()
                if isinstance(child, ctk.CTkButton) and child.cget("text") == "📁 Gán DA"
            )
            assign_button.invoke()
            assign_handler.assert_called_once()
        finally:
            bar.destroy()


if __name__ == "__main__":
    unittest.main()
