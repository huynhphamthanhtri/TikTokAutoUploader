"""
test_tiktok_dedup_engine.py - Unit test suite cho Dedup Watchdog Engine.
Bao phủ 100% logic phân tích video trùng lặp, shadowban, cơ sở dữ liệu SQLite,
API client và worker giám sát tự động.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tiktok_dedup_engine import (
    DedupConfig,
    DedupDatabase,
    DedupWatchdogWorker,
    ProfileContext,
    TikTokDedupApiClient,
    VideoItemAnalysis,
    analyze_tiktok_video,
    format_cookie_header,
)


class TestAnalyzeTikTokVideo(unittest.TestCase):
    """Kiểm tra thuật toán nhận diện video trùng lặp và shadowban."""

    def test_original_video_when_group_id_matches_aweme_id(self):
        item = {
            "id": "7481111111111111111",
            "group_id": "7481111111111111111",
            "desc": "Original video post",
            "createTime": 1720000000,
            "stats": {"playCount": 1500},
            "display_penalty_type": 0,
            "in_reviewing": 0,
        }
        res = analyze_tiktok_video(item)
        self.assertEqual(res.aweme_id, "7481111111111111111")
        self.assertEqual(res.group_id, "7481111111111111111")
        self.assertFalse(res.is_dup)
        self.assertFalse(res.is_shadow)
        self.assertEqual(res.play_count, 1500)
        self.assertEqual(res.original_url, "https://www.tiktok.com/@tiktok/video/7481111111111111111")

    def test_duplicate_video_when_group_id_differs_from_aweme_id(self):
        item = {
            "aweme_id": "7482222222222222222",
            "group_id": "7481111111111111111",  # Gốc của người khác
            "desc": "Reup video clip",
            "create_time": 1720000000,
            "statistics": {"play_count": 800},
        }
        res = analyze_tiktok_video(item)
        self.assertEqual(res.aweme_id, "7482222222222222222")
        self.assertEqual(res.group_id, "7481111111111111111")
        self.assertTrue(res.is_dup)
        self.assertEqual(res.original_url, "https://www.tiktok.com/@tiktok/video/7481111111111111111")

    def test_missing_group_id_defaults_to_original(self):
        item = {
            "id": "7483333333333333333",
            "desc": "No group_id provided",
        }
        res = analyze_tiktok_video(item)
        self.assertEqual(res.aweme_id, "7483333333333333333")
        self.assertEqual(res.group_id, "7483333333333333333")
        self.assertFalse(res.is_dup)

    def test_under_48_hours_window(self):
        now = 1720000000
        # 10 giờ trước (< 48h)
        item_fresh = {"id": "1", "create_time": now - (10 * 3600)}
        res_fresh = analyze_tiktok_video(item_fresh, now_timestamp=now)
        self.assertTrue(res_fresh.is_under_48h)

        # 50 giờ trước (> 48h)
        item_old = {"id": "2", "create_time": now - (50 * 3600)}
        res_old = analyze_tiktok_video(item_old, now_timestamp=now)
        self.assertFalse(res_old.is_under_48h)

    def test_shadowban_and_penalty_detection(self):
        # 1. in_reviewing == 1 (giam duyệt)
        item_reviewing = {"id": "1", "in_reviewing": 1, "display_penalty_type": 0}
        res1 = analyze_tiktok_video(item_reviewing)
        self.assertTrue(res1.is_shadow)
        self.assertIn("in_reviewing=1", res1.shadow_reason or "")

        # 2. display_penalty_type != 0 (án phạt hiển thị)
        item_penalty = {"id": "2", "in_reviewing": 0, "display_penalty_type": 102}
        res2 = analyze_tiktok_video(item_penalty)
        self.assertTrue(res2.is_shadow)
        self.assertIn("102", res2.shadow_reason or "")

        # 3. Bình thường
        item_normal = {"id": "3", "in_reviewing": 0, "display_penalty_type": 0}
        res3 = analyze_tiktok_video(item_normal)
        self.assertFalse(res3.is_shadow)
        self.assertIsNone(res3.shadow_reason)


class TestCookieHeaderFormatter(unittest.TestCase):
    """Kiểm tra format cookie cho HTTP Header."""

    def test_format_from_json_string(self):
        cookies_json = json.dumps([
            {"name": "sessionid", "value": "abc123xyz"},
            {"name": "tt_csrf_token", "value": "token_val"},
        ])
        header = format_cookie_header(cookies_json)
        self.assertIn("sessionid=abc123xyz", header)
        self.assertIn("tt_csrf_token=token_val", header)

    def test_format_from_raw_string(self):
        raw = "sessionid=raw_session_value; other=val"
        self.assertEqual(format_cookie_header(raw), raw)

    def test_format_empty(self):
        self.assertEqual(format_cookie_header(""), "")
        self.assertEqual(format_cookie_header([]), "")


class TestDedupDatabase(unittest.TestCase):
    """Kiểm tra lưu trữ và truy vấn SQLite."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.tmp_dir.name) / "test_dedup.db"
        self.db = DedupDatabase(self.db_file)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_watchlist_operations(self):
        self.db.register_profile("p1", "channel_1", next_check_at=1000)
        self.db.register_profile("p2", "channel_2", next_check_at=2000)

        watchlist = self.db.get_all_watchlist()
        self.assertEqual(len(watchlist), 2)

        # Due profiles at t=1500
        due = self.db.get_due_profiles(now_ms=1500)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["profile_id"], "p1")

        # Pause p1
        self.db.set_profile_paused("p1", True)
        due_after_pause = self.db.get_due_profiles(now_ms=1500)
        self.assertEqual(len(due_after_pause), 0)

        # Update next check
        self.db.update_next_check("p2", 500)
        due_p2 = self.db.get_due_profiles(now_ms=600)
        self.assertEqual(len(due_p2), 1)
        self.assertEqual(due_p2[0]["profile_id"], "p2")

        # Delete from watchlist
        self.db.remove_from_watchlist("p1")
        self.assertEqual(len(self.db.get_all_watchlist()), 1)

    def test_audit_log_operations(self):
        # Insert log
        inserted = self.db.log_detected(
            profile_id="p1",
            channel="channel_1",
            aweme_id="video_1",
            group_id="orig_video_1",
            desc="Test duplicate",
            create_time=1700000000,
            action="delete",
            status="detected",
        )
        self.assertTrue(inserted)

        # Deduplicate same aweme_id for profile
        inserted_again = self.db.log_detected(
            profile_id="p1",
            channel="channel_1",
            aweme_id="video_1",
            group_id="orig_video_1",
            desc="Test duplicate",
            create_time=1700000000,
            action="delete",
            status="detected",
        )
        self.assertFalse(inserted_again)

        # Update status
        now_ms = int(time.time() * 1000)
        self.db.update_log_status("p1", "video_1", status="deleted", deleted_at=now_ms)

        logs = self.db.get_audit_logs(limit=10)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "deleted")
        self.assertEqual(logs[0]["deleted_at"], now_ms)
        self.assertEqual(logs[0]["group_id"], "orig_video_1")


