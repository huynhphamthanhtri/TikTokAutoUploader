import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from browser_lifecycle import ProfileLifecycle, get_lifecycle, remove_lifecycle, _kill_pid


class TestProfileLifecycle(unittest.TestCase):
    def setUp(self):
        self.lc = ProfileLifecycle()

    def test_initial_state_cancelled(self):
        self.assertTrue(self.lc.is_cancelled)
        self.assertEqual(self.lc.generation, 0)

    def test_begin_creates_new_generation(self):
        gen = self.lc.begin()
        self.assertEqual(gen, 1)
        self.assertEqual(self.lc.generation, 1)
        self.assertFalse(self.lc.is_cancelled)

    def test_begin_increments(self):
        g1 = self.lc.begin()
        g2 = self.lc.begin()
        self.assertEqual(g2, g1 + 1)

    def test_cancel_after_begin(self):
        gen = self.lc.begin()
        self.assertFalse(self.lc.is_cancelled)
        self.assertTrue(self.lc.is_current(gen))
        self.lc.cancel()
        self.assertTrue(self.lc.is_cancelled)
        self.assertFalse(self.lc.is_current(gen))

    def test_is_current_false_wrong_gen(self):
        gen = self.lc.begin()
        self.assertFalse(self.lc.is_current(gen + 1))

    def test_is_current_false_after_cancel(self):
        gen = self.lc.begin()
        self.lc.cancel()
        self.assertFalse(self.lc.is_current(gen))

    def test_begin_resets_drivers(self):
        self.lc.set_automation_driver("old_driver")
        self.lc.set_manual_driver("old_manual")
        gen = self.lc.begin()
        self.assertIsNone(self.lc.get_automation_driver())
        self.assertIsNone(self.lc.get_manual_driver())
        self.assertTrue(self.lc.is_current(gen))

    def test_automation_driver_get_set(self):
        d = object()
        self.lc.set_automation_driver(d)
        self.assertIs(self.lc.get_automation_driver(), d)

    def test_automation_driver_with_service(self):
        d = object()
        svc = object()
        self.lc.set_automation_driver(d, svc)
        self.assertIs(self.lc.get_automation_service(), svc)

    def test_manual_driver_get_set(self):
        d = object()
        self.lc.set_manual_driver(d)
        self.assertIs(self.lc.get_manual_driver(), d)

    def test_startup_future(self):
        f = object()
        self.lc.set_startup_future(f)
        self.assertIs(self.lc.get_startup_future(), f)

    def test_observer(self):
        o = object()
        self.lc.set_observer(o)
        self.assertIs(self.lc.get_observer(), o)

    def test_add_pid_none(self):
        self.lc.add_pid(None)
        self.assertEqual(len(self.lc.owned_pids()), 0)

    def test_add_pid_valid(self):
        self.lc.add_pid(1234)
        self.assertIn(1234, self.lc.owned_pids())

    def test_add_pid_string(self):
        self.lc.add_pid("5678")
        self.assertIn(5678, self.lc.owned_pids())

    def test_begin_clears_pids(self):
        self.lc.add_pid(9999)
        self.lc.begin()
        self.assertEqual(len(self.lc.owned_pids()), 0)

    def test_has_active_driver_false_initial(self):
        self.assertFalse(self.lc.has_active_driver())

    def test_has_active_driver_auto(self):
        self.lc.set_automation_driver("d")
        self.assertTrue(self.lc.has_active_driver())

    def test_has_active_driver_manual(self):
        self.lc.set_manual_driver("d")
        self.assertTrue(self.lc.has_active_driver())

    def test_has_active_driver_both(self):
        self.lc.set_automation_driver("a")
        self.lc.set_manual_driver("m")
        self.assertTrue(self.lc.has_active_driver())

    def test_cleanup_fast_clears_drivers(self):
        self.lc.set_automation_driver("d")
        self.lc.set_manual_driver("m")
        self.lc.cleanup_fast()
        self.assertIsNone(self.lc.get_automation_driver())
        self.assertIsNone(self.lc.get_manual_driver())
        self.assertTrue(self.lc.is_cancelled)

    def test_cleanup_fast_idempotent(self):
        self.lc.cleanup_fast()
        self.lc.cleanup_fast()

    def test_double_cancel_ok(self):
        self.lc.begin()
        self.lc.cancel()
        self.lc.cancel()
        self.assertTrue(self.lc.is_cancelled)


