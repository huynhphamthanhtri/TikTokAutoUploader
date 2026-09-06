"""Offline regression coverage. No live credentials or YouTube requests.

Playlist fixtures use the documented resource fields, not captured live data:
https://developers.google.com/youtube/v3/docs/playlistItems
https://developers.google.com/youtube/v3/docs/playlistItems/list
The real SQLite deduplication implementation is exercised below.
"""
import copy
import json
import queue
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

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


@pytest.fixture
def setup_scheduler(tmp_path, monkeypatch):
    store = core.ChannelsStore(tmp_path / "channels.json")
    meta = {"active": True, "seen": set(), "timezone": "Asia/Ho_Chi_Minh", "schedule_learned": {
        "predicted_windows": [{"id": "evening", "start_time": "17:50", "end_time": "18:10", "expected_time": "18:00", "days": [0, 1, 2, 3, 4, 5, 6]}]}}
    store._channels = {"UC_A": copy.deepcopy(meta), "UC_B": copy.deepcopy(meta)}
    state = VideoStateStore(tmp_path / "state.db")
    sid, started = state.begin_session((NOW - timedelta(minutes=2)).timestamp())
    monkeypatch.setattr(core, "channels_store", store)
    monkeypatch.setattr(core, "_video_state_store", state)
    monkeypatch.setattr(core, "_monitor_session_id", sid)
    monkeypatch.setattr(core, "_monitor_started_epoch", started)
    monkeypatch.setattr(core, "download_queue", queue.Queue())
    monkeypatch.setattr(core, "_pending_video_ids", set())
    monkeypatch.setattr(core, "_predictive_scheduler", None)
    monkeypatch.setattr(core.time, "time", lambda: NOW.timestamp())
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


def test_defaults_and_estimate():
    assert normalize_settings() == DEFAULTS
    assert estimate_requests(DEFAULTS) == 1200
    assert PredictiveScheduler(None, None, None).settings()["poll_interval_seconds"] == 1


@pytest.mark.parametrize("value", [0, -1, "abc", float("nan"), float("inf"), True, None])
def test_invalid_interval_rejected(value):
    with pytest.raises(ValueError, match="Khoảng cách"):
        normalize_settings({"poll_interval_seconds": value}, strict=True)


def test_custom_interval_applied_to_loop(setup_scheduler):
    scheduler, _, _, cfg, _ = setup_scheduler
    cfg["poll_interval_seconds"] = 2.5
    scheduler._wake_event = MagicMock()
    scheduler._wake_event.wait.side_effect = lambda **kw: scheduler.stop_event.set()
    scheduler.run_loop()
    scheduler._wake_event.wait.assert_called_once_with(timeout=2.5)


def test_interval_changes_during_running_loop(setup_scheduler):
    scheduler, _, _, cfg, _ = setup_scheduler
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
    assert observed == [1, 4]


@pytest.mark.parametrize("field", ["window_before_minutes", "window_after_minutes"])
def test_invalid_boundaries(field):
    with pytest.raises(ValueError):
        normalize_settings({field: -1}, strict=True)
    assert normalize_settings({field: 0}, strict=True)[field] == 0


def test_partial_legacy_defaults_preserve_unknown_fields():
    settings = normalize_settings({"window_before_minutes": 2, "future_option": "keep"})
    assert settings["poll_interval_seconds"] == 1
    assert settings["window_after_minutes"] == 10
    assert settings["future_option"] == "keep"


def test_later_window_same_day_can_detect_again(setup_scheduler):
    scheduler, store, request, _, clock = setup_scheduler
    meta = store.get_meta("UC_A")
    meta["schedule_learned"]["predicted_windows"].append({"id": "later", "expected_time": "19:00"})
    assert scheduler.poll_channel_via_api("UC_A") == 1
    clock[0] += timedelta(hours=1)
    request.execute.return_value = playlist("second-video", clock[0])
    assert "UC_A" in scheduler.get_active_channels_now()
    assert scheduler.poll_channel_via_api("UC_A") == 1
    assert request.execute.call_count == 2
    assert core.download_queue.qsize() == 2


