"""
test_app_icon.py - Tests for application branding icon resolution and window icon application.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ui_components import resolve_app_icon_path, apply_app_icon


class TestAppIcon(unittest.TestCase):
    def test_resolve_app_icon_path_finds_root_icon(self):
        icon_path = resolve_app_icon_path()
        self.assertIsNotNone(icon_path)
        self.assertTrue(icon_path.exists())
        self.assertTrue(icon_path.name.endswith(".ico"))

    def test_resolve_app_icon_path_custom_base(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            custom_ico = base / "icon.ico"
            custom_ico.write_bytes(b"dummy icon content")
            
            resolved = resolve_app_icon_path(app_base=base)
            self.assertEqual(resolved, custom_ico.resolve())

    def test_resolve_app_icon_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            resolved = resolve_app_icon_path(app_base=base)
            self.assertIsNone(resolved)

    def test_apply_app_icon_calls_iconbitmap_on_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            ico = base / "icon.ico"
            ico.write_bytes(b"ico")

            mock_win = MagicMock()
            success = apply_app_icon(mock_win, app_base=base)
            self.assertTrue(success)
            mock_win.iconbitmap.assert_called_with(str(ico.resolve()))


if __name__ == "__main__":
    unittest.main()
