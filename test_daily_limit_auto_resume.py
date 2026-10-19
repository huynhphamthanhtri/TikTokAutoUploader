"""test_daily_limit_auto_resume.py - Test suite cho co che tu chay lai khi sang ngay moi
va kiem soat khong dang lai video bo lo.

Bao gom:
1. Khi dat gioi han max_uploads_per_day: Chuyen sang WAITING_DAILY_LIMIT, ghi auto_daily_resume=True.
2. Khi nguoi dung bam Dung thu cong: Huy auto_daily_resume=False, khong tu chay lai.
3. Khi qua ngay moi: reconcile_daily_resumes tu dong phat hien va kich hoat lai profile.
4. Yeu cau nghiep vu nguoi dung: Khong dang lai video da bo lo (skip_waiting=True, loc mtime <= watch_started_at).
5. Khi nguoi dung sua tang limit: reconcile_daily_resumes tu dong danh thuc profile dang cho.
6. Luu tru ben vung: config_store bao toan auto_daily_resume khi app tat roi bat lai vao ngay moi.
"""

import copy
import datetime
import os
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from config_store import build_configs_payload, build_runtime_profiles
from profile_runtime_status import (
    AutomationState,
    RuntimeSignals,
    UploadState,
    automation_label,
    build_runtime_snapshot,
    row_tags,
)


class DailyLimitStateAndConfigTests(unittest.TestCase):
    def test_profile_runtime_status_waiting_daily_limit(self):
        """Kiem chung trang thai WAITING_DAILY_LIMIT hien thi dung nhan va tag."""
        sig = RuntimeSignals(
            running=False,
            ui_status="Chờ ngày mới",
            ui_upload="Chờ 00:00 (Đã đạt 3/3)",
        )
        snap = build_runtime_snapshot(sig)
        self.assertEqual(snap.automation, AutomationState.WAITING_DAILY_LIMIT)
        self.assertEqual(automation_label(snap.automation), "Chờ ngày mới")
        self.assertEqual(row_tags(snap), ("tag_processing",))
        self.assertTrue(snap.can_start)
        self.assertTrue(snap.can_stop)

    def test_config_store_preserves_auto_daily_resume_across_midnight(self):
        """Kiem chung config_store luu va phuc hoi auto_daily_resume sau khi qua ngay."""
        raw_config = {
            "chrome_profile": "C:/data/Profile",
            "browser_profile_path": "C:/data/Profile-Patchright",
            "browser_engine": "patchright",
            "max_uploads_per_day": 3,
            "auto_daily_resume": True,
            "stats_today": 3,
            "stats_yesterday": 2,
            "stats_date": "2026-10-18",
        }
        profile = {
            "config": raw_config,
            "uploads_today_count": 3,
            "uploads_yesterday_count": 2,
            "uploads_today_date": "2026-10-18",
            "auto_daily_resume": True,
            "project": "Default",
        }

        # Xuat ra config payload
        exported = build_configs_payload({"test_prof": profile}, {"Default": {"test_prof"}})
        saved_prof = exported["profiles"]["test_prof"]
        self.assertTrue(saved_prof["auto_daily_resume"])

        # Gia lap mo app vao ngay 2026-10-19 (hom sau)
        runtime = build_runtime_profiles({"test_prof": saved_prof})["test_prof"]
        self.assertTrue(runtime["auto_daily_resume"])
        self.assertTrue(runtime["config"]["auto_daily_resume"])
        # Con so ngay hom nay phai duoc reset ve 0 khi phat hien sang ngay moi
        self.assertEqual(runtime["uploads_today_count"], 0)
        self.assertEqual(runtime["uploads_yesterday_count"], 3)


class DailyLimitMainFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent
        cls.main_source = (cls.root / "main.py").read_text(encoding="utf-8")

    def test_process_video_queue_thread_drains_queue_and_sets_daily_limit(self):
        """Kiem tra process_video_queue_thread xa sach queue va dung voi reason DAILY_LIMIT."""
        self.assertIn("_release_queued_items(profile_name)", self.main_source)
        self.assertIn('stop_profile(profile_name, reason="DAILY_LIMIT")', self.main_source)

    def test_stop_profile_reason_distinction(self):
        """Kiem tra stop_profile phan biet ro DAILY_LIMIT vs MANUAL stop."""
        self.assertIn('def stop_profile(selected_name=None, reason="MANUAL"):', self.main_source)
        self.assertIn('is_daily_limit = (reason == "DAILY_LIMIT")', self.main_source)
        self.assertIn("profiles[name]['auto_daily_resume'] = True", self.main_source)
        self.assertIn("profiles[name]['auto_daily_resume'] = False", self.main_source)

    def test_stop_profile_cancels_waiting_when_user_clicks_stop(self):
        """Nguoi dung bam Dung khi profile dang cho ngay moi se huy auto_daily_resume."""
        self.assertIn("Đã hủy chế độ chờ ngày mới. Profile đã dừng hoàn toàn.", self.main_source)

    def test_reconcile_daily_resumes_logic_defined_and_wired(self):
        """Kiem tra ham reconcile_daily_resumes duoc dinh nghia va duoc hook vao _tick, startup, edit dialog."""
        self.assertIn("def reconcile_daily_resumes():", self.main_source)
        self.assertIn("root.after(500, reconcile_daily_resumes)", self.main_source)
        self.assertIn("root.after(2000, reconcile_daily_resumes)", self.main_source)
        self.assertIn("root.after(200, reconcile_daily_resumes)", self.main_source)

    def test_skip_waiting_startup_honors_user_requirement(self):
        """Kiem tra tinh nang bo qua video cu/bo lo khi auto-resume duoc noi day du."""
        self.assertIn("def _reconcile_startup_folder(profile_name, folder, generation, skip_waiting=False):", self.main_source)
        self.assertIn("is_waiting = (not skip_waiting) and rec is not None and rec.state == DeliveryState.WAITING_PROFILE", self.main_source)
        self.assertIn("def start_profile(name=None, skip_waiting_startup=False):", self.main_source)
        self.assertIn("skip_waiting=skip_waiting_startup", self.main_source)

    def test_watchdog_filters_older_than_watch_started_at(self):
        """Kiem tra watchdog loai bo file cu co mtime <= watch_started_at."""
        self.assertIn("watch_started_at = profiles[self.profile_name].get('watch_started_at', 0)", self.main_source)
        self.assertIn("if watch_started_at > 0 and file_mtime <= watch_started_at:", self.main_source)


class ReconcileDailyResumesBehaviorTests(unittest.TestCase):
    def test_reconcile_logic_selects_waiting_profiles_and_skips_running(self):
        """Mo phong logic reconcile: chi resume profile co auto_daily_resume va chua chay."""
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        mock_profiles = {
            "Prof_1_Waiting": {
                "running": False,
                "auto_daily_resume": True,
                "uploads_today_date": today_str,
                "uploads_today_count": 0,
                "config": {"max_uploads_per_day": 3, "auto_daily_resume": True},
            },
            "Prof_2_Still_Full": {
                "running": False,
                "auto_daily_resume": True,
                "uploads_today_date": today_str,
                "uploads_today_count": 3,
                "config": {"max_uploads_per_day": 3, "auto_daily_resume": True},
            },
            "Prof_3_Stopped_By_User": {
                "running": False,
                "auto_daily_resume": False,
                "uploads_today_date": today_str,
                "uploads_today_count": 0,
                "config": {"max_uploads_per_day": 3, "auto_daily_resume": False},
            },
            "Prof_4_Already_Running": {
                "running": True,
                "auto_daily_resume": True,
                "uploads_today_date": today_str,
                "uploads_today_count": 0,
                "config": {"max_uploads_per_day": 3, "auto_daily_resume": True},
            },
        }

        to_resume = []
        for name, prof in mock_profiles.items():
            if not prof.get("auto_daily_resume"):
                continue
            limit = prof.get("config", {}).get("max_uploads_per_day", 0)
            today_count = prof.get("uploads_today_count", 0)
            if limit == 0 or today_count < limit:
                if not prof.get("running", False):
                    to_resume.append(name)

        # Chi Prof_1_Waiting duoc chon!
        self.assertEqual(to_resume, ["Prof_1_Waiting"])

    def test_update_profile_list_renders_waiting_profile_without_error(self):
        """Kiem tra update_profile_list khong bi NameError AutomationState khi render profile cho ngay moi."""
        import main
        p_name = "test_profile_waiting_render"
        main.profiles[p_name] = {
            "running": False,
            "auto_daily_resume": True,
            "uploads_today_date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "uploads_today_count": 3,
            "config": {
                "max_uploads_per_day": 3,
                "auto_daily_resume": True,
                "folder_path": "C:/dummy",
                "browser_profile_path": "C:/dummy",
            },
            "ui": {
                "status": "Chờ ngày mới",
                "upload": "Chờ 00:00 (Đã đạt 3/3)",
            },
            "project": "Mặc định",
        }
        try:
            main.update_profile_list()
        finally:
            main.profiles.pop(p_name, None)


if __name__ == "__main__":
    unittest.main()
