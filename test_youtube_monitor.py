import os
import sys
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).resolve().parent))


class TestPublicMonitorApi(unittest.TestCase):
    def test_detection_callback_is_exported_for_main_autostart(self):
        import youtube_monitor
        self.assertTrue(callable(youtube_monitor.set_video_detected_callback))
        self.assertTrue(callable(youtube_monitor.set_video_ready_callback))


class TestPendingState(unittest.TestCase):
    def setUp(self):
        from youtube_monitor.core import _pending_video_ids, _pending_lock
        _pending_video_ids.clear()

    def test_try_pending_success(self):
        from youtube_monitor.core import _try_pending, _is_pending, _remove_pending
        self.assertTrue(_try_pending("chan1", "vid1"))
        self.assertTrue(_is_pending("chan1", "vid1"))

    def test_try_pending_duplicate(self):
        from youtube_monitor.core import _try_pending, _is_pending
        _try_pending("chan1", "vid1")
        self.assertFalse(_try_pending("chan1", "vid1"))

    def test_remove_pending(self):
        from youtube_monitor.core import _try_pending, _is_pending, _remove_pending
        _try_pending("chan1", "vid1")
        _remove_pending("chan1", "vid1")
        self.assertFalse(_is_pending("chan1", "vid1"))

    def test_pending_scoped_by_channel(self):
        from youtube_monitor.core import _try_pending
        self.assertTrue(_try_pending("chan1", "vid1"))
        self.assertTrue(_try_pending("chan2", "vid1"))


class TestRetryState(unittest.TestCase):
    def setUp(self):
        from youtube_monitor.core import _retry_after, _retry_lock
        _retry_after.clear()

    def test_schedule_and_clear(self):
        from youtube_monitor.core import _schedule_retry, _clear_retry, _get_retry_due
        _schedule_retry("chan1", "vid1", 1)
        remaining = _get_retry_due("chan1", "vid1")
        self.assertIsNotNone(remaining)
        self.assertGreater(remaining, 0)
        _clear_retry("chan1", "vid1")
        self.assertIsNone(_get_retry_due("chan1", "vid1"))

    def test_retry_delays(self):
        from youtube_monitor.core import RETRY_DELAYS
        self.assertEqual(len(RETRY_DELAYS), 4)
        self.assertEqual(RETRY_DELAYS[0], 0)
        self.assertEqual(RETRY_DELAYS[1], 15)
        self.assertEqual(RETRY_DELAYS[2], 45)
        self.assertEqual(RETRY_DELAYS[3], 120)


class TestErrorClassification(unittest.TestCase):
    def test_permanent_members_only(self):
        from youtube_monitor.core import _classify_download_error
        err = Exception("members-only content")
        self.assertEqual(_classify_download_error(err), "permanent")

    def test_permanent_private(self):
        from youtube_monitor.core import _classify_download_error
        err = Exception("this video is private")
        self.assertEqual(_classify_download_error(err), "permanent")

    def test_retry_generic(self):
        from youtube_monitor.core import _classify_download_error
        err = Exception("HTTP Error 500: Internal Server Error")
        self.assertEqual(_classify_download_error(err), "retry")

    def test_retry_block_youtube(self):
        from youtube_monitor.core import _classify_download_error
        err = Exception("Sign in to confirm you're not a bot")
        self.assertEqual(_classify_download_error(err), "retry_block")

    def test_retry_proxy_error(self):
        from youtube_monitor.core import _classify_download_error
        err = Exception("cannot connect to proxy 407")
        self.assertEqual(_classify_download_error(err), "retry_proxy")


