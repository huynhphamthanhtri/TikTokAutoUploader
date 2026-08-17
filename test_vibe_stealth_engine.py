"""
test_vibe_stealth_engine.py - Unit tests for DONGLAO Native Stealth Engine.
"""

import unittest
from vibe_stealth_engine import (
    generate_deterministic_seed,
    generate_stealth_js,
    attach_stealth_to_context,
    attach_stealth_to_page,
)


class TestVibeStealthEngine(unittest.TestCase):

    def test_deterministic_seed_consistency(self):
        seed1 = generate_deterministic_seed("uuid-12345", "canvas")
        seed2 = generate_deterministic_seed("uuid-12345", "canvas")
        seed3 = generate_deterministic_seed("uuid-99999", "canvas")
        
        self.assertEqual(seed1, seed2)
        self.assertNotEqual(seed1, seed3)
        self.assertGreaterEqual(seed1, 0)

    def test_generate_stealth_js_content(self):
        config = {
            "account_uuid": "test-uuid-abc",
            "hardware_concurrency": 16,
            "device_memory": 8,
            "webrtc": {
                "fakePublicIP": "45.61.124.46"
            },
            "webgl": {
                "vendor": "Google Inc. (NVIDIA)",
                "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"
            }
        }
        script = generate_stealth_js(config)
        self.assertIn("navigator.webdriver", script)
        self.assertIn("NVIDIA GeForce RTX 3060", script)
        self.assertIn("window.chrome", script)
        self.assertIn("CanvasRenderingContext2D.prototype.getImageData", script)
        self.assertIn("AudioBuffer.prototype.getChannelData", script)
        self.assertIn("userAgentData", script)

        # 42 GL params & 34 extensions verification
        self.assertIn("MAX_TEXTURE_SIZE", script)
        self.assertIn("MAX_VIEWPORT_DIMS", script)
        self.assertIn("ALIASED_LINE_WIDTH_RANGE", script)
        self.assertIn("getSupportedExtensions", script)
        self.assertIn("ANGLE_instanced_arrays", script)
        self.assertIn("OVR_multiview2", script)

        # WebRTC leak protection verification
        self.assertIn("45.61.124.46", script)
        self.assertIn("sanitizeSDP", script)
        self.assertIn("icecandidate", script)

        # Client Hints verification
        self.assertIn('formFactors: ["Desktop"]', script)
        self.assertIn("149", script)

        # 5 PDF viewer plugins verification
        self.assertIn("PDF Viewer", script)
        self.assertIn("Chrome PDF Viewer", script)
        self.assertIn("Chromium PDF Viewer", script)
        self.assertIn("Microsoft Edge PDF Viewer", script)
        self.assertIn("WebKit built-in PDF", script)

    def test_attach_stealth_to_context_and_page(self):
        class MockTarget:
            def __init__(self):
                self.scripts = []
            def add_init_script(self, script):
                self.scripts.append(script)

        mock_context = MockTarget()
        attach_stealth_to_context(mock_context, {"account_uuid": "123"})
        self.assertEqual(len(mock_context.scripts), 1)

        mock_page = MockTarget()
        attach_stealth_to_page(mock_page, {"account_uuid": "123"})
        self.assertEqual(len(mock_page.scripts), 1)


if __name__ == "__main__":
    unittest.main()
