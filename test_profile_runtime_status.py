import unittest

from profile_runtime_status import (
    AutomationState,
    BrowserState,
    HealthState,
    OperationState,
    RuntimeSignals,
    UploadState,
    batch_start_preflight,
    build_runtime_snapshot,
    browser_label,
    automation_label,
    upload_label,
    row_tags,
)


class RuntimeSnapshotTests(unittest.TestCase):
    def test_stopped_closed_idle(self):
        snap = build_runtime_snapshot(RuntimeSignals())
        self.assertEqual(snap.automation, AutomationState.STOPPED)
        self.assertEqual(snap.browser, BrowserState.CLOSED)
        self.assertEqual(snap.operation, OperationState.IDLE)
        self.assertEqual(snap.upload, UploadState.IDLE)
        self.assertTrue(snap.can_start)
        self.assertFalse(snap.can_stop)
        self.assertTrue(snap.can_open_browser)
        self.assertTrue(snap.can_check_cookie)
        self.assertEqual(row_tags(snap), ("tag_stopped",))

    def test_running_blocks_start(self):
        snap = build_runtime_snapshot(RuntimeSignals(running=True, ui_status="Đang chạy"))
        self.assertEqual(snap.automation, AutomationState.RUNNING)
        self.assertFalse(snap.can_start)
        self.assertTrue(snap.can_stop)
        self.assertEqual(automation_label(snap.automation), "Đang chạy")

    def test_watching_mode_when_observer_only(self):
        snap = build_runtime_snapshot(RuntimeSignals(
            running=True,
            observer_active=True,
            ui_status="Đang chạy",
            ui_browser="Chờ video",
        ))
        self.assertEqual(snap.automation, AutomationState.WATCHING)
        self.assertFalse(snap.can_start)
        self.assertTrue(snap.can_stop)

    def test_starting_blocks_everything_but_stop(self):
        snap = build_runtime_snapshot(RuntimeSignals(
            running=True,
            ui_status="Đang khởi động",
            ui_browser="Đang mở",
        ))
        self.assertEqual(snap.automation, AutomationState.STARTING)
        self.assertFalse(snap.can_start)
        self.assertTrue(snap.can_stop)
        self.assertFalse(snap.can_open_browser)
        self.assertFalse(snap.can_check_cookie)
        self.assertEqual(row_tags(snap), ("tag_processing",))

    def test_manual_browser_open_is_manual_tag_and_blocks_start(self):
        snap = build_runtime_snapshot(RuntimeSignals(manual_driver_alive=True))
        self.assertEqual(snap.browser, BrowserState.MANUAL_OPEN)
        self.assertFalse(snap.can_start)
        self.assertFalse(snap.can_check_cookie)
        self.assertTrue(snap.can_stop)
        self.assertEqual(browser_label(snap.browser), "Thủ công đang mở")
        self.assertEqual(row_tags(snap), ("tag_manual",))

    def test_close_unconfirmed_is_warning(self):
        snap = build_runtime_snapshot(RuntimeSignals(ui_browser="Đóng lỗi"))
        self.assertEqual(snap.browser, BrowserState.CLOSE_UNCONFIRMED)
        self.assertEqual(snap.health, HealthState.WARNING)
        self.assertEqual(row_tags(snap), ("tag_warning",))

    def test_error_marks_red(self):
        snap = build_runtime_snapshot(RuntimeSignals(
            ui_status="Lỗi",
            ui_browser="Bị lỗi",
            has_error=True,
        ))
        self.assertEqual(snap.automation, AutomationState.FAILED)
        self.assertEqual(snap.health, HealthState.ERROR)
        self.assertEqual(row_tags(snap), ("tag_error",))

    def test_session_busy_blocks_start(self):
        snap = build_runtime_snapshot(RuntimeSignals(session_busy=True))
        self.assertFalse(snap.can_start)
        self.assertFalse(snap.can_check_cookie)
        self.assertTrue(snap.can_stop)

    def test_operation_checking_cookie_blocked(self):
        snap = build_runtime_snapshot(RuntimeSignals(
            operation=OperationState.CHECKING_COOKIE.value,
        ))
        self.assertEqual(snap.operation, OperationState.CHECKING_COOKIE)
        self.assertFalse(snap.can_start)
        self.assertFalse(snap.can_check_cookie)

    def test_upload_labels(self):
        self.assertEqual(upload_label(UploadState.UPLOADING), "Đang tải video")
        self.assertEqual(upload_label(UploadState.SUCCEEDED), "Đã đăng")
        self.assertEqual(upload_label(UploadState.FAILED), "Đăng lỗi")
        self.assertEqual(upload_label(UploadState.IDLE), "Chờ video")

    def test_uploading_blocks_actions(self):
        snap = build_runtime_snapshot(RuntimeSignals(
            uploading=True,
            ui_status="Đang chạy",
        ))
        self.assertEqual(snap.upload, UploadState.UPLOADING)
        self.assertFalse(snap.can_start)
        self.assertTrue(snap.can_stop)

    def test_blocked_conflict_blocks_all(self):
        snap = build_runtime_snapshot(RuntimeSignals(blocked_conflict=True))
        self.assertFalse(snap.can_start)
        self.assertFalse(snap.can_open_browser)
        self.assertFalse(snap.can_check_cookie)
        self.assertTrue(snap.blocking_reason)

    def test_batch_start_preflight_filters(self):
        stopped = build_runtime_snapshot(RuntimeSignals())
        running = build_runtime_snapshot(RuntimeSignals(running=True, ui_status="Đang chạy"))
        snapshots = {"A": stopped, "B": running, "C": stopped}
        startable, skipped = batch_start_preflight(snapshots, ["A", "B", "C"])
        self.assertEqual([name for name, _ in startable], ["A", "C"])
        self.assertEqual([name for name, _ in skipped], ["B"])


if __name__ == "__main__":
    unittest.main()
