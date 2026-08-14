import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_maintenance import (
    FULL,
    OWNERSHIP_MARKER,
    QUICK,
    SESSION,
    adopt_legacy_owned_root,
    create_owned_root,
    maintain_browser,
    validate_configured_profile_roots,
    validate_maintenance_root,
    validate_target_path,
)
from patchright_profile_migration import create_patchright_profile


def write(path, content="data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="ascii")
    return path


class BrowserMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "Chrome" / "User Data"
        self.root.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_report_shape_and_unknown_mode(self):
        report = maintain_browser(self.root, "unknown")
        self.assertEqual(set(report), {"mode", "root", "removed", "skipped", "errors", "success"})
        self.assertFalse(report["success"])
        self.assertTrue(report["errors"])

    def test_quick_removes_allowlisted_caches_and_preserves_session_data(self):
        profile = self.root / "Default"
        removed = [
            write(profile / "Cache" / "item"),
            write(profile / "Code Cache" / "js" / "item"),
            write(profile / "Network" / "Cache" / "item"),
            write(self.root / "ShaderCache" / "item"),
        ]
        preserved = [
            write(profile / "Cookies"),
            write(profile / "Network" / "Cookies"),
            write(profile / "Local Storage" / "leveldb" / "item"),
            write(profile / "IndexedDB" / "item"),
            write(profile / "Service Worker" / "Database" / "item"),
            write(profile / "unrelated" / "item"),
        ]
        report = maintain_browser(self.root, QUICK)
        self.assertTrue(report["success"], report)
        self.assertTrue(all(not path.exists() for path in removed))
        self.assertTrue(all(path.exists() for path in preserved))

    def test_quick_removes_only_stale_singleton_files(self):
        stale = write(self.root / "SingletonLock")
        fresh = write(self.root / "SingletonCookie")
        now = time.time()
        os.utime(stale, (now - 100, now - 100))
        os.utime(fresh, (now, now))
        report = maintain_browser(self.root, QUICK, stale_lock_age_seconds=50, now=now)
        self.assertTrue(report["success"], report)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual(report["skipped"][0]["reason"], "Singleton lock is not stale")

    def test_session_removes_all_site_stores_from_all_profiles(self):
        profiles = (self.root / "Default", self.root / "Profile 7", self.root / "Guest Profile")
        targets = []
        for profile in profiles:
            targets.extend([
                write(profile / "Cookies"),
                write(profile / "Cookies-wal"),
                write(profile / "Network" / "Cookies"),
                write(profile / "Network" / "Cookies-journal"),
                write(profile / "Local Storage" / "leveldb" / "data"),
                write(profile / "Session Storage" / "data"),
                write(profile / "IndexedDB" / "data"),
                write(profile / "Service Worker" / "data"),
                write(profile / "WebStorage" / "data"),
            ])
            write(profile / "Preferences")
        report = maintain_browser(self.root, SESSION)
        self.assertTrue(report["success"], report)
        self.assertTrue(all(not target.exists() for target in targets))
        self.assertTrue(all((profile / "Preferences").exists() for profile in profiles))

    def test_session_includes_quick(self):
        cache = write(self.root / "Default" / "GPUCache" / "data")
        report = maintain_browser(self.root, SESSION)
        self.assertTrue(report["success"], report)
        self.assertFalse(cache.exists())

    def test_full_requires_module_marker(self):
        important = write(self.root / "important")
        report = maintain_browser(self.root, FULL)
        self.assertFalse(report["success"])
        self.assertTrue(important.exists())

    def test_full_rejects_forged_marker(self):
        write(self.root / OWNERSHIP_MARKER, "not-owned")
        important = write(self.root / "important")
        report = maintain_browser(self.root, FULL)
        self.assertFalse(report["success"])
        self.assertTrue(important.exists())

    def test_full_removes_children_and_preserves_root_and_marker(self):
        owned = self.base / "owned"
        self.assertTrue(create_owned_root(owned))
        write(owned / "Default" / "Preferences")
        write(owned / "Local State")
        report = maintain_browser(owned, FULL)
        self.assertTrue(report["success"], report)
        self.assertTrue(owned.is_dir())
        self.assertEqual({item.name for item in owned.iterdir()}, {OWNERSHIP_MARKER})

    def test_full_patchright_reset_removes_browser_data_and_preserves_metadata(self):
        managed = self.base / "Auto_Data"
        legacy = managed / "account-1" / "Profile"
        legacy.mkdir(parents=True)
        owned = create_patchright_profile(legacy, managed)
        marker = owned / ".patchright-profile-owned"
        state_file = owned / ".patchright-migration.json"
        metadata_before = {path.name: path.read_bytes() for path in (marker, state_file)}
        write(owned / "Default" / "Preferences")
        write(owned / "Local State")

        report = maintain_browser(owned, FULL)

        self.assertTrue(report["success"], report)
        self.assertEqual({item.name for item in owned.iterdir()}, set(metadata_before))
        self.assertEqual(
            {path.name: path.read_bytes() for path in (marker, state_file)},
            metadata_before,
        )

    def test_full_patchright_reset_rejects_tampered_marker_path(self):
        managed = self.base / "Auto_Data"
        legacy = managed / "account-1" / "Profile"
        legacy.mkdir(parents=True)
        owned = create_patchright_profile(legacy, managed)
        marker = owned / ".patchright-profile-owned"
        marker_data = json.loads(marker.read_text(encoding="ascii"))
        marker_data["patchright_profile"] = str(self.base / "outside")
        marker.write_text(json.dumps(marker_data), encoding="ascii")
        browser_data = write(owned / "Default" / "Preferences")

        report = maintain_browser(owned, FULL)

        self.assertFalse(report["success"])
        self.assertTrue(browser_data.exists())

    def test_full_patchright_reset_rejects_missing_state_metadata(self):
        managed = self.base / "Auto_Data"
        legacy = managed / "account-1" / "Profile"
        legacy.mkdir(parents=True)
        owned = create_patchright_profile(legacy, managed)
        (owned / ".patchright-migration.json").unlink()
        browser_data = write(owned / "Default" / "Preferences")

        report = maintain_browser(owned, FULL)

        self.assertFalse(report["success"])
        self.assertTrue(browser_data.exists())

    def test_create_owned_root_never_marks_existing_directory(self):
        existing = self.base / "existing"
        existing.mkdir()
        self.assertFalse(create_owned_root(existing))
        self.assertFalse((existing / OWNERSHIP_MARKER).exists())

    def test_adopt_legacy_root_only_inside_managed_parent(self):
        managed = self.base / "Auto_Data"
        profile = managed / "one" / "Profile"
        profile.mkdir(parents=True)
        self.assertTrue(adopt_legacy_owned_root(profile, managed))
        self.assertFalse(adopt_legacy_owned_root(profile, managed))
        outside = self.base / "outside" / "Profile"
        outside.mkdir(parents=True)
        with self.assertRaises(ValueError):
            adopt_legacy_owned_root(outside, managed)

    def test_target_must_be_strictly_beneath_root(self):
        child = write(self.root / "Default" / "Cache" / "x")
        self.assertEqual(validate_target_path(self.root, child), child.resolve())
        with self.assertRaises(ValueError):
            validate_target_path(self.root, self.root)
        with self.assertRaises(ValueError):
            validate_target_path(self.root, self.base / "outside")

    def test_filesystem_root_is_rejected(self):
        filesystem_root = Path(self.root.anchor)
        with self.assertRaises(ValueError):
            validate_maintenance_root(filesystem_root)

    def test_forbidden_root_overlap_is_rejected_without_deletion(self):
        cache = write(self.root / "Default" / "Cache" / "x")
        report = maintain_browser(self.root, QUICK, forbidden_roots=[self.root / "Default"])
        self.assertFalse(report["success"])
        self.assertTrue(cache.exists())

    def test_configured_profile_root_overlap_is_rejected(self):
        nested = self.root / "nested"
        with self.assertRaises(ValueError):
            validate_configured_profile_roots([self.root, nested])
        report = maintain_browser(self.root, QUICK, configured_profile_roots=[nested])
        self.assertFalse(report["success"])

    def test_deletion_errors_are_reported(self):
        cache = write(self.root / "Default" / "Cache" / "x")
        with patch("browser_maintenance.shutil.rmtree", side_effect=PermissionError("busy")):
            report = maintain_browser(self.root, QUICK)
        self.assertFalse(report["success"])
        self.assertTrue(cache.exists())
        self.assertIn("busy", report["errors"][0]["error"])

    def test_symlink_root_and_target_are_rejected(self):
        link_root = self.base / "linked-root"
        try:
            link_root.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlinks are unavailable")
        report = maintain_browser(link_root, QUICK)
        self.assertFalse(report["success"])
        with self.assertRaises(ValueError):
            validate_target_path(self.root, link_root / "Default")


if __name__ == "__main__":
    unittest.main()
