"""
test_delete_profile.py - Comprehensive Unit Tests for Profile Deletion Logic
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import main


class TestDeleteProfileLogic(unittest.TestCase):
    def setUp(self):
        # Backup global states
        self.orig_profiles = dict(main.profiles)
        self.orig_projects = {k: set(v) for k, v in main.projects.items()}
        self.orig_monetization_cache = dict(main.monetization_cache)

        # Clear and initialize test state
        main.profiles.clear()
        main.projects.clear()
        main.projects["Mặc định"] = set()
        main.monetization_cache.clear()

        self.tmp_dir = tempfile.mkdtemp(prefix="test_del_prof_")

    def tearDown(self):
        # Restore global states
        main.profiles.clear()
        main.profiles.update(self.orig_profiles)
        main.projects.clear()
        main.projects.update(self.orig_projects)
        main.monetization_cache.clear()
        main.monetization_cache.update(self.orig_monetization_cache)

        if os.path.exists(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch("main._license_guard", return_value=True)
    @patch("main.messagebox.askyesno")
    @patch("main.save_configs")
    @patch("main.update_profile_list")
    def test_delete_single_profile(self, mock_update_ui, mock_save, mock_ask, mock_lic):
        """Deleting a single profile removes it from profiles, project set, and refreshes UI."""
        mock_ask.side_effect = [True, False]  # 1st: confirm delete, 2nd: don't delete disk

        # Mock profile
        main.profiles["Profile_Alpha"] = {
            "running": False,
            "session_busy": False,
            "manual_driver": None,
            "project": "Mặc định",
            "config": {},
        }
        main.projects["Mặc định"].add("Profile_Alpha")
        main.monetization_cache["Profile_Alpha"] = {"kyc_status": "APPROVED"}

        # Mock tree selection
        main.tree.selection = MagicMock(return_value=["item_1"])
        main.tree.item = MagicMock(return_value={"values": ["Profile_Alpha"]})

        main.delete_profile()

        self.assertNotIn("Profile_Alpha", main.profiles)
        self.assertNotIn("Profile_Alpha", main.projects["Mặc định"])
        self.assertNotIn("Profile_Alpha", main.monetization_cache)
        mock_save.assert_called_once()
        mock_update_ui.assert_called_once()

    @patch("main._license_guard", return_value=True)
    @patch("main.messagebox.askyesno")
    @patch("main.save_configs")
    @patch("main.update_profile_list")
    def test_delete_multi_profiles_batch(self, mock_update_ui, mock_save, mock_ask, mock_lic):
        """Deleting multiple selected profiles deletes all of them in a single batch operation."""
        mock_ask.side_effect = [True, False]  # 1st: confirm delete, 2nd: don't delete disk

        # Setup 3 profiles
        for i in range(1, 4):
            name = f"Profile_{i}"
            main.profiles[name] = {
                "running": False,
                "session_busy": False,
                "manual_driver": None,
                "project": "Mặc định",
                "config": {},
            }
            main.projects["Mặc định"].add(name)
            main.monetization_cache[name] = {"balance": i * 100}

        # Mock multi-selection in tree
        main.tree.selection = MagicMock(return_value=["item_1", "item_2", "item_3"])
        def item_side_effect(iid):
            num = iid.split("_")[1]
            return {"values": [f"Profile_{num}"]}
        main.tree.item = MagicMock(side_effect=item_side_effect)

        main.delete_profile()

        for i in range(1, 4):
            name = f"Profile_{i}"
            self.assertNotIn(name, main.profiles)
            self.assertNotIn(name, main.projects["Mặc định"])
            self.assertNotIn(name, main.monetization_cache)

        mock_save.assert_called_once()
        mock_update_ui.assert_called_once()

    @patch("main._license_guard", return_value=True)
    @patch("main.messagebox.showerror")
    @patch("main.save_configs")
    @patch("main.update_profile_list")
    def test_delete_blocked_when_profile_is_running(self, mock_update_ui, mock_save, mock_error, mock_lic):
        """When any selected profile is currently running or busy, delete is blocked with error."""
        main.profiles["Active_Profile"] = {
            "running": True,  # Running!
            "session_busy": False,
            "manual_driver": None,
            "project": "Mặc định",
            "config": {},
        }
        main.projects["Mặc định"].add("Active_Profile")

        main.tree.selection = MagicMock(return_value=["item_active"])
        main.tree.item = MagicMock(return_value={"values": ["Active_Profile"]})

        main.delete_profile()

        # Should remain in profiles, no save or UI update
        self.assertIn("Active_Profile", main.profiles)
        mock_error.assert_called_once()
        mock_save.assert_not_called()
        mock_update_ui.assert_not_called()

    @patch("main._license_guard", return_value=True)
    @patch("main.messagebox.askyesno")
    @patch("main.save_configs")
    @patch("main.update_profile_list")
    def test_delete_with_disk_cleanup_option(self, mock_update_ui, mock_save, mock_ask, mock_lic):
        """When user selects disk cleanup option, profile directory on disk is removed."""
        mock_ask.side_effect = [True, True]  # 1st: confirm delete, 2nd: YES delete disk

        prof_disk_dir = os.path.join(self.tmp_dir, "test_browser_profile_data")
        os.makedirs(prof_disk_dir, exist_ok=True)
        test_file = os.path.join(prof_disk_dir, "cookie.json")
        with open(test_file, "w") as f:
            f.write("{}")

        self.assertTrue(os.path.isdir(prof_disk_dir))

        main.profiles["Profile_Disk_Clean"] = {
            "running": False,
            "session_busy": False,
            "manual_driver": None,
            "project": "Mặc định",
            "config": {"browser_profile_path": prof_disk_dir},
        }
        main.projects["Mặc định"].add("Profile_Disk_Clean")

        main.tree.selection = MagicMock(return_value=["item_disk"])
        main.tree.item = MagicMock(return_value={"values": ["Profile_Disk_Clean"]})

        main.delete_profile()

        # Check in-memory state
        self.assertNotIn("Profile_Disk_Clean", main.profiles)
        # Check that directory was cleaned up on disk!
        self.assertFalse(os.path.exists(prof_disk_dir))
        mock_save.assert_called_once()
        mock_update_ui.assert_called_once()


if __name__ == "__main__":
    unittest.main()
