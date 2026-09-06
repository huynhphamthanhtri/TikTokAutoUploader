import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock
import unittest

from youtube_monitor.key_manager import ApiKeyManager
from youtube_monitor.predictive_scheduler import PredictiveScheduler
from youtube_monitor.schedule_learner import PollingWindow


class MockChannelsStore:
    def __init__(self, channels=None):
        self._channels = channels or {}

    def all_items(self):
        return self._channels

    def get_meta(self, cid):
        return self._channels.get(cid)

    def update_meta(self, cid, **kwargs):
        if cid in self._channels:
            self._channels[cid].update(kwargs)


class TestPredictiveScheduler(unittest.TestCase):
    def test_scheduler_active_channels_now(self):
        # Set up a channel with a window covering right now
        now_dt = datetime.fromisoformat("2026-09-07T18:00:00+07:00")
        channel_meta = {
            "active": True,
            "timezone": "Asia/Ho_Chi_Minh",
            "manual_windows": [
                {
                    "id": "win1",
                    "start_time": "17:50",
                    "end_time": "18:10",
                    "days": [0, 1, 2, 3, 4, 5, 6],
                    "enabled": True,
                }
            ],
        }

        store = MockChannelsStore({"UC_TEST_1": channel_meta})
        key_mgr = ApiKeyManager(["KEY_TEST"])
        on_detected = MagicMock(return_value=True)

        scheduler = PredictiveScheduler(store, key_mgr, on_detected)

        # In window -> UC_TEST_1 is returned
        active = scheduler.get_active_channels_now(now_dt)
        self.assertIn("UC_TEST_1", active)

        # Out of window (18:30)
        out_dt = datetime.fromisoformat("2026-09-07T18:30:00+07:00")
        active_out = scheduler.get_active_channels_now(out_dt)
        self.assertNotIn("UC_TEST_1", active_out)

    def test_scheduler_fast_track_trigger(self):
        channel_meta = {"active": True, "manual_windows": []}
        store = MockChannelsStore({"UC_FAST": channel_meta})
        key_mgr = ApiKeyManager(["KEY_TEST"])
        on_detected = MagicMock(return_value=True)

        scheduler = PredictiveScheduler(store, key_mgr, on_detected)

        # Not in window
        out_dt = datetime.fromisoformat("2026-09-07T12:00:00+07:00")
        self.assertNotIn("UC_FAST", scheduler.get_active_channels_now(out_dt))

        # Trigger immediate poll via WebSub callback
        scheduler.trigger_immediate_poll("UC_FAST")
        active = scheduler.get_active_channels_now(out_dt)
        self.assertIn("UC_FAST", active)

    def test_scheduler_poll_channel_via_api(self):
        channel_meta = {"active": True, "uploads_playlist_id": "UU_TEST"}
        store = MockChannelsStore({"UC_TEST": channel_meta})
        key_mgr = ApiKeyManager(["KEY_TEST"])

        # Mock youtube client
        mock_yt = MagicMock()
        mock_list_req = MagicMock()
        mock_list_req.execute.return_value = {
            "items": [
                {
                    "snippet": {"publishedAt": "2026-09-07T11:00:00Z"},
                    "contentDetails": {"videoId": "VID_123"},
                }
            ]
        }
        mock_yt.playlistItems().list.return_value = mock_list_req
        key_mgr.get_youtube_client = MagicMock(return_value=mock_yt)

        detected_calls = []

        def on_detected(cid, vid, pub, det, src):
            detected_calls.append((cid, vid, src))
            return True

        scheduler = PredictiveScheduler(store, key_mgr, on_detected)
        count = scheduler.poll_channel_via_api("UC_TEST", source="API_PREDICTIVE")

        self.assertEqual(count, 1)
        self.assertEqual(len(detected_calls), 1)
        self.assertEqual(detected_calls[0], ("UC_TEST", "VID_123", "API_PREDICTIVE"))


if __name__ == "__main__":
    unittest.main()
