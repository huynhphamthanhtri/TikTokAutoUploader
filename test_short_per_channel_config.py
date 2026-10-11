"""Test suite for per-channel Short duration threshold and centralized under-threshold folder."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from youtube_monitor import core


class TestShortPerChannelConfig(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.channels_path = Path(self.tmp_dir) / "channels.json"
        self.config_path = Path(self.tmp_dir) / "youtube_config.json"
        self.orig_channels_store = core.channels_store
        self.orig_config_json = core.CONFIG_JSON
        core.CONFIG_JSON = self.config_path
        core.channels_store = core.ChannelsStore(self.channels_path)

    def tearDown(self):
        core.channels_store = self.orig_channels_store
        core.CONFIG_JSON = self.orig_config_json
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_short_min_duration_is_30s(self):
        """Channels added without specifying short_min_duration default to 30.0s."""
        core.channels_store.add_channel("UC_A", "/folder/A", profile_name="ProfA")
        meta = core.channels_store.get_meta("UC_A")
        self.assertEqual(meta.get("short_min_duration"), 30.0)

    def test_per_channel_custom_min_duration_persists(self):
        """Setting custom short_min_duration for a channel survives save and reload."""
        core.channels_store.add_channel("UC_B", "/folder/B", profile_name="ProfB", short_min_duration=40.0)
        self.assertEqual(core.channels_store.get_meta("UC_B")["short_min_duration"], 40.0)

        # Update via method
        updated = core.channels_store.set_short_min_duration("UC_B", 35.0)
        self.assertEqual(updated, 35.0)
        core.channels_store.save_now()

        # Reload store from disk
        store2 = core.ChannelsStore(self.channels_path)
        store2.load()
        meta = store2.get_meta("UC_B")
        self.assertEqual(meta["short_min_duration"], 35.0)

    def test_set_channel_short_min_duration_helper(self):
        """Helper function updates store and returns confirmation message."""
        core.channels_store.add_channel("UC_C", "/folder/C")
        ok, msg = core.set_channel_short_min_duration("UC_C", 25)
        self.assertTrue(ok)
        self.assertIn("25s", msg)
        self.assertEqual(core.channels_store.get_meta("UC_C")["short_min_duration"], 25.0)

        # Nonexistent channel
        ok_non, err_msg = core.set_channel_short_min_duration("UC_NONEXISTENT", 25)
        self.assertFalse(ok_non)

    def test_centralized_short_under_dir_config(self):
        """Global short_under_threshold_dir can be set and retrieved."""
        custom_dir = str(Path(self.tmp_dir) / "custom_shorts_dir")
        ok, res_path = core.set_short_under_threshold_dir(custom_dir)
        self.assertTrue(ok)
        self.assertTrue(Path(custom_dir).exists())

        retrieved = core.get_short_under_threshold_dir()
        self.assertEqual(str(retrieved.resolve()), str(Path(custom_dir).resolve()))

    def _fake_finalize(self, p, o, t, v):
        out = Path(o) / f"{v}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"finalized data")
        return str(out)

    @patch("youtube_monitor.core._claim_download", return_value=True)
    @patch("youtube_monitor.core._release_download")
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration")
    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp")
    @patch("youtube_monitor.core._safe_emit_video_ready")
    @patch("youtube_monitor.core._run_ytdlp_download")
    def test_under_threshold_routes_to_central_dir_and_skips_upload(
        self, mock_ytdlp, mock_emit, mock_slow, mock_probe, mock_rel, mock_claim
    ):
        """A video with duration < channel threshold goes to central directory and is NOT uploaded."""
        central_dir = Path(self.tmp_dir) / "central_shorts"
        core.set_short_under_threshold_dir(str(central_dir))

        channel_folder = Path(self.tmp_dir) / "channel_out"
        channel_folder.mkdir(parents=True, exist_ok=True)
        core.channels_store.add_channel(
            "UC_TEST", str(channel_folder), profile_name="TestProfile",
            process_short=True, short_min_duration=30.0
        )

        fake_video_file = Path(self.tmp_dir) / "fake_short.mp4"
        fake_video_file.write_bytes(b"fake video data")
        mock_ytdlp.return_value = ({"title": "Short Under", "duration": 22.0}, str(fake_video_file), "")
        mock_probe.return_value = 22.0

        with patch("youtube_monitor.core._finalize_video", side_effect=self._fake_finalize):
            outcome = core._download_one_result("UC_TEST", "vid_under_22")
        self.assertTrue(outcome.ok, msg=f"Download failed: {outcome.detail} ({outcome.failure_class})")

        # Final video must be placed in central directory, NOT channel folder
        self.assertEqual(Path(outcome.final_path).resolve().parent, central_dir.resolve())
        self.assertTrue(Path(outcome.final_path).exists())

        # Crucial check: VideoReadyIntent must NOT have been emitted to TikTok
        mock_emit.assert_not_called()
        # Slowdown helper should NOT have been called
        mock_slow.assert_not_called()

    @patch("youtube_monitor.core._claim_download", return_value=True)
    @patch("youtube_monitor.core._release_download")
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration")
    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp")
    @patch("youtube_monitor.core._safe_emit_video_ready", return_value=(True, ""))
    @patch("youtube_monitor.core._run_ytdlp_download")
    def test_in_threshold_slows_down_and_uploads(
        self, mock_ytdlp, mock_emit, mock_slow, mock_probe, mock_rel, mock_claim
    ):
        """A video within [min_duration, 60s] is slowed down, stored in channel folder, and uploaded."""
        channel_folder = Path(self.tmp_dir) / "channel_out"
        channel_folder.mkdir(parents=True, exist_ok=True)
        core.channels_store.add_channel(
            "UC_SLOW", str(channel_folder), profile_name="TestProfile",
            process_short=True, short_min_duration=30.0
        )

        fake_video_file = Path(self.tmp_dir) / "fake_35s.mp4"
        fake_video_file.write_bytes(b"fake 35s video data")
        fake_slowed_file = Path(self.tmp_dir) / "fake_slowed_61s.mp4"
        fake_slowed_file.write_bytes(b"fake slowed video data")

        mock_ytdlp.return_value = ({"title": "Video 35s", "duration": 35.0}, str(fake_video_file), "")
        mock_probe.return_value = 35.0
        mock_slow.return_value = (str(fake_slowed_file), [str(fake_slowed_file)])

        with patch("youtube_monitor.core._finalize_video", side_effect=self._fake_finalize):
            outcome = core._download_one_result("UC_SLOW", "vid_slow_35")
        self.assertTrue(outcome.ok, msg=f"Download failed: {outcome.detail} ({outcome.failure_class})")

        # Final video must be placed in channel folder
        self.assertEqual(Path(outcome.final_path).resolve().parent, channel_folder.resolve())

        # Slowdown helper MUST be called
        mock_slow.assert_called_once_with(str(fake_video_file), core.SHORT_TARGET_DURATION)

        # Upload MUST be called with VideoReadyIntent
        mock_emit.assert_called_once()
        intent = mock_emit.call_args[0][0]
        self.assertEqual(intent.profile_name, "TestProfile")
        self.assertEqual(intent.youtube_video_id, "vid_slow_35")

    @patch("youtube_monitor.core._claim_download", return_value=True)
    @patch("youtube_monitor.core._release_download")
    @patch("youtube_monitor.core.ffmpeg_helper.probe_duration")
    @patch("youtube_monitor.core.slowdown_to_min_duration_in_temp")
    @patch("youtube_monitor.core._safe_emit_video_ready", return_value=(True, ""))
    @patch("youtube_monitor.core._run_ytdlp_download")
    def test_channel_custom_40s_threshold_behavior(
        self, mock_ytdlp, mock_emit, mock_slow, mock_probe, mock_rel, mock_claim
    ):
        """When channel has threshold 40s: 35s is under threshold (central dir, no upload)."""
        central_dir = Path(self.tmp_dir) / "central_shorts"
        core.set_short_under_threshold_dir(str(central_dir))

        channel_folder = Path(self.tmp_dir) / "channel_40"
        channel_folder.mkdir(parents=True, exist_ok=True)
        core.channels_store.add_channel(
            "UC_40", str(channel_folder), profile_name="Profile40",
            process_short=True, short_min_duration=40.0
        )

        fake_video_file = Path(self.tmp_dir) / "fake_35s.mp4"
        fake_video_file.write_bytes(b"data")
        mock_ytdlp.return_value = ({"title": "Video 35s", "duration": 35.0}, str(fake_video_file), "")
        mock_probe.return_value = 35.0

        with patch("youtube_monitor.core._finalize_video", side_effect=self._fake_finalize):
            outcome = core._download_one_result("UC_40", "vid_35")
        self.assertTrue(outcome.ok, msg=f"Download failed: {outcome.detail} ({outcome.failure_class})")

        # Since threshold is 40s, 35s is under threshold -> central dir, NO upload
        self.assertEqual(Path(outcome.final_path).resolve().parent, central_dir.resolve())
        mock_emit.assert_not_called()
        mock_slow.assert_not_called()


if __name__ == "__main__":
    unittest.main()
