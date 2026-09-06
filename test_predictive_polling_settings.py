"""Offline regression coverage. No live credentials or YouTube requests.

Playlist fixtures use the documented resource fields, not captured live data:
https://developers.google.com/youtube/v3/docs/playlistItems
https://developers.google.com/youtube/v3/docs/playlistItems/list
The real SQLite deduplication implementation is exercised below.
"""
import copy
import json
import queue
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from youtube_monitor import core
from youtube_monitor.key_manager import ApiKeyManager
from youtube_monitor.polling_settings import DEFAULTS, KEY_STATUS_LABELS, estimate_requests, normalize_settings
from youtube_monitor.predictive_scheduler import PredictiveScheduler
from youtube_monitor.state_store import VideoStateStore


NOW = datetime(2026, 9, 7, 11, 0, tzinfo=timezone.utc)


def playlist(video="new-video", published=None):
    return {"kind": "youtube#playlistItemListResponse", "items": [{
        "kind": "youtube#playlistItem", "id": "playlist-item", "snippet": {
            "publishedAt": NOW.isoformat(),
            "resourceId": {"kind": "youtube#video", "videoId": video},
        }, "contentDetails": {"videoId": video, "videoPublishedAt": (published or NOW).isoformat()},
    }], "pageInfo": {"totalResults": 1, "resultsPerPage": 1}}


