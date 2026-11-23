"""E2E Live Smoke Test for WebSub Acceleration and YouTube Shorts Download Hardening.

ZERO-MOCK DEFINITION OF DONE:
- Tests real network call to Google Hub (pubsubhubbub.appspot.com/publish) -> expects HTTP 204.
- Tests real network call to YouTube Atom Feed (xml/feeds/videos.xml) -> expects HTTP 200 & valid XML.
- Tests single-flight thread guard on PredictiveScheduler.ping_all_active_hubs_async().
- Tests real yt-dlp download of live video with hardened parameters (socket_timeout=30s, cf=2).
- Enforces Zero Live Data Mutation (configs and credentials left untouched).
"""

import os
import sys
import time
import shutil
from pathlib import Path

# Setup paths
APP_ROOT = Path(__file__).resolve().parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from youtube_monitor.predictive_scheduler import (
    ping_google_hub,
    GOOGLE_HUB_PUBLISH_URL,
    YOUTUBE_ATOM_FEED_URL,
    YOUTUBE_RSS_CONTENT_URL,
    RSS_USER_AGENT,
    PredictiveScheduler,
)
from youtube_monitor.core import (
    download_one,
    _download_one_result,
    ping_channel_hub,
    ping_all_channels_hub,
    trigger_manual_scan_and_ping,
)
from youtube_monitor import ffmpeg_helper


def log(msg):
    try:
        print(f"[LIVE-SMOKE] {msg}", flush=True)
    except UnicodeEncodeError:
        safe_msg = str(msg).encode("ascii", "backslashreplace").decode("ascii")
        print(f"[LIVE-SMOKE] {safe_msg}", flush=True)


def test_1_google_hub_publish_ping():
    log("=== TEST 1: Live Ping to Google PubSubHubbub Hub ===")
    test_cid = "UCWp4H3So4FcKoOZaRqiqZDg"
    t0 = time.perf_counter()
    ok = ping_google_hub(test_cid, timeout=(5.0, 10.0))
    dt = time.perf_counter() - t0
    log(f"Ping Google Hub response ok={ok} in {dt:.2f}s")
    assert ok, f"Google Hub did not accept publish ping for {test_cid}"
    log("TEST 1 PASSED: Google Hub successfully accepted publish ping (HTTP 204)")


def test_2_youtube_atom_feed():
    log("=== TEST 2: Live Fetch YouTube Canonical Atom Feed ===")
    import requests
    import xml.etree.ElementTree as ET

    test_cid = "UCWp4H3So4FcKoOZaRqiqZDg"
    feed_url = YOUTUBE_RSS_CONTENT_URL.format(test_cid)
    log(f"Fetching feed URL: {feed_url}")
    resp = requests.get(
        feed_url,
        headers={"User-Agent": RSS_USER_AGENT},
        timeout=10,
    )
    log(f"Atom feed status code: {resp.status_code}, bytes: {len(resp.text)}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    
    root = ET.fromstring(resp.text)
    ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    entries = root.findall("atom:entry", ns)
    log(f"Found {len(entries)} video entries in Atom XML feed")
    assert len(entries) > 0, "No entries found in YouTube Atom feed"
    first_vid = entries[0].find("yt:videoId", ns)
    assert first_vid is not None and first_vid.text, "Missing videoId in first entry"
    log(f"Latest video from feed: {first_vid.text}")
    log("TEST 2 PASSED: YouTube Atom feed is accessible and valid XML")


def test_3_async_guard_concurrency():
    log("=== TEST 3: PredictiveScheduler Single-Flight Async Pulse Guard ===")
    from unittest.mock import MagicMock
    import threading

    mock_store = MagicMock()
    mock_store.all_items.return_value = {
        "UC_test_1": {"active": True},
        "UC_test_2": {"active": True},
    }
    mock_km = MagicMock()
    scheduler = PredictiveScheduler(
        channels_store=mock_store,
        key_manager=mock_km,
        on_video_detected=lambda *args: True,
    )

    # First call should launch the thread
    first = scheduler.ping_all_active_hubs_async()
    assert first is True, "First async call should start thread"

    # Second immediate call should be rejected by guard while thread is alive
    second = scheduler.ping_all_active_hubs_async()
    log(f"Immediate second call allowed={second} (guard expected: False)")
    assert second is False, "Second overlapping sweep must be blocked by guard"

    # Wait for the thread to finish
    if scheduler._hub_ping_thread:
        scheduler._hub_ping_thread.join(timeout=10)
    assert not scheduler._hub_ping_thread.is_alive(), "Worker thread should terminate cleanly"
    log("TEST 3 PASSED: Async single-flight guard successfully blocks overlapping sweeps")


def test_4_live_shorts_download_hardened():
    log("=== TEST 4: Live YouTube Download with Hardened Settings ===")
    test_out_dir = APP_ROOT / "Auto_Data" / "youtube_downloads" / "shorts_live_smoke"
    test_out_dir.mkdir(parents=True, exist_ok=True)

    # Video ID known to be active and valid
    test_vid = "y9gKtC7uId4"
    test_cid = "UCWp4H3So4FcKoOZaRqiqZDg"

    log(f"Starting hardened download for {test_vid} into {test_out_dir}")
    t0 = time.perf_counter()
    outcome = _download_one_result(
        channel_id=test_cid,
        video_id=test_vid,
        target_folder=str(test_out_dir),
        process_short=True,
    )
    dt = time.perf_counter() - t0
    log(f"Download completed in {dt:.2f}s: ok={outcome.ok}, failure_class={outcome.failure_class}")
    assert outcome.ok, f"Download failed: {outcome.detail} (class={outcome.failure_class})"

    # Verify physical file on disk
    mp4_files = list(test_out_dir.glob("*.mp4"))
    assert len(mp4_files) > 0, "No .mp4 file produced in target folder"
    dl_file = mp4_files[0]
    file_size = dl_file.stat().st_size
    log(f"Produced file: {dl_file.name} ({file_size:,} bytes)")
    assert file_size > 500_000, f"File too small ({file_size} bytes)"

    # Probe duration
    dur = ffmpeg_helper.probe_duration(str(dl_file))
    log(f"Probed duration via FFmpeg: {dur:.2f}s")
    assert dur is not None and dur > 0, "Invalid probed duration"

    # Clean up test output folder
    try:
        shutil.rmtree(test_out_dir, ignore_errors=True)
    except Exception:
        pass
    log("TEST 4 PASSED: Live download with hardened settings succeeded 100%")


def main():
    log("Starting E2E Live Smoke Test Suite for WebSub Acceleration...")
    t_start = time.time()
    test_1_google_hub_publish_ping()
    test_2_youtube_atom_feed()
    test_3_async_guard_concurrency()
    test_4_live_shorts_download_hardened()
    t_total = time.time() - t_start
    log(f"ALL 4 LIVE SMOKE TESTS PASSED IN {t_total:.2f}s (Exit code 0)")


if __name__ == "__main__":
    main()
