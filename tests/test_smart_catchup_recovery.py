import unittest
from unittest.mock import patch, MagicMock
import datetime
from pathlib import Path
import tempfile

from schedule_manager import ScheduleManager


class TestSmartCatchupRecovery(unittest.TestCase):
    """Kiểm thử thuật toán Smart Staggered Catch-up khi mở máy."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_catchup.db"
        self.mgr = ScheduleManager(storage_file=str(self.db_path))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_catchup_single_missed_video(self):
        """Khi có 1 video bị lỡ hẹn do tắt máy, video đó phải được đặt giờ về 'now' để đăng bù ngay."""
        now = datetime.datetime(2026, 11, 6, 14, 0, 0)
        missed_epoch = int((now - datetime.timedelta(hours=3)).timestamp())

        task = {
            "id": "task_missed_single",
            "profile_name": "AUTO 31",
            "video_path": str(Path(self.tmp_dir.name) / "v1.mp4"),
            "video_filename": "v1.mp4",
            "execution_mode": "LOCAL_QUEUE",
            "scheduled_time": (now - datetime.timedelta(hours=3)).isoformat(),
            "scheduled_epoch": missed_epoch,
            "status": "PENDING",
        }
        self.mgr.add_tasks([task])
        self.mgr.update_task("task_missed_single", {
            "execution_mode": "LOCAL_QUEUE",
            "status": "PENDING",
            "scheduled_epoch": missed_epoch,
        })

        class MockDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with patch("schedule_manager.datetime.datetime", MockDateTime):
            recovered = self.mgr.recover_missed_tasks(max_daily_posts=2)

        self.assertEqual(len(recovered), 1)
        rec_task = recovered[0]
        self.assertEqual(rec_task["id"], "task_missed_single")
        # Video 1 phải được đặt epoch đúng bằng now_epoch để đăng ngay lập tức
        self.assertEqual(rec_task["scheduled_epoch"], int(now.timestamp()))
        self.assertIn("Đăng bù ngay khi mở máy", rec_task["error_message"])

    def test_catchup_five_missed_videos_staggered(self):
        """Khi dồn ứ 5 video, Video 1 đăng ngay, các video còn lại dàn đều tối đa 2 bài/ngày."""
        now = datetime.datetime(2026, 11, 6, 10, 0, 0)  # 10h sáng
        missed_epoch = int((now - datetime.timedelta(days=2)).timestamp())

        tasks = []
        for i in range(1, 6):
            tasks.append({
                "id": f"task_missed_{i}",
                "profile_name": "AUTO 31",
                "video_path": str(Path(self.tmp_dir.name) / f"v{i}.mp4"),
                "video_filename": f"v{i}.mp4",
                "execution_mode": "LOCAL_QUEUE",
                "scheduled_time": (now - datetime.timedelta(days=2)).isoformat(),
                "scheduled_epoch": missed_epoch + i * 60,
                "status": "PENDING",
            })
        self.mgr.add_tasks(tasks)
        for i in range(1, 6):
            self.mgr.update_task(f"task_missed_{i}", {
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
                "scheduled_epoch": missed_epoch + i * 60,
            })

        class MockDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with patch("schedule_manager.datetime.datetime", MockDateTime):
            recovered = self.mgr.recover_missed_tasks(max_daily_posts=2)

        self.assertEqual(len(recovered), 5)

        # Video 1: Đăng bù ngay
        self.assertEqual(recovered[0]["scheduled_epoch"], int(now.timestamp()))
        self.assertIn("Đăng bù ngay khi mở máy", recovered[0]["error_message"])

        # Kiểm tra phân bổ ngày: Không ngày nào có quá 2 bài!
        dates_count = {}
        for t in recovered:
            dt = datetime.datetime.fromisoformat(t["scheduled_time"])
            d_key = dt.date()
            dates_count[d_key] = dates_count.get(d_key, 0) + 1

        for d_key, cnt in dates_count.items():
            self.assertLessEqual(cnt, 2, f"Ngày {d_key} vượt quá giới hạn 2 bài/ngày: có {cnt} bài!")

        # Phải phân bổ qua 3 ngày: hôm nay (2 bài), ngày mai (2 bài), ngày kia (1 bài)
        self.assertEqual(dates_count[datetime.date(2026, 11, 6)], 2)
        self.assertEqual(dates_count[datetime.date(2026, 11, 7)], 2)
        self.assertEqual(dates_count[datetime.date(2026, 11, 8)], 1)

    def test_transient_failure_retries_and_succeeds(self):
        """Lỗi mạng tạm thời kích hoạt retry lần 2 sau 3s; lần 2 thành công thì task đạt SERVER_SCHEDULED."""
        import main
        main.global_schedule_manager = self.mgr
        main.profiles = {
            "AUTO 31": {"config": {"tiktok_account": "user_31"}}
        }

        task = {
            "id": "task_retry_success",
            "profile_name": "AUTO 31",
            "video_path": str(Path(self.tmp_dir.name) / "v_retry.mp4"),
            "video_filename": "v_retry.mp4",
            "execution_mode": "SERVER_SCHEDULE",
            "scheduled_time": "2026-11-06T18:00:00",
            "scheduled_epoch": 1793919900,
            "status": "UPLOADING",
            "retry_count": 0,
        }
        self.mgr.add_tasks([task])
        self.mgr.update_task("task_retry_success", {"scheduled_epoch": 1793919900, "status": "UPLOADING"})

        t = self.mgr.get_task_by_id("task_retry_success")

        # Mock lần 1 lỗi mạng, lần 2 thành công
        res1 = MagicMock()
        res1.outcome = "failed"
        res1.message = "Connection reset by peer"
        res1.details = {}

        res2 = MagicMock()
        res2.outcome = "posted"
        res2.message = "Posted successfully"
        res2.details = {"item_id": "item_retry_999", "video_url": "https://tiktok.com/v/999"}

        with patch("main.run_direct_upload", side_effect=[res1, res2]) as mock_upload, \
             patch("time.sleep") as mock_sleep:
            ok, msg = main._execute_schedule_task_upload(t, force_immediate=False)
            self.assertTrue(ok)
            self.assertEqual(mock_upload.call_count, 2)
            mock_sleep.assert_called_with(3)

        updated_t = self.mgr.get_task_by_id("task_retry_success")
        self.assertEqual(updated_t["status"], "SERVER_SCHEDULED")
        self.assertEqual(updated_t["retry_count"], 1)

    def test_schedule_disabled_fallback_without_retry(self):
        """Khi TikTok từ chối do ab_value='0' (schedule_disabled), không retry mà fallback ngay sang LOCAL_QUEUE."""
        import main
        main.global_schedule_manager = self.mgr
        main.profiles = {
            "AUTO 31": {"config": {"tiktok_account": "user_31"}}
        }

        task = {
            "id": "task_ab_zero",
            "profile_name": "AUTO 31",
            "video_path": str(Path(self.tmp_dir.name) / "v_ab.mp4"),
            "video_filename": "v_ab.mp4",
            "execution_mode": "SERVER_SCHEDULE",
            "scheduled_time": "2026-11-06T18:00:00",
            "scheduled_epoch": 1793919900,
            "status": "UPLOADING",
            "retry_count": 0,
        }
        self.mgr.add_tasks([task])
        self.mgr.update_task("task_ab_zero", {"scheduled_epoch": 1793919900, "status": "UPLOADING"})

        t = self.mgr.get_task_by_id("task_ab_zero")

        res_ab = MagicMock()
        res_ab.outcome = "failed"
        res_ab.message = "Tính năng lên lịch đăng bài bị vô hiệu hóa (ab_value='0')"
        res_ab.details = {"reason": "schedule_disabled", "ab_value": "0"}

        with patch("main.run_direct_upload", return_value=res_ab) as mock_upload:
            ok, msg = main._execute_schedule_task_upload(t, force_immediate=False)
            self.assertTrue(ok)
            # Chỉ gọi 1 lần duy nhất, không retry vô ích vì TikTok đã chặn
            self.assertEqual(mock_upload.call_count, 1)

        updated_t = self.mgr.get_task_by_id("task_ab_zero")
        self.assertEqual(updated_t["execution_mode"], "LOCAL_QUEUE")
        self.assertEqual(updated_t["status"], "PENDING")
        self.assertIn("Hàng đợi máy tính", updated_t["error_message"])


if __name__ == "__main__":
    unittest.main()
