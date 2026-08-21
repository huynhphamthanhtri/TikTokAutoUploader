"""Tests for the owned ngrok agent lifecycle (no global ngrok.kill)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil

from youtube_monitor import ngrok_owner


class NgrokOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_ngrok_owner_"))
        self.ownership_file = self.tmp_dir / "ownership.json"
        self.config_file = self.tmp_dir / "agent.yml"
        self.bin_path = self.tmp_dir / "ngrok.exe"
        self.bin_path.write_text("fake_ngrok_binary")

        ngrok_owner.CONFIG_DIR = self.tmp_dir
        ngrok_owner.AGENT_CONFIG_YML = self.config_file
        ngrok_owner.OWNERSHIP_JSON = self.ownership_file

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _fake_proc(self, pid, create_time=None, exe=None):
        proc = MagicMock()
        proc.pid = pid
        proc.is_running.return_value = True
        proc.create_time.return_value = create_time or 1234567890.0
        proc.exe.return_value = str(exe or self.bin_path)
        proc.terminate.return_value = None
        proc.wait.return_value = None
        return proc

    def test_write_and_read_ownership_roundtrip(self):
        record = {"owner_uuid": "u1", "agent_pid": 123, "public_url": "https://a.ngrok-free.app"}
        ngrok_owner.write_ownership(record)
        self.assertEqual(ngrok_owner.read_ownership(), record)

    def test_clear_ownership_removes_file(self):
        ngrok_owner.write_ownership({"agent_pid": 1})
        ngrok_owner.clear_ownership()
        self.assertEqual(ngrok_owner.read_ownership(), {})

    def test_record_matches_process_true_when_exact(self):
        proc = self._fake_proc(99, create_time=111.0)
        record = {
            "agent_pid": 99,
            "agent_create_time": 111.0,
            "agent_exe": str(self.bin_path),
        }
        with patch("youtube_monitor.ngrok_owner.psutil.Process", return_value=proc):
            self.assertTrue(ngrok_owner._record_matches_process(record))

    def test_record_does_not_match_other_pid(self):
        proc = self._fake_proc(100, create_time=222.0)
        record = {
            "agent_pid": 99,
            "agent_create_time": 111.0,
            "agent_exe": str(self.bin_path),
        }
        with patch("youtube_monitor.ngrok_owner.psutil.Process", return_value=proc):
            self.assertFalse(ngrok_owner._record_matches_process(record))

    def test_record_does_not_match_other_exe(self):
        proc = self._fake_proc(99, create_time=111.0, exe=self.tmp_dir / "other.exe")
        record = {
            "agent_pid": 99,
            "agent_create_time": 111.0,
            "agent_exe": str(self.bin_path),
        }
        with patch("youtube_monitor.ngrok_owner.psutil.Process", return_value=proc):
            self.assertFalse(ngrok_owner._record_matches_process(record))

    def test_record_does_not_match_dead_process(self):
        proc = self._fake_proc(99, create_time=111.0)
        proc.is_running.return_value = False
        record = {"agent_pid": 99, "agent_create_time": 111.0, "agent_exe": str(self.bin_path)}
        with patch("youtube_monitor.ngrok_owner.psutil.Process", return_value=proc):
            self.assertFalse(ngrok_owner._record_matches_process(record))

    def test_stop_does_not_kill_when_no_ownership(self):
        ok, msg = ngrok_owner.stop_owned_agent()
        self.assertTrue(ok)
        self.assertIn("Không có ngrok agent", msg)

    def test_stop_does_not_kill_unknown_record(self):
        ngrok_owner.write_ownership({"agent_pid": 999, "agent_create_time": 1.0, "agent_exe": str(self.bin_path)})
        with patch("youtube_monitor.ngrok_owner.psutil.Process", side_effect=psutil.NoSuchProcess(999)):
            ok, msg = ngrok_owner.stop_owned_agent()
        self.assertTrue(ok)
        self.assertIn("không còn thuộc sở hữu", msg)
        self.assertEqual(ngrok_owner.read_ownership(), {})

    def test_stop_kills_exact_owned_pid(self):
        proc = self._fake_proc(77, create_time=555.0)
        record = {
            "agent_pid": 77,
            "agent_create_time": 555.0,
            "agent_exe": str(self.bin_path),
            "public_url": "https://x.ngrok-free.app",
        }
        ngrok_owner.write_ownership(record)
        with patch("youtube_monitor.ngrok_owner.psutil.Process", return_value=proc), \
             patch("youtube_monitor.ngrok_owner.ngrok.disconnect") as mock_disc:
            ok, msg = ngrok_owner.stop_owned_agent()
        self.assertTrue(ok)
        proc.terminate.assert_called_once()
        self.assertEqual(ngrok_owner.read_ownership(), {})

    def test_start_records_owned_tunnel(self):
        tunnel = MagicMock()
        tunnel.public_url = "https://owned.ngrok-free.app"
        tunnel.name = "tun-1"
        proc = self._fake_proc(42, create_time=999.0)

        with patch("youtube_monitor.ngrok_owner.ngrok_helper.get_ngrok_bin_path", return_value=str(self.bin_path)), \
             patch("youtube_monitor.ngrok_owner._resolve_auth_token", return_value=("tok", "environment")), \
             patch("youtube_monitor.ngrok_owner.ngrok.set_auth_token"), \
             patch("youtube_monitor.ngrok_owner.ngrok.connect", return_value=tunnel) as mock_connect, \
             patch("youtube_monitor.ngrok_owner.ngrok.get_ngrok_process") as mock_get_proc:
            mock_get_proc.return_value = MagicMock(proc=proc)
            ok, payload = ngrok_owner.start_owned_agent(5000, "inst1", 7)

        self.assertTrue(ok)
        self.assertEqual(payload["public_url"], "https://owned.ngrok-free.app")
        self.assertEqual(payload["agent_pid"], 42)
        self.assertEqual(payload["monitor_generation"], 7)
        record = ngrok_owner.read_ownership()
        self.assertEqual(record["owner_uuid"], payload["owner_uuid"])
        self.assertEqual(record["public_url"], "https://owned.ngrok-free.app")
        self.assertEqual(record["target_port"], 5000)
        self.assertNotIn("authtoken", str(record).lower())

    def test_start_failure_when_no_binary(self):
        with patch("youtube_monitor.ngrok_owner.ngrok_helper.get_ngrok_bin_path", return_value=None), \
             patch("youtube_monitor.ngrok_owner.ngrok_helper.ensure_ngrok", return_value=(False, "no download")):
            ok, msg = ngrok_owner.start_owned_agent(5000, "inst1", 7)
        self.assertFalse(ok)
        self.assertIn("ngrok binary", msg)

    def test_resolve_token_from_environment(self):
        with patch.dict(os.environ, {"NGROK_AUTHTOKEN": "env_token_123"}, clear=True), \
             patch("youtube_monitor.ngrok_owner._ngrok_config_candidates", return_value=[]):
            token, source = ngrok_owner._resolve_auth_token()
        self.assertEqual(token, "env_token_123")
        self.assertEqual(source, "environment")

    def test_resolve_token_from_user_config(self):
        cfg_file = self.tmp_dir / "ngrok.yml"
        cfg_file.write_text("version: '2'\nauthtoken: cfg_token_456\nregion: us\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), \
             patch("youtube_monitor.ngrok_owner._ngrok_config_candidates", return_value=[cfg_file]):
            token, source = ngrok_owner._resolve_auth_token()
        self.assertEqual(token, "cfg_token_456")
        self.assertEqual(source, "user_config")

    def test_resolve_token_env_overrides_config(self):
        cfg_file = self.tmp_dir / "ngrok.yml"
        cfg_file.write_text("authtoken: cfg_token\n", encoding="utf-8")
        with patch.dict(os.environ, {"NGROK_AUTHTOKEN": "env_token"}, clear=True), \
             patch("youtube_monitor.ngrok_owner._ngrok_config_candidates", return_value=[cfg_file]):
            token, _ = ngrok_owner._resolve_auth_token()
        self.assertEqual(token, "env_token")

    def test_resolve_token_skips_malformed_config(self):
        bad_file = self.tmp_dir / "bad.yml"
        bad_file.write_text("{{{not valid yaml\n[", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), \
             patch("youtube_monitor.ngrok_owner._ngrok_config_candidates", return_value=[bad_file]):
            token, source = ngrok_owner._resolve_auth_token()
        self.assertEqual(token, "")
        self.assertEqual(source, "none")

    def test_resolve_token_none_when_missing(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch("youtube_monitor.ngrok_owner._ngrok_config_candidates", return_value=[]):
            token, source = ngrok_owner._resolve_auth_token()
        self.assertEqual(token, "")
        self.assertEqual(source, "none")

    def test_validate_auth_ready_ok(self):
        with patch("youtube_monitor.ngrok_owner._resolve_auth_token", return_value=("tok", "environment")):
            ok, msg = ngrok_owner.validate_auth_ready()
        self.assertTrue(ok)
        self.assertIn("sẵn sàng", msg)

    def test_validate_auth_ready_missing(self):
        with patch("youtube_monitor.ngrok_owner._resolve_auth_token", return_value=("", "none")):
            ok, msg = ngrok_owner.validate_auth_ready()
        self.assertFalse(ok)
        self.assertIn("add-authtoken", msg)

    @patch("youtube_monitor.ngrok_owner.ngrok_helper.get_ngrok_bin_path")
    def test_start_missing_token_does_not_connect(self, mock_bin):
        mock_bin.return_value = str(self.bin_path)
        with patch.dict(os.environ, {}, clear=True), \
             patch("youtube_monitor.ngrok_owner._ngrok_config_candidates", return_value=[]), \
             patch("youtube_monitor.ngrok_owner.ngrok.connect") as mock_connect, \
             patch("youtube_monitor.ngrok_owner.ngrok.set_auth_token") as mock_set:
            ok, msg = ngrok_owner.start_owned_agent(5000, "inst1", 7)
        self.assertFalse(ok)
        self.assertIn("authtoken", msg)
        mock_connect.assert_not_called()
        mock_set.assert_not_called()

    @patch("youtube_monitor.ngrok_owner.ngrok_helper.get_ngrok_bin_path")
    def test_start_4018_classified(self, mock_bin):
        mock_bin.return_value = str(self.bin_path)
        with patch("youtube_monitor.ngrok_owner._resolve_auth_token", return_value=("tok", "environment")), \
             patch("youtube_monitor.ngrok_owner.ngrok.set_auth_token"), \
             patch("youtube_monitor.ngrok_owner.ngrok.connect", side_effect=Exception("ERR_NGROK_4018 auth failed")):
            ok, msg = ngrok_owner.start_owned_agent(5000, "inst1", 7)
        self.assertFalse(ok)
        self.assertIn("4018", msg)

    @patch("youtube_monitor.ngrok_owner.ngrok_helper.get_ngrok_bin_path")
    def test_token_not_written_to_ownership(self, mock_bin):
        mock_bin.return_value = str(self.bin_path)
        tunnel = MagicMock()
        tunnel.public_url = "https://t.ngrok-free.app"
        tunnel.name = "t1"
        proc = self._fake_proc(5, create_time=1.0)
        with patch("youtube_monitor.ngrok_owner._resolve_auth_token", return_value=("secret_token_XYZ", "environment")), \
             patch("youtube_monitor.ngrok_owner.ngrok.set_auth_token") as mock_set, \
             patch("youtube_monitor.ngrok_owner.ngrok.connect", return_value=tunnel), \
             patch("youtube_monitor.ngrok_owner.ngrok.get_ngrok_process") as mock_gp:
            mock_gp.return_value = MagicMock(proc=proc)
            ok, payload = ngrok_owner.start_owned_agent(5000, "inst1", 7)
        self.assertTrue(ok)
        mock_set.assert_called_once()
        record_str = str(ngrok_owner.read_ownership()) + str(payload)
        self.assertNotIn("secret_token_XYZ", record_str)
        self.assertNotIn("authtoken", str(ngrok_owner.read_ownership()).lower())


if __name__ == "__main__":
    unittest.main()