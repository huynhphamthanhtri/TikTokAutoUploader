"""
test_orbita_integration.py - Unit and integration tests for HT Browser (Orbita 144) C++ native anti-detect.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import browser_patchright_glue as glue
import profile_config_engine as pce


class TestOrbitaIntegration(unittest.TestCase):
    def test_write_profile_config_files_creates_valid_files(self):
        """Verify write_profile_config_files generates both data.huynhthang and data.orbita with license_key."""
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = pce.generate_stealth_profile_config(
                account_uuid="acc_test_orbita",
                profile_name="AUTO 22",
                proxy_info={"server": "http://38.154.233.85:5495", "username": "u1", "password": "p1"},
                geoip_info={"timezone": "America/New_York", "ip": "38.154.233.85", "latitude": 40.5, "longitude": -74.4},
            )
            with patch("profile_config_engine.find_ttm_raw_profile_file", return_value=None):
                pce.write_profile_config_files(temp_dir, cfg)
            
            p = Path(temp_dir)
            p_dh = p / "data.huynhthang"
            p_orb = p / "data.orbita"
            
            self.assertTrue(p_dh.exists(), "data.huynhthang must be written for HT Browser C++ engine")
            self.assertTrue(p_orb.exists(), "data.orbita must be written for HT Browser C++ engine")
            
            with open(p_dh, "r", encoding="utf-8") as f:
                dh_data = json.load(f)
            
            self.assertTrue(bool(dh_data.get("profile_name")), "profile_name must be present in C++ config")
            self.assertTrue(bool(dh_data["license_key"]))
            self.assertTrue(dh_data["canvas"]["noiseEnabled"])
            self.assertTrue(dh_data["audio"]["noiseEnabled"])

    def test_build_session_config_writes_configs_and_sets_args(self):
        """Verify build_session_config sets --antidetect-optional, UnoPhase2FollowUp and writes config files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config = {
                "browser_profile_path": temp_dir,
                "account_uuid": "acc_build_test",
                "profile_name": "AUTO 22",
                "proxy_string": "38.154.233.85:5495:user:pass",
                "fingerprint": {
                    "timezone": "America/New_York",
                    "geo_exit_ip": "38.154.233.85",
                }
            }
            session = glue.build_session_config(config)
            
            # Check args
            self.assertIn("--antidetect-optional", session.args)
            self.assertTrue(any("UnoPhase2FollowUp" in arg for arg in session.args))
            
            # Check files on disk
            p = Path(temp_dir)
            self.assertTrue((p / "data.huynhthang").exists())
            self.assertTrue((p / "data.orbita").exists())

    def test_resolve_browser_executable_preference(self):
        """Verify resolve_browser_executable prioritizes orbita-browser-144 as primary engine for all profiles."""
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            orbita_dir = base / "Browser" / "orbita-browser-144"
            orbita_dir.mkdir(parents=True)
            orbita_exe = orbita_dir / "chrome.exe"
            orbita_exe.write_bytes(b"x")

            chrome64_dir = base / "Browser" / "chrome-win64"
            chrome64_dir.mkdir(parents=True)
            chrome64_exe = chrome64_dir / "chrome.exe"
            chrome64_exe.write_bytes(b"x")

            resolved = glue.resolve_browser_executable(app_base=base, profile_name="AUTO 22")
            self.assertEqual(resolved, str(orbita_exe))

            resolved_standalone = glue.resolve_browser_executable(app_base=base, profile_name="STANDALONE_PROFILE")
            self.assertEqual(resolved_standalone, str(orbita_exe))

    def test_proxy_obj_normalization_formats(self):
        """Verify proxy object normalization handles both server URL format and host/port format."""
        cfg1 = pce.generate_stealth_profile_config("uuid1", proxy_info={"server": "http://1.2.3.4:8080", "username": "a", "password": "b"})
        self.assertEqual(cfg1["proxy"]["host"], "1.2.3.4")
        self.assertEqual(cfg1["proxy"]["port"], 8080)
        self.assertEqual(cfg1["proxy"]["type"], "http")

        cfg2 = pce.generate_stealth_profile_config("uuid2", proxy_info={"host": "5.6.7.8", "port": 9090, "type": "socks5", "user": "c", "pass": "d"})
        self.assertEqual(cfg2["proxy"]["host"], "5.6.7.8")
        self.assertEqual(cfg2["proxy"]["port"], 9090)
        self.assertEqual(cfg2["proxy"]["type"], "socks5")
        self.assertEqual(cfg2["proxy"]["username"], "c")
        self.assertEqual(cfg2["proxy"]["password"], "d")


if __name__ == "__main__":
    unittest.main()
