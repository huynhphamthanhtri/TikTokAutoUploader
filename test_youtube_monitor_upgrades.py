"""test_youtube_monitor_upgrades.py - Regression tests for the YouTube Monitor upgrade
phases: WebSub lease-aware resubscribe, channel metadata persistence/enrichment, temp
cleanup, and the frozen-safe yt-dlp updater."""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from youtube_monitor import core
from youtube_monitor import ytdlp_updater


class TestWebSubNeedsResubscribe(unittest.TestCase):

    def _set_status(self, status):
        core._subscription_status.clear()
        if status is not None:
            core._subscription_status["UCX"] = status

    def tearDown(self):
        core._subscription_status.clear()

    def test_never_verified_needs_resubscribe(self):
        self._set_status({})
        self.assertTrue(core._needs_resubscribe("UCX"))

    def test_missing_entry_needs_resubscribe(self):
        self._set_status(None)
        self.assertTrue(core._needs_resubscribe("UCX"))

    def test_valid_lease_no_resubscribe(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        self._set_status({"verified_at": "now", "lease_expires_at": future})
        self.assertFalse(core._needs_resubscribe("UCX"))

    def test_expired_lease_needs_resubscribe(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self._set_status({"verified_at": "now", "lease_expires_at": past})
        self.assertTrue(core._needs_resubscribe("UCX"))

    def test_missing_lease_expiry_needs_resubscribe(self):
        self._set_status({"verified_at": "now"})
        self.assertTrue(core._needs_resubscribe("UCX"))

    def test_hub_error_needs_resubscribe(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        self._set_status({"verified_at": "now", "lease_expires_at": future, "last_error": "500"})
        self.assertTrue(core._needs_resubscribe("UCX"))

    def test_lease_near_expiry_needs_resubscribe(self):
        soon = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        self._set_status({"verified_at": "now", "lease_expires_at": soon})
        self.assertTrue(core._needs_resubscribe("UCX"))


class TestChannelMetadata(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = core.ChannelsStore(Path(self.tmp.name) / "channels.json")

    def tearDown(self):
        core.channels_store = core.ChannelsStore(core.CHANNELS_JSON)
        self.tmp.cleanup()

    def test_add_channel_stores_metadata(self):
        self.store.add_channel("UC1", "/tmp/folder", profile_name="P1",
                               title="Kênh A", thumbnail="http://t", channel_url="http://c")
        meta = self.store.get_meta("UC1")
        self.assertEqual(meta["title"], "Kênh A")
        self.assertEqual(meta["thumbnail"], "http://t")
        self.assertEqual(meta["channel_url"], "http://c")
        self.assertTrue(meta["added_at"])

    def test_metadata_survives_save_load(self):
        self.store.add_channel("UC1", "/tmp/folder", profile_name="P1", title="Kênh B")
        self.store.update_meta("UC1", thumbnail="http://t", meta_attempted=True)
        self.store.save_now()
        store2 = core.ChannelsStore(Path(self.tmp.name) / "channels.json")
        store2.load()
        meta = store2.get_meta("UC1")
        self.assertEqual(meta["title"], "Kênh B")
        self.assertEqual(meta["thumbnail"], "http://t")
        self.assertTrue(meta["meta_attempted"])

    def test_update_meta_preserves_other_fields(self):
        self.store.add_channel("UC1", "/tmp/folder", profile_name="P1")
        self.store.update_meta("UC1", title="T", meta_attempted=True)
        meta = self.store.get_meta("UC1")
        self.assertEqual(meta["title"], "T")
        self.assertEqual(meta["profile_name"], "P1")
        self.assertTrue(meta["meta_attempted"])

    def test_ensure_channel_metadata_fetches_once(self):
        original_store = core.channels_store
        try:
            core.channels_store = self.store
            self.store.add_channel("UC1", "/tmp/folder")
            called = {"count": 0}

            class FakeResponse:
                def list(self, part, id=None):
                    called["count"] += 1
                    return SimpleNamespace(execute=lambda: {"items": [{"snippet": {"title": "Kênh Đẹp",
                                                                                    "thumbnails": {"default": {"url": "http://t"}}}}]})

            class FakeYouTube:
                def channels(self):
                    return FakeResponse()

            core._ensure_channel_metadata("UC1", FakeYouTube())
            self.assertEqual(called["count"], 1)
            meta = core.channels_store.get_meta("UC1")
            self.assertEqual(meta["title"], "Kênh Đẹp")
            self.assertEqual(meta["thumbnail"], "http://t")
            self.assertTrue(meta["meta_attempted"])
            # second call: title present -> no API call
            core._ensure_channel_metadata("UC1", FakeYouTube())
            self.assertEqual(called["count"], 1)
        finally:
            core.channels_store = original_store

    def test_ensure_channel_metadata_marks_attempt_on_failure(self):
        original_store = core.channels_store
        try:
            core.channels_store = self.store
            self.store.add_channel("UC1", "/tmp/folder")

            class FakeResponse:
                def get(self, part, id=None):
                    raise RuntimeError("api down")

            class FakeYouTube:
                def channels(self):
                    return FakeResponse()

            meta = core._ensure_channel_metadata("UC1", FakeYouTube())
            self.assertTrue(core.channels_store.get_meta("UC1").get("meta_attempted"))
            # failure path does not fabricate a title
            self.assertFalse(meta.get("title"))
        finally:
            core.channels_store = original_store


class TestTempCleanup(unittest.TestCase):

    def test_cleans_only_stale_temp_files(self):
        tmp = tempfile.TemporaryDirectory()
        old = Path(tmp.name) / "abc.slow.mp4"
        fresh = Path(tmp.name) / "def.slow.mp4"
        old.write_bytes(b"old")
        fresh.write_bytes(b"fresh")
        old_time = time.time() - 2 * 86400
        os.utime(old, (old_time, old_time))
        original = core.TEMP_DIR
        try:
            core.TEMP_DIR = Path(tmp.name)
            removed = core._cleanup_temp_dl(older_than_seconds=86400)
        finally:
            core.TEMP_DIR = original
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        tmp.cleanup()


class TestYtdlpUpdater(unittest.TestCase):

    def test_version_is_reported(self):
        version = ytdlp_updater.get_ytdlp_version()
        self.assertIsInstance(version, str)
        self.assertTrue(version)

    def test_update_refused_in_frozen(self):
        with mock.patch.object(sys, "frozen", True, create=True):
            ok, msg = ytdlp_updater.update_ytdlp()
        self.assertFalse(ok)
        self.assertIn("frozen", msg.lower())

    @mock.patch("requests.get")
    def test_check_latest_detects_newer(self, mock_get):
        mock_get.return_value = mock.Mock(status_code=200, json=lambda: {"info": {"version": "9999.99.99"}})
        current, latest, has_update, error = ytdlp_updater.check_latest_ytdlp()
        self.assertEqual(error, "")
        self.assertTrue(has_update)
        self.assertEqual(latest, "9999.99.99")
        self.assertEqual(current, ytdlp_updater.get_ytdlp_version())

    @mock.patch("requests.get")
    def test_check_latest_handles_error(self, mock_get):
        mock_get.side_effect = RuntimeError("no network")
        current, latest, has_update, error = ytdlp_updater.check_latest_ytdlp()
        self.assertTrue(error)
        self.assertFalse(has_update)


if __name__ == "__main__":
    unittest.main()