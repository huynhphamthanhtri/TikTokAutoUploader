"""
test_ui_dialogs.py - Unit tests for ui_dialogs.py
"""

import unittest
import customtkinter as ctk

from ui_dialogs import BatchSetProxyModal, MonetizationDetailModal, CreateEditProfileModal


class TestUIDialogs(unittest.TestCase):
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
                cls.root.destroy()
            except Exception:
                pass

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

    def test_batch_set_proxy_modal_parse_and_preview(self):
        """BatchSetProxyModal parses proxy lines, reports invalid lines, and masks password."""
        saved_data = {}

        def mock_save(data):
            saved_data.update(data)
            return True

        dialog = BatchSetProxyModal(
            self.root,
            selected_profiles=["prof_1", "prof_2"],
            on_save=mock_save,
        )

        # Input 2 lines: 1 valid with auth, 1 valid without auth, 1 invalid line
        raw_text = "1.2.3.4:8080:admin:secretpass\n5.6.7.8:9090\ninvalid_proxy_line"
        dialog.text_input.insert("1.0", raw_text)
        valid, invalid = dialog._parse_lines()

        self.assertEqual(len(valid), 2)
        self.assertEqual(invalid, [3])  # Line 3 is invalid

        dialog._update_preview()
        preview_content = dialog.preview_box.get("1.0", "end")
        self.assertNotIn("secretpass", preview_content)
        self.assertIn("admin:***", preview_content)

        # Apply save
        dialog._apply_save()
        self.assertIn("prof_1", saved_data)
        self.assertEqual(saved_data["prof_1"]["proxy_string"], "1.2.3.4:8080:admin:secretpass")

    def test_create_edit_profile_modal_save_contract(self):
        """CreateEditProfileModal packages config and invokes on_save."""
        saved_config = {}

        def mock_save(cfg):
            saved_config.update(cfg)
            return True

        dialog = CreateEditProfileModal(
            self.root,
            title="Tạo Profile Mới",
            initial_config={"profile_name": "Test_Profile", "tiktok_account": "test_user"},
            available_projects=["Mặc định", "Dự Án 1"],
            on_save=mock_save,
        )

        dialog.name_var.set("Test_Profile_Updated")
        dialog.project_var.set("Dự Án 1")
        dialog._save_clicked()

        self.assertEqual(saved_config["profile_name"], "Test_Profile_Updated")
        self.assertEqual(saved_config["project_name"], "Dự Án 1")


if __name__ == "__main__":
    unittest.main()
