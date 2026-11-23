"""
test_batch_add_profiles.py - Tests for batch import dialog and default update policy.
"""

import tempfile
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import main
from account_io import plan_import, parse_data_into_records, parse_format, DEFAULT_FORMAT


class TestBatchAddProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.LICENSE_OK = True

    def test_batch_import_default_policy_in_source(self):
        root_dir = Path(__file__).resolve().parent
        main_src = (root_dir / "main.py").read_text(encoding="utf-8")
        self.assertIn("dup_policy_var = StringVar(opt, value='Cập nhật')", main_src)
        self.assertIn("DONGLAO-TIKTOK — Import Tài Khoản Hàng Loạt", main_src)
        self.assertIn("DONGLAO-TIKTOK — Xuất Dữ Liệu Tài Khoản", main_src)
        # Verify pin-to-bottom layout structure
        self.assertIn("btn_row.pack(side='bottom', fill='x'", main_src)
        self.assertIn("prev_frame.pack(side='bottom', fill='x'", main_src)
        # Verify project ComboBox in batch import dialog
        self.assertIn("project_combo = ctk.CTkComboBox(", main_src)
        self.assertIn("_resolve_import_project(project_combo.get())", main_src)

    def test_plan_import_update_policy(self):
        existing = {"Profile_01", "Profile_02"}
        records = [
            {"name": "Profile_01", "email": "new_email@gmail.com", "cookie_str": "sessionid=xyz"},
            {"name": "Profile_03", "email": "p3@gmail.com"},
        ]
        plans = plan_import(records, existing, policy="update")
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[0]["action"], "update")
        self.assertEqual(plans[1]["action"], "add")

    def test_batch_imported_profile_gets_cxx_template(self):
        from profile_config_engine import generate_stealth_profile_config, write_profile_config_files
        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "Profile"
            cfg = generate_stealth_profile_config(
                account_uuid="batch_uuid_100",
                profile_name="BATCH_PROFILE_100",
            )
            write_profile_config_files(profile_dir, cfg)
            self.assertTrue((profile_dir / "data.huynhthang").exists())
            self.assertTrue((profile_dir / "data.orbita").exists())
            self.assertGreater((profile_dir / "data.huynhthang").stat().st_size, 1000)

    def test_resolve_import_project_rules(self):
        self.assertEqual(main._resolve_import_project(""), "Mặc định")
        self.assertEqual(main._resolve_import_project("   "), "Mặc định")
        self.assertEqual(main._resolve_import_project("  Shop Thời Trang  "), "Shop Thời Trang")
        with self.assertRaises(ValueError):
            main._resolve_import_project(main.ALL_OPTION)

    def test_default_import_project_follows_active_filter(self):
        fake_var = MagicMock()
        fake_projects = {"Mặc định": set(), "Team A": set()}
        with patch.object(main, "projects", fake_projects), patch.object(main, "selected_project_var", fake_var):
            fake_var.get.return_value = "Team A"
            self.assertEqual(main._default_import_project(), "Team A")

            fake_var.get.return_value = main.ALL_OPTION
            self.assertEqual(main._default_import_project(), "Mặc định")

            fake_var.get.return_value = "Deleted Project"
            self.assertEqual(main._default_import_project(), "Mặc định")

    def test_apply_import_plans_assigns_new_project_and_preserves_updated_profile_project(self):
        with tempfile.TemporaryDirectory() as td:
            fake_profiles = {
                "existing_acc": {
                    "config": {
                        "folder_path": str(Path(td) / "Old/Video"),
                        "chrome_profile": str(Path(td) / "Old/Profile"),
                        "project_name": "Dự án Cũ",
                        "email": "old@gmail.com",
                        "cookie_str": "old=1",
                    },
                    "project": "Dự án Cũ",
                }
            }
            fake_projects = {"Mặc định": set(), "Dự án Cũ": {"existing_acc"}}

            fields = parse_format("email|idTiktok|cookie")
            text = "\n".join([
                "new@gmail.com|@existing_acc|cookie=updated",
                "shop@gmail.com|https://www.tiktok.com/@shop_new|cookie=new1",
                "onlymail@gmail.com||cookie=new2",
            ])
            records, errors = parse_data_into_records(text, fields)
            self.assertEqual(errors, [])
            plans = plan_import(records, set(fake_profiles.keys()), policy="update")

            with patch.object(main, "profiles", fake_profiles), \
                 patch.object(main, "projects", fake_projects), \
                 patch.object(main, "app_base_dir", return_value=Path(td)), \
                 patch.object(main, "update_status"):
                result = main._apply_import_plans(plans, proxy_type="http", default_project="Dự án Mới")

                self.assertEqual(result["added"], ["shop_new", "onlymail@gmail.com"])
                self.assertEqual(result["updated"], ["existing_acc"])
                self.assertEqual(result["created_project_name"], "Dự án Mới")
                self.assertEqual(result["target_project"], "Dự án Mới")

                # 3-field project invariant for newly added profiles
                for added_name in ("shop_new", "onlymail@gmail.com"):
                    self.assertEqual(fake_profiles[added_name]["project"], "Dự án Mới")
                    self.assertEqual(fake_profiles[added_name]["config"]["project_name"], "Dự án Mới")
                    self.assertIn(added_name, fake_projects["Dự án Mới"])
                    self.assertNotIn(added_name, fake_projects["Mặc định"])

                # Updated profile keeps its original project
                self.assertEqual(fake_profiles["existing_acc"]["project"], "Dự án Cũ")
                self.assertEqual(fake_profiles["existing_acc"]["config"]["project_name"], "Dự án Cũ")
                self.assertIn("existing_acc", fake_projects["Dự án Cũ"])
                self.assertNotIn("existing_acc", fake_projects["Dự án Mới"])
                self.assertEqual(fake_profiles["existing_acc"]["config"]["email"], "new@gmail.com")

    def test_update_only_import_does_not_create_empty_new_project(self):
        with tempfile.TemporaryDirectory() as td:
            fake_profiles = {
                "existing_acc": {
                    "config": {
                        "folder_path": str(Path(td) / "Old/Video"),
                        "chrome_profile": str(Path(td) / "Old/Profile"),
                        "project_name": "Dự án Cũ",
                        "email": "old@gmail.com",
                    },
                    "project": "Dự án Cũ",
                }
            }
            fake_projects = {"Mặc định": set(), "Dự án Cũ": {"existing_acc"}}
            plans = plan_import([{"name": "existing_acc", "email": "fresh@gmail.com"}], set(fake_profiles.keys()), policy="update")

            with patch.object(main, "profiles", fake_profiles), \
                 patch.object(main, "projects", fake_projects), \
                 patch.object(main, "app_base_dir", return_value=Path(td)), \
                 patch.object(main, "update_status"):
                result = main._apply_import_plans(plans, proxy_type="http", default_project="Dự án Không Tạo")
                self.assertEqual(result["added"], [])
                self.assertEqual(result["updated"], ["existing_acc"])
                self.assertIsNone(result["created_project_name"])
                self.assertNotIn("Dự án Không Tạo", fake_projects)

    def test_email_folder_sanitization_collision_allocates_unique_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            fake_profiles = {}
            fake_projects = {"Mặc định": set()}
            fields = parse_format("email|password")
            records, errors = parse_data_into_records("a.b@gmail.com|pw1\nab@gmail.com|pw2", fields)
            self.assertEqual(errors, [])
            plans = plan_import(records, set(), policy="update")

            with patch.object(main, "profiles", fake_profiles), \
                 patch.object(main, "projects", fake_projects), \
                 patch.object(main, "app_base_dir", return_value=Path(td)), \
                 patch.object(main, "update_status"):
                result = main._apply_import_plans(plans, proxy_type="http", default_project="Mặc định")
                self.assertEqual(result["added"], ["a.b@gmail.com", "ab@gmail.com"])
                dir1 = fake_profiles["a.b@gmail.com"]["config"]["chrome_profile"]
                dir2 = fake_profiles["ab@gmail.com"]["config"]["chrome_profile"]
                self.assertNotEqual(dir1, dir2)
                self.assertTrue(Path(dir1).exists())
                self.assertTrue(Path(dir2).exists())

    def test_rollback_import_removes_newly_created_custom_project(self):
        with tempfile.TemporaryDirectory() as td:
            fake_profiles = {}
            fake_projects = {"Mặc định": set()}
            plans = plan_import([{"name": "acc_rb", "email": "rb@gmail.com"}], set(), policy="update")

            with patch.object(main, "profiles", fake_profiles), \
                 patch.object(main, "projects", fake_projects), \
                 patch.object(main, "app_base_dir", return_value=Path(td)), \
                 patch.object(main, "update_status"):
                result = main._apply_import_plans(plans, proxy_type="http", default_project="Dự án Tạm")
                self.assertIn("Dự án Tạm", fake_projects)
                self.assertIn("acc_rb", fake_profiles)

                main._rollback_import(result)
                self.assertNotIn("acc_rb", fake_profiles)
                self.assertNotIn("Dự án Tạm", fake_projects)


if __name__ == "__main__":
    unittest.main()
