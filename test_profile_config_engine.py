"""
test_profile_config_engine.py - Unit tests for profile_config_engine.py
"""

import json
import os
import unittest
from unittest.mock import patch

from profile_config_engine import (
    CHROME_MAJOR,
    CHROME_FULL_VERSION,
    DEFAULT_GL_PARAM_VALUES,
    DEFAULT_PLUGINS,
    DEFAULT_WEBGL_EXTENSIONS,
    generate_deterministic_seed,
    generate_stealth_profile_config,
    generate_orbita_profile_config,
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


if __name__ == "__main__":
    unittest.main()
