import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from browser_profile_quarantine import (
    DEFAULT_RETAIN_DAYS,
    MANIFEST_NAME,
    PATCHRIGHT_OWNERSHIP_MARKER,
    cleanup_quarantines,
    latest_quarantine,
    list_quarantines,
    quarantine_profile,
    restore_quarantine,
    restore_target,
)


def make_owned_profile(root, name="Profile-Patchright", account_id="acc-123"):
    target = Path(root) / name
    target.mkdir(parents=True, exist_ok=True)
    marker = {
        "format": "patchright-profile-v1",
        "legacy_profile": str(Path(root) / "Profile"),
        "patchright_profile": str(target),
        "account_id": account_id,
    }
    (target / PATCHRIGHT_OWNERSHIP_MARKER).write_text(
        json.dumps(marker), encoding="ascii"
    )
    (target / "Cookies").write_text("session-data", encoding="utf-8")
    return target


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="quarantine-test-"))

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_quarantine_moves_profile_and_writes_manifest(self):
        account = make_owned_profile(self.work)
        destination, manifest = quarantine_profile(
            account, account_uuid="acc-123", profile_name="acc"
        )
        self.assertFalse(account.exists())
        self.assertTrue(destination.is_dir())
        self.assertEqual(manifest["account_uuid"], "acc-123")
        self.assertEqual(manifest["profile_name"], "acc")
        original_path = Path(manifest["original_path"])
        self.assertEqual(original_path.name, account.name)
        self.assertTrue(os.path.samefile(original_path.parent, account.parent))
        self.assertTrue((destination / "Cookies").is_file())
        self.assertTrue((destination / MANIFEST_NAME).is_file())

    def test_quarantine_rejects_account_mismatch(self):
        account = make_owned_profile(self.work, account_id="acc-other")
        with self.assertRaises(ValueError):
            quarantine_profile(account, account_uuid="acc-123")

    def test_quarantine_rejects_unowned_profile(self):
        unowned = Path(self.work) / "Profile-Patchright"
        unowned.mkdir(parents=True)
        with self.assertRaises(ValueError):
            quarantine_profile(unowned)

    def test_list_quarantines_newest_first(self):
        now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        _d1, _m1 = quarantine_profile(
            make_owned_profile(self.work), account_uuid="acc-123",
            now=now + timedelta(hours=1),
        )
        _d2, _m2 = quarantine_profile(
            make_owned_profile(self.work), account_uuid="acc-123",
            now=now + timedelta(hours=2),
        )
        found = list_quarantines(make_owned_profile(self.work))
        self.assertEqual(len(found), 2)
        self.assertTrue(found[0]["created_at"] > found[1]["created_at"])
        latest = latest_quarantine(make_owned_profile(self.work))
        self.assertEqual(latest["created_at"], found[0]["created_at"])

    def test_cleanup_keeps_latest_and_removes_older(self):
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)
        _d1, _m1 = quarantine_profile(
            make_owned_profile(self.work), account_uuid="acc-123", now=now
        )
        _d2, _m2 = quarantine_profile(
            make_owned_profile(self.work), account_uuid="acc-123",
            now=now + timedelta(days=1),
        )
        _d3, _m3 = quarantine_profile(
            make_owned_profile(self.work), account_uuid="acc-123",
            now=now + timedelta(days=2),
        )
        account = make_owned_profile(self.work)
        removed = cleanup_quarantines(
            account, now=now + timedelta(days=7), retain_days=DEFAULT_RETAIN_DAYS
        )
        self.assertEqual(len(removed), 2)
        remaining = list_quarantines(account)
        self.assertEqual(len(remaining), 1)

    def test_cleanup_removes_latest_when_expired(self):
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)
        quarantine_profile(make_owned_profile(self.work), account_uuid="acc-123", now=now)
        account = make_owned_profile(self.work)
        removed = cleanup_quarantines(
            account, now=now + timedelta(days=10), retain_days=DEFAULT_RETAIN_DAYS
        )
        self.assertEqual(len(removed), 1)
        self.assertEqual(list_quarantines(account), [])

    def test_cleanup_does_not_remove_valid_latest_before_expiry(self):
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)
        quarantine_profile(make_owned_profile(self.work), account_uuid="acc-123", now=now)
        account = make_owned_profile(self.work)
        removed = cleanup_quarantines(
            account, now=now + timedelta(days=1), retain_days=DEFAULT_RETAIN_DAYS
        )
        self.assertEqual(removed, [])
        self.assertEqual(len(list_quarantines(account)), 1)

    def test_cleanup_removes_single_expired_quarantine(self):
        now = datetime(2026, 8, 10, tzinfo=timezone.utc)
        quarantine_profile(make_owned_profile(self.work), account_uuid="acc-123", now=now)
        account = make_owned_profile(self.work)
        removed = cleanup_quarantines(
            account, now=now + timedelta(days=8), retain_days=DEFAULT_RETAIN_DAYS
        )
        self.assertEqual(len(removed), 1)

    def test_restore_moves_quarantine_back(self):
        account = make_owned_profile(self.work)
        destination, manifest = quarantine_profile(
            account, account_uuid="acc-123", profile_name="acc"
        )
        restored = restore_quarantine(destination)
        self.assertFalse(destination.exists())
        self.assertTrue(account.is_dir())
        self.assertTrue((account / "Cookies").is_file())
        self.assertTrue(os.path.samefile(restored["original_path"], account))

    def test_restore_rejects_when_target_exists(self):
        account = make_owned_profile(self.work)
        destination, _manifest = quarantine_profile(
            account, account_uuid="acc-123"
        )
        account.mkdir()
        with self.assertRaises(ValueError):
            restore_quarantine(destination)

    def test_restore_target_derived_from_quarantine(self):
        account = make_owned_profile(self.work)
        destination, _manifest = quarantine_profile(
            account, account_uuid="acc-123"
        )
        target = restore_target(destination)
        self.assertEqual(target.name, account.name)
        self.assertTrue(os.path.samefile(target.parent, account.parent))


if __name__ == "__main__":
    unittest.main()
