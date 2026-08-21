"""Integration test for monitor lifecycle (start/stop/restart)."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))


class TestMonitorLifecycle(unittest.TestCase):
    @staticmethod
    def _wait_for_stop(*_args, **_kwargs):
        import youtube_monitor.core as core
        core.stop_event.wait(5)

    @staticmethod
    def _reset_monitor_state():
        import youtube_monitor.core as core
        core.stop_event.set()
        core.channels_store.stop_autosave()
        core._stop_callback_server()
        core._join_all_threads(timeout=10)
        core._all_threads.clear()
        for work_queue in (core.websub_payload_queue, core.download_queue):
            while True:
                try:
                    work_queue.get_nowait()
                    work_queue.task_done()
                except core.queue.Empty:
                    break
        core._monitor_started = False
        core._monitor_started_epoch = None

    def setUp(self):
        self._reset_monitor_state()

    def tearDown(self):
        self._reset_monitor_state()

    def test_start_stop_restart(self):
        with patch("youtube_monitor.core.make_server") as mock_ms, \
             patch("youtube_monitor.core.ngrok_owner.start_owned_agent") as mock_owned_start, \
             patch("youtube_monitor.core.ngrok_owner.stop_owned_agent"), \
             patch("youtube_monitor.core.ngrok_owner.owned_agent_alive", return_value=(True, {})), \
             patch("youtube_monitor.core.ngrok_owner.validate_auth_ready", return_value=(True, "ready (environment)")), \
             patch("youtube_monitor.core.requests.post") as mock_post, \
             patch("youtube_monitor.core.requests.get") as mock_get, \
             patch("youtube_monitor.core._ngrok_bin_path", return_value=None), \
             patch("youtube_monitor.core._load_tiktok_proxies", return_value=({}, [])), \
             patch("youtube_monitor.core.websub_processor_worker", side_effect=self._wait_for_stop), \
             patch("youtube_monitor.core.worker_main", side_effect=self._wait_for_stop), \
             patch("youtube_monitor.core._retry_maintainer", side_effect=self._wait_for_stop), \
             patch("youtube_monitor.core._resubscribe_worker", side_effect=self._wait_for_stop), \
             patch("youtube_monitor.core._recovery_worker", side_effect=self._wait_for_stop):

            from youtube_monitor.core import (
                start_monitor, stop_monitor, get_status, get_monitor_health,
            )

            # Setup mocks
            mock_server = MagicMock()
            mock_server.server_address = ("0.0.0.0", 5000)
            mock_ms.return_value = mock_server

            mock_owned_start.return_value = (True, {"public_url": "http://abc.ngrok-free.app"})

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
             patch("youtube_monitor.core.ngrok_owner.start_owned_agent") as mock_owned_start, \
             patch("youtube_monitor.core.ngrok_owner.stop_owned_agent"), \
             patch("youtube_monitor.core.ngrok_owner.owned_agent_alive", return_value=(True, {})), \
             patch("youtube_monitor.core.ngrok_owner.validate_auth_ready", return_value=(True, "ready (environment)")), \
             patch("youtube_monitor.core.requests.post"), \
             patch("youtube_monitor.core.requests.get") as mock_get, \
             patch("youtube_monitor.core._ngrok_bin_path", return_value=None), \
             patch("youtube_monitor.core._load_tiktok_proxies", return_value=({}, [])), \
             patch("youtube_monitor.core.websub_processor_worker", side_effect=self._wait_for_stop), \
             patch("youtube_monitor.core.worker_main", side_effect=self._wait_for_stop), \
             patch("youtube_monitor.core._retry_maintainer", side_effect=self._wait_for_stop), \
             patch("youtube_monitor.core._resubscribe_worker", side_effect=self._wait_for_stop), \
             patch("youtube_monitor.core._recovery_worker", side_effect=self._wait_for_stop):

            from youtube_monitor.core import start_monitor, stop_monitor

            mock_server = MagicMock()
            mock_server.server_address = ("0.0.0.0", 5000)
            mock_ms.return_value = mock_server

            mock_owned_start.return_value = (True, {"public_url": "http://abc.ngrok-free.app"})

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

    def test_start_fails_before_workers_when_auth_missing(self):
        with patch("youtube_monitor.core.make_server") as mock_ms, \
             patch("youtube_monitor.core.ngrok_owner.validate_auth_ready", return_value=(False, "Ngrok chưa được xác thực. add-authtoken")), \
             patch("youtube_monitor.core.requests.get") as mock_get, \
             patch("youtube_monitor.core._load_tiktok_proxies", return_value=({}, [])):

            from youtube_monitor.core import start_monitor, get_status, _all_threads

            mock_server = MagicMock()
            mock_server.server_address = ("0.0.0.0", 5000)
            mock_ms.return_value = mock_server
            mock_get.return_value.status_code = 200

            ok, msg = start_monitor()
            self.assertFalse(ok, f"Start should fail when ngrok auth missing: {msg}")
            self.assertIn("authtoken", msg.lower())

            status = get_status()
            self.assertFalse(status["running"])
            self.assertEqual(status["monitor_state"], "STOPPED")
            self.assertIn("authtoken", status["last_error"].lower())

            alive = [t for t in list(_all_threads) if t.is_alive()]
            self.assertEqual(len(alive), 0, f"No worker should be running after auth failure: {alive}")

    def test_start_fails_before_workers_when_ngrok_connect_fails(self):
        with patch("youtube_monitor.core.make_server") as mock_ms, \
             patch("youtube_monitor.core.ngrok_owner.validate_auth_ready", return_value=(True, "ready (environment)")), \
             patch("youtube_monitor.core.ngrok_owner.start_owned_agent", return_value=(False, "Ngrok authtoken bị từ chối (ERR_NGROK_4018).")), \
             patch("youtube_monitor.core.requests.get") as mock_get, \
             patch("youtube_monitor.core._load_tiktok_proxies", return_value=({}, [])):

            from youtube_monitor.core import start_monitor, get_status, _all_threads

            mock_server = MagicMock()
            mock_server.server_address = ("0.0.0.0", 5000)
            mock_ms.return_value = mock_server
            mock_get.return_value.status_code = 200

            ok, msg = start_monitor()
            self.assertFalse(ok, f"Start should fail when ngrok connect fails: {msg}")
            self.assertIn("4018", msg)

            status = get_status()
            self.assertFalse(status["running"])
            self.assertEqual(status["monitor_state"], "STOPPED")
            self.assertIn("4018", status["last_error"])

            alive = [t for t in list(_all_threads) if t.is_alive()]
            self.assertEqual(len(alive), 0, f"No worker should be running after ngrok failure: {alive}")

    def test_start_fails_before_workers_when_tunnel_verify_fails(self):
        with patch("youtube_monitor.core.make_server") as mock_ms, \
             patch("youtube_monitor.core.ngrok_owner.validate_auth_ready", return_value=(True, "ready (environment)")), \
             patch("youtube_monitor.core.ngrok_owner.start_owned_agent", return_value=(True, {"public_url": "http://abc.ngrok-free.app"})), \
             patch("youtube_monitor.core.ngrok_owner.stop_owned_agent"), \
             patch("youtube_monitor.core.requests.get") as mock_get, \
             patch("youtube_monitor.core._verify_ngrok_tunnel", return_value=False), \
             patch("youtube_monitor.core._load_tiktok_proxies", return_value=({}, [])):

            from youtube_monitor.core import start_monitor, get_status, _all_threads

            mock_server = MagicMock()
            mock_server.server_address = ("0.0.0.0", 5000)
            mock_ms.return_value = mock_server
            mock_get.return_value.status_code = 200

            ok, msg = start_monitor()
            self.assertFalse(ok, f"Start should fail when tunnel verify fails: {msg}")
            self.assertIn("tunnel", msg.lower())

            status = get_status()
            self.assertFalse(status["running"])
            self.assertEqual(status["monitor_state"], "STOPPED")

            alive = [t for t in list(_all_threads) if t.is_alive()]
            self.assertEqual(len(alive), 0, f"No worker should be running after verify failure: {alive}")


if __name__ == "__main__":
    unittest.main()
