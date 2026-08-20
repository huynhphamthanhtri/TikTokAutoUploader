"""
test_profile_table_engine.py - Unit tests for build_row_model and ProfileTableEngine.
"""

import unittest
from profile_table_engine import ProfileTableEngine, build_row_model


class TestProfileTableEngine(unittest.TestCase):

    def test_build_row_model_pure(self):
        profile = {
            "name": "TEST_01",
            "running": True,
            "config": {
                "account_uuid": "uuid_01",
                "tiktok_id": "my_tiktok",
                "cookie_str": "sessionid=abc;",
                "session_auth_state": "live",
                "use_proxy": True,
                "folder_path": "C:/Video",
            },
            "ui": {
                "status": "Đang chạy",
                "login": "Live",
                "proxy": "OK: 1.1.1.1",
                "upload": "Đang đăng",
                "last_error": "",
            },
        }

        model = build_row_model("TEST_01", profile)
        self.assertEqual(model["uuid"], "uuid_01")
        self.assertEqual(model["name"], "TEST_01")
        self.assertEqual(model["values"][0], "TEST_01")
        self.assertEqual(model["values"][1], "@my_tiktok")
        self.assertEqual(model["values"][2], "🟢 Live")
        self.assertEqual(model["values"][3], "⚡ Đang chạy")
        self.assertTrue(model["filter_keys"]["is_cookie_live"])
        self.assertTrue(model["filter_keys"]["is_running"])

    def test_dirty_tracking_and_revisions(self):
        engine = ProfileTableEngine()
        engine.mark_dirty("TEST_A")
        engine.mark_dirty("TEST_B")
        self.assertEqual(engine.get_revision("TEST_A"), 1)

        dirty = engine.pop_dirty()
        self.assertIn("TEST_A", dirty)
        self.assertIn("TEST_B", dirty)
        self.assertEqual(len(engine.pop_dirty()), 0)

        # Revision increases on subsequent mark
        engine.mark_dirty("TEST_A")
        self.assertEqual(engine.get_revision("TEST_A"), 2)


if __name__ == "__main__":
    unittest.main()
