# -*- coding: utf-8 -*-
"""
Tests for API Pool resilience, socket reset recovery (10054), key rotation,
uploads playlist derivation, and error handling.
"""
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from youtube_monitor.core import (
    derive_uploads_playlist_id,
    get_channel_id_from_link,
    get_channel_from_video_id,
    _get_uploads_playlist_id,
    add_channel_for_profile,
    execute_api_request,
    api_key_manager,
    channels_store,
)
from youtube_monitor.key_manager import ApiKeyManager, ApiKeyInfo


class TestApiPoolResilience(unittest.TestCase):
    def setUp(self):
        # 1. Start patching disk persistence before any test logic to guarantee zero live file mutation
        save_patcher = patch("youtube_monitor.core._save_config")
        self.mock_save_config = save_patcher.start()
        self.addCleanup(save_patcher.stop)

        # 2. Patch get_config to return a fresh isolated dictionary copy on each call
        cfg_patcher = patch("youtube_monitor.core.get_config", side_effect=lambda: {
            "api_keys": ["KEY_TEST_ISOLATED"],
            "api_keys_pool": []
        })
        self.mock_get_config = cfg_patcher.start()
        self.addCleanup(cfg_patcher.stop)

        # 3. Patch channels_store.save_now to avoid disk mutation on channels.json
        chan_save_patcher = patch.object(channels_store, "save_now")
        self.mock_chan_save = chan_save_patcher.start()
        self.addCleanup(chan_save_patcher.stop)

        # 4. Patch api_key_manager with a fresh isolated test instance (leaving global singleton untouched)
        self.test_key_manager = ApiKeyManager(["KEY_TEST_ISOLATED"])
        mgr_patcher = patch("youtube_monitor.core.api_key_manager", self.test_key_manager)
        self.mock_mgr = mgr_patcher.start()
        self.addCleanup(mgr_patcher.stop)

    def test_derive_uploads_playlist_id(self):
        # Standard 24-character YouTube Channel ID
        cid = "UCMYZuVn_4jB67gvoXwk38Xg"
        self.assertEqual(len(cid), 24)
        derived = derive_uploads_playlist_id(cid)
        self.assertEqual(derived, "UUMYZuVn_4jB67gvoXwk38Xg")

        # Invalid / non-standard IDs return None
        self.assertIsNone(derive_uploads_playlist_id("UCshort"))
        self.assertIsNone(derive_uploads_playlist_id("invalid_channel"))
        self.assertIsNone(derive_uploads_playlist_id(""))

    def test_get_channel_id_from_link_fast_path(self):
        # Fast path for standard channel URL derives playlist without making API calls
        link = "https://www.youtube.com/channel/UCMYZuVn_4jB67gvoXwk38Xg"
        res = get_channel_id_from_link(link)
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "UCMYZuVn_4jB67gvoXwk38Xg")
        self.assertEqual(res["playlistId"], "UUMYZuVn_4jB67gvoXwk38Xg")

    def test_get_uploads_playlist_id_derivation(self):
        cid = "UCWp4H3So4FcKoOZaRqiqZDg"
        playlist_id = _get_uploads_playlist_id(cid)
        self.assertEqual(playlist_id, "UUWp4H3So4FcKoOZaRqiqZDg")

    def test_execute_api_request_recovers_from_10054_socket_reset(self):
        km = ApiKeyManager()
        km._keys_map["KEY_A"] = ApiKeyInfo(key="KEY_A", status="ACTIVE")

        call_count = {"count": 0}

        def mock_builder(yt_client):
            call_count["count"] += 1
            if call_count["count"] == 1:
                raise ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host")
            return {"items": [{"id": "item1"}]}

        with patch("youtube_monitor.core.api_key_manager", km):
            result = execute_api_request(mock_builder, action_desc="test_10054")
            self.assertEqual(result, {"items": [{"id": "item1"}]})
            self.assertEqual(call_count["count"], 2)

    def test_execute_api_request_rotates_on_quota_exceeded(self):
        km = ApiKeyManager()
        km._keys_map["KEY_1"] = ApiKeyInfo(key="KEY_1", status="ACTIVE")
        km._keys_map["KEY_2"] = ApiKeyInfo(key="KEY_2", status="ACTIVE")

        from googleapiclient.errors import HttpError
        import httplib2

        resp_403 = httplib2.Response({"status": 403, "reason": "Forbidden"})
        quota_err = HttpError(resp_403, b'{"error": {"errors": [{"reason": "quotaExceeded"}]}}')

        call_counts = {"KEY_1": 0, "KEY_2": 0}

        def mock_builder(yt_client):
            # Check which key is currently leased/attempted by looking at last_used_at or consecutive_errors
            if km._keys_map["KEY_1"].status == "ACTIVE":
                call_counts["KEY_1"] += 1
                raise quota_err
            call_counts["KEY_2"] += 1
            return {"items": [{"title": "Success on Key 2"}]}

        with patch("youtube_monitor.core.api_key_manager", km):
            result = execute_api_request(mock_builder, action_desc="test_quota_rotation")
            self.assertEqual(result, {"items": [{"title": "Success on Key 2"}]})
            self.assertEqual(km._keys_map["KEY_1"].status, "QUOTA_EXCEEDED")
            self.assertEqual(km._keys_map["KEY_2"].status, "ACTIVE")
            self.assertEqual(call_counts["KEY_1"], 1)
            self.assertEqual(call_counts["KEY_2"], 1)

    def test_add_channel_for_profile_handles_socket_reset_gracefully(self):
        km = ApiKeyManager()
        km._keys_map["KEY_1"] = ApiKeyInfo(key="KEY_1", status="ACTIVE")

        test_cid = "UC" + "A" * 22  # 24 chars standard YouTube channel ID
        mock_service = MagicMock()
        mock_service.channels().list().execute.return_value = {
            "items": [{
                "snippet": {"title": "Test Channel Unique", "thumbnails": {}},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU" + "A" * 22}}
            }]
        }
        mock_service.playlistItems().list().execute.return_value = {"items": []}

        try:
            with patch("youtube_monitor.core.api_key_manager", km), \
                 patch.object(km, "get_youtube_client", return_value=mock_service), \
                 patch("youtube_monitor.core.analyze_channel_schedule", return_value=(True, {})):

                ok, cid = add_channel_for_profile(f"https://www.youtube.com/channel/{test_cid}", "ProfileTest", "/tmp/ptest")
                self.assertTrue(ok)
                self.assertEqual(cid, test_cid)

                meta = channels_store.get_meta(cid)
                self.assertEqual(meta["title"], "Test Channel Unique")
                self.assertEqual(meta["uploads_playlist_id"], "UU" + "A" * 22)
        finally:
            channels_store.remove_channel(test_cid)

    def test_add_channel_for_profile_returns_graceful_error_on_total_failure(self):
        km = ApiKeyManager()
        km._keys_map["KEY_1"] = ApiKeyInfo(key="KEY_1", status="ACTIVE")

        with patch("youtube_monitor.core.api_key_manager", km), \
             patch("youtube_monitor.core.get_channel_id_from_link", side_effect=ConnectionResetError(10054, "Socket reset")):
            ok, msg = add_channel_for_profile("https://www.youtube.com/@some_handle", "Profile1", "/tmp/p1")
            self.assertFalse(ok)
            self.assertIn("Không lấy được Channel ID", msg)
            self.assertIn("Socket reset", msg)

    def test_execute_api_request_does_not_mutate_live_config_on_disk(self):
        """Regression test for Rule 14: execute_api_request must NEVER overwrite live config."""
        from youtube_monitor.core import CONFIG_JSON

        orig_bytes = CONFIG_JSON.read_bytes() if CONFIG_JSON.exists() else None
        self.mock_save_config.reset_mock()

        km = ApiKeyManager()
        km._keys_map["MOCK_KEY_SHOULD_NEVER_REACH_DISK"] = ApiKeyInfo(key="MOCK_KEY_SHOULD_NEVER_REACH_DISK", status="ACTIVE")

        def mock_builder(yt_client):
            return {"items": [{"id": "safety_check"}]}

        with patch("youtube_monitor.core.api_key_manager", km):
            result = execute_api_request(mock_builder, action_desc="safety_check")
            self.assertEqual(result, {"items": [{"id": "safety_check"}]})

        # Verify disk saver mock intercepted the write
        self.assertTrue(self.mock_save_config.called)
        saved_payload = self.mock_save_config.call_args[0][0]
        self.assertIn("MOCK_KEY_SHOULD_NEVER_REACH_DISK", saved_payload.get("api_keys", []))

        # Verify disk file is 100% byte-for-byte identical
        if orig_bytes is not None:
            self.assertEqual(CONFIG_JSON.read_bytes(), orig_bytes)