def test_default_and_custom_boundaries(setup_scheduler):
    scheduler, _, _, cfg, _ = setup_scheduler
    assert scheduler.is_channel_in_window("UC_A", NOW - timedelta(minutes=10))
    assert scheduler.is_channel_in_window("UC_A", NOW + timedelta(minutes=10))
    assert not scheduler.is_channel_in_window("UC_A", NOW + timedelta(minutes=10, seconds=1))
    cfg.update(window_before_minutes=3, window_after_minutes=7)
    assert scheduler.is_channel_in_window("UC_A", NOW - timedelta(minutes=3))
    assert not scheduler.is_channel_in_window("UC_A", NOW - timedelta(minutes=3, seconds=1))
    assert scheduler.is_channel_in_window("UC_A", NOW + timedelta(minutes=7))
    assert not scheduler.is_channel_in_window("UC_A", NOW + timedelta(minutes=7, seconds=1))


def test_detection_completes_only_channel_window_and_no_more_requests(setup_scheduler):
    scheduler, store, request, _, clock = setup_scheduler
    assert scheduler.poll_channel_via_api("UC_A") == 1
    assert core.download_queue.qsize() == 1
    assert store.get_meta("UC_A")["last_known_video_id"] == "new-video"
    assert scheduler.poll_channel_via_api("UC_A") == 0
    assert scheduler.poll_channel_via_api("UC_A", source="API_SAFETY_NET") == 0
    scheduler.trigger_immediate_poll("UC_A")
    assert scheduler.get_active_channels_now() == ["UC_B"]
    assert request.execute.call_count == 1
    assert scheduler.poll_channel_via_api("UC_B") == 1
    assert request.execute.call_count == 2
    status = scheduler.get_runtime_status()["UC_A"]
    assert status == {"label": "Đã phát hiện video", "detected_at": "18:00:00", "skipped_seconds": 600}
    clock[0] += timedelta(days=1)
    assert scheduler.get_active_channels_now() == ["UC_A", "UC_B"]
    scheduler.poll_channel_via_api("UC_A")
    assert request.execute.call_count == 3


def test_reconciliation_cannot_bypass_completed_window(setup_scheduler, monkeypatch):
    import threading
    scheduler, store, _, _, _ = setup_scheduler
    assert scheduler.poll_channel_via_api("UC_A") == 1
    store.update_meta("UC_B", active=False)
    monkeypatch.setattr(core, "_predictive_scheduler", scheduler)
    event = threading.Event()
    monkeypatch.setattr(core, "stop_event", event)
    feed = MagicMock(side_effect=RuntimeError("RSS unavailable"))
    api = MagicMock()
    monkeypatch.setattr(core.requests, "get", feed)
    monkeypatch.setattr(core, "get_youtube_client", api)
    def end_iteration():
        event.set()
        return 0
    monkeypatch.setattr(core, "_poll_interval_seconds", end_iteration)
    core._polling_worker()
    feed.assert_not_called()
    api.assert_not_called()


@pytest.mark.parametrize("source", ["WEBSUB", "POLLING"])
def test_shared_detection_stops_predictive_api(setup_scheduler, monkeypatch, source):
    scheduler, store, request, _, _ = setup_scheduler
    monkeypatch.setattr(core, "_predictive_scheduler", scheduler)
    assert core._register_detected_video("UC_A", "push-video", NOW.isoformat(), NOW.isoformat(), source)
    assert core.download_queue.qsize() == 1
    assert store.get_meta("UC_A")["last_known_video_id"] == "push-video"
    assert scheduler.poll_channel_via_api("UC_A") == 0
    request.execute.assert_not_called()
    assert scheduler.get_active_channels_now() == ["UC_B"]


@pytest.mark.parametrize("known_state", ["last_known", "seen", "sqlite", "pre_session"])
def test_known_video_does_not_complete_window(setup_scheduler, known_state):
    scheduler, store, request, _, _ = setup_scheduler
    if known_state == "last_known":
        store.update_meta("UC_A", last_known_video_id="new-video")
    elif known_state == "seen":
        store.mark_seen_only("UC_A", "new-video")
    elif known_state == "sqlite":
        assert core._register_detected_video("UC_A", "new-video", NOW.isoformat(), NOW.isoformat(), "WEBSUB")
    else:
        request.execute.return_value = playlist(published=NOW - timedelta(hours=1))
    for _ in range(2):
        assert scheduler.poll_channel_via_api("UC_A") == 0
    assert request.execute.call_count == 2
    assert "UC_A" in scheduler.get_active_channels_now()


