"""
test_profile_config_engine.py - Unit tests for profile_config_engine.py
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import profile_config_engine as pce
from profile_config_engine import (
    CHROME_MAJOR,
    CHROME_FULL_VERSION,
    DEFAULT_GL_PARAM_VALUES,
    DEFAULT_PLUGINS,
    DEFAULT_WEBGL_EXTENSIONS,
    generate_deterministic_seed,
    generate_stealth_profile_config,
    generate_orbita_profile_config,
    write_profile_config_files,
)

BASE_TEMPLATE = {
    "profile_name": "AUTO 6",
    "license_key": "F2E37BBD44DEA545F4880DF1ABEAB190CD8B0A833C84C53712348E6FB61A5C88",
    "canvas": {"noiseEnabled": True, "noiseSeed": 7},
    "audio": {"noiseEnabled": True, "noiseSeed": 8},
}


class TestProfileConfigEngine(unittest.TestCase):
    def test_deterministic_seed_consistency(self):
        """Same account_uuid and salt always produces the exact same seed."""
        uuid_1 = "acc-1234-5678-abcd"
        seed_1a = generate_deterministic_seed(uuid_1, "canvas")
        seed_1b = generate_deterministic_seed(uuid_1, "canvas")
        self.assertEqual(seed_1a, seed_1b)
        self.assertIsInstance(seed_1a, int)
        self.assertGreaterEqual(seed_1a, 0)

    def test_different_seeds_for_different_salts_and_uuids(self):
        """Different seeds for canvas vs audio, and different uuids."""
        uuid_1 = "acc-1234-5678-abcd"
        uuid_2 = "acc-9999-8888-zzzz"
        canvas_seed_1 = generate_deterministic_seed(uuid_1, "canvas")
        audio_seed_1 = generate_deterministic_seed(uuid_1, "audio")
        canvas_seed_2 = generate_deterministic_seed(uuid_2, "canvas")

        self.assertNotEqual(canvas_seed_1, audio_seed_1)
        self.assertNotEqual(canvas_seed_1, canvas_seed_2)

    def test_generate_stealth_profile_config_structure(self):
        """Config schema contains all 42 GL params, 34 extensions, 5 plugins, formFactors, and Chrome 149."""
        uuid_test = "test-uuid-4444"
        geoip = {
            "timezone": "Europe/Berlin",
            "latitude": 52.5200,
            "longitude": 13.4050,
            "ip": "185.220.101.5",
        }
        config = generate_stealth_profile_config(
            account_uuid=uuid_test,
            geoip_info=geoip,
            user_agent=f"Custom/{CHROME_FULL_VERSION}",
        )

        self.assertEqual(config["profile_name"], uuid_test)
        self.assertTrue(config["canvas"]["noiseEnabled"])
        self.assertIsInstance(config["canvas"]["noiseSeed"], int)
        self.assertTrue(config["audio"]["noiseEnabled"])
        self.assertIsInstance(config["audio"]["noiseSeed"], int)
        self.assertEqual(config["timezone"]["name"], "Europe/Berlin")
        self.assertEqual(config["geoLocation"]["latitude"], 52.5200)
        self.assertEqual(config["geoLocation"]["longitude"], 13.4050)
        self.assertEqual(config["webrtc"]["fakePublicIP"], "185.220.101.5")
        self.assertFalse(config["webrtc"]["disableWebRTC"])
        self.assertEqual(config["navigator"]["userAgent"], f"Custom/{CHROME_FULL_VERSION}")
        
        # 43 GL params & 34 extensions
        self.assertEqual(len(config["webgl"]["glParamValues"]), len(DEFAULT_GL_PARAM_VALUES))
        self.assertEqual(len(config["webgl"]["extensions"]), len(DEFAULT_WEBGL_EXTENSIONS))
        
        # 5 plugins
        self.assertEqual(len(config["plugins"]["list"]), 5)
        plugin_names = [p["name"] for p in config["plugins"]["list"]]
        self.assertIn("PDF Viewer", plugin_names)
        self.assertIn("Chrome PDF Viewer", plugin_names)
        self.assertIn("Chromium PDF Viewer", plugin_names)
        self.assertIn("Microsoft Edge PDF Viewer", plugin_names)
        self.assertIn("WebKit built-in PDF", plugin_names)

        # Client Hints: Chrome 149 & formFactors Desktop
        self.assertIn("formFactors", config["clientHints"])
        self.assertEqual(config["clientHints"]["formFactors"], ["Desktop"])
        self.assertEqual(config["clientHints"]["fullVersion"], CHROME_FULL_VERSION)

    def test_backward_compatible_alias(self):
        """generate_orbita_profile_config is an exact alias of generate_stealth_profile_config."""
        self.assertIs(generate_orbita_profile_config, generate_stealth_profile_config)

    def test_proxy_auto_detection_for_webrtc(self):
        """When geoip is empty, fakePublicIP is extracted from proxy_info."""
        config = generate_stealth_profile_config(
            account_uuid="test-proxy-uuid",
            proxy_info={"server": "http://45.61.124.46:6375"},
        )
        self.assertEqual(config["webrtc"]["fakePublicIP"], "45.61.124.46")


class TestWriteProfileConfigTemplateFlow(unittest.TestCase):
    """Gate tests for write_profile_config_files template flow (v1.1.1)."""

    def _template_root(self, td: str) -> str:
        """Build a fake module dir with assets/templates and return its path."""
        module_dir = Path(td) / "module"
        tpl_dir = module_dir / "assets" / "templates"
        tpl_dir.mkdir(parents=True)
        (tpl_dir / "base_data.huynhthang").write_text(json.dumps(BASE_TEMPLATE), encoding="utf-8")
        (tpl_dir / "base_data.orbita").write_text(json.dumps(BASE_TEMPLATE), encoding="utf-8")
        return str(module_dir / "profile_config_engine.py")

    def test_template_sets_dynamic_profile_name(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(pce, "__file__", self._template_root(td)), \
                patch.object(pce, "find_ttm_raw_profile_file", return_value=None):
            prof = Path(td) / "prof"
            write_profile_config_files(prof, {"profile_name": "BKT_8"})
            data = json.loads((prof / "data.huynhthang").read_text(encoding="utf-8"))
            self.assertEqual(data["profile_name"], "BKT_8")
            self.assertEqual(data["license_key"], BASE_TEMPLATE["license_key"])

    def test_template_syncs_proxy(self):
        proxy = {"host": "1.2.3.4", "port": 8080, "type": "http"}
        with tempfile.TemporaryDirectory() as td, \
                patch.object(pce, "__file__", self._template_root(td)), \
                patch.object(pce, "find_ttm_raw_profile_file", return_value=None):
            prof = Path(td) / "prof"
            write_profile_config_files(prof, {"profile_name": "P1", "proxy": proxy})
            data = json.loads((prof / "data.huynhthang").read_text(encoding="utf-8"))
            self.assertEqual(data["proxy"], proxy)
            self.assertEqual(data["profile_name"], "P1")

    def test_template_writes_identical_files(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(pce, "__file__", self._template_root(td)), \
                patch.object(pce, "find_ttm_raw_profile_file", return_value=None):
            prof = Path(td) / "prof"
            write_profile_config_files(prof, {"profile_name": "P2"})
            self.assertEqual(
                (prof / "data.huynhthang").read_bytes(),
                (prof / "data.orbita").read_bytes(),
            )

    def test_corrupt_template_falls_back_to_binary_copy(self):
        module_dir = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as td:
            fake_module = Path(td) / "module"
            tpl_dir = fake_module / "assets" / "templates"
            tpl_dir.mkdir(parents=True)
            raw = b"{ not valid json \x00\xff }"
            (tpl_dir / "base_data.huynhthang").write_bytes(raw)
            with patch.object(pce, "__file__", str(fake_module / "profile_config_engine.py")), \
                    patch.object(pce, "find_ttm_raw_profile_file", return_value=None):
                prof = Path(td) / "prof"
                write_profile_config_files(prof, {"profile_name": "P3"})
                self.assertEqual((prof / "data.huynhthang").read_bytes(), raw)
                self.assertEqual((prof / "data.orbita").read_bytes(), raw)

    def test_missing_template_falls_back_to_json(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(pce, "__file__", str(Path(td) / "module" / "profile_config_engine.py")), \
                patch.object(pce, "find_ttm_raw_profile_file", return_value=None):
            prof = Path(td) / "prof"
            write_profile_config_files(prof, {"profile_name": "P4"})
            data = json.loads((prof / "data.huynhthang").read_text(encoding="utf-8"))
            self.assertEqual(data["profile_name"], "P4")
            self.assertTrue(data.get("license_key"))

    def test_template_flow_ignores_ttm_raw_file(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(pce, "__file__", self._template_root(td)), \
                patch.object(
                    pce,
                    "find_ttm_raw_profile_file",
                    return_value=Path(td) / "some_ttm" / "data.huynhthang",
                ):
            prof = Path(td) / "prof"
            write_profile_config_files(prof, {"profile_name": "BKT_8"})
            data = json.loads((prof / "data.huynhthang").read_text(encoding="utf-8"))
            self.assertEqual(data["profile_name"], "BKT_8")


if __name__ == "__main__":
    unittest.main()
