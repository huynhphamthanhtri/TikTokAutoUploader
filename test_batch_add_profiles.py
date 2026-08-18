"""
test_batch_add_profiles.py - Tests for batch import dialog and default update policy.
"""

import unittest
from unittest.mock import patch
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
        import tempfile
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


if __name__ == "__main__":
    unittest.main()
