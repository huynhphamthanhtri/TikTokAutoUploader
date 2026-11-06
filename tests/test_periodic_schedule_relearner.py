import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta
import os
import sys

from youtube_monitor.schedule_learner import ScheduleLearner, PollingWindow
from youtube_monitor.predictive_scheduler import (
    PredictiveScheduler,
    RELEARN_INTERVAL_SECONDS,
    RELEARN_STAGGER_DELAY_SECONDS,
)


class TestPeriodicScheduleRelearner(unittest.TestCase):
    """Bộ kiểm thử kiểm chứng cơ chế Re-learning định kỳ 3 ngày và an toàn dữ liệu."""

    def setUp(self):
        self.learner = ScheduleLearner("Asia/Ho_Chi_Minh")

    def test_recluster_and_prune_unlocked_preserves_locked_and_manual(self):
        """Khung giờ MANUAL và LOCKED phải được bảo lưu 100%, khung LEARNED cũ bị loại bỏ."""
        existing_windows = [
            # 1. Khung thủ công người dùng tạo
            PollingWindow(
                id="man_0800",
                start_time="07:50",
                end_time="08:10",
                expected_time="08:00",
                days=[0, 1, 2, 3, 4, 5, 6],
                locked=False,
                source="MANUAL",
            ),
            # 2. Khung tự học cũ nhưng người dùng ĐÃ BẤM KHÓA
            PollingWindow(
                id="pred_1200_locked",
                start_time="11:50",
                end_time="12:10",
                expected_time="12:00",
                days=[0, 1, 2, 3, 4, 5, 6],
                locked=True,
                source="LEARNED",
            ),
            # 3. Khung tự học cũ KHÔNG KHÓA (creator từng đăng 18:00 nhưng nay đã bỏ)
            PollingWindow(
                id="pred_1800_old",
                start_time="17:50",
                end_time="18:10",
                expected_time="18:00",
                days=[0, 1, 2, 3, 4, 5, 6],
                locked=False,
                source="LEARNED",
            ),
        ]

        # 30 video gần nhất: Creator chuyển sang đăng tập trung lúc 20:00 (13:00 UTC)
        # Không còn video nào lúc 18:00
        new_published_iso_list = [
            f"2026-11-{day:02d}T13:00:00Z" for day in range(1, 21)
        ]

        history, final_windows, analysis = self.learner.recluster_and_prune_unlocked(
            new_published_iso_list, existing_windows
        )

        window_ids = [w.id for w in final_windows]
        expected_times = [w.expected_time for w in final_windows]

        # A. Phải bảo lưu khung MANUAL và LOCKED
        self.assertIn("man_0800", window_ids)
        self.assertIn("pred_1200_locked", window_ids)

        # B. Khung LEARNED cũ 18:00 (không khóa) phải biến mất hoàn toàn
        self.assertNotIn("pred_1800_old", window_ids)
        self.assertNotIn("18:00", expected_times)

        # C. Khung giờ mới 20:00 phải được sinh ra và nạp vào
        self.assertIn("20:00", expected_times)

    def test_recluster_and_prune_unlocked_avoids_locked_overlap(self):
        """Khung giờ mới nếu trùng sát với khung giờ LOCKED có sẵn thì không được tạo trùng lặp."""
        existing_windows = [
            PollingWindow(
                id="pred_1200_locked",
                start_time="11:50",
                end_time="12:10",
                expected_time="12:00",
                days=[0, 1, 2, 3, 4, 5, 6],
                locked=True,
                source="LEARNED",
            )
        ]

        # Danh sách video mới đăng lúc 12:05 (05:05 UTC) sát với khung 12:00 đã locked
        new_published_iso = [
            f"2026-11-{day:02d}T05:05:00Z" for day in range(1, 10)
        ]

        history, final_windows, analysis = self.learner.recluster_and_prune_unlocked(
            new_published_iso, existing_windows
        )

        # Chỉ có 1 window duy nhất (giữ nguyên locked window, không sinh thêm window 12:05 trùng đè)
        self.assertEqual(len(final_windows), 1)
        self.assertEqual(final_windows[0].id, "pred_1200_locked")

    def test_periodic_relearn_interval_gating(self):
        """Chỉ kích hoạt re-learn khi kênh chưa từng phân tích hoặc đã quá 3 ngày (259,200s)."""
        mock_store = MagicMock()
        mock_key_mgr = MagicMock()

        now_utc = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
        one_day_ago = (now_utc - timedelta(days=1)).isoformat()
        four_days_ago = (now_utc - timedelta(days=4)).isoformat()

        mock_store.all_items.return_value = {
            "channel_fresh": {
                "active": True,
                "schedule_learned": {"last_analyzed_at": one_day_ago},
            },
            "channel_due": {
                "active": True,
                "schedule_learned": {"last_analyzed_at": four_days_ago},
            },
            "channel_never": {
                "active": True,
                "schedule_learned": {},
            },
            "channel_paused": {
                "active": False,
                "schedule_learned": {"last_analyzed_at": four_days_ago},
            },
        }

        scheduler = PredictiveScheduler(
            channels_store=mock_store,
            key_manager=mock_key_mgr,
            on_video_detected=MagicMock(),
            clock=lambda: now_utc,
        )

        # Mock reanalyze_channel_schedule
        scheduler.reanalyze_channel_schedule = MagicMock(return_value=True)
        # Giả lập không có kênh nào đang micro-poll
        scheduler.get_active_channels_now = MagicMock(return_value=[])

        # Với 1 lượt chạy _check_and_run_periodic_relearn (xử lý 1 kênh mỗi lần với stagger delay)
        # patch sleep/wait để test chạy siêu tốc
        with patch.object(scheduler.stop_event, "wait", return_value=False):
            scheduler._check_and_run_periodic_relearn()

        # Kênh channel_due (quá 3 ngày) phải được kích hoạt reanalyze
        scheduler.reanalyze_channel_schedule.assert_called_once_with("channel_due")

    def test_periodic_relearn_preemption_on_active_window(self):
        """Nếu có kênh bước vào khung giờ micro-poll khẩn cấp, re-learn phải lập tức tạm dừng nhường quyền."""
        mock_store = MagicMock()
        mock_key_mgr = MagicMock()

        now_utc = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)
        four_days_ago = (now_utc - timedelta(days=4)).isoformat()

        mock_store.all_items.return_value = {
            "channel_due": {
                "active": True,
                "schedule_learned": {"last_analyzed_at": four_days_ago},
            }
        }

        scheduler = PredictiveScheduler(
            channels_store=mock_store,
            key_manager=mock_key_mgr,
            on_video_detected=MagicMock(),
            clock=lambda: now_utc,
        )

        scheduler.reanalyze_channel_schedule = MagicMock(return_value=True)
        # Có kênh đang trong khung giờ micro-poll khẩn cấp
        scheduler.get_active_channels_now = MagicMock(return_value=["urgent_channel_1"])

        scheduler._check_and_run_periodic_relearn()

        # Không được gọi reanalyze vì phải nhường quyền micro-poll
        scheduler.reanalyze_channel_schedule.assert_not_called()

    def test_incremental_learning_on_api_detection(self):
        """Khi bắt được video qua API micro-poll, phải tự động cập nhật lịch học ngay mà không đợi 3 ngày."""
        mock_store = MagicMock()
        mock_key_mgr = MagicMock()

        mock_store.get_meta.return_value = {
            "active": True,
            "uploads_playlist_id": "UU12345",
            "last_known_video_id": "old_vid",
            "seen": set(),
        }

        # Mock API response trả về video mới
        mock_key_mgr.execute_with_pool.return_value = {
            "items": [
                {
                    "contentDetails": {
                        "videoId": "new_vid_999",
                        "videoPublishedAt": "2026-11-06T10:00:00Z",
                    },
                    "snippet": {},
                }
            ]
        }

        on_detected = MagicMock(return_value=True)

        scheduler = PredictiveScheduler(
            channels_store=mock_store,
            key_manager=mock_key_mgr,
            on_video_detected=on_detected,
        )

        scheduler._update_channel_learned_schedule = MagicMock()

        detected = scheduler.poll_channel_via_api("channel_test", source="API_PREDICTIVE", max_results=1)

        self.assertEqual(detected, 1)
        on_detected.assert_called_once()
        # Xác nhận _update_channel_learned_schedule được gọi với publishedAt tương ứng
        scheduler._update_channel_learned_schedule.assert_called_once_with(
            "channel_test", "2026-11-06T10:00:00Z"
        )

    def test_reanalyze_channel_schedule_execution_and_resilience(self):
        """Kiểm chứng reanalyze_channel_schedule tiêu tốn đúng 1 quota và xử lý lỗi an toàn."""
        mock_store = MagicMock()
        mock_key_mgr = MagicMock()

        mock_store.get_meta.return_value = {
            "active": True,
            "uploads_playlist_id": "UU_test_channel",
            "timezone": "Asia/Ho_Chi_Minh",
            "manual_windows": [],
            "schedule_learned": {"predicted_windows": []},
        }

        # 1. Test thành công: API trả về 30 video
        mock_key_mgr.execute_with_pool.return_value = {
            "items": [
                {"snippet": {"publishedAt": f"2026-11-{day:02d}T13:00:00Z"}}
                for day in range(1, 31)
            ]
        }

        scheduler = PredictiveScheduler(
            channels_store=mock_store,
            key_manager=mock_key_mgr,
            on_video_detected=MagicMock(),
        )

        ok = scheduler.reanalyze_channel_schedule("UC_test_channel")
        self.assertTrue(ok)
        mock_key_mgr.execute_with_pool.assert_called_once()
        mock_store.update_meta.assert_called_once()
        args, kwargs = mock_store.update_meta.call_args
        self.assertEqual(args[0], "UC_test_channel")
        self.assertIn("schedule_learned", kwargs)
        learned = kwargs["schedule_learned"]
        self.assertIn("last_analyzed_at", learned)
        self.assertGreater(len(learned["predicted_windows"]), 0)

        # 2. Test resilience: API quăng Exception (quota limit, timeout)
        mock_key_mgr.execute_with_pool.side_effect = Exception("Quota exceeded 403")
        ok_error = scheduler.reanalyze_channel_schedule("UC_test_channel")
        self.assertFalse(ok_error)  # Không văng exception, trả về False an toàn


if __name__ == "__main__":
    unittest.main()
