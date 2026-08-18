"""
test_antidetect_suite.py - Unit tests for Anti-Detect Engine upgrades.
"""

import unittest
from unittest.mock import patch, MagicMock

from vibe_stealth_engine import (
    CHROME_MAJOR,
    CHROME_FULL_VERSION,
    generate_deterministic_seed,
    generate_stealth_js,
)
from profile_config_engine import (
    CHROME_MAJOR as CFG_CHROME_MAJOR,
    CHROME_FULL_VERSION as CFG_CHROME_FULL_VERSION,
    generate_stealth_profile_config,
)
from browser_patchright_glue import build_session_config, SessionMode


class TestAntiDetectSuite(unittest.TestCase):
    def test_no_expose_gc_in_launch_args(self):
        """Verify that --expose-gc is strictly excluded from launch args."""
        dummy_config = {
            "browser_profile_path": "e:\\dummy\\path",
            "headless": True,
            "browser_executable": "e:\\dummy\\chrome.exe",
        }
        session_cfg = build_session_config(dummy_config)
        args_str = " ".join(session_cfg.args)
        self.assertNotIn("--expose-gc", args_str, "--expose-gc must NOT be present in launch args (triggers bot detection)")
        # Make sure --max-old-space-size=256 is still retained for RAM management
        self.assertIn("--js-flags=--max-old-space-size=256", session_cfg.args)

    def test_webrtc_protection_flags_in_launch_args(self):
        """Verify WebRTC anti-leak flags are present in Chromium args."""
        dummy_config = {
            "browser_profile_path": "e:\\dummy\\path",
            "headless": True,
            "browser_executable": "e:\\dummy\\chrome.exe",
        }
        session_cfg = build_session_config(dummy_config)
        self.assertIn("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", session_cfg.args)
        self.assertIn("--disable-webrtc-multiple-routes", session_cfg.args)

    def test_stealth_js_native_masking_present(self):
        """Verify JavaScript stealth script contains makeNative and Function.prototype.toString protection."""
        cfg = generate_stealth_profile_config(
            account_uuid="test_acc_123",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            profile_name="Profile_Test",
        )
        js = generate_stealth_js(cfg)
        
        # Native Code Masking checks
        self.assertIn("const _nativeFns = new Map();", js)
        self.assertIn("Function.prototype.toString", js)
        self.assertIn("[native code]", js)
        self.assertIn("function makeNative(fn, name)", js)

        # Hook wrappers with makeNative
        self.assertIn("makeNative(function getParameter", js)
        self.assertIn("makeNative(function getImageData", js)
        self.assertIn("makeNative(function toDataURL", js)
        self.assertIn("makeNative(function getChannelData", js)
        self.assertIn("makeNative(function getHighEntropyValues", js)
        self.assertIn("makeNative(function createOffer", js)
        self.assertIn("makeNative(function query", js)

    def test_canvas_noise_idempotency_structure(self):
        """Verify WeakMap caching logic is present in Canvas noise to ensure consistent hash per canvas."""
        js = generate_stealth_js({"account_uuid": "idempotent_profile"})
        self.assertIn("_canvasNoiseCache = new WeakMap()", js)
        self.assertIn("_dataUrlCache = new WeakMap()", js)
        self.assertIn("canvasCache[cacheKey]", js)

    def test_chrome_version_consistency(self):
        """Verify Chrome major version matches across modules and is 149."""
        self.assertEqual(CHROME_MAJOR, "149")
        self.assertEqual(CFG_CHROME_MAJOR, "149")
        self.assertEqual(CHROME_FULL_VERSION, "149.0.7827.55")
        self.assertEqual(CFG_CHROME_FULL_VERSION, "149.0.7827.55")

    def test_deterministic_seeds_stability(self):
        """Verify seeds are strictly deterministic and distinct per salt."""
        seed_canvas_1 = generate_deterministic_seed("acc_xyz", "canvas")
        seed_canvas_2 = generate_deterministic_seed("acc_xyz", "canvas")
        seed_audio = generate_deterministic_seed("acc_xyz", "audio")
        
        self.assertEqual(seed_canvas_1, seed_canvas_2)
        self.assertNotEqual(seed_canvas_1, seed_audio)
        self.assertGreater(seed_canvas_1, 0)
        self.assertLess(seed_canvas_1, 2147483647)


if __name__ == "__main__":
    unittest.main()
