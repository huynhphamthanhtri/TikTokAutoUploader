# -*- coding: utf-8 -*-
"""
test_schedule_manager_view_scope.py - Kiểm thử bộ lọc mở rộng và quản lý vòng đời tác vụ:
1. filter_date_range: 'TODAY', 'TOMORROW', 'NEXT_7_DAYS', 'PAST', 'ALL'.
2. filter_profiles: Lọc theo danh sách profile của 1 Dự án (hỗ trợ project rỗng, SQL parameter binding an toàn).
3. view_scope: 'ACTIVE' (chỉ hiện bài chờ hoặc chưa quá hạn), 'ARCHIVED' (bài cũ/quá hạn), 'ALL'.
4. get_summary_stats: posted_today bao gồm cả bài server schedule hôm nay đã đến giờ.
5. clear_finished_tasks: dọn dẹp an toàn các bài SERVER_SCHEDULED đã quá hạn mốc 1 giờ.
Tuân thủ tuyệt đối Rule 14 AGENTS.md (Zero Live Data Mutation) bằng SQLite trong thư mục tạm.
"""

import datetime
import os
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from schedule_manager import ScheduleManager


class TestScheduleManagerViewScope(TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="test_sched_scope_"))
        self.db_path = self.tmp_dir / "test_scope.db"
        self.mgr = ScheduleManager(storage_file=self.db_path)

        # Mốc thời gian tham chiếu cố định: 2026-11-06 12:00:00
        self.ref_now = datetime.datetime(2026, 11, 6, 12, 0, 0)
        self._seed_tasks()

    def tearDown(self):
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _seed_tasks(self):
        tasks = [
            # 1. Quá khứ: SERVER_SCHEDULED từ 26/10/2026 (Stale task)
            {
                "id": "task_past_oct",
                "profile_name": "AUTO 25",
                "video_path": "vid1.mp4",
                "title": "Old Oct Task",
                "scheduled_time": "2026-10-26T12:00:00",
                "execution_mode": "SERVER_SCHEDULE",
                "status": "SERVER_SCHEDULED",
            },
            # 2. Hôm nay: SERVER_SCHEDULED sáng nay (đã qua lúc 08:30)
            {
                "id": "task_today_past_server",
                "profile_name": "AUTO 25",
                "video_path": "vid2.mp4",
                "title": "Today Morning Server Task",
                "scheduled_time": "2026-11-06T08:30:00",
                "execution_mode": "SERVER_SCHEDULE",
                "status": "SERVER_SCHEDULED",
            },
            # 3. Hôm nay: SERVER_SCHEDULED chiều nay (sắp tới lúc 18:00)
            {
                "id": "task_today_future_server",
                "profile_name": "AUTO 31",
                "video_path": "vid3.mp4",
                "title": "Today Evening Server Task",
                "scheduled_time": "2026-11-06T18:00:00",
                "execution_mode": "SERVER_SCHEDULE",
                "status": "SERVER_SCHEDULED",
            },
            # 4. Hôm nay: LOCAL_QUEUE đã đăng thành công lúc 10:00
            {
                "id": "task_today_success_local",
                "profile_name": "AUTO 31",
                "video_path": "vid4.mp4",
                "title": "Today Local Posted Task",
                "scheduled_time": "2026-11-06T10:00:00",
                "execution_mode": "LOCAL_QUEUE",
                "status": "SUCCESS",
                "completed_at": "2026-11-06T10:02:15",
            },
            # 5. Ngày mai: PENDING
            {
                "id": "task_tomorrow_pending",
                "profile_name": "OTHER_PROF",
                "video_path": "vid5.mp4",
                "title": "Tomorrow Pending Task",
                "scheduled_time": "2026-11-07T12:00:00",
                "execution_mode": "LOCAL_QUEUE",
                "status": "PENDING",
            },
            # 6. Tuần sau (trong 7 ngày): SERVER_SCHEDULED
            {
                "id": "task_next_week",
                "profile_name": "OTHER_PROF",
                "video_path": "vid6.mp4",
                "title": "Next Week Task",
                "scheduled_time": "2026-11-10T12:00:00",
                "execution_mode": "SERVER_SCHEDULE",
                "status": "SERVER_SCHEDULED",
            },
        ]
        self.mgr.add_tasks(tasks)

    def test_filter_profiles_multi_and_empty(self):
        """Kiểm tra lọc theo danh sách profiles (Dự án có nhiều profile hoặc rỗng)."""
        # Dự án chứa AUTO 25 và AUTO 31
        proj_profiles = ["AUTO 25", "AUTO 31"]
        tasks = self.mgr.get_all_tasks(filter_profiles=proj_profiles)
        self.assertEqual(len(tasks), 4)
        for t in tasks:
            self.assertIn(t["profile_name"], proj_profiles)

        count = self.mgr.count_tasks(filter_profiles=proj_profiles)
        self.assertEqual(count, 4)

        # Dự án mới tạo, chưa có profile nào (danh sách rỗng)
        empty_tasks = self.mgr.get_all_tasks(filter_profiles=[])
        self.assertEqual(len(empty_tasks), 0)
        self.assertEqual(self.mgr.count_tasks(filter_profiles=[]), 0)

        # filter_profiles là None -> không lọc theo project, lấy tất cả 6
        all_tasks = self.mgr.get_all_tasks(filter_profiles=None)
        self.assertEqual(len(all_tasks), 6)

    def test_filter_date_range(self):
        """Kiểm tra lọc theo dải ngày: TODAY, TOMORROW, NEXT_7_DAYS, PAST."""
        # 1. TODAY (2026-11-06): task_today_past_server, task_today_future_server, task_today_success_local
        today_tasks = self.mgr.get_all_tasks(filter_date_range="TODAY", reference_dt=self.ref_now)
        today_ids = {t["id"] for t in today_tasks}
        self.assertEqual(today_ids, {"task_today_past_server", "task_today_future_server", "task_today_success_local"})
        self.assertEqual(self.mgr.count_tasks(filter_date_range="TODAY", reference_dt=self.ref_now), 3)

        # 2. TOMORROW (2026-11-07): task_tomorrow_pending
        tomorrow_tasks = self.mgr.get_all_tasks(filter_date_range="TOMORROW", reference_dt=self.ref_now)
        self.assertEqual(len(tomorrow_tasks), 1)
        self.assertEqual(tomorrow_tasks[0]["id"], "task_tomorrow_pending")

        # 3. NEXT_7_DAYS (2026-11-06 đến 2026-11-13): gồm TODAY, TOMORROW và NEXT_WEEK
        next7_tasks = self.mgr.get_all_tasks(filter_date_range="NEXT_7_DAYS", reference_dt=self.ref_now)
        self.assertEqual(len(next7_tasks), 5)

        # 4. PAST (Trước hôm nay 2026-11-06): task_past_oct
        past_tasks = self.mgr.get_all_tasks(filter_date_range="PAST", reference_dt=self.ref_now)
        self.assertEqual(len(past_tasks), 1)
        self.assertEqual(past_tasks[0]["id"], "task_past_oct")

    def test_view_scope_active_vs_archived(self):
        """Kiểm tra view_scope: ACTIVE loại bỏ task quá hạn đã xong; ARCHIVED hiển thị task cũ."""
        # ACTIVE: Không bao gồm task_past_oct (vì đã quá hạn từ tháng 10)
        active_tasks = self.mgr.get_all_tasks(view_scope="ACTIVE", reference_dt=self.ref_now)
        active_ids = {t["id"] for t in active_tasks}
        self.assertNotIn("task_past_oct", active_ids)
        self.assertIn("task_today_future_server", active_ids)
        self.assertIn("task_tomorrow_pending", active_ids)

        # ARCHIVED: Chứa task_past_oct
        archived_tasks = self.mgr.get_all_tasks(view_scope="ARCHIVED", reference_dt=self.ref_now)
        archived_ids = {t["id"] for t in archived_tasks}
        self.assertIn("task_past_oct", archived_ids)

    def test_get_summary_stats_includes_today_server_past(self):
        """get_summary_stats đếm posted_today gồm cả task SUCCESS và task SERVER_SCHEDULED hôm nay đã đến giờ."""
        stats = self.mgr.get_summary_stats(reference_dt=self.ref_now)
        # 1 local SUCCESS (task_today_success_local) + 1 server scheduled sáng nay (task_today_past_server) = 2
        self.assertEqual(stats["posted_today"], 2)
        # Server scheduled tổng cộng 4 task (task_past_oct, task_today_past_server, task_today_future_server, task_next_week)
        self.assertEqual(stats["server_scheduled"], 4)

    def test_clear_finished_tasks_with_stale_server(self):
        """clear_finished_tasks dọn dẹp cả task SERVER_SCHEDULED cũ quá hạn 1 tiếng."""
        # Trước khi dọn: có task_past_oct và task_today_success_local
        cleared = self.mgr.clear_finished_tasks(clear_past_server=True, reference_dt=self.ref_now)
        # Sẽ dọn: task_today_success_local (SUCCESS) và task_past_oct (SERVER_SCHEDULED từ tháng 10)
        # task_today_past_server (lúc 08:30 so với 12:00 là quá 3.5h nên cũng được dọn)
        self.assertGreaterEqual(cleared, 2)

        # Kiểm tra task_past_oct đã được xóa
        remaining = self.mgr.get_task_by_id("task_past_oct")
        self.assertIsNone(remaining)

        # Task tương lai task_today_future_server vẫn còn nguyên
        future_task = self.mgr.get_task_by_id("task_today_future_server")
        self.assertIsNotNone(future_task)
