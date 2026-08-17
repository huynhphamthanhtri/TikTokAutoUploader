"""
test_add_profile_dialog.py - Comprehensive Unit Tests for the Modernized Add Profile Modal.
"""

import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile
import customtkinter as ctk
import main


class TestAddProfileDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.LICENSE_OK = True

    @classmethod
    def tearDownClass(cls):
        try:
            for after_id in main.root.tk.eval('after info').split():
                try:
                    main.root.after_cancel(after_id)
                except Exception:
                    pass
            main.root.update_idletasks()
        except Exception:
            pass

    def test_add_profile_contract_in_main(self):
        root_dir = Path(__file__).resolve().parent
        main_src = (root_dir / "main.py").read_text(encoding="utf-8")
        self.assertIn("def add_profile():", main_src)
        self.assertIn("DONGLAO-TIKTOK — Thêm Hồ Sơ Mới", main_src)
        self.assertIn("_apply_quick_paste", main_src)
        self.assertIn("_test_proxy_live", main_src)
        self.assertIn("_sync_auto_paths", main_src)

    @patch("main.messagebox.showerror")
    @patch("main.toast_manager.enqueue")
    @patch("main.save_configs")
    def test_add_profile_full_save_flow(self, mock_save, mock_toast, mock_error):
        test_name = "Unit_Test_Profile_99"
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Ensure cleanup
            if test_name in main.profiles:
                del main.profiles[test_name]
                
            try:
                # Open modal
                main.add_profile()
                main.root.update_idletasks()
                
                # Verify that TopLevel was created
                toplevels = [w for w in main.root.winfo_children() if isinstance(w, ctk.CTkToplevel)]
                self.assertTrue(len(toplevels) > 0)
                dlg = toplevels[-1]
                self.assertEqual(dlg.title(), "DONGLAO-TIKTOK — Thêm Hồ Sơ Mới")
            finally:
                if test_name in main.profiles:
                    del main.profiles[test_name]
                for w in main.root.winfo_children():
                    if isinstance(w, ctk.CTkToplevel):
                        try:
                            w.destroy()
                        except Exception:
                            pass


if __name__ == "__main__":
    unittest.main()
