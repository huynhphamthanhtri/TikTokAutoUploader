# -*- coding: utf-8 -*-
"""
Tests for Phase 2: Batch Execution Engine (audit_channels_activity_data_api)
"""
import copy
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

from youtube_monitor.core import (
    audit_channels_activity_data_api,
    channels_store,
    _store_lock,
)


@pytest.fixture(autouse=True)
def clean_channels_store(monkeypatch, tmp_path):
    temp_channels_file = tmp_path / "temp_channels.json"
    temp_channels_file.write_text("{}", encoding="utf-8")

    # 1. Quiesce any active autosave thread without writing to production
    channels_store._autosave_stop.set()
    if channels_store._autosave_thread and channels_store._autosave_thread.is_alive():
        channels_store._autosave_thread.join(timeout=2)

    with _store_lock:
        orig = copy.deepcopy(channels_store._channels)
        orig_dirty = channels_store._dirty
        orig_revision = channels_store._revision
        orig_path = channels_store.path

        channels_store.path = temp_channels_file
        channels_store._dirty = False
        channels_store._revision = 0

    mock_save = MagicMock(return_value=True)
    monkeypatch.setattr(channels_store, "save_now", mock_save)

    try:
        yield
    finally:
        channels_store._autosave_stop.set()
        if channels_store._autosave_thread and channels_store._autosave_thread.is_alive():
            channels_store._autosave_thread.join(timeout=2)

        with _store_lock:
            channels_store._channels = orig
            channels_store._dirty = orig_dirty
            channels_store._revision = orig_revision
            channels_store.path = orig_path


def test_batch_audit_empty_list():
    res = audit_channels_activity_data_api(channels_list=[])
    assert res["total"] == 0
    assert res["completed"] == 0
    assert res["ok"] == 0
    assert res["empty"] == 0
    assert res["error"] == 0
    assert res["cancelled"] is False
    assert res["results"] == []