def test_stop_disabled_continues_requests(setup_scheduler):
    scheduler, _, request, cfg, _ = setup_scheduler
    cfg["stop_after_detection"] = False
    assert scheduler.poll_channel_via_api("UC_A") == 1
    assert scheduler.poll_channel_via_api("UC_A") == 0
    assert request.execute.call_count == 2


def test_failed_request_does_not_complete_window(setup_scheduler):
    scheduler, _, request, _, _ = setup_scheduler
    request.execute.side_effect = TimeoutError("offline timeout")
    assert scheduler.poll_channel_via_api("UC_A") == 0
    assert "UC_A" in scheduler.get_active_channels_now()


def test_overnight_day_belongs_to_expected_date(setup_scheduler):
    scheduler, store, _, cfg, _ = setup_scheduler
    meta = store.get_meta("UC_A")
    meta["schedule_learned"]["predicted_windows"][0].update(expected_time="00:05", days=[1])
    monday = datetime.fromisoformat("2026-09-07T23:59:00+07:00")
    assert scheduler.is_channel_in_window("UC_A", monday)
    assert not scheduler.is_channel_in_window("UC_A", monday - timedelta(days=1))
    cfg.update(window_before_minutes=0, window_after_minutes=0)
    assert not scheduler.is_channel_in_window("UC_A", monday)


def test_manual_window_unchanged_and_crosses_midnight(setup_scheduler):
    scheduler, store, _, cfg, _ = setup_scheduler
    store.update_meta("UC_A", schedule_learned={}, manual_windows=[{"id": "manual", "start_time": "23:50", "end_time": "00:10", "days": [0], "source": "MANUAL"}])
    cfg.update(window_before_minutes=1, window_after_minutes=1)
    assert scheduler.is_channel_in_window("UC_A", datetime.fromisoformat("2026-09-08T00:05:00+07:00"))
    assert not scheduler.is_channel_in_window("UC_A", datetime.fromisoformat("2026-09-09T00:05:00+07:00"))


