"""
test_taskbar_manager.py - Unit tests for taskbar_manager.py (Window Isolation & AUMID).
"""

import sys
import unittest
from unittest.mock import patch, MagicMock

import taskbar_manager as tm


class TestTaskbarManager(unittest.TestCase):
    def test_isolate_profile_windows_non_win32_graceful(self):
        with patch("sys.platform", "linux"):
            count = tm.isolate_profile_windows("test_profile")
            self.assertEqual(count, 0)

    def test_set_window_app_user_model_id_invalid_hwnd(self):
        result = tm.set_window_app_user_model_id(0, "DONGLAO.Profile.test")
        self.assertFalse(result)

    def test_find_hwnds_for_empty_pids(self):
        hwnds = tm.find_hwnds_for_pids([])
        self.assertEqual(hwnds, [])

    @patch("taskbar_manager.isolate_profile_windows", return_value=1)
    def test_schedule_taskbar_isolation(self, mock_isolate):
        tm.schedule_taskbar_isolation("TestProfile", retries=1, interval=0.01)
        import time
        time.sleep(0.05)
        mock_isolate.assert_called()


if __name__ == "__main__":
    unittest.main()
