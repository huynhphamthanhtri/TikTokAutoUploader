"""
test_watchdog_service.py - Unit tests for SharedWatchdogManager and WatchToken.
"""

import tempfile
import time
import unittest
from pathlib import Path

from watchdog_service import SharedWatchdogManager, canonical_path


class TestWatchdogService(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.folder_a = Path(self.temp_dir.name) / "profile_a_video"
        self.folder_b = Path(self.temp_dir.name) / "profile_b_video"
        self.folder_a.mkdir(parents=True, exist_ok=True)
        self.folder_b.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_canonical_path_normalization(self):
        p1 = str(self.folder_a)
        p2 = str(self.folder_a).lower()
        self.assertEqual(canonical_path(p1), canonical_path(p2))

    def test_schedule_and_conflict_handling(self):
        enqueued = []
        mgr = SharedWatchdogManager(
            max_stability_workers=2,
            file_stable_delay=0.01,
            enqueue_callback=lambda prof, f, gen: enqueued.append((prof, f, gen)),
        )

        ok, msg = mgr.schedule_folder("PROFILE_A", str(self.folder_a), generation=1)
        self.assertTrue(ok)

        # Conflict: Another profile trying to schedule the same folder
        ok2, msg2 = mgr.schedule_folder("PROFILE_B", str(self.folder_a), generation=1)
        self.assertFalse(ok2)
        self.assertIn("already watched", msg2)

        # Different folder succeeds
        ok3, msg3 = mgr.schedule_folder("PROFILE_B", str(self.folder_b), generation=1)
        self.assertTrue(ok3)

        # Unschedule
        mgr.unschedule_profile("PROFILE_A")
        # Now PROFILE_B can schedule folder_a
        ok4, msg4 = mgr.schedule_folder("PROFILE_B", str(self.folder_a), generation=2)
        self.assertTrue(ok4)

        mgr.stop()

    def test_stability_check_and_generation_gating(self):
        enqueued = []
        mgr = SharedWatchdogManager(
            max_stability_workers=2,
            file_stable_delay=0.01,
            enqueue_callback=lambda prof, f, gen: enqueued.append((prof, f, gen)),
        )

        mgr.schedule_folder("PROFILE_A", str(self.folder_a), generation=10)

        # Create a video file in folder_a
        vid_file = self.folder_a / "test_video.mp4"
        vid_file.write_bytes(b"dummy_mp4_bytes_12345")

        mgr.handle_file_event(str(vid_file))
        time.sleep(0.08)

        self.assertEqual(len(enqueued), 1)
        self.assertEqual(enqueued[0][0], "PROFILE_A")
        self.assertEqual(enqueued[0][1], str(vid_file))
        self.assertEqual(enqueued[0][2], 10)

        mgr.stop()


if __name__ == "__main__":
    unittest.main()
