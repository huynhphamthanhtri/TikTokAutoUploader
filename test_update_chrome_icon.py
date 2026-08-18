"""
test_update_chrome_icon.py - Tests for PE icon patching on browser executables.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.update_chrome_icon import update_executable_icon, patch_orbita_144_icon


class TestUpdateChromeIcon(unittest.TestCase):
    def test_update_executable_icon_patches_pe(self):
        root = Path(__file__).resolve().parent
        ico_path = root / "icon.ico"
        real_exe = root / "Browser" / "orbita-browser-144" / "chrome.exe"

        if not real_exe.exists() or not ico_path.exists():
            self.skipTest("Missing chrome.exe or icon.ico for testing")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_exe = Path(temp_dir) / "chrome_test.exe"
            shutil.copyfile(real_exe, temp_exe)

            ok = update_executable_icon(temp_exe, ico_path, backup=False)
            self.assertTrue(ok)
            self.assertTrue(temp_exe.exists())
            self.assertGreater(temp_exe.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
