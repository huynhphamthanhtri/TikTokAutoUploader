"""
test_config_service.py - Unit tests for ConfigPersistenceService.
"""

import tempfile
import time
import unittest
from pathlib import Path

from config_service import ConfigPersistenceService, ConfigServiceResult


class TestConfigService(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_file = Path(self.temp_dir.name) / "test_configs.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_debounced_save_and_flush(self):
        ui_dispatched = []
        svc = ConfigPersistenceService(
            config_path=self.config_file,
            debounce_seconds=0.05,
            ui_dispatcher=lambda cb: (ui_dispatched.append(True), cb()),
        )

        profiles = {
            "P1": {
                "name": "P1",
                "running": False,
                "config": {"account_uuid": "u1", "name": "P1", "cookie_str": "test"},
            }
        }
        projects = {"Mặc định": {"P1"}}

        req_rev = svc.request_save(profiles, projects, ui_callback=lambda: None)
        self.assertEqual(req_rev, 1)

        # Flush should wait for writer to persist
        res = svc.flush_now(timeout=2.0)
        self.assertEqual(res, ConfigServiceResult.SUCCESS)

        status = svc.get_status()
        self.assertTrue(status["is_synced"])
        self.assertEqual(status["persisted_revision"], 1)
        self.assertTrue(self.config_file.exists())
        self.assertTrue(len(ui_dispatched) > 0)

        svc.shutdown(timeout=1.0)

    def test_concurrent_mutations(self):
        svc = ConfigPersistenceService(
            config_path=self.config_file,
            debounce_seconds=0.02,
        )

        profiles = {}
        for i in range(10):
            profiles[f"P_{i}"] = {
                "name": f"P_{i}",
                "running": False,
                "config": {"account_uuid": f"u_{i}", "name": f"P_{i}"},
            }
            svc.request_save(profiles, {"Mặc định": set(profiles.keys())})

        res = svc.flush_now(timeout=2.0)
        self.assertEqual(res, ConfigServiceResult.SUCCESS)
        self.assertTrue(svc.get_status()["persisted_revision"] >= 10)

        svc.shutdown(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