def test_batch_audit_mixed_results_and_store_update():
    # Setup test channels in channels_store
    cid1 = "UC_TEST_OK"
    cid2 = "UC_TEST_EMPTY"
    cid3 = "UC_TEST_ERROR"

    with _store_lock:
        channels_store._channels[cid1] = {
            "title": "Channel 1",
            "active": True,
            "last_pub_utc": 1000.0,  # Existing bot watermark
            "last_known_video_id": "vid_wm_1",  # Existing bot watermark video
            "latest_observed_video_id": "",
        }
        channels_store._channels[cid2] = {
            "title": "Channel 2",
            "active": True,
            "last_pub_utc": None,
            "last_known_video_id": "",
            "latest_observed_video_id": "",
        }
        channels_store._channels[cid3] = {
            "title": "Channel 3",
            "active": True,
            "last_pub_utc": 2000.0,
            "last_known_video_id": "vid_wm_3",
            "latest_observed_video_id": "",
        }

    mock_now = time.time()
    mock_responses = {
        cid1: {
            "channel_id": cid1,
            "uploads_playlist_id": "UU_TEST_OK",
            "status": "OK",
            "video_id": "vid_ok",
            "video_title": "Video OK",
            "published_iso": "2023-11-14T20:00:00Z",
            "published_utc": mock_now - 3600.0,  # 1 hour ago
            "error": "",
        },
        cid2: {
            "channel_id": cid2,
            "uploads_playlist_id": "UU_TEST_EMPTY",
            "status": "EMPTY",
            "video_id": "",
            "video_title": "",
            "published_iso": "",
            "published_utc": None,
            "error": "Kênh chưa có video",
        },
        cid3: {
            "channel_id": cid3,
            "uploads_playlist_id": "UU_TEST_ERROR",
            "status": "ERROR",
            "video_id": "",
            "video_title": "",
            "published_iso": "",
            "published_utc": None,
            "error": "QuotaExceeded",
        },
    }

    def fake_fetch(cid, uploads_playlist_id=None):
        return mock_responses[cid]

    progress_calls = []

    def progress_cb(completed, total, res_item, run_id=None):
        progress_calls.append((completed, total, res_item["channel_id"], res_item["status"], run_id))

    channels_to_scan = [
        {"channel_id": cid1, "uploads_playlist_id": "UU_TEST_OK"},
        {"channel_id": cid2, "uploads_playlist_id": "UU_TEST_EMPTY"},
        {"channel_id": cid3, "uploads_playlist_id": "UU_TEST_ERROR"},
    ]

    with patch("youtube_monitor.core.fetch_channel_latest_video_api", side_effect=fake_fetch), \
         patch.object(channels_store, "save_now") as mock_save:

        res = audit_channels_activity_data_api(
            channels_list=channels_to_scan,
            progress_callback=progress_cb,
            max_workers=2,
            run_id="test_run_123",
        )

        assert res["run_id"] == "test_run_123"
        assert res["total"] == 3
        assert res["completed"] == 3
        assert res["ok"] == 1
        assert res["empty"] == 1
        assert res["error"] == 1
        assert res["cancelled"] is False
        assert len(res["results"]) == 3
        assert len(progress_calls) == 3
        assert progress_calls[0][4] == "test_run_123"
        mock_save.assert_called_once()

    # Verify channel 1 in store
    meta1 = channels_store.get_meta(cid1)
    assert meta1["latest_observed_video_id"] == "vid_ok"
    assert meta1["latest_observed_video_title"] == "Video OK"
    assert meta1["latest_observed_pub_utc"] == mock_now - 3600.0
    assert meta1["last_api_audit_status"] == "OK"
    assert meta1["last_api_audit_error"] == ""
    # CRITICAL: Verify watermarks were NEVER touched!
    assert meta1["last_pub_utc"] == 1000.0
    assert meta1["last_known_video_id"] == "vid_wm_1"

    # Verify channel 2 in store
    meta2 = channels_store.get_meta(cid2)
    assert meta2["latest_observed_video_id"] == ""
    assert meta2["last_api_audit_status"] == "EMPTY"
    assert meta2["last_api_audit_error"] == "Kênh chưa có video"
    assert meta2["last_pub_utc"] is None
    assert meta2["last_known_video_id"] == ""

    # Verify channel 3 in store
    meta3 = channels_store.get_meta(cid3)
    assert meta3["last_api_audit_status"] == "ERROR"
    assert meta3["last_api_audit_error"] == "QuotaExceeded"
    assert meta3["last_pub_utc"] == 2000.0
    assert meta3["last_known_video_id"] == "vid_wm_3"

    # Verify evaluations are attached to results
    by_cid = {r["channel_id"]: r for r in res["results"]}
    assert by_cid[cid1]["evaluation"]["category"] == "IN_THRESHOLD"
    assert by_cid[cid1]["evaluation"]["audit_status"] == "OK"

    assert by_cid[cid2]["evaluation"]["category"] == "NO_DATA"
    assert by_cid[cid2]["evaluation"]["audit_status"] == "EMPTY"

    assert by_cid[cid3]["evaluation"]["category"] == "EXCEEDED_THRESHOLD"
    assert by_cid[cid3]["evaluation"]["audit_status"] == "ERROR"


