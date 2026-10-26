# -*- coding: utf-8 -*-
"""Unit tests for Phase 1: Core API & Data Architecture with Codex review additions."""
import datetime
import math
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from youtube_monitor.core import (
    ChannelsStore,
    evaluate_channel_activity,
    fetch_channel_latest_video_api,
    format_time_ago,
    set_channel_active,
)


class TestPhase1ChannelActivity(unittest.TestCase):
    def test_format_time_ago(self):
        self.assertEqual(format_time_ago(None), "Chưa có dữ liệu")
        self.assertEqual(format_time_ago(-10), "Vừa xong")
        self.assertEqual(format_time_ago(45), "Vừa xong")
        self.assertEqual(format_time_ago(120), "2 phút trước")
        self.assertEqual(format_time_ago(3600 * 5), "5 giờ trước")
        self.assertEqual(format_time_ago(3600 * 25), "Hôm qua")
        self.assertEqual(format_time_ago(3600 * 24 * 3), "3 ngày trước")
        self.assertEqual(format_time_ago(3600 * 24 * 65), "2 tháng trước")

    def test_evaluate_channel_activity_classification(self):
        now = 1700000000.0

        # Case 1: Normal - 12 hours ago (threshold = 48)
        meta_normal = {
            "channel_id": "UC_NORM",
            "title": "Normal Channel",
            "last_pub_utc": now - (12 * 3600),
            "added_at": "2023-11-01T00:00:00+00:00",
        }
        res_normal = evaluate_channel_activity(meta_normal, now_utc=now, threshold_hours=48.0)
        self.assertEqual(res_normal["category"], "IN_THRESHOLD")
        self.assertEqual(res_normal["data_source"], "MONITOR_WATERMARK")
        self.assertFalse(res_normal["is_recently_added"])

        # Case 2: Boundary test - Exactly 48 hours is IN_THRESHOLD
        meta_exact = {
            "channel_id": "UC_EXACT",
            "title": "Exact Channel",
            "last_pub_utc": now - (48 * 3600),
        }
        res_exact = evaluate_channel_activity(meta_exact, now_utc=now, threshold_hours=48.0)
        self.assertEqual(res_exact["category"], "IN_THRESHOLD")

        # Case 3: Stale - 48 hours + 1 second is EXCEEDED_THRESHOLD
        meta_stale = {
            "channel_id": "UC_STALE",
            "title": "Stale Channel",
            "last_pub_utc": now - (48 * 3600 + 1),
        }
        res_stale = evaluate_channel_activity(meta_stale, now_utc=now, threshold_hours=48.0)
        self.assertEqual(res_stale["category"], "EXCEEDED_THRESHOLD")
        self.assertIn("⚠ Quá 48h", res_stale["age_display"])

        # Case 4: No data - last_pub_utc is None
        meta_nodata = {
            "channel_id": "UC_NODATA",
            "title": "No Data Channel",
            "last_pub_utc": None,
        }
        res_nodata = evaluate_channel_activity(meta_nodata, now_utc=now, threshold_hours=48.0)
        self.assertEqual(res_nodata["category"], "NO_DATA")
        self.assertIsNone(res_nodata["latest_pub_utc"])
        self.assertEqual(res_nodata["age_display"], "Chưa có dữ liệu")

        # Case 5: Priority of latest_observed_pub_utc over last_pub_utc
        meta_observed = {
            "channel_id": "UC_OBS",
            "last_pub_utc": now - (100 * 3600),  # Old watermark
            "latest_observed_pub_utc": now - (5 * 3600),  # Recent observed API video
            "latest_observed_video_id": "VID_NEW",
            "latest_observed_video_title": "Fresh Video",
        }
        res_obs = evaluate_channel_activity(meta_observed, now_utc=now, threshold_hours=48.0)
        self.assertEqual(res_obs["category"], "IN_THRESHOLD")
        self.assertEqual(res_obs["data_source"], "API_OBSERVATION")
        self.assertEqual(res_obs["latest_video_id"], "VID_NEW")
        self.assertEqual(res_obs["latest_video_title"], "Fresh Video")

    def test_evaluate_channel_activity_recently_added_and_future_boundary(self):
        now = 1700000000.0
        # Added 5 hours ago -> is_recently_added == True
        added_dt = datetime.datetime.fromtimestamp(now - (5 * 3600), datetime.timezone.utc)
        meta = {
            "channel_id": "UC_NEW_CH",
            "title": "New Channel",
            "added_at": added_dt.isoformat(),
            "last_pub_utc": now - (100 * 3600),
        }
        res = evaluate_channel_activity(meta, now_utc=now, threshold_hours=48.0)
        self.assertTrue(res["is_recently_added"])
        self.assertEqual(res["category"], "EXCEEDED_THRESHOLD")

        # Future added_at (e.g. clock skewed to 2099) must NOT be considered recently added
        meta_future = {
            "channel_id": "UC_FUT",
            "added_at": "2099-01-01T00:00:00+00:00",
            "last_pub_utc": now - (100 * 3600),
        }
        res_future = evaluate_channel_activity(meta_future, now_utc=now, threshold_hours=48.0)
        self.assertFalse(res_future["is_recently_added"])

    def test_channels_store_observed_audit_persistence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "test_channels.json"
            store = ChannelsStore(json_path)
            store.add_channel("UC_AUDIT_1", "/tmp/ch1", profile_name="ProfileX")

            # Initial state
            meta = store.get_meta("UC_AUDIT_1")
            self.assertEqual(meta["latest_observed_video_id"], "")
            self.assertIsNone(meta["latest_observed_pub_utc"])

            # Update observed audit
            ok = store.update_observed_audit(
                "UC_AUDIT_1",
                video_id="VID_ABC_123",
                video_title="Great Test Video",
                pub_utc=1700123456.0,
                status="OK",
                error="",
                audit_time=1700123500.0,
            )
            self.assertTrue(ok)
            store.save_now()

            # Verify watermark last_pub_utc was NOT changed
            self.assertIsNone(store.get_meta("UC_AUDIT_1").get("last_pub_utc"))

            # Reload into new store
            store2 = ChannelsStore(json_path)
            store2.load()
            meta2 = store2.get_meta("UC_AUDIT_1")
            self.assertEqual(meta2["latest_observed_video_id"], "VID_ABC_123")
            self.assertEqual(meta2["latest_observed_video_title"], "Great Test Video")
            self.assertEqual(meta2["latest_observed_pub_utc"], 1700123456.0)
            self.assertEqual(meta2["last_api_audit_status"], "OK")
            self.assertEqual(meta2["last_api_audit_success_at"], 1700123500.0)

    def test_channels_store_out_of_order_audit_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "test_channels.json"
            store = ChannelsStore(json_path)
            store.add_channel("UC_OOO", "/tmp/ch_ooo")

            # First audit completes at t=200
            ok1 = store.update_observed_audit(
                "UC_OOO",
                video_id="VID_200",
                video_title="Newer Video",
                pub_utc=1700000200.0,
                status="OK",
                audit_time=200.0,
            )
            self.assertTrue(ok1)
            self.assertEqual(store.get_meta("UC_OOO")["latest_observed_video_id"], "VID_200")

            # Stale audit that was delayed finishes at t=100 -> must be rejected!
            ok2 = store.update_observed_audit(
                "UC_OOO",
                video_id="VID_100",
                video_title="Stale Video",
                pub_utc=1700000100.0,
                status="OK",
                audit_time=100.0,
            )
            self.assertFalse(ok2)
            # Metadata still has newer VID_200
            self.assertEqual(store.get_meta("UC_OOO")["latest_observed_video_id"], "VID_200")

    def test_channels_store_empty_audit_behavior(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "test_channels.json"
            store = ChannelsStore(json_path)
            store.add_channel("UC_EMPTY", "/tmp/ch_empty")

            store.update_observed_audit(
                "UC_EMPTY",
                video_id="OLD_VID",
                video_title="Old Title",
                pub_utc=1700000000.0,
                status="OK",
                audit_time=100.0,
            )
            self.assertEqual(store.get_meta("UC_EMPTY")["latest_observed_video_id"], "OLD_VID")

            # Audit returns EMPTY playlist at t=200
            store.update_observed_audit(
                "UC_EMPTY",
                status="EMPTY",
                audit_time=200.0,
            )
            meta = store.get_meta("UC_EMPTY")
            self.assertEqual(meta["last_api_audit_status"], "EMPTY")
            self.assertEqual(meta["latest_observed_video_id"], "")
            self.assertIsNone(meta["latest_observed_pub_utc"])
            self.assertEqual(meta["last_api_audit_success_at"], 200.0)

    def test_channels_store_set_active_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "test_channels.json"
            store = ChannelsStore(json_path)
            store.add_channel("UC_ACT_1", "/tmp/act1")

            # Active is True by default
            self.assertTrue(store.get_meta("UC_ACT_1")["active"])

            # 1. Set to False -> UPDATED
            res1 = store.set_active("UC_ACT_1", False)
            self.assertEqual(res1, "UPDATED")
            self.assertFalse(store.get_meta("UC_ACT_1")["active"])

            # 2. Set to False again -> UNCHANGED
            res2 = store.set_active("UC_ACT_1", False)
            self.assertEqual(res2, "UNCHANGED")

            # 3. Non-existent channel -> NOT_FOUND
            res3 = store.set_active("UC_NON_EXISTENT", False)
            self.assertEqual(res3, "NOT_FOUND")

    def test_fetch_channel_latest_video_api_success(self):
        mock_resp = {
            "items": [{
                "snippet": {
                    "title": "Latest Episode 99",
                    "publishedAt": "2023-11-10T10:00:00Z",
                },
                "contentDetails": {
                    "videoId": "VID_EP99",
                    "videoPublishedAt": "2023-11-10T10:05:00Z",
                }
            }]
        }

        with patch("youtube_monitor.core.execute_api_request", return_value=mock_resp):
            res = fetch_channel_latest_video_api("UCAAAAAAAAAAAAAAAAAAAAAA", "UUAAAAAAAAAAAAAAAAAAAAAA")
            self.assertEqual(res["status"], "OK")
            self.assertEqual(res["video_id"], "VID_EP99")
            self.assertEqual(res["video_title"], "Latest Episode 99")
            self.assertEqual(res["published_iso"], "2023-11-10T10:05:00Z")
            self.assertIsNotNone(res["published_utc"])

    def test_fetch_channel_latest_video_api_empty_playlist(self):
        mock_resp = {"items": []}
        with patch("youtube_monitor.core.execute_api_request", return_value=mock_resp):
            res = fetch_channel_latest_video_api("UCAAAAAAAAAAAAAAAAAAAAAA", "UUAAAAAAAAAAAAAAAAAAAAAA")
            self.assertEqual(res["status"], "EMPTY")
            self.assertEqual(res["video_id"], "")
            self.assertIsNone(res["published_utc"])
            self.assertIn("chưa có video", res["error"])

    def test_fetch_channel_latest_video_api_malformed_response(self):
        with patch("youtube_monitor.core.execute_api_request", return_value="Not a dictionary"):
            res = fetch_channel_latest_video_api("UCAAAAAAAAAAAAAAAAAAAAAA", "UUAAAAAAAAAAAAAAAAAAAAAA")
            self.assertEqual(res["status"], "ERROR")
            self.assertIn("không đúng cấu trúc", res["error"])

    def test_fetch_channel_latest_video_api_error(self):
        with patch("youtube_monitor.core.execute_api_request", side_effect=RuntimeError("Google 503 backend error")):
            res = fetch_channel_latest_video_api("UCAAAAAAAAAAAAAAAAAAAAAA", "UUAAAAAAAAAAAAAAAAAAAAAA")
            self.assertEqual(res["status"], "ERROR")
            self.assertIn("Google 503", res["error"])
