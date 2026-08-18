"""
test_browser_engine_patcher.py - Tests for the hardened browser engine patch
scripts (license NOP bypass + branding) on staging copies.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import patch_all_browser_engines, patch_chrome_version_info
from scripts.browser_engine_patcher import (
    BRANDING_PAIRS,
    LICENSE_EXPECTED_BYTES,
    LICENSE_NOPPED_BYTES,
    LICENSE_PATCH_OFFSET,
    make_staging_copy,
    patch_branding,
    patch_engine_dir,
    patch_license_nops,
    read_at,
    verify_branding,
    verify_license_nops,
)


def make_fake_engine(root: Path, *, dll_license: bytes = LICENSE_EXPECTED_BYTES):
    """Build a minimal fake engine dir with placeholder chrome.exe/chrome.dll."""
    engine = root / "fake-browser-144"
    ver = engine / "144.0.7559.96"
    ver.mkdir(parents=True)

    dll = bytearray(b"\x00" * (LICENSE_PATCH_OFFSET + 6))
    dll[LICENSE_PATCH_OFFSET:LICENSE_PATCH_OFFSET + 6] = dll_license
    dll += "@huynhthang".encode("utf-16le") + "HT Browser".encode("utf-16le")
    (ver / "chrome.dll").write_bytes(bytes(dll))

    exe_data = b"placeholder-exe" + "@huynhthang".encode("utf-16le") + "HT Browser".encode("utf-16le") + b"tail"
    (engine / "chrome.exe").write_bytes(exe_data)
    return engine


class TestMakeStagingCopy(unittest.TestCase):
    def test_copies_and_excludes_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir()
            (src / "chrome.exe").write_bytes(b"x")
            (src / "chrome.exe.original_backup").write_bytes(b"y")
            (src / "chrome_icon_test.exe").write_bytes(b"z")
            (src / "debug.log").write_bytes(b"w")
            (src / "keep.bin").write_bytes(b"k")

            staging = Path(td) / "staging"
            make_staging_copy(src, staging)
            names = {p.name for p in staging.rglob("*") if p.is_file()}
            self.assertEqual(names, {"chrome.exe", "keep.bin"})

    def test_rejects_staging_inside_source(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir()
            with self.assertRaises(ValueError):
                make_staging_copy(src, src / "nested")

    def test_rejects_same_dir(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                make_staging_copy(Path(td), Path(td))


class TestPatchLicenseNops(unittest.TestCase):
    def test_expected_bytes_are_nop_patched(self):
        with tempfile.TemporaryDirectory() as td:
            engine = make_fake_engine(Path(td))
            dll = engine / "144.0.7559.96" / "chrome.dll"
            report = patch_license_nops(dll, min_size=0)
            self.assertEqual(report["state"], "patched")
            self.assertTrue(verify_license_nops(dll))
            self.assertTrue((dll.with_name(dll.name + ".original_backup")).exists())

    def test_already_nop_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            engine = make_fake_engine(Path(td), dll_license=LICENSE_NOPPED_BYTES)
            dll = engine / "144.0.7559.96" / "chrome.dll"
            first = patch_license_nops(dll, min_size=0)
            second = patch_license_nops(dll, min_size=0)
            self.assertEqual(first["state"], "already")
            self.assertEqual(second["state"], "already")
            self.assertTrue(verify_license_nops(dll))
            self.assertEqual(read_at(dll, LICENSE_PATCH_OFFSET, 6), LICENSE_NOPPED_BYTES)

    def test_unexpected_bytes_raise_and_do_not_modify(self):
        with tempfile.TemporaryDirectory() as td:
            engine = make_fake_engine(Path(td), dll_license=b"\xde\xad\xbe\xef\x00\x01")
            dll = engine / "144.0.7559.96" / "chrome.dll"
            before = dll.read_bytes()
            with self.assertRaises(ValueError):
                patch_license_nops(dll, min_size=0)
            self.assertEqual(dll.read_bytes(), before)

    def test_too_small_raises(self):
        with tempfile.TemporaryDirectory() as td:
            dll = Path(td) / "chrome.dll"
            dll.write_bytes(b"tiny")
            with self.assertRaises(ValueError):
                patch_license_nops(dll)

    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                patch_license_nops(Path(td) / "nope.dll", min_size=0)


class TestPatchBranding(unittest.TestCase):
    def test_same_length_strings_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "chrome.exe"
            f.write_bytes(b"AA" + "@huynhthang".encode("utf-16le") + "HT Browser".encode("utf-16le") + b"BB")
            report = patch_branding(f)
            self.assertTrue(report["applied"])
            data = f.read_bytes()
            self.assertNotIn("@huynhthang".encode("utf-16le"), data)
            self.assertNotIn("HT Browser".encode("utf-16le"), data)
            self.assertIn("DONGLAO-APP".encode("utf-16le"), data)
            self.assertIn("DONGLAO144".encode("utf-16le"), data)
            self.assertTrue((f.with_name(f.name + ".original_backup")).exists())

    def test_missing_source_string_reported_not_applied(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "chrome.exe"
            f.write_bytes(b"no branding strings here")
            report = patch_branding(f)
            self.assertFalse(report["applied"])
            self.assertEqual(sum(p["count"] for p in report["pairs"]), 0)
            self.assertFalse((f.with_name(f.name + ".original_backup")).exists())

    def test_apply_twice_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "chrome.exe"
            f.write_bytes(b"x" + "@huynhthang".encode("utf-16le"))
            patch_branding(f)
            first = f.read_bytes()
            patch_branding(f)
            second = f.read_bytes()
            self.assertEqual(first, second)
            self.assertEqual(verify_branding(f), [{"source": "@huynhthang", "count": 0}, {"source": "HT Browser", "count": 0}])


class TestPatchEngineDir(unittest.TestCase):
    def test_patches_staging_engine(self):
        with tempfile.TemporaryDirectory() as td:
            src_root = Path(td) / "src"
            src_root.mkdir()
            make_fake_engine(src_root)
            staging = Path(td) / "staging"
            engine = make_staging_copy(src_root / "fake-browser-144", staging / "fake-browser-144")
            report = patch_engine_dir(engine, allowed_root=staging, min_dll_size=0)
            self.assertEqual(report["license"]["state"], "patched")
            self.assertTrue(verify_license_nops(engine / "144.0.7559.96" / "chrome.dll"))

    def test_refuses_outside_allowed_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_fake_engine(root)
            engine = root / "fake-browser-144"
            other_root = root / "elsewhere"
            other_root.mkdir()
            with self.assertRaises(ValueError):
                patch_engine_dir(engine, allowed_root=other_root)

    def test_source_engine_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            src_root = Path(td) / "src"
            src_root.mkdir()
            engine = make_fake_engine(src_root)
            dll = engine / "144.0.7559.96" / "chrome.dll"
            before = dll.read_bytes()
            staging = Path(td) / "staging"
            staged = make_staging_copy(engine, staging / engine.name)
            patch_engine_dir(staged, allowed_root=staging, min_dll_size=0)
            self.assertEqual(dll.read_bytes(), before)
            self.assertTrue(verify_license_nops(staged / "144.0.7559.96" / "chrome.dll"))


class TestScriptCLI(unittest.TestCase):
    def test_patch_all_supports_multiple_engines(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir()
            make_fake_engine(src)
            staging = Path(td) / "staging"
            staging.mkdir()
            code = patch_all_browser_engines.main(["--source", str(src), "--staging", str(staging), "--engines", "fake-browser-144", "--min-dll-size", "0"])
            self.assertEqual(code, 0)
            self.assertTrue((staging / "fake-browser-144").exists())
            self.assertTrue(verify_license_nops(staging / "fake-browser-144" / "144.0.7559.96" / "chrome.dll"))
            src_dll = src / "fake-browser-144" / "144.0.7559.96" / "chrome.dll"
            self.assertEqual(read_at(src_dll, LICENSE_PATCH_OFFSET, 6), LICENSE_EXPECTED_BYTES)

    def test_patch_all_rejects_staging_inside_source(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir()
            with self.assertRaises(ValueError):
                patch_all_browser_engines.main(["--source", str(src), "--staging", str(src / "nested")])

    def test_chrome_version_info_branding_only(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            src.mkdir()
            make_fake_engine(src)
            staging = Path(td) / "staging"
            staging.mkdir()
            code = patch_chrome_version_info.main(["--source", str(src), "--staging", str(staging), "--engine", "fake-browser-144"])
            self.assertEqual(code, 0)
            staged = staging / "fake-browser-144"
            self.assertNotIn("@huynhthang".encode("utf-16le"), (staged / "chrome.exe").read_bytes())
            src_exe = (src / "fake-browser-144" / "chrome.exe").read_bytes()
            self.assertIn("@huynhthang".encode("utf-16le"), src_exe)


if __name__ == "__main__":
    unittest.main()