class TestTikTokDedupApiClient(unittest.TestCase):
    """Kiểm tra gọi API TikTok với mock session."""

    def test_fetch_post_feed_success(self):
        ctx = ProfileContext(profile_id="p1", tiktok_account="chan_1", sec_uid="sec_123")
        client = TikTokDedupApiClient(ctx)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "itemList": [
                {"id": "v1", "desc": "Video 1"},
                {"id": "v2", "desc": "Video 2"},
            ]
        }

        with patch.object(client.session, "get", return_value=mock_resp) as mock_get:
            items = client.fetch_post_feed(count=20)
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["id"], "v1")
            mock_get.assert_called_once()
            args, kwargs = mock_get.call_args
            self.assertIn("https://www.tiktok.com/api/post/item_list/", args[0])
            self.assertEqual(kwargs["params"]["secUid"], "sec_123")
            self.assertEqual(kwargs["params"]["count"], 20)

    def test_delete_post_success(self):
        ctx = ProfileContext(profile_id="p1", tiktok_account="chan_1")
        client = TikTokDedupApiClient(ctx)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": 0, "status_msg": "success"}

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            ok = client.delete_post("748123456789")
            self.assertTrue(ok)
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertIn("/api/web/item/delete", args[0])
            self.assertEqual(kwargs["json"], {"item_id": "748123456789"})

    def test_set_post_private_success(self):
        ctx = ProfileContext(profile_id="p1", tiktok_account="chan_1")
        client = TikTokDedupApiClient(ctx)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status_code": 0}

        with patch.object(client.session, "post", return_value=mock_resp) as mock_post:
            ok = client.set_post_private("748123456789")
            self.assertTrue(ok)
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertIn("/api/web/item/privacy", args[0])
            self.assertEqual(kwargs["json"], {"item_id": "748123456789", "privacy": 1})


