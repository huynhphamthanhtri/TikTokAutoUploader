"""
test_browser_engine_manager.py - Unit tests for Browser Engine Manager, SHA-256 validation, and multi-alias resolution.
"""

import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import browser_engine_manager as bem
import browser_patchright_glue as glue


class TestBrowserEngineManager(unittest.TestCase):
    def test_compute_sha256(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            f = Path(temp_dir) / "test.dat"
            f.write_bytes(b"hello world ht browser 144")
            expected_hash = hashlib.sha256(b"hello world ht browser 144").hexdigest().lower()
            self.assertEqual(bem.compute_sha256(f), expected_hash)

    def test_get_local_engine_info_discovers_installed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            b_dir = base / "Browser"
            ht_dir = b_dir / "ht-browser-144"
            ht_dir.mkdir(parents=True)
            (ht_dir / "chrome.exe").write_bytes(b"dummy")
            (ht_dir / "144.0.7559.96").mkdir()

            info = bem.get_local_engine_info(app_base=base)
            self.assertTrue(info["has_ht_144"])
            self.assertIsNotNone(info["primary_executable"])
            self.assertEqual(len(info["installed_engines"]), 1)
            self.assertEqual(info["installed_engines"][0]["name"], "ht-browser-144")

    def test_extract_engine_zip_atomic_with_checksum(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            zip_path = base / "engine.zip"
            
            # Create a mock engine zip
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("chrome.exe", b"mock_executable_bytes")
                zf.writestr("144.0.7559.96/chrome.dll", b"mock_dll_bytes")

            valid_hash = bem.compute_sha256(zip_path)
            target_dir = base / "Browser" / "ht-browser-144"

            # 1. Test failure on invalid hash
            with self.assertRaises(ValueError):
                bem.extract_engine_zip_atomic(zip_path, target_dir, expected_sha256="wrong_hash_12345")

            # 2. Test success on valid hash
            extracted = bem.extract_engine_zip_atomic(zip_path, target_dir, expected_sha256=valid_hash)
            self.assertEqual(extracted, target_dir)
            self.assertTrue((target_dir / "chrome.exe").exists())
            self.assertTrue((target_dir / "144.0.7559.96" / "chrome.dll").exists())

    def test_resolve_browser_executable_supports_ht_and_donglao_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            
            # Test ht-browser-144 priority
            ht_dir = base / "Browser" / "ht-browser-144"
            ht_dir.mkdir(parents=True)
            ht_exe = ht_dir / "htbrowser.exe"
            ht_exe.write_bytes(b"x")

            resolved = glue.resolve_browser_executable(app_base=base)
            self.assertEqual(resolved, str(ht_exe))

    def test_resolve_browser_executable_prefers_donglao_144(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)

            donglao_dir = base / "Browser" / "donglao-browser-144"
            donglao_dir.mkdir(parents=True)
            donglao_exe = donglao_dir / "chrome.exe"
            donglao_exe.write_bytes(b"x")

            orbita_dir = base / "Browser" / "orbita-browser-144"
            orbita_dir.mkdir(parents=True)
            orbita_exe = orbita_dir / "chrome.exe"
            orbita_exe.write_bytes(b"x")

            chrome64_dir = base / "Browser" / "chrome-win64"
            chrome64_dir.mkdir(parents=True)
            chrome64_exe = chrome64_dir / "chrome.exe"
            chrome64_exe.write_bytes(b"x")

            resolved = glue.resolve_browser_executable(app_base=base)
            self.assertEqual(resolved, str(donglao_exe))

    def test_get_local_engine_info_prefers_donglao_144(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            b_dir = base / "Browser"
            donglao_dir = b_dir / "donglao-browser-144"
            donglao_dir.mkdir(parents=True)
            (donglao_dir / "chrome.exe").write_bytes(b"dummy")
            (donglao_dir / "144.0.7559.96").mkdir()

            info = bem.get_local_engine_info(app_base=base)
            self.assertTrue(info["has_ht_144"])
            self.assertIsNotNone(info["primary_executable"])
            self.assertEqual(info["installed_engines"][0]["name"], "donglao-browser-144")


if __name__ == "__main__":
    unittest.main()
