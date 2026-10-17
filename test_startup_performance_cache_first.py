import time
import unittest
from unittest.mock import MagicMock, patch

import main


class StartupPerformanceCacheFirstTests(unittest.TestCase):
    def setUp(self):
        self.device_id = main._device_fingerprint()
        self.valid_key = "TEST-VALID-KEY-123"

    def test_verify_license_cache_returns_true_instantly_when_valid(self):
        now = int(time.time())
        mock_cache = {
            "key": self.valid_key,
            "device_id": self.device_id,
            "last_ok": now - 3600,  # Checked 1 hour ago
            "expiry_epoch": now + 86400 * 30,  # Valid for 30 days
        }
        with patch("main._load_license_cache", return_value=mock_cache):
            t0 = time.perf_counter()
            ok, info, msg = main.verify_license_cache(self.valid_key)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            self.assertTrue(ok)
            self.assertEqual(info["status"], "CACHED")
            self.assertLess(elapsed_ms, 5.0, "verify_license_cache must execute in < 5ms")

    def test_verify_license_cache_rejects_expired_grace_period(self):
        now = int(time.time())
        mock_cache = {
            "key": self.valid_key,
            "device_id": self.device_id,
            "last_ok": now - (main.LICENSE_GRACE_SECONDS + 100),  # Beyond 3 days
            "expiry_epoch": now + 86400 * 30,
        }
        with patch("main._load_license_cache", return_value=mock_cache):
            ok, info, msg = main.verify_license_cache(self.valid_key)
            self.assertFalse(ok)
            self.assertIn("quá hạn", msg)

    def test_verify_license_cache_rejects_mismatched_device(self):
        now = int(time.time())
        mock_cache = {
            "key": self.valid_key,
            "device_id": "different-device-id",
            "last_ok": now,
            "expiry_epoch": now + 86400 * 30,
        }
        with patch("main._load_license_cache", return_value=mock_cache):
            ok, info, msg = main.verify_license_cache(self.valid_key)
            self.assertFalse(ok)
            self.assertIn("không khớp", msg)

    def test_verify_license_cache_rejects_expired_license(self):
        now = int(time.time())
        mock_cache = {
            "key": self.valid_key,
            "device_id": self.device_id,
            "last_ok": now,
            "expiry_epoch": now - 1000,  # Expired in past
        }
        with patch("main._load_license_cache", return_value=mock_cache):
            ok, info, msg = main.verify_license_cache(self.valid_key)
            self.assertFalse(ok)
            self.assertIn("hết hạn", msg)

    def test_require_license_boots_instantly_with_cache_first(self):
        now = int(time.time())
        mock_cache = {
            "key": self.valid_key,
            "device_id": self.device_id,
            "last_ok": now,
            "expiry_epoch": now + 86400 * 30,
        }
        main.LICENSE_OK = False
        main.LICENSE_KEY = None

        with patch("main._load_license_cache", return_value=mock_cache), \
             patch("main._validate_against_sheet") as mock_sheet, \
             patch("main._background_license_sync") as mock_bg_sync, \
             patch("main._license_dialog") as mock_dialog:

            main.require_license_then_boot()

            # Main thread must be granted immediately without calling synchronous sheet validation
            self.assertTrue(main.LICENSE_OK)
            self.assertEqual(main.LICENSE_KEY, self.valid_key)
            mock_sheet.assert_not_called()
            mock_dialog.assert_not_called()
            mock_bg_sync.assert_called_once_with(self.valid_key)

    def test_background_license_sync_updates_cache_on_success(self):
        now = int(time.time())
        with patch("main._validate_against_sheet", return_value=(True, {"status": "ACTIVE", "expiry": "2028-12-31"}, "OK")), \
             patch("main._save_license_cache") as mock_save:
            main._background_license_sync(self.valid_key)
            mock_save.assert_called_once()
            saved_data = mock_save.call_args[0][0]
            self.assertEqual(saved_data["key"], self.valid_key)
            self.assertGreaterEqual(saved_data["last_ok"], now - 2)

    def test_background_license_sync_handles_banned_key(self):
        with patch("main._validate_against_sheet", return_value=(False, {"status": "BANNED"}, "Key bị khóa")), \
             patch("main._license_dialog") as mock_dialog, \
             patch("main.stop_all_in_project") as mock_stop:
            main.LICENSE_OK = True
            main._background_license_sync(self.valid_key)
            # Process pending Tkinter after(0) callbacks
            main.root.update()
            self.assertFalse(main.LICENSE_OK)
            mock_stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