class TestPredictivePollingSettings(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.orig_channels_store = core.channels_store
        self.orig_video_state_store = core._video_state_store
        self.orig_session_id = core._monitor_session_id
        self.orig_started_epoch = core._monitor_started_epoch
        self.orig_download_queue = core.download_queue
        self.orig_pending = core._pending_video_ids
        self.orig_scheduler = core._predictive_scheduler
        self.orig_config_json = core.CONFIG_JSON

    def tearDown(self):
        core.channels_store = self.orig_channels_store
        core._video_state_store = self.orig_video_state_store
        core._monitor_session_id = self.orig_session_id
        core._monitor_started_epoch = self.orig_started_epoch
        core.download_queue = self.orig_download_queue
        core._pending_video_ids = self.orig_pending
        core._predictive_scheduler = self.orig_scheduler
        core.CONFIG_JSON = self.orig_config_json
        self.tmp_dir.cleanup()

    def _setup_scheduler(self):
        import uuid
        channels_file = self.tmp_path / f"channels_{uuid.uuid4().hex}.json"
        store = core.ChannelsStore(channels_file)
        meta = {"active": True, "seen": set(), "timezone": "Asia/Ho_Chi_Minh", "schedule_learned": {
            "predicted_windows": [{"id": "evening", "start_time": "17:50", "end_time": "18:10", "expected_time": "18:00", "days": [0, 1, 2, 3, 4, 5, 6]}]}}
        store._channels = {"UC_A": copy.deepcopy(meta), "UC_B": copy.deepcopy(meta)}
        state = VideoStateStore(self.tmp_path / f"state_{uuid.uuid4().hex}.db")
        sid, started = state.begin_session((NOW - timedelta(minutes=2)).timestamp())
        core.channels_store = store
        core._video_state_store = state
        core._monitor_session_id = sid
        core._monitor_started_epoch = started
        core.download_queue = queue.Queue()
        core._pending_video_ids = set()
        core._predictive_scheduler = None
        
        keys = ApiKeyManager(["offline-key"])
        client = MagicMock()
        request = client.playlistItems.return_value.list.return_value
        request.execute.return_value = playlist()
        keys.get_youtube_client = MagicMock(return_value=client)
        settings = dict(DEFAULTS)
        clock = [NOW]
        scheduler = PredictiveScheduler(store, keys, core._register_detected_video,
                                        settings_provider=lambda: settings, clock=lambda: clock[0])
        return scheduler, store, request, settings, clock

    def test_defaults_and_estimate(self):
        self.assertEqual(normalize_settings(), DEFAULTS)
        self.assertEqual(estimate_requests(DEFAULTS), 1200)
        self.assertEqual(PredictiveScheduler(None, None, None).settings()["poll_interval_seconds"], 1)

    def test_invalid_interval_rejected(self):
        for value in [0, -1, "abc", float("nan"), float("inf"), True, None]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Khoảng cách"):
                    normalize_settings({"poll_interval_seconds": value}, strict=True)

    def test_custom_interval_applied_to_loop(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, _, _, cfg, _ = self._setup_scheduler()
            cfg["poll_interval_seconds"] = 2.5
            scheduler._wake_event = MagicMock()
            scheduler._wake_event.wait.side_effect = lambda **kw: scheduler.stop_event.set()
            scheduler.run_loop()
            scheduler._wake_event.wait.assert_called_once_with(timeout=2.5)

    def test_interval_changes_during_running_loop(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, _, _, cfg, _ = self._setup_scheduler()
            cfg["stop_after_detection"] = False
            observed = []
            def wait(timeout):
                observed.append(timeout)
                cfg["poll_interval_seconds"] = 4
                if len(observed) == 2:
                    scheduler.stop_event.set()
            scheduler._wake_event = MagicMock()
            scheduler._wake_event.wait.side_effect = wait
            scheduler.run_loop()
            self.assertEqual(observed, [1, 4])

    def test_invalid_boundaries(self):
        for field in ["window_before_minutes", "window_after_minutes"]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    normalize_settings({field: -1}, strict=True)
                self.assertEqual(normalize_settings({field: 0}, strict=True)[field], 0)

    def test_partial_legacy_defaults_preserve_unknown_fields(self):
        settings = normalize_settings({"window_before_minutes": 2, "future_option": "keep"})
        self.assertEqual(settings["poll_interval_seconds"], 1)
        self.assertEqual(settings["window_after_minutes"], 10)
        self.assertEqual(settings["future_option"], "keep")

    def test_later_window_same_day_can_detect_again(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, store, request, _, clock = self._setup_scheduler()
            meta = store.get_meta("UC_A")
            meta["schedule_learned"]["predicted_windows"].append({"id": "later", "expected_time": "19:00"})
            self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 1)
            clock[0] += timedelta(hours=1)
            request.execute.return_value = playlist("second-video", clock[0])
            self.assertIn("UC_A", scheduler.get_active_channels_now())
            self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 1)
            self.assertEqual(request.execute.call_count, 2)
            self.assertEqual(core.download_queue.qsize(), 2)

    def test_default_and_custom_boundaries(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, _, _, cfg, _ = self._setup_scheduler()
            self.assertTrue(scheduler.is_channel_in_window("UC_A", NOW - timedelta(minutes=10)))
            self.assertTrue(scheduler.is_channel_in_window("UC_A", NOW + timedelta(minutes=10)))
            self.assertFalse(scheduler.is_channel_in_window("UC_A", NOW + timedelta(minutes=10, seconds=1)))
            cfg.update(window_before_minutes=3, window_after_minutes=7)
            self.assertTrue(scheduler.is_channel_in_window("UC_A", NOW - timedelta(minutes=3)))
            self.assertFalse(scheduler.is_channel_in_window("UC_A", NOW - timedelta(minutes=3, seconds=1)))
            self.assertTrue(scheduler.is_channel_in_window("UC_A", NOW + timedelta(minutes=7)))
            self.assertFalse(scheduler.is_channel_in_window("UC_A", NOW + timedelta(minutes=7, seconds=1)))

    def test_detection_completes_only_channel_window_and_no_more_requests(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, store, request, _, clock = self._setup_scheduler()
            self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 1)
            self.assertEqual(core.download_queue.qsize(), 1)
            self.assertEqual(store.get_meta("UC_A")["last_known_video_id"], "new-video")
            self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 0)
            self.assertEqual(scheduler.poll_channel_via_api("UC_A", source="API_SAFETY_NET"), 0)
            scheduler.trigger_immediate_poll("UC_A")
            self.assertEqual(scheduler.get_active_channels_now(), ["UC_B"])
            self.assertEqual(request.execute.call_count, 1)
            self.assertEqual(scheduler.poll_channel_via_api("UC_B"), 1)
            self.assertEqual(request.execute.call_count, 2)
            status = scheduler.get_runtime_status()["UC_A"]
            self.assertEqual(status, {"label": "Đã phát hiện video", "detected_at": "18:00:00", "skipped_seconds": 600})
            clock[0] += timedelta(days=1)
            self.assertEqual(scheduler.get_active_channels_now(), ["UC_A", "UC_B"])
            scheduler.poll_channel_via_api("UC_A")
            self.assertEqual(request.execute.call_count, 3)

    def test_reconciliation_cannot_bypass_completed_window(self):
        import threading
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, store, _, _, _ = self._setup_scheduler()
            self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 1)
            store.update_meta("UC_B", active=False)
            core._predictive_scheduler = scheduler
            event = threading.Event()
            core.stop_event = event
            feed = MagicMock(side_effect=RuntimeError("RSS unavailable"))
            api = MagicMock()
            with patch.object(core.requests, "get", feed), \
                 patch.object(core, "get_youtube_client", api), \
                 patch.object(core, "_poll_interval_seconds", lambda: (event.set(), 0)[1]):
                core._polling_worker()
            feed.assert_not_called()
            api.assert_not_called()

    def test_shared_detection_stops_predictive_api(self):
        for source in ["WEBSUB", "POLLING"]:
            with self.subTest(source=source):
                with patch.object(core.time, "time", return_value=NOW.timestamp()):
                    scheduler, store, request, _, _ = self._setup_scheduler()
                    core._predictive_scheduler = scheduler
                    self.assertTrue(core._register_detected_video("UC_A", "push-video", NOW.isoformat(), NOW.isoformat(), source))
                    self.assertEqual(core.download_queue.qsize(), 1)
                    self.assertEqual(store.get_meta("UC_A")["last_known_video_id"], "push-video")
                    self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 0)
                    request.execute.assert_not_called()
                    self.assertEqual(scheduler.get_active_channels_now(), ["UC_B"])

    def test_known_video_does_not_complete_window(self):
        for known_state in ["last_known", "seen", "sqlite", "pre_session"]:
            with self.subTest(known_state=known_state):
                with patch.object(core.time, "time", return_value=NOW.timestamp()):
                    scheduler, store, request, _, _ = self._setup_scheduler()
                    if known_state == "last_known":
                        store.update_meta("UC_A", last_known_video_id="new-video")
                    elif known_state == "seen":
                        store.mark_seen_only("UC_A", "new-video")
                    elif known_state == "sqlite":
                        self.assertTrue(core._register_detected_video("UC_A", "new-video", NOW.isoformat(), NOW.isoformat(), "WEBSUB"))
                    else:
                        request.execute.return_value = playlist(published=NOW - timedelta(hours=1))
                    for _ in range(2):
                        self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 0)
                    self.assertEqual(request.execute.call_count, 2)
                    self.assertIn("UC_A", scheduler.get_active_channels_now())

    def test_stop_disabled_continues_requests(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, _, request, cfg, _ = self._setup_scheduler()
            cfg["stop_after_detection"] = False
            self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 1)
            self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 0)
            self.assertEqual(request.execute.call_count, 2)

    def test_failed_request_does_not_complete_window(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, _, request, _, _ = self._setup_scheduler()
            request.execute.side_effect = TimeoutError("offline timeout")
            self.assertEqual(scheduler.poll_channel_via_api("UC_A"), 0)
            self.assertIn("UC_A", scheduler.get_active_channels_now())

    def test_overnight_day_belongs_to_expected_date(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, store, _, cfg, _ = self._setup_scheduler()
            meta = store.get_meta("UC_A")
            meta["schedule_learned"]["predicted_windows"][0].update(expected_time="00:05", days=[1])
            monday = datetime.fromisoformat("2026-09-07T23:59:00+07:00")
            self.assertTrue(scheduler.is_channel_in_window("UC_A", monday))
            self.assertFalse(scheduler.is_channel_in_window("UC_A", monday - timedelta(days=1)))
            cfg.update(window_before_minutes=0, window_after_minutes=0)
            self.assertFalse(scheduler.is_channel_in_window("UC_A", monday))

    def test_manual_window_unchanged_and_crosses_midnight(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, store, _, cfg, _ = self._setup_scheduler()
            store.update_meta("UC_A", schedule_learned={}, manual_windows=[{"id": "manual", "start_time": "23:50", "end_time": "00:10", "days": [0], "source": "MANUAL"}])
            cfg.update(window_before_minutes=1, window_after_minutes=1)
            self.assertTrue(scheduler.is_channel_in_window("UC_A", datetime.fromisoformat("2026-09-08T00:05:00+07:00")))
            self.assertFalse(scheduler.is_channel_in_window("UC_A", datetime.fromisoformat("2026-09-09T00:05:00+07:00")))

    def test_legacy_config_and_restart_persistence(self):
        path = self.tmp_path / "config.json"
        core.CONFIG_JSON = path
        path.write_text(json.dumps({"api_keys": ["test"], "unrelated": {"keep": 42}}), encoding="utf-8")
        self.assertEqual(core.get_config()["predictive_polling"], DEFAULTS)
        cfg = {"poll_interval_seconds": 3.5, "window_before_minutes": 4, "window_after_minutes": 9, "stop_after_detection": False}
        core.set_predictive_polling(cfg)
        self.assertEqual(core.get_config()["predictive_polling"], cfg)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["unrelated"], {"keep": 42})
        self.assertEqual(on_disk["api_keys"], ["test"])
        self.assertEqual(on_disk["predictive_polling"], cfg)

    def test_settings_load_in_fresh_process(self):
        import subprocess
        import sys
        path = self.tmp_path / "config.json"
        core.CONFIG_JSON = path
        settings = {**DEFAULTS, "poll_interval_seconds": 2.75, "stop_after_detection": False}
        core.set_predictive_polling(settings)
        code = "import sys,json; from pathlib import Path; from youtube_monitor import core; core.CONFIG_JSON=Path(sys.argv[1]); print(json.dumps(core.get_config()['predictive_polling']))"
        result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), settings)

    def test_pool_status_accepts_legacy_null_metadata(self):
        with patch.object(core, "api_key_manager", ApiKeyManager()), \
             patch.object(core, "get_config", lambda: {"api_keys": ["offline"], "api_keys_pool": None}):
            self.assertEqual(core.get_api_keys_pool_status()[0]["status"], "ACTIVE")

    def test_channel_latest_persists(self):
        with patch.object(core.time, "time", return_value=NOW.timestamp()):
            scheduler, store, *_ = self._setup_scheduler()
            scheduler.poll_channel_via_api("UC_A")
            store.save_now()
            reloaded = core.ChannelsStore(store.path)
            reloaded.load()
            self.assertEqual(reloaded.get_meta("UC_A")["last_known_video_id"], "new-video")

    def test_status_refresh_preserves_runtime_cooldown(self):
        manager = ApiKeyManager(["offline"])
        manager._keys_map["offline"].status = "RATE_LIMITED"
        manager._keys_map["offline"].retry_after = core.time.time() + 600
        with patch.object(core, "api_key_manager", manager), \
             patch.object(core, "get_config", lambda: {"api_keys": ["offline"]}):
            self.assertEqual(core.get_api_keys_pool_status()[0]["status"], "RATE_LIMITED")

    def test_vietnamese_presentation_keeps_enums(self):
        from youtube_monitor.polling_ui import mask_key, format_number
        self.assertEqual(set(KEY_STATUS_LABELS), {"ACTIVE", "QUOTA_EXCEEDED", "RATE_LIMITED", "INVALID", "DISABLED"})
        self.assertEqual(KEY_STATUS_LABELS["QUOTA_EXCEEDED"], "Hết hạn mức")
        self.assertNotEqual(mask_key("short"), "short")
        self.assertEqual(format_number(1200), "1.200")
        self.assertIn("⚡ API & Quét Video", Path("app_ui.py").read_text(encoding="utf-8"))

    def test_real_ui_estimate_validation_save_and_labels(self):
        import customtkinter as ctk
        from youtube_monitor.polling_ui import ApiPollingView
        core.CONFIG_JSON = self.tmp_path / "config.json"
        with patch.object(core, "get_api_keys_pool_status", lambda: []), \
             patch.object(core, "get_predictive_runtime_status", lambda: {}):
            root = ctk.CTk()
            root.withdraw()
            try:
                view = ApiPollingView(root)
                self.assertIn("1.200 lượt", view.estimate.get())
                view.fields["poll_interval_seconds"].set("2")
                self.assertIn("600 lượt", view.estimate.get())
                view._save()
                self.assertEqual(core.get_config()["predictive_polling"]["poll_interval_seconds"], 2)
                view.fields["poll_interval_seconds"].set("0")
                view._save()
                self.assertIn("lớn hơn 0", view.feedback.get())
                self.assertEqual(core.get_config()["predictive_polling"]["poll_interval_seconds"], 2)
                view.fields["poll_interval_seconds"].set("0,5")
                self.assertIn("nhiều hạn mức", view.estimate.get())
                labels = []
                def visit(widget):
                    if isinstance(widget, (ctk.CTkLabel, ctk.CTkButton, ctk.CTkCheckBox)):
                        labels.append(widget.cget("text"))
                    for child in widget.winfo_children():
                        visit(child)
                visit(view)
                fake_pool = [{"key": "AIzaSyTest1234567890", "status": "ACTIVE", "last_used_at": 100.0}]
                with patch.object(core, "get_api_keys_pool_status", lambda: fake_pool):
                    view._refresh_keys()
                    initial_children = list(view.keys_frame.winfo_children())
                    self.assertGreater(len(initial_children), 0)
                    fake_pool[0]["last_used_at"] = 105.0
                    view._refresh_keys()
                    self.assertEqual(initial_children, list(view.keys_frame.winfo_children()))

                view.destroy()
            finally:
                root.destroy()

    def test_get_config_mtime_caching_and_invalidation(self):
        cfg_file = self.tmp_path / "youtube_config.json"
        cfg_file.write_text('{"predictive_polling": {"poll_interval_seconds": 3}}', encoding="utf-8")
        core.CONFIG_JSON = cfg_file

        c1 = core.get_config()
        self.assertEqual(c1["predictive_polling"]["poll_interval_seconds"], 3)

        core.set_predictive_polling({"poll_interval_seconds": 5})
        c2 = core.get_config()
        self.assertEqual(c2["predictive_polling"]["poll_interval_seconds"], 5)


if __name__ == "__main__":
    unittest.main()
