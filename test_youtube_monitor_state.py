import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from youtube_monitor.state_store import VideoStateStore


class TestVideoStateStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.store = VideoStateStore(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_pre_session_video_is_caught_up_after_restart(self):
        sid, started = self.store.begin_session(1000)
        should_queue, state = self.store.register_detection("UC1", "old", 999, sid, started, "POLLING")
        self.assertTrue(should_queue)
        self.assertEqual(state, "DISCOVERED")

    def test_explicit_first_channel_seed_is_baseline_only(self):
        sid, started = self.store.begin_session(1000)
        should_queue, state = self.store.register_detection(
            "UC1", "existing", 999, sid, started, "POLLING", baseline=True
        )
        self.assertFalse(should_queue)
        self.assertEqual(state, "BASELINE_IGNORED")

    def test_post_session_video_is_discovered_even_when_detected_late(self):
        sid, started = self.store.begin_session(1000)
        should_queue, state = self.store.register_detection("UC1", "new", 1001, sid, started, "POLLING")
        self.assertTrue(should_queue)
        self.assertEqual(state, "DISCOVERED")

    def test_websub_and_polling_are_atomic_duplicates(self):
        sid, started = self.store.begin_session(1000)
        first = self.store.register_detection("UC1", "same", 1001, sid, started, "WEBSUB")
        second = self.store.register_detection("UC1", "same", 1001, sid, started, "POLLING")
        self.assertTrue(first[0])
        self.assertFalse(second[0])
        self.assertEqual(self.store.recoverable()[0]["detection_source"], "POLLING+WEBSUB")

    def test_retry_survives_new_store_instance(self):
        sid, started = self.store.begin_session(1000)
        self.store.register_detection("UC1", "retry", 1001, sid, started, "WEBSUB")
        self.store.transition("UC1", "retry", "RETRY_WAIT", attempts=2, next_retry_at=0)
        reopened = VideoStateStore(self.store.path)
        rows = reopened.recoverable(now=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["attempts"], 2)


class TestDetectionCoordinator(unittest.TestCase):
    def test_polling_can_enqueue_post_start_video_missed_by_websub(self):
        from youtube_monitor.core import _register_detected_video, download_queue
        while not download_queue.empty():
            download_queue.get_nowait()
        durable = MagicMock()
        durable.register_detection.return_value = (True, "DISCOVERED")
        with patch("youtube_monitor.core._monitor_started_epoch", 1000), \
             patch("youtube_monitor.core._monitor_session_id", "session"), \
             patch("youtube_monitor.core.time.time", return_value=1002), \
             patch("youtube_monitor.core._get_video_state_store", return_value=durable), \
             patch("youtube_monitor.core._try_pending", return_value=True), \
             patch("youtube_monitor.core.channels_store") as channels:
            channels.get_meta.return_value = {
                "active": True, "seen": set(), "last_pub_utc": None,
            }
            channels.should_reject_by_watermark.return_value = False
            queued = _register_detected_video(
                "UC1", "missed", "1970-01-01T00:16:41Z", "detected", "POLLING"
            )
        self.assertTrue(queued)
        self.assertEqual(download_queue.get_nowait()[1], "missed")
        durable.transition.assert_called_once_with("UC1", "missed", "QUEUED_DOWNLOAD")
        channels.update_watermark.assert_called_once()

    def test_polling_enqueues_video_published_while_app_was_off(self):
        from youtube_monitor.core import _register_detected_video, download_queue
        while not download_queue.empty():
            download_queue.get_nowait()
        durable = MagicMock()
        durable.register_detection.return_value = (True, "DISCOVERED")
        with patch("youtube_monitor.core._monitor_started_epoch", 1000), \
             patch("youtube_monitor.core._monitor_session_id", "session"), \
             patch("youtube_monitor.core.time.time", return_value=1001), \
             patch("youtube_monitor.core._get_video_state_store", return_value=durable), \
             patch("youtube_monitor.core._try_pending", return_value=True), \
             patch("youtube_monitor.core.channels_store") as channels:
            channels.get_meta.return_value = {
                "active": True, "seen": set(), "last_pub_utc": None,
            }
            channels.should_reject_by_watermark.return_value = False
            queued = _register_detected_video(
                "UC1", "old", "1970-01-01T00:16:39Z", "detected", "POLLING"
            )
        self.assertTrue(queued)
        self.assertEqual(download_queue.get_nowait()[1], "old")


class TestVideoReadyCallback(unittest.TestCase):
    def tearDown(self):
        from youtube_monitor.core import set_video_ready_callback
        set_video_ready_callback(None)

    def test_ready_callback_returns_application_result(self):
        from youtube_monitor.core import VideoReadyIntent, _safe_emit_video_ready, set_video_ready_callback
        callback = MagicMock(return_value=(True, "enqueued"))
        intent = VideoReadyIntent("P", "video.mp4", "UC1", "vid1", "Title")
        set_video_ready_callback(callback)
        self.assertEqual(_safe_emit_video_ready(intent), (True, "enqueued"))
        callback.assert_called_once_with(intent)

    def test_missing_or_failed_callback_keeps_delivery_waiting(self):
        from youtube_monitor.core import VideoReadyIntent, _safe_emit_video_ready, set_video_ready_callback
        intent = VideoReadyIntent("P", "video.mp4", "UC1", "vid1", "Title")
        set_video_ready_callback(None)
        self.assertEqual(_safe_emit_video_ready(intent), (False, "ready_callback_missing"))
        set_video_ready_callback(MagicMock(side_effect=RuntimeError("boom")))
        self.assertEqual(_safe_emit_video_ready(intent), (False, "ready_callback_failed"))


if __name__ == "__main__":
    unittest.main()
