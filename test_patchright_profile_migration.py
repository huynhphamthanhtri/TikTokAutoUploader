import json
import tempfile
import unittest
from pathlib import Path

from patchright_profile_migration import (
    OWNERSHIP_MARKER,
    STATE_FILE,
    MigrationState,
    advance_migration,
    cleanup_legacy_profile,
    create_patchright_profile,
    derive_patchright_profile_path,
    mark_profile_login_verified,
    migration_status,
)


class PatchrightProfileMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.managed = Path(self.temp.name) / "Auto_Data"
        self.legacy = self.managed / "account-1" / "Profile"
        self.legacy.mkdir(parents=True)
        (self.legacy / "Cookies").write_text("legacy database", encoding="ascii")

    def tearDown(self):
        self.temp.cleanup()

    def test_derives_fixed_sibling_and_rejects_other_names(self):
        self.assertEqual(
            derive_patchright_profile_path(self.legacy),
            self.legacy.with_name("Profile-Patchright").resolve(),
        )
        with self.assertRaises(ValueError):
            derive_patchright_profile_path(self.legacy.with_name("Default"))

    def test_creation_is_empty_except_metadata_and_tracks_pending(self):
        target = create_patchright_profile(self.legacy, self.managed)
        self.assertEqual({item.name for item in target.iterdir()}, {OWNERSHIP_MARKER, STATE_FILE})
        self.assertTrue((self.legacy / "Cookies").exists())
        status = migration_status(target)
        self.assertEqual(status["state"], "created")
        self.assertEqual(status["history"], ["pending", "created"])
        self.assertEqual(create_patchright_profile(self.legacy, self.managed), target)

    def test_preexisting_unowned_target_is_never_adopted(self):
        target = self.legacy.with_name("Profile-Patchright")
        target.mkdir()
        (target / "Cookies").write_text("do not touch", encoding="ascii")
        with self.assertRaises(ValueError):
            create_patchright_profile(self.legacy, self.managed)
        self.assertFalse((target / OWNERSHIP_MARKER).exists())
        self.assertTrue((target / "Cookies").exists())

    def test_transitions_are_ordered_and_persisted(self):
        target = create_patchright_profile(self.legacy, self.managed)
        with self.assertRaises(ValueError):
            advance_migration(target, MigrationState.LOGIN_VERIFIED)
        for state in (
            MigrationState.COOKIES_IMPORTED,
            MigrationState.LOGIN_VERIFIED,
            MigrationState.UPLOAD_VERIFIED,
        ):
            status = advance_migration(target, state)
        self.assertEqual(status["state"], "upload_verified")
        persisted = json.loads((target / STATE_FILE).read_text(encoding="ascii"))
        self.assertEqual(persisted["history"][-1], "upload_verified")

    def test_cleanup_requires_upload_verification_and_confirmation(self):
        target = create_patchright_profile(self.legacy, self.managed)
        with self.assertRaises(PermissionError):
            cleanup_legacy_profile(target, self.managed)
        with self.assertRaises(RuntimeError):
            cleanup_legacy_profile(target, self.managed, explicit_confirmation=True)
        self.assertTrue(self.legacy.exists())

    def test_cleanup_deletes_only_legacy_after_all_guards(self):
        target = create_patchright_profile(self.legacy, self.managed)
        for state in ("cookies_imported", "login_verified", "upload_verified"):
            advance_migration(target, state)
        result = cleanup_legacy_profile(target, self.managed, explicit_confirmation=True)
        self.assertFalse(self.legacy.exists())
        self.assertTrue(target.exists())
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["history"][-2:], ["legacy_cleanup_pending", "completed"])

    def test_mark_login_verified_skips_cookie_step(self):
        target = create_patchright_profile(self.legacy, self.managed)
        status = mark_profile_login_verified(target, note="auto-login")
        self.assertEqual(status["state"], "login_verified")
        self.assertEqual(status["history"], ["pending", "created", "login_verified"])
        persisted = json.loads((target / STATE_FILE).read_text(encoding="ascii"))
        self.assertEqual(persisted["note"], "auto-login")
        self.assertEqual(mark_profile_login_verified(target)["state"], "login_verified")

    def test_mark_login_verified_rejects_non_created_state(self):
        target = create_patchright_profile(self.legacy, self.managed)
        advance_migration(target, MigrationState.COOKIES_IMPORTED)
        with self.assertRaises(ValueError):
            mark_profile_login_verified(target)

    def test_cleanup_rejects_wrong_managed_root_and_tampered_marker(self):
        target = create_patchright_profile(self.legacy, self.managed)
        for state in ("cookies_imported", "login_verified", "upload_verified"):
            advance_migration(target, state)
        outside = Path(self.temp.name) / "other"
        outside.mkdir()
        with self.assertRaises(ValueError):
            cleanup_legacy_profile(target, outside, explicit_confirmation=True)
        marker = json.loads((target / OWNERSHIP_MARKER).read_text(encoding="ascii"))
        marker["patchright_profile"] = str(outside)
        (target / OWNERSHIP_MARKER).write_text(json.dumps(marker), encoding="ascii")
        with self.assertRaises(ValueError):
            cleanup_legacy_profile(target, self.managed, explicit_confirmation=True)
        self.assertTrue(self.legacy.exists())


if __name__ == "__main__":
    unittest.main()
