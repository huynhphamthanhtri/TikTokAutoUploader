"""Pure runtime-status model for the main profile table.

No Tkinter dependency. Builds a deterministic snapshot from structured
signals so the UI never infers state by scanning Vietnamese text strings.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AutomationState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    WATCHING = "WATCHING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


class BrowserState(str, Enum):
    CLOSED = "CLOSED"
    OPENING = "OPENING"
    AUTOMATION_OPEN = "AUTOMATION_OPEN"
    MANUAL_OPEN = "MANUAL_OPEN"
    CLOSING = "CLOSING"
    SAVING_SESSION = "SAVING_SESSION"
    CLOSE_UNCONFIRMED = "CLOSE_UNCONFIRMED"


class OperationState(str, Enum):
    IDLE = "IDLE"
    CHECKING_COOKIE = "CHECKING_COOKIE"
    CAPTURING_SESSION = "CAPTURING_SESSION"
    RESETTING_BROWSER = "RESETTING_BROWSER"
    RESTORING_BROWSER = "RESTORING_BROWSER"
    INSPECTING_ACCOUNT = "INSPECTING_ACCOUNT"


class UploadState(str, Enum):
    IDLE = "IDLE"
    QUEUED = "QUEUED"
    UPLOADING = "UPLOADING"
    POSTING = "POSTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    LIMIT_REACHED = "LIMIT_REACHED"


class HealthState(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class RuntimeSignals:
    running: bool = False
    observer_active: bool = False
    driver_alive: bool = False
    manual_driver_alive: bool = False
    session_busy: bool = False
    uploading: bool = False
    operation: str = OperationState.IDLE.value
    has_error: bool = False
    blocked_conflict: bool = False
    ui_status: str = ""
    ui_browser: str = ""
    ui_upload: str = ""


@dataclass(frozen=True)
class ProfileRuntimeSnapshot:
    automation: AutomationState = AutomationState.STOPPED
    browser: BrowserState = BrowserState.CLOSED
    operation: OperationState = OperationState.IDLE
    upload: UploadState = UploadState.IDLE
    health: HealthState = HealthState.OK
    blocking_reason: str = ""
    can_start: bool = True
    can_stop: bool = False
    can_open_browser: bool = True
    can_check_cookie: bool = True

    def row_key(self):
        return (
            self.automation.value,
            self.browser.value,
            self.operation.value,
            self.upload.value,
            self.health.value,
        )


def _norm(value):
    return str(value or "").strip().lower()


def build_runtime_snapshot(signals: RuntimeSignals) -> ProfileRuntimeSnapshot:
    ui_status = _norm(signals.ui_status)
    ui_browser = _norm(signals.ui_browser)
    ui_upload = _norm(signals.ui_upload)
    operation = _safe_operation(signals.operation)

    if operation == OperationState.CHECKING_COOKIE:
        operation = OperationState.CHECKING_COOKIE

    # --- Automation ---
    if ui_status == "đang khởi động":
        automation = AutomationState.STARTING
    elif ui_status == "đang dừng":
        automation = AutomationState.STOPPING
    elif ui_status == "lỗi" or signals.has_error:
        automation = AutomationState.FAILED
    elif signals.running:
        if (
            signals.observer_active
            and not signals.driver_alive
            and ui_browser in ("chờ video", "chưa mở", "đã đóng", "")
        ):
            automation = AutomationState.WATCHING
        else:
            automation = AutomationState.RUNNING
    else:
        automation = AutomationState.STOPPED

    # --- Browser ---
    if signals.manual_driver_alive:
        browser = BrowserState.MANUAL_OPEN
    elif operation == OperationState.CHECKING_COOKIE or signals.session_busy:
        browser = BrowserState.OPENING
    elif ui_browser in ("đóng lỗi", "đóng chưa sạch", "mất kết nối"):
        browser = BrowserState.CLOSE_UNCONFIRMED
    elif ui_browser == "đang đóng":
        browser = BrowserState.CLOSING
    elif ui_browser == "đang lưu session":
        browser = BrowserState.SAVING_SESSION
    elif signals.driver_alive or ui_browser in ("đang mở", "sẵn sàng", "patchright đang mở"):
        browser = BrowserState.AUTOMATION_OPEN
    else:
        browser = BrowserState.CLOSED

    # --- Upload ---
    if signals.uploading:
        upload = UploadState.UPLOADING
    elif ui_upload == "đã đăng" or ui_upload.startswith("đã đăng"):
        upload = UploadState.SUCCEEDED
    elif ui_upload in ("đăng lỗi", "bị kẹt") or "đăng lỗi" in ui_upload:
        upload = UploadState.FAILED
    elif ui_upload == "đạt giới hạn":
        upload = UploadState.LIMIT_REACHED
    elif ui_upload in ("dry-run ok (chưa post)", "không rõ"):
        upload = UploadState.UNCERTAIN
    elif ui_upload in ("đang tải video", "đang đăng"):
        upload = UploadState.UPLOADING
    elif ui_upload == "có video mới":
        upload = UploadState.QUEUED
    else:
        upload = UploadState.IDLE

    # --- Health ---
    if signals.has_error or signals.blocked_conflict or automation == AutomationState.FAILED:
        health = HealthState.ERROR
    elif browser == BrowserState.CLOSE_UNCONFIRMED:
        health = HealthState.WARNING
    else:
        health = HealthState.OK

    blocking_reason, can_start, can_stop, can_open_browser, can_check_cookie = (
        _action_capabilities(
            automation,
            browser,
            operation,
            upload,
            signals,
            blocked_conflict=signals.blocked_conflict,
        )
    )

    return ProfileRuntimeSnapshot(
        automation=automation,
        browser=browser,
        operation=operation,
        upload=upload,
        health=health,
        blocking_reason=blocking_reason,
        can_start=can_start,
        can_stop=can_stop,
        can_open_browser=can_open_browser,
        can_check_cookie=can_check_cookie,
    )


def _safe_operation(value):
    try:
        return OperationState(str(value or OperationState.IDLE.value).strip().upper())
    except ValueError:
        return OperationState.IDLE


def _action_capabilities(
    automation,
    browser,
    operation,
    upload,
    signals,
    *,
    blocked_conflict,
):
    reason = ""
    can_start = True
    can_stop = False
    can_open_browser = True
    can_check_cookie = True

    busy_operation = operation != OperationState.IDLE

    if blocked_conflict:
        reason = "Profile bị xung đột ownership"
        can_start = can_open_browser = can_check_cookie = False

    if signals.manual_driver_alive:
        reason = "Browser thủ công đang mở"
        can_start = can_open_browser = can_check_cookie = False

    if automation in (AutomationState.STARTING, AutomationState.STOPPING):
        reason = reason or "Đang chuyển trạng thái"
        can_start = can_open_browser = can_check_cookie = False

    if automation == AutomationState.RUNNING or automation == AutomationState.WATCHING:
        can_start = False
        can_open_browser = False
        if not reason:
            reason = "Automation đang hoạt động"

    if signals.session_busy or busy_operation:
        can_start = can_open_browser = can_check_cookie = False
        if not reason:
            reason = "Thao tác session đang chạy"

    if signals.uploading or upload in (
        UploadState.UPLOADING,
        UploadState.QUEUED,
        UploadState.POSTING,
    ):
        can_start = can_open_browser = can_check_cookie = False
        if not reason:
            reason = "Đang xử lý upload"

    if automation in (AutomationState.RUNNING, AutomationState.WATCHING, AutomationState.STARTING):
        can_stop = True
    if signals.manual_driver_alive or signals.driver_alive:
        can_stop = True
    if signals.session_busy or busy_operation:
        can_stop = True
    if signals.uploading or upload in (
        UploadState.UPLOADING,
        UploadState.QUEUED,
        UploadState.POSTING,
    ):
        can_stop = True

    return reason, can_start, can_stop, can_open_browser, can_check_cookie


AUTOMATION_LABELS = {
    AutomationState.STOPPED: "Đã dừng",
    AutomationState.STARTING: "Đang khởi động",
    AutomationState.WATCHING: "Đang theo dõi",
    AutomationState.RUNNING: "Đang chạy",
    AutomationState.STOPPING: "Đang dừng",
    AutomationState.FAILED: "Lỗi",
}

BROWSER_LABELS = {
    BrowserState.CLOSED: "Đã đóng",
    BrowserState.OPENING: "Đang mở",
    BrowserState.AUTOMATION_OPEN: "Tự động đang mở",
    BrowserState.MANUAL_OPEN: "Thủ công đang mở",
    BrowserState.CLOSING: "Đang đóng",
    BrowserState.SAVING_SESSION: "Đang lưu session",
    BrowserState.CLOSE_UNCONFIRMED: "Đóng chưa sạch",
}

OPERATION_LABELS = {
    OperationState.IDLE: "",
    OperationState.CHECKING_COOKIE: "Đang kiểm tra cookie",
    OperationState.CAPTURING_SESSION: "Đang lấy session",
    OperationState.RESETTING_BROWSER: "Đang reset browser",
    OperationState.RESTORING_BROWSER: "Đang khôi phục browser",
    OperationState.INSPECTING_ACCOUNT: "Đang kiểm tra tài khoản",
}

UPLOAD_LABELS = {
    UploadState.IDLE: "Chờ video",
    UploadState.QUEUED: "Có video mới",
    UploadState.UPLOADING: "Đang tải video",
    UploadState.POSTING: "Đang đăng",
    UploadState.SUCCEEDED: "Đã đăng",
    UploadState.FAILED: "Đăng lỗi",
    UploadState.UNCERTAIN: "Không rõ",
    UploadState.LIMIT_REACHED: "Đạt giới hạn",
}


def automation_label(state):
    return AUTOMATION_LABELS.get(state, AutomationState.STOPPED.value)


def browser_label(state):
    return BROWSER_LABELS.get(state, BrowserState.CLOSED.value)


def operation_label(state):
    return OPERATION_LABELS.get(state, "")


def upload_label(state):
    return UPLOAD_LABELS.get(state, UploadState.IDLE.value)


def row_tags(snapshot):
    """TTK tags derived from the snapshot (stable, not string-scraped)."""
    if snapshot.health == HealthState.ERROR or snapshot.automation == AutomationState.FAILED:
        return ("tag_error",)
    if snapshot.browser == BrowserState.MANUAL_OPEN:
        return ("tag_manual",)
    if snapshot.browser == BrowserState.CLOSE_UNCONFIRMED:
        return ("tag_warning",)
    if snapshot.automation in (
        AutomationState.STARTING,
        AutomationState.STOPPING,
    ) or snapshot.operation != OperationState.IDLE:
        return ("tag_processing",)
    if snapshot.automation in (AutomationState.RUNNING, AutomationState.WATCHING):
        return ("tag_ready",)
    return ("tag_stopped",)


def batch_start_preflight(snapshots, names):
    """Pure preflight for a batch Start. Returns (startable, skipped) lists."""
    startable = []
    skipped = []
    for name in names:
        snapshot = snapshots.get(name)
        if snapshot is None:
            skipped.append((name, "Không tồn tại"))
            continue
        if not snapshot.can_start:
            skipped.append((name, snapshot.blocking_reason or "Đang bận"))
            continue
        startable.append((name, snapshot))
    return startable, skipped