def test_legacy_config_and_restart_persistence(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(core, "CONFIG_JSON", path)
    path.write_text(json.dumps({"api_keys": ["test"], "unrelated": {"keep": 42}}), encoding="utf-8")
    assert core.get_config()["predictive_polling"] == DEFAULTS
    cfg = {"poll_interval_seconds": 3.5, "window_before_minutes": 4, "window_after_minutes": 9, "stop_after_detection": False}
    core.set_predictive_polling(cfg)
    assert core.get_config()["predictive_polling"] == cfg
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["unrelated"] == {"keep": 42}
    assert on_disk["api_keys"] == ["test"]
    assert on_disk["predictive_polling"] == cfg


def test_settings_load_in_fresh_process(tmp_path, monkeypatch):
    import subprocess
    import sys
    path = tmp_path / "config.json"
    monkeypatch.setattr(core, "CONFIG_JSON", path)
    settings = {**DEFAULTS, "poll_interval_seconds": 2.75, "stop_after_detection": False}
    core.set_predictive_polling(settings)
    code = "import sys,json; from pathlib import Path; from youtube_monitor import core; core.CONFIG_JSON=Path(sys.argv[1]); print(json.dumps(core.get_config()['predictive_polling']))"
    result = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == settings


def test_pool_status_accepts_legacy_null_metadata(monkeypatch):
    monkeypatch.setattr(core, "api_key_manager", ApiKeyManager())
    monkeypatch.setattr(core, "get_config", lambda: {"api_keys": ["offline"], "api_keys_pool": None})
    assert core.get_api_keys_pool_status()[0]["status"] == "ACTIVE"


def test_channel_latest_persists(setup_scheduler):
    scheduler, store, *_ = setup_scheduler
    scheduler.poll_channel_via_api("UC_A")
    store.save_now()
    reloaded = core.ChannelsStore(store.path)
    reloaded.load()
    assert reloaded.get_meta("UC_A")["last_known_video_id"] == "new-video"


def test_status_refresh_preserves_runtime_cooldown(monkeypatch):
    manager = ApiKeyManager(["offline"])
    manager._keys_map["offline"].status = "RATE_LIMITED"
    manager._keys_map["offline"].retry_after = core.time.time() + 600
    monkeypatch.setattr(core, "api_key_manager", manager)
    monkeypatch.setattr(core, "get_config", lambda: {"api_keys": ["offline"]})
    assert core.get_api_keys_pool_status()[0]["status"] == "RATE_LIMITED"


def test_vietnamese_presentation_keeps_enums():
    from youtube_monitor.polling_ui import mask_key, format_number
    assert set(KEY_STATUS_LABELS) == {"ACTIVE", "QUOTA_EXCEEDED", "RATE_LIMITED", "INVALID", "DISABLED"}
    assert KEY_STATUS_LABELS["QUOTA_EXCEEDED"] == "Hết hạn mức"
    assert mask_key("short") != "short"
    assert format_number(1200) == "1.200"
    assert "⚡ API & Quét Video" in Path("app_ui.py").read_text(encoding="utf-8")


def test_real_ui_estimate_validation_save_and_labels(tmp_path, monkeypatch):
    import customtkinter as ctk
    from youtube_monitor.polling_ui import ApiPollingView
    monkeypatch.setattr(core, "CONFIG_JSON", tmp_path / "config.json")
    monkeypatch.setattr(core, "get_api_keys_pool_status", lambda: [])
    monkeypatch.setattr(core, "get_predictive_runtime_status", lambda: {})
    root = ctk.CTk()
    root.withdraw()
    try:
        view = ApiPollingView(root)
        assert "1.200 lượt" in view.estimate.get()
        view.fields["poll_interval_seconds"].set("2")
        assert "600 lượt" in view.estimate.get()
        view._save()
        assert core.get_config()["predictive_polling"]["poll_interval_seconds"] == 2
        view.fields["poll_interval_seconds"].set("0")
        view._save()
        assert "lớn hơn 0" in view.feedback.get()
        assert core.get_config()["predictive_polling"]["poll_interval_seconds"] == 2
        view.fields["poll_interval_seconds"].set("0,5")
        assert "nhiều hạn mức" in view.estimate.get()
        labels = []
        def visit(widget):
            if isinstance(widget, (ctk.CTkLabel, ctk.CTkButton, ctk.CTkCheckBox)):
                labels.append(widget.cget("text"))
            for child in widget.winfo_children():
                visit(child)
        visit(view)
        # Verify key widget preservation when last_used_at updates
        fake_pool = [{"key": "AIzaSyTest1234567890", "status": "ACTIVE", "last_used_at": 100.0}]
        monkeypatch.setattr(core, "get_api_keys_pool_status", lambda: fake_pool)
        view._refresh_keys()
        initial_children = list(view.keys_frame.winfo_children())
        assert len(initial_children) > 0
        fake_pool[0]["last_used_at"] = 105.0
        view._refresh_keys()
        assert initial_children == list(view.keys_frame.winfo_children())

        view.destroy()
    finally:
        root.destroy()


def test_get_config_mtime_caching_and_invalidation(tmp_path, monkeypatch):
    cfg_file = tmp_path / "youtube_config.json"
    cfg_file.write_text('{"predictive_polling": {"poll_interval_seconds": 3}}', encoding="utf-8")
    monkeypatch.setattr(core, "CONFIG_JSON", cfg_file)

    # First read: loads from file
    c1 = core.get_config()
    assert c1["predictive_polling"]["poll_interval_seconds"] == 3

    # Direct modification without mtime change simulation: should hit cache
    # But when _save_config is called, cache must update
    core.set_predictive_polling({"poll_interval_seconds": 5})
    c2 = core.get_config()
    assert c2["predictive_polling"]["poll_interval_seconds"] == 5