class TestDedupWatchdogWorker(unittest.TestCase):
    """Kiểm tra quy trình chạy của Watchdog Worker."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db = DedupDatabase(Path(self.tmp_dir.name) / "test_worker.db")
        self.profile = ProfileContext(profile_id="p1", tiktok_account="chan_1", sec_uid="sec_1")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_scan_profile_dry_run_does_not_call_delete(self):
        cfg = DedupConfig(enabled=True, action="dry_run", action_delay_seconds=0.01)
        worker = DedupWatchdogWorker(self.db, cfg, profile_provider=lambda: [self.profile])

        # Giả lập 2 video: 1 gốc, 1 trùng < 48h
        now = int(time.time())
        fake_feed = [
            {"id": "v_orig", "group_id": "v_orig", "create_time": now - 3600},
            {"id": "v_dup", "group_id": "v_other_root", "create_time": now - 7200, "desc": "Duplicate clip"},
        ]

        with patch("tiktok_dedup_engine.TikTokDedupApiClient.fetch_post_feed", return_value=fake_feed), \
             patch("tiktok_dedup_engine.TikTokDedupApiClient.delete_post") as mock_del:

            detected = worker.scan_profile_now(self.profile)
            self.assertEqual(detected, 1)
            mock_del.assert_not_called()  # Dry run không gọi delete

            logs = self.db.get_audit_logs()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["aweme_id"], "v_dup")
            self.assertEqual(logs[0]["action"], "dry_run")
            self.assertEqual(logs[0]["status"], "detected")

    def test_scan_profile_live_delete_executes_delete_and_updates_log(self):
        cfg = DedupConfig(enabled=True, action="delete", action_delay_seconds=0.01)
        worker = DedupWatchdogWorker(self.db, cfg, profile_provider=lambda: [self.profile])

        now = int(time.time())
        fake_feed = [
            {"id": "v_dup", "group_id": "v_other_root", "create_time": now - 7200, "desc": "Dup to delete"},
        ]

        with patch("tiktok_dedup_engine.TikTokDedupApiClient.fetch_post_feed", return_value=fake_feed), \
             patch("tiktok_dedup_engine.TikTokDedupApiClient.delete_post", return_value=True) as mock_del:

            detected = worker.scan_profile_now(self.profile)
            self.assertEqual(detected, 1)
            mock_del.assert_called_once_with("v_dup")

            logs = self.db.get_audit_logs()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["status"], "deleted")
            self.assertIsNotNone(logs[0]["deleted_at"])

    def test_scan_profile_live_hide_executes_set_post_private(self):
        cfg = DedupConfig(enabled=True, action="hide", action_delay_seconds=0.01)
        worker = DedupWatchdogWorker(self.db, cfg, profile_provider=lambda: [self.profile])

        now = int(time.time())
        fake_feed = [
            {"id": "v_dup", "group_id": "v_other_root", "create_time": now - 7200, "desc": "Dup to hide"},
        ]

        with patch("tiktok_dedup_engine.TikTokDedupApiClient.fetch_post_feed", return_value=fake_feed), \
             patch("tiktok_dedup_engine.TikTokDedupApiClient.set_post_private", return_value=True) as mock_hide:

            detected = worker.scan_profile_now(self.profile)
            self.assertEqual(detected, 1)
            mock_hide.assert_called_once_with("v_dup")

            logs = self.db.get_audit_logs()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["status"], "hidden")

    def test_worker_start_stop_lifecycle(self):
        cfg = DedupConfig(enabled=False)
        worker = DedupWatchdogWorker(self.db, cfg, profile_provider=lambda: [])
        self.assertFalse(worker.is_running())

        worker.start()
        self.assertTrue(worker.is_running())

        worker.stop()
        self.assertFalse(worker.is_running())


class TestDedupWatchdogView(unittest.TestCase):
    """Kiểm tra giao diện DedupWatchdogView."""

    def setUp(self):
        import customtkinter as ctk
        try:
            self.root = ctk.CTk()
            self.root.withdraw()
        except Exception:
            self.root = None

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db = DedupDatabase(Path(self.tmp_dir.name) / "test_ui.db")
        self.cfg = DedupConfig(enabled=False)
        self.worker = DedupWatchdogWorker(self.db, self.cfg, profile_provider=lambda: [])

    def tearDown(self):
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
        self.tmp_dir.cleanup()

    def test_view_creation_and_subtab_switching(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

        from tiktok_dedup_ui import DedupWatchdogView

        view = DedupWatchdogView(
            self.root,
            db=self.db,
            worker=self.worker,
            get_profiles_func=lambda: [ProfileContext("p1", "test_chan")],
        )
        try:
            self.assertIsNotNone(view.log_tree)
            self.assertIsNotNone(view.watch_tree)
            self.assertIsNotNone(view.btn_toggle)

            # Check subtab switch to watchlist
            view._switch_subtab("watchlist")
            self.assertEqual(view._active_subtab, "watchlist")
            self.assertTrue(view.watch_container.winfo_manager())
            self.assertFalse(view.logs_container.winfo_manager())

            # Check subtab switch back to logs
            view._switch_subtab("logs")
            self.assertEqual(view._active_subtab, "logs")
            self.assertTrue(view.logs_container.winfo_manager())
            self.assertFalse(view.watch_container.winfo_manager())

            # Check action change
            view._on_action_changed("Ẩn Private (Chỉ mình tôi)")
            self.assertEqual(self.worker.config.action, "hide")

            # Check interval change
            view._on_interval_changed("120 phút")
            self.assertEqual(self.worker.config.interval_minutes, 120)
        finally:
            view.destroy()


if __name__ == "__main__":
    unittest.main()
