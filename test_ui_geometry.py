"""
test_ui_geometry.py - Unit tests for responsive dialog centering and viewport fitting.
"""

import unittest
from unittest.mock import MagicMock

from ui_components import calculate_centered_geometry, fit_and_center_dialog


class TestUIGeometry(unittest.TestCase):
    def test_laptop_1366x768_constrains_height_and_centers(self):
        # On a 1366x768 screen, a 980x780 dialog should be clamped to fit available viewport
        w, h, x, y, geom = calculate_centered_geometry(
            pref_w=980,
            pref_h=780,
            screen_w=1366,
            screen_h=768,
            min_w=400,
            min_h=300,
            margin_w=60,
            margin_h=96,
        )
        self.assertEqual(w, 980)
        self.assertEqual(h, 672)  # 768 - 96
        self.assertEqual(x, (1366 - 980) // 2)
        self.assertEqual(y, max(0, (768 - 672) // 2 - 15))
        self.assertEqual(geom, f"{w}x{h}+{x}+{y}")
        self.assertTrue(y + h <= 768 - 40, "Dialog should not overlap Windows taskbar")

    def test_full_hd_1920x1080_preserves_dimensions_and_centers(self):
        w, h, x, y, geom = calculate_centered_geometry(
            pref_w=960,
            pref_h=720,
            screen_w=1920,
            screen_h=1080,
            min_w=400,
            min_h=300,
        )
        self.assertEqual(w, 960)
        self.assertEqual(h, 720)
        self.assertEqual(x, (1920 - 960) // 2)  # 480
        self.assertEqual(y, (1080 - 720) // 2 - 15)  # 165
        self.assertEqual(geom, "960x720+480+165")

    def test_small_screen_respects_minsize(self):
        w, h, x, y, geom = calculate_centered_geometry(
            pref_w=900,
            pref_h=800,
            screen_w=640,
            screen_h=480,
            min_w=500,
            min_h=400,
        )
        self.assertGreaterEqual(w, 500)
        self.assertGreaterEqual(h, 384)  # Clamped within min/avail
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_fit_and_center_dialog_applies_to_mock_window(self):
        mock_dlg = MagicMock()
        mock_dlg.winfo_screenwidth.return_value = 1920
        mock_dlg.winfo_screenheight.return_value = 1080

        w, h = fit_and_center_dialog(mock_dlg, 800, 600, min_w=400, min_h=300)
        self.assertEqual(w, 800)
        self.assertEqual(h, 600)
        mock_dlg.geometry.assert_called_once()
        called_geom = mock_dlg.geometry.call_args[0][0]
        self.assertTrue("+" in called_geom, "Geometry string must include +X+Y offset")
        mock_dlg.minsize.assert_called_once_with(400, 300)


if __name__ == "__main__":
    unittest.main()
