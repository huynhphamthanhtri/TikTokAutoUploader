"""Integration test for monitor lifecycle (start/stop/restart)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))


class TestMonitorLifecycle(unittest.TestCase):
    def test_start_stop_restart(self):
        with patch("youtube_monitor.core.make_server") as mock_ms, \
             patch("youtube_monitor.core.ngrok.connect") as mock_ng, \
             patch("youtube_monitor.core.ngrok.kill") as mock_kill, \
             patch("youtube_monitor.core.requests.post") as mock_post, \
             patch("youtube_monitor.core.requests.get") as mock_get, \
             patch("youtube_monitor.core._ngrok_bin_path", return_value=None), \
             patch("youtube_monitor.core._load_tiktok_proxies", return_value=({}, [])):

            from youtube_monitor.core import (
                start_monitor, stop_monitor, get_status, get_monitor_health,
            )

            # Setup mocks
            mock_server = MagicMock()
            mock_server.server_address = ("0.0.0.0", 5000)
            mock_ms.return_value = mock_server

            mock_tunnel = MagicMock()
            mock_tunnel.public_url = "http://abc.ngrok-free.app"
            mock_ng.return_value = mock_tunnel

            def _mock_get(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                if "hub.challenge=" in url:
                    import urllib.parse
                    q = urllib.parse.urlparse(url).query
                    params = urllib.parse.parse_qs(q)
                    ch = params.get("hub.challenge", [""])[0]
                    resp.text = ch
                else:
                    resp.text = "ok"
                    resp.json.return_value = {"ok": True, "instance_id": "test"}
                return resp

            mock_get.side_effect = _mock_get

            mock_post.return_value.status_code = 202

            # Start
            ok, msg = start_monitor()
            self.assertTrue(ok, f"Start failed: {msg}")

            status = get_status()
            self.assertTrue(status["running"])
            self.assertTrue(status["healthy"])
            self.assertEqual(status["callback_port"], 5000)

            health_ok, health_msg = get_monitor_health()
            self.assertTrue(health_ok, f"Health check failed: {health_msg}")

            # Stop
            ok, msg = stop_monitor()
            self.assertTrue(ok, f"Stop failed: {msg}")

            status2 = get_status()
            self.assertFalse(status2["running"])

            # Restart
            ok, msg = start_monitor()
            self.assertTrue(ok, f"Restart failed: {msg}")

            health_ok2, health_msg2 = get_monitor_health()
            self.assertTrue(health_ok2, f"Health after restart: {health_msg2}")

            # Final stop
            ok, msg = stop_monitor()
            self.assertTrue(ok, f"Final stop failed: {msg}")

            # Clean start after full stop
            ok, msg = start_monitor()
            self.assertTrue(ok, f"Clean start failed: {msg}")

            ok, msg = stop_monitor()
            self.assertTrue(ok, f"Clean stop failed: {msg}")

    def test_double_start_returns_ok(self):
        with patch("youtube_monitor.core.make_server") as mock_ms, \
             patch("youtube_monitor.core.ngrok.connect") as mock_ng, \
             patch("youtube_monitor.core.ngrok.kill"), \
             patch("youtube_monitor.core.requests.post"), \
             patch("youtube_monitor.core.requests.get") as mock_get, \
             patch("youtube_monitor.core._ngrok_bin_path", return_value=None), \
             patch("youtube_monitor.core._load_tiktok_proxies", return_value=({}, [])):

            from youtube_monitor.core import start_monitor, stop_monitor

            mock_server = MagicMock()
            mock_server.server_address = ("0.0.0.0", 5000)
            mock_ms.return_value = mock_server

            mock_tunnel = MagicMock()
            mock_tunnel.public_url = "http://abc.ngrok-free.app"
            mock_ng.return_value = mock_tunnel

            def _mock_get2(url, **kw):
                resp = MagicMock()
                resp.status_code = 200
                if "hub.challenge=" in url:
                    import urllib.parse
                    q = urllib.parse.urlparse(url).query
                    params = urllib.parse.parse_qs(q)
                    ch = params.get("hub.challenge", [""])[0]
                    resp.text = ch
                else:
                    resp.text = "ok"
                    resp.json.return_value = {"ok": True, "instance_id": "test"}
                return resp

            mock_get.side_effect = _mock_get2

            ok1, _ = start_monitor()
            self.assertTrue(ok1)

            ok2, msg2 = start_monitor()
            self.assertTrue(ok2, f"Double start should return OK: {msg2}")

            ok3, msg3 = stop_monitor()
            self.assertTrue(ok3, f"Stop failed: {msg3}")

    def test_stop_when_not_started(self):
        from youtube_monitor.core import stop_monitor
        ok, msg = stop_monitor()
        self.assertTrue(ok)
        self.assertIn("chưa chạy", msg.lower())


if __name__ == "__main__":
    unittest.main()