def test_batch_audit_cancellation():
    cids = [f"UC_CANCEL_{i}" for i in range(10)]
    for cid in cids:
        with _store_lock:
            channels_store._channels[cid] = {"title": cid, "active": True}

    cancel_evt = threading.Event()
    call_count = 0

    def slow_fetch(cid, uploads_playlist_id=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            cancel_evt.set()
        time.sleep(0.05)
        return {
            "channel_id": cid,
            "status": "OK",
            "video_id": f"vid_{cid}",
            "video_title": f"Title {cid}",
            "published_iso": "2023-11-14T20:00:00Z",
            "published_utc": 1700000000.0,
            "error": "",
        }

    channels_to_scan = [{"channel_id": cid} for cid in cids]

    with patch("youtube_monitor.core.fetch_channel_latest_video_api", side_effect=slow_fetch), \
         patch.object(channels_store, "save_now"):

        res = audit_channels_activity_data_api(
            channels_list=channels_to_scan,
            cancellation_event=cancel_evt,
            max_workers=2,
        )

        assert res["cancelled"] is True
        assert res["completed"] < 10


def test_batch_audit_worker_exception_handling():
    cid = "UC_CRASH"
    with _store_lock:
        channels_store._channels[cid] = {"title": "Crash Channel", "active": True}

    def crashing_fetch(cid, uploads_playlist_id=None):
        raise RuntimeError("Network cable unplugged")

    with patch("youtube_monitor.core.fetch_channel_latest_video_api", side_effect=crashing_fetch), \
         patch.object(channels_store, "save_now"):

        res = audit_channels_activity_data_api(
            channels_list=[{"channel_id": cid}],
            max_workers=1,
        )

        assert res["total"] == 1
        assert res["completed"] == 1
        assert res["error"] == 1
        meta = channels_store.get_meta(cid)
        assert meta["last_api_audit_status"] == "ERROR"
        assert "Network cable unplugged" in meta["last_api_audit_error"]


def test_batch_audit_mismatched_and_malformed_worker_results():
    """Verify that worker returning wrong channel ID or non-dict is defensively normalized to target_cid."""
    cid_target = "UC_TARGET"
    cid_rogue = "UC_ROGUE"

    with _store_lock:
        channels_store._channels[cid_target] = {
            "title": "Target Channel",
            "active": True,
            "latest_observed_video_id": "",
        }
        channels_store._channels[cid_rogue] = {
            "title": "Rogue Channel",
            "active": True,
            "latest_observed_video_id": "",
        }

    # Worker mistakenly returns cid_rogue when asked for cid_target
    def rogue_fetch(cid, uploads_playlist_id=None):
        return {
            "channel_id": cid_rogue,  # MISMATCH
            "status": "OK",
            "video_id": "vid_rogue",
            "video_title": "Rogue Video",
            "published_iso": "2023-11-14T20:00:00Z",
            "published_utc": time.time() - 100,
            "error": "",
        }

    with patch("youtube_monitor.core.fetch_channel_latest_video_api", side_effect=rogue_fetch), \
         patch.object(channels_store, "save_now"):

        res = audit_channels_activity_data_api(
            channels_list=[{"channel_id": cid_target}],
            max_workers=1,
        )

        assert res["completed"] == 1
        # Target must be updated, rogue must NOT be touched
        meta_target = channels_store.get_meta(cid_target)
        meta_rogue = channels_store.get_meta(cid_rogue)
        assert meta_target["latest_observed_video_id"] == "vid_rogue"
        assert meta_rogue["latest_observed_video_id"] == ""


def test_batch_audit_callback_mutation_isolation():
    """Verify that callback mutating the received object does not corrupt batch results."""
    cid = "UC_MUTATE"
    with _store_lock:
        channels_store._channels[cid] = {"title": "Test", "active": True}

    def evil_callback(completed, total, item, run_id=None):
        # Callback tries to empty the dictionary
        item.clear()

    mock_resp = {
        "channel_id": cid,
        "status": "OK",
        "video_id": "vid_123",
        "video_title": "Test Title",
        "published_iso": "2023-11-14T20:00:00Z",
        "published_utc": time.time() - 3600,
        "error": "",
    }

    with patch("youtube_monitor.core.fetch_channel_latest_video_api", return_value=mock_resp), \
         patch.object(channels_store, "save_now"):

        res = audit_channels_activity_data_api(
            channels_list=[{"channel_id": cid}],
            progress_callback=evil_callback,
            max_workers=1,
        )

        # res["results"][0] must NOT be empty!
        assert len(res["results"]) == 1
        assert res["results"][0]["channel_id"] == cid
        assert res["results"][0]["video_id"] == "vid_123"
        assert "evaluation" in res["results"][0]
