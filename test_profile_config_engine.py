"""
test_profile_config_engine.py - Unit tests for profile_config_engine.py
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from profile_config_engine import (
    generate_deterministic_seed,
    generate_orbita_profile_config,
    write_profile_config_files,
)


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

    def test_generate_orbita_profile_config_structure(self):
        """Config schema contains all required Orbita / data.huynhthang sections."""
        uuid_test = "test-uuid-4444"
        geoip = {
            "timezone": "Europe/Berlin",
            "latitude": 52.5200,
            "longitude": 13.4050,
            "ip": "185.220.101.5",
        }
        config = generate_orbita_profile_config(
            account_uuid=uuid_test,
            geoip_info=geoip,
            user_agent="Custom/144.0.0.0",
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
        self.assertEqual(config["navigator"]["userAgent"], "Custom/144.0.0.0")
        self.assertIn("brands", config["clientHints"])

    def test_license_key_is_empty_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VIBE_ORBITA_LICENSE_KEY", None)
            config = generate_orbita_profile_config("uuid-no-license")
        self.assertEqual(config["license_key"], "")

    def test_write_profile_config_files(self):
        """Both data.huynhthang and data.orbita are generated in profile dir."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = generate_orbita_profile_config("uuid-writer-test")
            write_profile_config_files(tmp_dir, config)

            path_ht = os.path.join(tmp_dir, "data.huynhthang")
            path_orbita = os.path.join(tmp_dir, "data.orbita")

            self.assertTrue(os.path.exists(path_ht))
            self.assertTrue(os.path.exists(path_orbita))

            with open(path_ht, "r", encoding="utf-8") as f:
                loaded_ht = json.load(f)
            with open(path_orbita, "r", encoding="utf-8") as f:
                loaded_orbita = json.load(f)

            self.assertEqual(loaded_ht["profile_name"], "uuid-writer-test")
            self.assertEqual(loaded_orbita["profile_name"], "uuid-writer-test")


if __name__ == "__main__":
    unittest.main()
