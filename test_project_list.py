"""
test_project_list.py - Keyed reconciliation tests for ProjectList sidebar buttons.

ProjectList.update_projects() must never destroy/recreate buttons that already exist:
the previous destroy-everything approach raced CTk's <Configure> redraw and produced
``_tkinter.TclError: invalid command name ... ctkcanvas`` during profile refresh.
"""

import os
import unittest
import customtkinter as ctk

from ui_components import ProjectList, UIThemeTokens


@unittest.skipIf(os.environ.get("CI", "").lower() == "true", "Interactive Tk rendering is verified on a desktop runner")
class TestProjectList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")
        self.project_list = ProjectList(self.root)
        self.project_list.pack(fill="x", padx=6, pady=2)
        self.root.update_idletasks()

    def tearDown(self):
        if self.project_list.winfo_exists():
            self.project_list.destroy()
        self.root.update_idletasks()

    def test_same_projects_keep_button_identity(self):
        counts = {"P1": 2, "P2": 5}
        self.project_list.update_projects(counts, active_project="P1")
        first = dict(self.project_list._buttons)
        self.project_list.update_projects(counts, active_project="P1")
        self.project_list.update_projects(counts, active_project="P1")
        self.assertEqual(set(self.project_list._buttons), {"P1", "P2"})
        self.assertIs(self.project_list._buttons["P1"], first["P1"])
        self.assertIs(self.project_list._buttons["P2"], first["P2"])

    def test_count_change_updates_text_in_place(self):
        self.project_list.update_projects({"P1": 2}, active_project="P1")
        button = self.project_list._buttons["P1"]
        self.assertEqual(button.cget("text"), "P1 (2)")
        self.project_list.update_projects({"P1": 9}, active_project="P1")
        self.assertIs(self.project_list._buttons["P1"], button)
        self.assertEqual(button.cget("text"), "P1 (9)")

    def test_active_project_change_only_updates_style(self):
        self.project_list.update_projects({"P1": 1, "P2": 1}, active_project="P1")
        button_p1 = self.project_list._buttons["P1"]
        button_p2 = self.project_list._buttons["P2"]
        self.assertEqual(button_p1.cget("fg_color"), UIThemeTokens.BG_SIDEBAR_ACTIVE)
        self.assertEqual(button_p2.cget("fg_color"), "transparent")
        self.project_list.update_projects({"P1": 1, "P2": 1}, active_project="P2")
        self.assertIs(self.project_list._buttons["P1"], button_p1)
        self.assertIs(self.project_list._buttons["P2"], button_p2)
        self.assertEqual(button_p1.cget("fg_color"), "transparent")
        self.assertEqual(button_p2.cget("fg_color"), UIThemeTokens.BG_SIDEBAR_ACTIVE)

    def test_new_project_creates_exactly_one_button(self):
        self.project_list.update_projects({"P1": 1}, active_project="P1")
        baseline = set(self.project_list._buttons)
        self.project_list.update_projects({"P1": 1, "P3": 4}, active_project="P1")
        self.assertEqual(set(self.project_list._buttons) - baseline, {"P3"})
        self.assertIn("P3", self.project_list._buttons)

    def test_removed_project_destroys_only_that_button(self):
        self.project_list.update_projects({"P1": 1, "P2": 1, "P3": 1}, active_project="P1")
        removed = self.project_list._buttons["P2"]
        self.project_list.update_projects({"P1": 1, "P3": 1}, active_project="P1")
        self.assertEqual(set(self.project_list._buttons), {"P1", "P3"})
        self.assertEqual(removed.winfo_exists(), 0)
        self.assertIn("P1", self.project_list._buttons)
        self.assertIn("P3", self.project_list._buttons)

    def test_consecutive_updates_create_no_surplus_widgets(self):
        # The app passes the full project set every refresh; buttons must be reused
        # in place, never recreated, so the widget count stays equal to the count.
        for i in range(20):
            counts = {"P1": i, "P2": i * 2}
            self.project_list.update_projects(counts, active_project="P1")
            self.assertEqual(len(self.project_list._buttons), 2)
            self.root.update_idletasks()

    def test_stress_refresh_never_raises_tclerror(self):
        counts = {f"PROJECT_{i}": i for i in range(1, 6)}
        try:
            for _ in range(100):
                self.project_list.update_projects(counts, active_project="PROJECT_1")
                self.root.update_idletasks()
        except Exception as error:  # noqa: BLE001 - any TclError here is a regression
            self.fail(f"refresh churn raised {type(error).__name__}: {error}")
        self.assertEqual(len(self.project_list._buttons), 5)


if __name__ == "__main__":
    unittest.main()