class TestWebSubXmlParsing(unittest.TestCase):
    def test_parse_atom_xml(self):
        from youtube_monitor.core import _parse_websub_xml
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:yt="http://www.youtube.com/xml/schemas/2015">
          <entry>
            <yt:videoId>abc123def</yt:videoId>
            <yt:channelId>UCtest</yt:channelId>
            <published>2024-01-15T10:30:00+00:00</published>
          </entry>
        </feed>'''
        result = _parse_websub_xml(xml)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "abc123def")
        self.assertEqual(result[0][1], "UCtest")
        self.assertEqual(result[0][2], "2024-01-15T10:30:00+00:00")

    def test_multiple_entries(self):
        from youtube_monitor.core import _parse_websub_xml
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:yt="http://www.youtube.com/xml/schemas/2015">
          <entry>
            <yt:videoId>vid1</yt:videoId>
            <yt:channelId>UCone</yt:channelId>
            <published>2024-01-15T10:30:00+00:00</published>
          </entry>
          <entry>
            <yt:videoId>vid2</yt:videoId>
            <yt:channelId>UCtwo</yt:channelId>
            <published>2024-01-15T11:00:00+00:00</published>
          </entry>
        </feed>'''
        result = _parse_websub_xml(xml)
        self.assertEqual(len(result), 2)

    def test_empty_feed(self):
        from youtube_monitor.core import _parse_websub_xml
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
        </feed>'''
        result = _parse_websub_xml(xml)
        self.assertEqual(len(result), 0)

    def test_invalid_xml(self):
        from youtube_monitor.core import _parse_websub_xml
        result = _parse_websub_xml("not xml at all")
        self.assertEqual(len(result), 0)

    def test_no_video_id(self):
        from youtube_monitor.core import _parse_websub_xml
        xml = '''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:yt="http://www.youtube.com/xml/schemas/2015">
          <entry>
            <yt:channelId>UCtest</yt:channelId>
          </entry>
        </feed>'''
        result = _parse_websub_xml(xml)
        self.assertEqual(len(result), 0)


class TestPollingBackfillPrevention(unittest.TestCase):
    def test_empty_history_needs_polling_baseline(self):
        from youtube_monitor.core import _channel_needs_polling_baseline
        self.assertTrue(_channel_needs_polling_baseline({"seen": set(), "last_pub_utc": None}))
        self.assertTrue(_channel_needs_polling_baseline({}))
        self.assertFalse(_channel_needs_polling_baseline({"seen": {"vid1"}, "last_pub_utc": None}))
        self.assertFalse(_channel_needs_polling_baseline({"seen": set(), "last_pub_utc": 100.0}))

    def test_polling_watermark_rejects_existing_or_older_items(self):
        from youtube_monitor.core import _polling_item_is_at_or_before_watermark
        meta = {"last_pub_utc": 100.0}
        self.assertTrue(_polling_item_is_at_or_before_watermark(meta, 99.0))
        self.assertTrue(_polling_item_is_at_or_before_watermark(meta, 100.0))
        self.assertFalse(_polling_item_is_at_or_before_watermark(meta, 101.0))
        self.assertFalse(_polling_item_is_at_or_before_watermark({"last_pub_utc": None}, 99.0))
        self.assertFalse(_polling_item_is_at_or_before_watermark(meta, None))

    def test_seed_polling_baseline_marks_existing_items(self):
        from youtube_monitor.core import _seed_polling_baseline, iso_to_epoch
        items = [
            {
                "contentDetails": {"videoId": "old1"},
                "snippet": {"publishedAt": "2026-07-23T10:00:00Z"},
            },
            {
                "contentDetails": {"videoId": "old2"},
                "snippet": {"publishedAt": "2026-07-23T11:00:00Z"},
            },
        ]
        with patch("youtube_monitor.core.channels_store") as store:
            seeded = _seed_polling_baseline("UCtest", items)
        self.assertEqual(seeded, 2)
        store.mark_seen_only.assert_has_calls([call("UCtest", "old1"), call("UCtest", "old2")])
        store.update_watermark.assert_called_once_with("UCtest", iso_to_epoch("2026-07-23T11:00:00Z"))


class TestSubscriptionStatus(unittest.TestCase):
    def setUp(self):
        from youtube_monitor.core import _subscription_status, _subscription_inflight, _subscription_lock
        with _subscription_lock:
            _subscription_status.clear()
            _subscription_inflight.clear()

    def test_subscribe_tracks_request(self):
        from youtube_monitor.core import subscribe_websub
        with patch("youtube_monitor.core.requests.post") as mock_post:
            mock_post.return_value.status_code = 202
            subscribe_websub("UCtest", "http://example.com/callback")
        from youtube_monitor.core import _subscription_status
        self.assertIn("UCtest", _subscription_status)
        self.assertIn("requested_at", _subscription_status["UCtest"])
        self.assertEqual(_subscription_status["UCtest"]["last_status"], 202)
        self.assertTrue(_subscription_status["UCtest"]["verification_pending"])
        self.assertGreater(_subscription_status["UCtest"]["next_retry_at"], 0)

    def test_subscribe_and_polling_share_official_xml_feed_path(self):
        from youtube_monitor.core import YOUTUBE_FEED_URL_TEMPLATE
        self.assertEqual(
            YOUTUBE_FEED_URL_TEMPLATE.format("UCtest"),
            "https://www.youtube.com/xml/feeds/videos.xml?channel_id=UCtest",
        )

    def test_accepted_subscribe_waits_for_async_verification(self):
        from youtube_monitor.core import subscribe_websub
        with patch("youtube_monitor.core.requests.post") as mock_post:
            mock_post.return_value.status_code = 202
            self.assertTrue(subscribe_websub("UCtest", "http://example.com/callback"))
            self.assertFalse(subscribe_websub("UCtest", "http://example.com/callback"))
        self.assertEqual(mock_post.call_count, 1)

    def test_subscribe_request_error(self):
        import time
        from youtube_monitor.core import subscribe_websub
        t0 = time.time()
        with patch("youtube_monitor.core.requests.post") as mock_post:
            mock_post.side_effect = Exception("Connection refused")
            subscribe_websub("UCtest", "http://example.com/callback")
        from youtube_monitor.core import _subscription_status
        self.assertIn("UCtest", _subscription_status)
        self.assertIn("last_error", _subscription_status["UCtest"])
        self.assertEqual(_subscription_status["UCtest"]["retry_attempts"], 1)
        # Next retry must be 5 hours later (18,000s)
        self.assertGreaterEqual(_subscription_status["UCtest"]["next_retry_at"], t0 + 17900)
        # Extended timeout must be used (10, 60)
        self.assertEqual(mock_post.call_args.kwargs["timeout"], (10, 60))

    def test_verified_callback_wins_when_subscribe_post_times_out(self):
        from youtube_monitor import core

        def verified_then_timeout(*_args, **_kwargs):
            with core._subscription_lock:
                core._subscription_status["UCtest"].update({
                    "verified_at": "2026-09-06T15:00:00+00:00",
                    "verification_pending": False,
                    "lease_seconds": 432000,
                })
            raise core.requests.ReadTimeout("late response")

        with patch.object(core, "_proxy_pool", []), \
                patch("youtube_monitor.core.requests.post", side_effect=verified_then_timeout):
            self.assertTrue(core.subscribe_websub("UCtest", "http://example.com/callback"))
        self.assertEqual(core._subscription_status["UCtest"]["last_error"], "")
        self.assertEqual(core._subscription_status["UCtest"]["next_retry_at"], 0)

    def test_duplicate_subscribe_is_suppressed_while_request_inflight(self):
        from youtube_monitor.core import subscribe_websub
        entered = threading.Event()
        release = threading.Event()

        def slow_post(*args, **kwargs):
            entered.set()
            release.wait(2)
            response = MagicMock()
            response.status_code = 202
            return response

        with patch("youtube_monitor.core.requests.post", side_effect=slow_post) as mock_post:
            first = threading.Thread(
                target=subscribe_websub,
                args=("UCtest", "http://example.com/callback"),
            )
            first.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(subscribe_websub("UCtest", "http://example.com/callback"))
            release.set()
            first.join(2)
        self.assertFalse(first.is_alive())
        self.assertEqual(mock_post.call_count, 1)

    def test_subscribe_backoff_suppresses_immediate_retry(self):
        from youtube_monitor.core import subscribe_websub
        with patch("youtube_monitor.core.requests.post", side_effect=Exception("timeout")) as mock_post:
            self.assertFalse(subscribe_websub("UCtest", "http://example.com/callback"))
            self.assertFalse(subscribe_websub("UCtest", "http://example.com/callback"))
        self.assertEqual(mock_post.call_count, 1)

    def test_subscribe_never_uses_profile_proxy(self):
        from youtube_monitor import core
        with patch.object(core, "_proxy_pool", ["http://user:pass@proxy.test:8080"]), \
                patch.object(core, "_proxy_rr_index", 0), \
                patch("youtube_monitor.core.requests.post",
                      side_effect=core.requests.ReadTimeout("direct timeout")) as mock_post:
            self.assertFalse(core.subscribe_websub("UCtest", "http://example.com/callback"))
        self.assertEqual(mock_post.call_count, 1)
        self.assertNotIn("proxies", mock_post.call_args.kwargs)
        self.assertNotIn("prefer_proxy", core._subscription_status["UCtest"])

    def test_verification_via_health_route(self):
        from youtube_monitor.core import _subscription_status, _subscription_lock
        with _subscription_lock:
            _subscription_status["UCtest"] = {
                "verified_at": "2024-01-15T10:00:00",
                "lease_seconds": 86400,
            }
        with _subscription_lock:
            self.assertIsNotNone(_subscription_status["UCtest"]["verified_at"])


class TestCallbackServerHealth(unittest.TestCase):
    @patch("youtube_monitor.core.make_server")
    @patch("youtube_monitor.core.requests.get")
    def test_start_server_ok(self, mock_get, mock_make):
        from youtube_monitor.core import _start_callback_server, _stop_callback_server
        mock_server = MagicMock()
        mock_server.server_address = ("0.0.0.0", 5000)
        mock_make.return_value = mock_server
        mock_get.return_value.status_code = 200
        ok, port = _start_callback_server(5000)
        self.assertTrue(ok)
        self.assertEqual(port, 5000)
        _stop_callback_server()

    @patch("youtube_monitor.core.make_server")
    @patch("youtube_monitor.core.requests.get")
    def test_start_server_health_fail_then_ok(self, mock_get, mock_make):
        from youtube_monitor.core import _start_callback_server, _stop_callback_server
        mock_server = MagicMock()
        mock_server.server_address = ("0.0.0.0", 5000)
        mock_make.return_value = mock_server
        mock_get.side_effect = [Exception("timeout"), MagicMock(status_code=200)]
        ok, port = _start_callback_server(5000)
        self.assertTrue(ok)
        _stop_callback_server()

    @patch("youtube_monitor.core.make_server")
    def test_start_server_bind_error(self, mock_make):
        from youtube_monitor.core import _start_callback_server, _stop_callback_server
        mock_make.side_effect = OSError("Address already in use")
        with patch("youtube_monitor.core.requests.get") as mock_get:
            mock_get.side_effect = Exception("Connection refused")
            ok, port = _start_callback_server(5000)
            self.assertFalse(ok)
        _stop_callback_server()


class TestNgrokVerification(unittest.TestCase):
    def setUp(self):
        from youtube_monitor.core import _reset_ngrok_verification_log_state
        _reset_ngrok_verification_log_state()

    def tearDown(self):
        from youtube_monitor.core import _reset_ngrok_verification_log_state
        _reset_ngrok_verification_log_state()

    @patch("youtube_monitor.core.requests.get")
    def test_verify_tunnel_ok(self, mock_get):
        from youtube_monitor.core import _verify_ngrok_tunnel
        expected_challenge = "verify_aabbccdd1122"
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = expected_challenge
        with patch("uuid.uuid4") as mock_uuid:
            mock_instance = MagicMock()
            mock_instance.hex = "aabbccdd1122"
            mock_uuid.return_value = mock_instance
            self.assertTrue(_verify_ngrok_tunnel("http://test.ngrok.io"))

    @patch("youtube_monitor.core.requests.get")
    def test_verify_tunnel_wrong_response(self, mock_get):
        from youtube_monitor.core import _verify_ngrok_tunnel
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = "wrong_challenge"
        self.assertFalse(_verify_ngrok_tunnel("http://test.ngrok.io"))

    @patch("youtube_monitor.core.requests.get")
    def test_verify_tunnel_timeout(self, mock_get):
        from youtube_monitor.core import _verify_ngrok_tunnel
        mock_get.side_effect = Exception("timeout")
        self.assertFalse(_verify_ngrok_tunnel("http://test.ngrok.io"))

    @patch("youtube_monitor.core.log")
    @patch("youtube_monitor.core.requests.get")
    def test_repeated_healthy_verification_logs_once(self, mock_get, mock_log):
        from youtube_monitor.core import _verify_ngrok_tunnel
        mock_get.return_value.status_code = 200
        mock_get.return_value.text = "verify_same"
        with patch("youtube_monitor.core.uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "same"
            self.assertTrue(_verify_ngrok_tunnel("https://test.ngrok.io/"))
            self.assertTrue(_verify_ngrok_tunnel("https://test.ngrok.io"))
        self.assertEqual(mock_log.call_args_list, [call("[Ngrok] Tunnel verified")])

    @patch("youtube_monitor.core.log")
    @patch("youtube_monitor.core.requests.get")
    def test_recovery_and_url_change_log_verified_again(self, mock_get, mock_log):
        from youtube_monitor.core import _verify_ngrok_tunnel
        with patch("youtube_monitor.core.uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "ok"
            mock_get.return_value.status_code = 200
            mock_get.return_value.text = "verify_ok"
            self.assertTrue(_verify_ngrok_tunnel("https://one.ngrok.io"))
            mock_get.return_value.status_code = 502
            self.assertFalse(_verify_ngrok_tunnel("https://one.ngrok.io"))
            mock_get.return_value.status_code = 200
            mock_get.return_value.text = "verify_ok"
            self.assertTrue(_verify_ngrok_tunnel("https://one.ngrok.io"))
            self.assertTrue(_verify_ngrok_tunnel("https://two.ngrok.io"))
        verified_logs = [logged_call for logged_call in mock_log.call_args_list if logged_call == call("[Ngrok] Tunnel verified")]
        self.assertEqual(len(verified_logs), 3)


class TestDownloadOnePermanentTemp(unittest.TestCase):
    @patch("youtube_monitor.core._claim_download", return_value=True)
    @patch("youtube_monitor.core.channels_store")
    @patch("youtube_monitor.core._run_ytdlp_download")
    def test_duration_limit_permanent(self, mock_ydl, mock_store, mock_claim):
        from youtube_monitor.core import download_one
        mock_store.get_meta.return_value = {"folder": tempfile.gettempdir(), "profile_name": "test"}
        mock_store.get_config.return_value = {"max_video_minutes": 1}
        mock_ydl.return_value = (None, None, "duration > giới hạn")
        result = download_one("chan_test", "vid_test")
        self.assertFalse(result)

    @patch("youtube_monitor.core._claim_download", return_value=True)
    @patch("youtube_monitor.core.channels_store")
    @patch("youtube_monitor.core._run_ytdlp_download")
    def test_download_exception_temporary(self, mock_ydl, mock_store, mock_claim):
        from youtube_monitor.core import download_one, _clear_retry
        mock_store.get_meta.return_value = {"folder": tempfile.gettempdir(), "profile_name": "test"}
        mock_ydl.side_effect = Exception("timed out")
        result = download_one("chan_test", "vid2")
        self.assertFalse(result)
        _clear_retry("chan_test", "vid2")


class TestIsoToEpoch(unittest.TestCase):
    def test_iso_with_z(self):
        from youtube_monitor.core import iso_to_epoch
        ep = iso_to_epoch("2024-01-15T10:30:00Z")
        self.assertIsNotNone(ep)
        self.assertGreater(ep, 1e9)

    def test_iso_with_offset(self):
        from youtube_monitor.core import iso_to_epoch
        ep = iso_to_epoch("2024-01-15T10:30:00+00:00")
        self.assertIsNotNone(ep)

    def test_invalid(self):
        from youtube_monitor.core import iso_to_epoch
        self.assertIsNone(iso_to_epoch("not-a-date"))


class TestExtractVideoId(unittest.TestCase):
    def test_youtube_url(self):
        from youtube_monitor.core import _extract_video_id
        self.assertEqual(_extract_video_id("https://youtu.be/abc123def"), "abc123def")

    def test_watch_url(self):
        from youtube_monitor.core import _extract_video_id
        self.assertEqual(_extract_video_id("https://www.youtube.com/watch?v=abc123def"), "abc123def")

    def test_shorts_url(self):
        from youtube_monitor.core import _extract_video_id
        self.assertEqual(_extract_video_id("https://www.youtube.com/shorts/abc123def"), "abc123def")

    def test_plain_id(self):
        from youtube_monitor.core import _extract_video_id
        self.assertEqual(_extract_video_id("abc123def"), "abc123def")

    def test_empty(self):
        from youtube_monitor.core import _extract_video_id
        self.assertEqual(_extract_video_id(""), "")


class TestCallbackServerLifecycle(unittest.TestCase):
    @patch("youtube_monitor.core.make_server")
    def test_stop_server_noop_when_none(self, mock_make):
        from youtube_monitor.core import _stop_callback_server
        _stop_callback_server()

    def test_server_stop_clears_globals(self):
        import youtube_monitor.core as core
        core._callback_server = MagicMock()
        core._callback_server_thread = MagicMock()
        core._callback_port = 5000
        core._callback_instance_id = "test"
        core._stop_callback_server()
        self.assertIsNone(core._callback_server)
        self.assertIsNone(core._callback_server_thread)
        self.assertIsNone(core._callback_port)
        self.assertIsNone(core._callback_instance_id)


class TestGetMonitorHealth(unittest.TestCase):
    def setUp(self):
        import youtube_monitor.core as core
        core._monitor_started = False
        core._callback_port = None

    def test_not_running(self):
        from youtube_monitor.core import get_monitor_health
        ok, msg = get_monitor_health()
        self.assertFalse(ok)

    @patch("youtube_monitor.core.requests.get")
    def test_healthy(self, mock_get):
        from youtube_monitor.core import get_monitor_health
        import youtube_monitor.core as core
        core._monitor_started = True
        core._callback_port = 5000
        mock_get.return_value.status_code = 200
        polling = MagicMock()
        polling.is_alive.return_value = True
        polling.name = "youtube-polling-reconciliation"
        with patch("youtube_monitor.core._all_threads", [polling]):
            ok, msg = get_monitor_health()
        self.assertTrue(ok)

    @patch("youtube_monitor.core.requests.get")
    def test_unhealthy(self, mock_get):
        from youtube_monitor.core import get_monitor_health
        import youtube_monitor.core as core
        core._monitor_started = True
        core._callback_port = 5000
        mock_get.side_effect = Exception("timeout")
        ok, msg = get_monitor_health()
        self.assertFalse(ok)


class TestGetStatusExtended(unittest.TestCase):
    def test_get_status_contains_new_fields(self):
        from youtube_monitor.core import get_status
        status = get_status()
        expected_keys = {
            "running", "healthy", "health_msg", "callback_url", "callback_port",
            "callback_verified", "last_callback_post", "subscriptions_total",
            "subscriptions_ok", "subscriptions_degraded", "pending",
        }
        for key in expected_keys:
            self.assertIn(key, status, f"Missing key: {key}")


class TestNormalizeChannelUrl(unittest.TestCase):
    def test_handle_prefix(self):
        from youtube_monitor.core import _normalize_channel_url
        url = _normalize_channel_url("@testchannel")
        self.assertIn("youtube.com", url)

    def test_empty_returns_empty(self):
        from youtube_monitor.core import _normalize_channel_url
        self.assertEqual(_normalize_channel_url(""), "")


class TestStagingCleanupTempDir(unittest.TestCase):
    def test_cleanup_no_crash_on_missing(self):
        from youtube_monitor.core import _staging_dir
        p = Path(tempfile.mkdtemp()) / "nonexistent"
        staging = _staging_dir(str(p))
        self.assertTrue(staging.exists())


class TestResourceAssetsNoFFmpeg(unittest.TestCase):
    def test_ffmpeg_and_ngrok_not_in_resource_assets(self):
        from version import RESOURCE_ASSETS
        self.assertNotIn("FFmpeg", RESOURCE_ASSETS)
        self.assertNotIn("ngrok.exe", RESOURCE_ASSETS)
        self.assertNotIn("service_account.json", RESOURCE_ASSETS)

    def test_resource_assets_still_has_browser(self):
        from version import RESOURCE_ASSETS
        browser = RESOURCE_ASSETS.get("Browser")
        self.assertIsNotNone(browser)
        self.assertEqual(browser["type"], "zip_dir")
        self.assertIn("Browser/donglao-browser-144/chrome.exe", browser["validate"])
        self.assertNotIn("Browser/chromedriver.exe", browser["validate"])


class TestFfmpegSourceUI(unittest.TestCase):
    @patch("youtube_monitor.ffmpeg_helper.check_ffmpeg")
    def test_update_ffmpeg_status_shows_source(self, mock_check):
        mock_check.return_value = (True, "FFmpeg sẵn sàng (System PATH)", "System PATH")
        from youtube_monitor.ui import YouTubeMonitorView
        view = YouTubeMonitorView.__new__(YouTubeMonitorView)
        view.ffmpeg_status_label = MagicMock()
        view._update_ffmpeg_status()
        call_args = view.ffmpeg_status_label.configure.call_args
        self.assertIn("System PATH", call_args[1]["text"])

    @patch("youtube_monitor.ffmpeg_helper.check_ffmpeg")
    def test_update_ffmpeg_status_not_found(self, mock_check):
        mock_check.return_value = (False, "Không tìm thấy ffmpeg", "")
        from youtube_monitor.ui import YouTubeMonitorView
        view = YouTubeMonitorView.__new__(YouTubeMonitorView)
        view.ffmpeg_status_label = MagicMock()
        view._update_ffmpeg_status()
        call_args = view.ffmpeg_status_label.configure.call_args
        self.assertIn("Chưa cài", call_args[1]["text"])


if __name__ == "__main__":
    unittest.main()
