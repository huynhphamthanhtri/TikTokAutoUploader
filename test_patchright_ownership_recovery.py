import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import browser_patchright_glue as glue
from patchright_profile_migration import create_patchright_profile, profile_owner_id


class PatchrightOwnershipRecoveryTests(unittest.TestCase):
    def setUp(self):
        import main

        self.main = main
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original_profiles = main.profiles
        main.profiles = {}

    def tearDown(self):
        self.main.profiles = self.original_profiles
        self.tmp.cleanup()

    def _legacy(self):
        legacy = self.root / "shared" / "Profile"
        legacy.mkdir(parents=True)
        return legacy

    def test_unverified_config_rebinds_marker_owner_without_touching_profile(self):
        legacy = self._legacy()
        target = create_patchright_profile(legacy, legacy.parent, account_id="marker-owner")
        config = {
            "chrome_profile": str(legacy),
            "account_uuid": "stale-config-owner",
            "profile_owner_state": "unverified",
        }
        self.main.profiles["Recovered"] = {"config": config}

        path, action = self.main._ensure_patchright_profile_with_recovery("Recovered", config)

        self.assertEqual(action, "rebound")
        self.assertEqual(Path(path).resolve(), target.resolve())
        self.assertEqual(config["account_uuid"], "marker-owner")
        self.assertEqual(config["profile_owner_state"], "verified")
        self.assertEqual(profile_owner_id(target), "marker-owner")

    def test_verified_foreign_conflict_provisions_new_root_without_mutating_foreign_profile(self):
        legacy = self._legacy()
        foreign_target = create_patchright_profile(legacy, legacy.parent, account_id="foreign-owner")
        foreign_config = {
            "chrome_profile": str(legacy),
            "browser_profile_path": str(foreign_target),
            "account_uuid": "foreign-owner",
            "profile_owner_state": "verified",
        }
        recovering_config = {
            "chrome_profile": str(legacy),
            "browser_profile_path": str(foreign_target),
            "account_uuid": "recovering-owner",
            "profile_owner_state": "verified",
            "cookie_str": "preserved-cookie",
            "proxy_string": "127.0.0.1:8080",
        }
        self.main.profiles.update({
            "Foreign": {"config": foreign_config},
            "Recovered": {"config": recovering_config},
        })

        with patch.object(self.main, "app_base_dir", return_value=self.root), \
             patch.object(self.main, "_profile_browser_process_count", return_value=0):
            path, action = self.main._ensure_patchright_profile_with_recovery("Recovered", recovering_config)

        new_target = Path(path)
        self.assertEqual(action, "provisioned")
        self.assertNotEqual(new_target, foreign_target)
        self.assertEqual(profile_owner_id(foreign_target), "foreign-owner")
        self.assertEqual(profile_owner_id(new_target), "recovering-owner")
        self.assertEqual(recovering_config["cookie_str"], "preserved-cookie")
        self.assertTrue(recovering_config["manual_login_pending"])
        self.assertEqual(Path(recovering_config["ownership_recovery_previous_patchright_path"]).resolve(), foreign_target.resolve())

    def test_active_browser_blocks_provision_without_mutating_config(self):
        legacy = self._legacy()
        foreign_target = create_patchright_profile(legacy, legacy.parent, account_id="foreign-owner")
        config = {
            "chrome_profile": str(legacy),
            "browser_profile_path": str(foreign_target),
            "account_uuid": "recovering-owner",
            "profile_owner_state": "verified",
        }
        self.main.profiles["Recovered"] = {"config": config}
        before = dict(config)

        with patch.object(self.main, "_profile_browser_process_count", return_value=1):
            with self.assertRaisesRegex(RuntimeError, "đang được sử dụng"):
                self.main._ensure_patchright_profile_with_recovery("Recovered", config)

        self.assertEqual(config, before)
        self.assertEqual(profile_owner_id(foreign_target), "foreign-owner")
