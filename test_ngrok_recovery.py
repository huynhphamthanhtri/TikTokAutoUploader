"""Tests for the ngrok runtime recovery watchdog."""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from youtube_monitor import core


class NgrokRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._orig_started = core._monitor_started
        self._orig_state = core._monitor_state
        self._orig_port = core._callback_port
        self._orig_url = core.public_callback_url
        self._orig_verified = core.public_callback_verified
        self._orig_attempt = core._recovery_attempt

    def tearDown(self):
        core._monitor_started = self._orig_started
        core._set_monitor_state(self._orig_state)
        core._callback_port = self._orig_port
        core.public_callback_url = self._orig_url
        core.public_callback_verified = self._orig_verified
        core._recovery_attempt = self._orig_attempt

    def test_ngrok_public_url_extraction(self):
        core.public_callback_url = "https://abc.ngrok-free.app/youtube_callback?owner=tok"
        self.assertEqual(core._ngrok_public_url(), "https://abc.ngrok-free.app")
        core.public_callback_url = None
        self.assertIsNone(core._ngrok_public_url())

    def test_callback_health_ok_when_port_none(self):
        core._callback_port = None
        self.assertFalse(core._callback_health_ok())

    @patch("youtube_monitor.core.requests.get")
    def test_callback_health_ok_true(self, mock_get):
        mock_get.return_value.status_code = 200
        core._callback_port = 5001
        self.assertTrue(core._callback_health_ok())

    @patch("youtube_monitor.core.requests.get")
    def test_callback_health_ok_false(self, mock_get):
        mock_get.return_value.status_code = 500
        core._callback_port = 5001
        self.assertFalse(core._callback_health_ok())

    @patch("youtube_monitor.core.requests.get", side_effect=Exception("conn"))
    def test_callback_health_ok_exception(self, mock_get):
        core._callback_port = 5001
        self.assertFalse(core._callback_health_ok())

    @patch("youtube_monitor.core._start_ngrok", return_value=True)
    @patch("youtube_monitor.core.ngrok_owner.stop_owned_agent")
    def test_recover_ngrok_success_resubscribes(self, mock_stop, mock_start):
        core.stop_event = threading.Event()
        core.public_callback_url = "https://new.ngrok-free.app/youtube_callback?owner=t"
        ok = core._recover_ngrok(run_gen=None)
        self.assertTrue(ok)
        mock_stop.assert_called_once()

    @patch("youtube_monitor.core._start_ngrok", return_value=False)
    @patch("youtube_monitor.core.ngrok_owner.stop_owned_agent")
    def test_recover_ngrok_failure(self, mock_stop, mock_start):
        core.stop_event = threading.Event()
        core.public_callback_url = "https://old.ngrok-free.app/youtube_callback?owner=t"
        ok = core._recover_ngrok(run_gen=None)
        self.assertFalse(ok)

    def test_retry_ngrok_recovery_when_not_started(self):
        core._monitor_started = False
        ok, msg = core.retry_ngrok_recovery()
        self.assertFalse(ok)

    @patch("youtube_monitor.core._recovery_kick.set")
    @patch("youtube_monitor.core._set_monitor_state")
    @patch("youtube_monitor.core._refresh_ngrok_auth_status")
    def test_retry_ngrok_recovery_resets_attempt(self, mock_refresh, mock_state, mock_kick):
        core._monitor_started = True
        core._ngrok_auth_status = "ready"
        core._recovery_attempt = 3
        ok, msg = core.retry_ngrok_recovery()
        self.assertTrue(ok)
        self.assertEqual(core._recovery_attempt, 0)
        mock_kick.assert_called_once()

    @patch("youtube_monitor.core._set_monitor_state")
    @patch("youtube_monitor.core._refresh_ngrok_auth_status")
    def test_retry_ngrok_recovery_blocked_without_auth(self, mock_refresh, mock_state):
        core._monitor_started = True
        core._ngrok_auth_status = "missing"
        ok, msg = core.retry_ngrok_recovery()
        self.assertFalse(ok)
        self.assertIn("authtoken", msg.lower())

    def test_wait_recovery_respects_kick(self):
        core._recovery_kick.set()
        start = time.time()
        core._wait_recovery(60)
        elapsed = time.time() - start
        self.assertLess(elapsed, 5)

    @patch("youtube_monitor.core.ngrok_owner.owned_agent_alive", return_value=(False, {}))
    @patch("youtube_monitor.core._callback_health_ok", return_value=False)
    @patch("youtube_monitor.core._recover_ngrok", return_value=False)
    @patch("youtube_monitor.core._wait_recovery")
    @patch("youtube_monitor.core.set_websub_health")
    @patch("youtube_monitor.core._refresh_ngrok_auth_status")
    def test_recovery_worker_degrades_after_max_attempts(self, mock_refresh, mock_health, mock_wait, mock_recover, mock_cb, mock_alive):
        core._monitor_started = True
        core._set_monitor_state("RUNNING")
        core._recovery_attempt = 0
        core._ngrok_auth_status = "ready"
        core.stop_event = threading.Event()

        def fake_wait(seconds):
            if core._recovery_attempt > core.MAX_RECOVERY_ATTEMPTS:
                core.stop_event.set()
            time.sleep(0.01)

        mock_wait.side_effect = fake_wait

        core._recovery_worker(run_gen=None)
        self.assertEqual(core._monitor_state, "DEGRADED")
        self.assertGreaterEqual(core._recovery_attempt, core.MAX_RECOVERY_ATTEMPTS)

    @patch("youtube_monitor.core._refresh_ngrok_auth_status")
    def test_recovery_worker_auth_missing_degrades_immediately(self, mock_refresh):
        core._monitor_started = True
        core._monitor_state = "RUNNING"
        core._recovery_attempt = 0
        core._ngrok_auth_status = "missing"
        core.stop_event = threading.Event()

        def fake_refresh():
            core._ngrok_auth_status = "missing"

        def fake_wait(seconds):
            core.stop_event.set()
            time.sleep(0.01)

        mock_refresh.side_effect = fake_refresh
        with patch("youtube_monitor.core._wait_recovery", side_effect=fake_wait), \
             patch("youtube_monitor.core._callback_health_ok", return_value=False), \
             patch("youtube_monitor.core.ngrok_owner.owned_agent_alive", return_value=(False, {})):
            core._recovery_worker(run_gen=None)
        self.assertEqual(core._monitor_state, "DEGRADED")
        self.assertEqual(core._recovery_attempt, 1)

    @patch("youtube_monitor.core._verify_ngrok_tunnel", return_value=True)
    @patch("youtube_monitor.core.ngrok_owner.owned_agent_alive", return_value=(True, {}))
    @patch("youtube_monitor.core._callback_health_ok", return_value=True)
    @patch("youtube_monitor.core._set_monitor_state")
    @patch("youtube_monitor.core.set_websub_health")
    def test_recovery_worker_healthy_passes(self, mock_health, mock_state, mock_cb, mock_alive, mock_verify):
        core._monitor_started = True
        core._monitor_state = "RUNNING"
        core._recovery_attempt = 0
        core.public_callback_url = "https://x.ngrok-free.app/youtube_callback?owner=t"
        core.stop_event = threading.Event()

        def fake_wait(seconds):
            core.stop_event.set()
            time.sleep(0.01)

        with patch("youtube_monitor.core.stop_event.wait", side_effect=fake_wait):
            core._recovery_worker(run_gen=None)
        self.assertEqual(core._monitor_state, "RUNNING")
        mock_health.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()