class TestGlobalLifecycleRegistry(unittest.TestCase):
    def tearDown(self):
        remove_lifecycle("test_prof")

    def test_get_lifecycle_creates(self):
        lc = get_lifecycle("test_prof")
        self.assertIsNotNone(lc)
        self.assertIsInstance(lc, ProfileLifecycle)

    def test_get_lifecycle_same_instance(self):
        lc1 = get_lifecycle("test_prof")
        lc2 = get_lifecycle("test_prof")
        self.assertIs(lc1, lc2)

    def test_remove_lifecycle(self):
        lc1 = get_lifecycle("test_prof")
        remove_lifecycle("test_prof")
        lc2 = get_lifecycle("test_prof")
        self.assertIsNot(lc1, lc2)

    def test_lifecycle_independent(self):
        lc_a = get_lifecycle("profile_a")
        lc_b = get_lifecycle("profile_b")
        self.assertIsNot(lc_a, lc_b)
        lc_a.begin()
        self.assertNotEqual(lc_a.generation, lc_b.generation)


class TestKillPid(unittest.TestCase):
    @patch("browser_lifecycle.psutil", None)
    def test_no_psutil(self):
        ok, action = _kill_pid(99999)
        self.assertFalse(ok)
        self.assertIsNone(action)

    def test_nonexistent_pid(self):
        ok, action = _kill_pid(99999999)
        self.assertTrue(ok)
        self.assertIn(action, ("gone",))

    def test_invalid_pid_zero(self):
        ok, _ = _kill_pid(0)
        # PID 0 is the System Idle Process on Windows – AccessDenied is acceptable
        self.assertTrue(ok)


class TestLifecycleCleanupReport(unittest.TestCase):
    def test_cleanup_empty_returns_report(self):
        lc = ProfileLifecycle()
        report = lc.cleanup(quit_timeout=1, kill_timeout=1)
        self.assertIn("drivers_quit", report)
        self.assertIn("pids_terminated", report)
        self.assertIn("pids_killed", report)
        self.assertIn("errors", report)

    def test_cleanup_with_mock_driver(self):
        lc = ProfileLifecycle()
        mock_drv = MagicMock()
        lc.set_automation_driver(mock_drv)
        lc.set_manual_driver(mock_drv)
        report = lc.cleanup(quit_timeout=1, kill_timeout=1)
        self.assertEqual(report["drivers_quit"], 2)
        self.assertEqual(mock_drv.quit.call_count, 2)

    def test_cleanup_driver_quit_error(self):
        lc = ProfileLifecycle()
        bad_drv = MagicMock()
        bad_drv.quit.side_effect = RuntimeError("quit failed")
        lc.set_automation_driver(bad_drv)
        report = lc.cleanup(quit_timeout=1, kill_timeout=1)
        self.assertEqual(report["drivers_quit"], 0)
        self.assertGreaterEqual(len(report["errors"]), 1)

    def test_cleanup_drivers_null_after(self):
        lc = ProfileLifecycle()
        lc.set_automation_driver(MagicMock())
        lc.cleanup(quit_timeout=1, kill_timeout=1)
        self.assertIsNone(lc.get_automation_driver())

    def test_cleanup_cancels_lifecycle(self):
        lc = ProfileLifecycle()
        gen = lc.begin()
        lc.cleanup(quit_timeout=1, kill_timeout=1)
        self.assertTrue(lc.is_cancelled)
        self.assertFalse(lc.is_current(gen))


if __name__ == "__main__":
    unittest.main()
