"""
ui_components.py - Các thành phần giao diện nền tảng (UI Foundation) cho VIBE_AUTO_UPLOAD-LP.

Mô-đun thuần Presentation:
- Không import main.py, browser, repository hay config_store.
- Chứa Design Tokens, SidebarButton, SummaryCard, ProjectList, SelectionActionBar,
  CollapsibleLogDrawer, ToastEvent, ToastManager, và helper calculate_summary_counts.
"""

from __future__ import annotations

import sys
from pathlib import Path
import ntpath
import tkinter as tk
import customtkinter as ctk
import queue
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


# ==============================================================================
# 1. DESIGN SYSTEM TOKENS (LIGHT THEME STANDARD)
# ==============================================================================

class UIThemeTokens:
    # Nền và bề mặt (Surfaces)
    BG_ROOT = "#f1f5f9"           # Slate 100
    BG_SIDEBAR = "#0f172a"        # Slate 900 (Dark sidebar for premium contrast)
    BG_HEADER = "#ffffff"         # White
    BG_CARD = "#ffffff"           # White
    BG_HOVER = "#e2e8f0"          # Slate 200
    BG_SIDEBAR_ACTIVE = "#1e293b" # Slate 800
    BG_SIDEBAR_HOVER = "#334155"  # Slate 700

    # Màu viền (Borders)
    BORDER_LIGHT = "#e2e8f0"      # Slate 200
    BORDER_FOCUS = "#3b82f6"      # Blue 500
    BORDER_SIDEBAR = "#1e293b"    # Slate 800

    # Màu chữ (Typography)
    TEXT_PRIMARY = "#0f172a"      # Slate 900
    TEXT_MUTED = "#64748b"        # Slate 500
    TEXT_SIDEBAR = "#cbd5e1"      # Slate 300
    TEXT_SIDEBAR_ACTIVE = "#ffffff"

    # Màu điểm nhấn & Trạng thái (Accents & Status)
    ACCENT_PRIMARY = "#2563eb"    # Blue 600
    ACCENT_PRIMARY_HOVER = "#1d4ed8"
    STATUS_LIVE = "#16a34a"       # Green 600
    STATUS_LIVE_BG = "#dcfce7"    # Green 100
    STATUS_RUNNING = "#0284c7"    # Sky 600
    STATUS_RUNNING_BG = "#e0f2fe" # Sky 100
    STATUS_WARN = "#d97706"       # Amber 600
    STATUS_WARN_BG = "#fef3c7"    # Amber 100
    STATUS_ERROR = "#dc2626"      # Red 600
    STATUS_ERROR_BG = "#fee2e2"   # Red 100

    # Fonts
    FONT_FAMILY = "Segoe UI"
    FONT_TITLE = ("Segoe UI Semibold", 15)
    FONT_SUBTITLE = ("Segoe UI", 11)
    FONT_BODY = ("Segoe UI", 10)
    FONT_BUTTON = ("Segoe UI Semibold", 10)
    FONT_BADGE = ("Segoe UI Semibold", 9)
    FONT_STAT_NUM = ("Segoe UI Semibold", 20)


# ==============================================================================
# 2. HELPER: REDACTION & SUMMARY CALCULATION
# ==============================================================================

def redact_proxy_string(proxy_str: str) -> str:
    """Mask proxy password in proxy strings (format: ip:port:user:pass -> ip:port:user:***)."""
    if not proxy_str or not isinstance(proxy_str, str):
        return ""
    text = proxy_str.strip()
    parts = text.split(":")
    if len(parts) >= 4:
        return f"{parts[0]}:{parts[1]}:{parts[2]}:***"
    return text


def calculate_summary_counts(
    profiles_dict: Dict[str, Any],
    active_project: str = "Tất cả",
    filter_text: str = "",
    ttl_seconds: int = 86400,
    current_timestamp: Optional[float] = None,
) -> Dict[str, int]:
    """Tính toán 4 chỉ số summary thuần túy (không đụng tới Tk UI, dễ unit-test).
    
    Định nghĩa:
    - total: Số profile thuộc project và khớp filter_text.
    - running: Số profile đang có status là 'running', 'processing', 'uploading', 'manual'.
    - cookie_live: Số profile có snapshot gần nhất là 'live' và chưa quá TTL.
    - errors: Số profile có status là 'error', 'failed', 'proxy_error', 'checkpoint'.
    """
    now = current_timestamp if current_timestamp is not None else time.time()
    total = 0
    running = 0
    cookie_live = 0
    errors = 0

    f_text = str(filter_text or "").strip().lower()

    for name, data in (profiles_dict or {}).items():
        if not isinstance(data, dict):
            continue
        cfg = data.get("config", {}) if isinstance(data.get("config"), dict) else {}
        proj = str(cfg.get("project_name", "") or "Mặc định").strip()

        # Kiểm tra lọc theo dự án
        if active_project and active_project != "Tất cả" and proj != active_project:
            continue

        # Kiểm tra lọc theo search text (quét toàn diện khớp với update_profile_list)
        if f_text:
            tiktok_id = str(cfg.get("tiktok_id", "") or cfg.get("tiktok_account", "") or "").lower()
            proxy = str(cfg.get("proxy_string", "") or "").lower()
            note = str(cfg.get("note", "") or "").lower()
            region = str(cfg.get("region", "") or cfg.get("geo_country", "") or "").lower()
            folder = str(cfg.get("folder_path", "") or "").lower()
            profile_dir = str(cfg.get("chrome_profile", "") or "").lower()
            last_err = str(data.get("last_error", "") or cfg.get("last_error", "") or "").lower()
            status_text = str(data.get("status", "") or cfg.get("status", "") or "").lower()

            search_blob = f"{name.lower()} {tiktok_id} {proxy} {note} {region} {folder} {profile_dir} {last_err} {status_text}"
            if f_text not in search_blob:
                continue

        total += 1

        status = str(data.get("status", "") or cfg.get("status", "")).lower()
        if any(r in status for r in ("running", "processing", "uploading", "manual", "đang chạy", "đang đăng")) or data.get("running"):
            running += 1

        if any(e in status for e in ("error", "failed", "die", "checkpoint", "proxy_error", "lỗi")):
            errors += 1

        # Cookie Live check: dựa vào session_auth_state, status, verified_at và TTL
        auth_state = str(cfg.get("session_auth_state", "")).lower()
        verified_at = cfg.get("session_verified_at")
        is_fresh = True
        if verified_at:
            try:
                v_ts = float(verified_at)
                is_fresh = (now - v_ts) <= ttl_seconds
            except (ValueError, TypeError):
                is_fresh = True

        if is_fresh and (auth_state in ("live", "verified") or ("live" in status or "sẵn sàng" in status or "sống" in status or "đã đăng nhập" in status)):
            cookie_live += 1

    return {
        "total": total,
        "running": running,
        "cookie_live": cookie_live,
        "errors": errors,
    }


# ==============================================================================
# 3. SIDEBAR BUTTON
# ==============================================================================

class SidebarButton(ctk.CTkButton):
    """Nút điều hướng trên Sidebar với icon, badge và trạng thái active."""

    def __init__(
        self,
        master: Any,
        text: str,
        command: Optional[Callable[[], None]] = None,
        icon_text: str = "",
        **kwargs: Any,
    ):
        display_text = f" {icon_text}  {text}" if icon_text else text
        super().__init__(
            master=master,
            text=display_text,
            command=command,
            anchor="w",
            height=38,
            corner_radius=8,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color="transparent",
            text_color=UIThemeTokens.TEXT_SIDEBAR,
            hover_color=UIThemeTokens.BG_SIDEBAR_HOVER,
            **kwargs,
        )
        self._is_active = False

    def set_active(self, active: bool) -> None:
        self._is_active = active
        if active:
            self.configure(
                fg_color=UIThemeTokens.BG_SIDEBAR_ACTIVE,
                text_color=UIThemeTokens.TEXT_SIDEBAR_ACTIVE,
            )
        else:
            self.configure(
                fg_color="transparent",
                text_color=UIThemeTokens.TEXT_SIDEBAR,
            )


# ==============================================================================
# 4. SUMMARY CARD WIDGET
# ==============================================================================

class SummaryCard(ctk.CTkFrame):
    """Thẻ thống kê số liệu trên Header Bar (Tiêu đề, Giá trị StringVar, Viền màu)."""

    def __init__(
        self,
        master: Any,
        title: str,
        value_var: ctk.StringVar,
        accent_color: str = UIThemeTokens.ACCENT_PRIMARY,
        badge_bg: str = UIThemeTokens.BG_HOVER,
        **kwargs: Any,
    ):
        super().__init__(
            master=master,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            **kwargs,
        )
        self.accent_color = accent_color
        
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=12, pady=8)

        top_row = ctk.CTkFrame(container, fg_color="transparent")
        top_row.pack(fill="x")

        self.title_label = ctk.CTkLabel(
            top_row,
            text=title,
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        )
        self.title_label.pack(side="left")

        # Color indicator dot
        dot = ctk.CTkLabel(
            top_row,
            text="●",
            font=("Segoe UI", 12),
            text_color=accent_color,
            width=14,
        )
        dot.pack(side="right")

        self.value_label = ctk.CTkLabel(
            container,
            textvariable=value_var,
            font=UIThemeTokens.FONT_STAT_NUM,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        )
        self.value_label.pack(anchor="w", pady=(2, 0))


# ==============================================================================
# 5. PROJECT LIST SIDEBAR COMPONENT
# ==============================================================================

class ProjectList(ctk.CTkScrollableFrame):
    """Danh sách dự án phẳng hiển thị tên dự án và badge số lượng profile."""

    def __init__(
        self,
        master: Any,
        on_select_project: Optional[Callable[[str], None]] = None,
        **kwargs: Any,
    ):
        super().__init__(
            master=master,
            fg_color="transparent",
            corner_radius=0,
            **kwargs,
        )
        self.on_select_project = on_select_project
        self._buttons: Dict[str, ctk.CTkButton] = {}
        self._active_project = "Tất cả"

    def update_projects(self, project_counts: Dict[str, int], active_project: str = "Tất cả") -> None:
        self._active_project = active_project
        # Clear existing buttons
        for btn in self._buttons.values():
            btn.destroy()
        self._buttons.clear()

        # Render list of projects
        for name, count in project_counts.items():
            btn_text = f"{name} ({count})"
            is_active = (name == active_project)
            btn = ctk.CTkButton(
                self,
                text=btn_text,
                anchor="w",
                height=32,
                corner_radius=6,
                font=UIThemeTokens.FONT_BUTTON,
                fg_color=UIThemeTokens.BG_SIDEBAR_ACTIVE if is_active else "transparent",
                text_color=UIThemeTokens.TEXT_SIDEBAR_ACTIVE if is_active else UIThemeTokens.TEXT_SIDEBAR,
                hover_color=UIThemeTokens.BG_SIDEBAR_HOVER,
                command=lambda p=name: self._on_clicked(p),
            )
            btn.pack(fill="x", padx=4, pady=1)
            self._buttons[name] = btn

    def _on_clicked(self, project_name: str) -> None:
        self._active_project = project_name
        for name, btn in self._buttons.items():
            if name == project_name:
                btn.configure(
                    fg_color=UIThemeTokens.BG_SIDEBAR_ACTIVE,
                    text_color=UIThemeTokens.TEXT_SIDEBAR_ACTIVE,
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=UIThemeTokens.TEXT_SIDEBAR,
                )
        if self.on_select_project:
            self.on_select_project(project_name)


# ==============================================================================
# 6. SELECTION ACTION BAR
# ==============================================================================

class SelectionActionBar(ctk.CTkFrame):
    """Thanh công cụ xuất hiện khi người dùng chọn nhiều profile."""

    def __init__(
        self,
        master: Any,
        handlers: Dict[str, Callable[[], None]],
        **kwargs: Any,
    ):
        super().__init__(
            master=master,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            height=40,
            **kwargs,
        )
        self.handlers = handlers
        self.count_var = ctk.StringVar(value="Đã chọn: 0")

        self.label = ctk.CTkLabel(
            self,
            textvariable=self.count_var,
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.label.pack(side="left", padx=(12, 8))

        ctk.CTkButton(
            self,
            text="Start",
            width=70,
            height=28,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.STATUS_LIVE,
            hover_color="#15803d",
            command=handlers.get("start_selected"),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            self,
            text="Stop",
            width=70,
            height=28,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            command=handlers.get("stop_selected"),
        ).pack(side="left", padx=3)

        ctk.CTkButton(
            self,
            text="Check Cookie",
            width=90,
            height=28,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            command=handlers.get("check_cookie"),
        ).pack(side="left", padx=3)

    def set_selection_count(self, count: int) -> None:
        self.count_var.set(f"Đã chọn: {count}")


# ==============================================================================
# 7. COLLAPSIBLE LOG DRAWER CONTAINER
# ==============================================================================

class CollapsibleLogDrawer(ctk.CTkFrame):
    """Container quản lý thanh tiêu đề thu gọn 32–36px và vùng chứa ScrolledText tabs.
    
    Sử dụng grid đồng nhất để toggle mở rộng/thu gọn mượt mà không xung đột geometry.
    """

    def __init__(
        self,
        master: Any,
        collapsed_height: int = 34,
        expanded_height: int = 180,
        **kwargs: Any,
    ):
        super().__init__(
            master=master,
            corner_radius=0,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            **kwargs,
        )
        self.collapsed_height = collapsed_height
        self.expanded_height = expanded_height
        self.is_expanded = False

        # Header bar click to toggle
        self.header_bar = ctk.CTkFrame(
            self,
            height=collapsed_height,
            corner_radius=0,
            fg_color="#f8fafc",
            cursor="hand2",
        )
        self.header_bar.pack(fill="x", side="top")
        self.header_bar.bind("<Button-1>", lambda e: self.toggle())

        self.title_label = ctk.CTkLabel(
            self.header_bar,
            text="📊 Nhật Ký Hoạt Động",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.title_label.pack(side="left", padx=12)
        self.title_label.bind("<Button-1>", lambda e: self.toggle())

        self.latest_status_label = ctk.CTkLabel(
            self.header_bar,
            text="Sẵn sàng",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.latest_status_label.pack(side="left", padx=10)
        self.latest_status_label.bind("<Button-1>", lambda e: self.toggle())

        self.toggle_btn = ctk.CTkButton(
            self.header_bar,
            text="▲ Mở rộng",
            width=80,
            height=24,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="transparent",
            text_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.BG_HOVER,
            command=self.toggle,
        )
        self.toggle_btn.pack(side="right", padx=10)

        # Content frame for log tabs
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        # Hidden by default

    def toggle(self) -> None:
        self.set_expanded(not self.is_expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.is_expanded = expanded
        if expanded:
            self.content_frame.pack(fill="both", expand=True, side="top", padx=6, pady=(0, 6))
            self.toggle_btn.configure(text="▼ Thu gọn")
        else:
            self.content_frame.pack_forget()
            self.toggle_btn.configure(text="▲ Mở rộng")

    def update_latest_status(self, text: str) -> None:
        redacted = redact_proxy_string(text)
        self.latest_status_label.configure(text=redacted[:90])


# ==============================================================================
# 8. THREAD-SAFE TOAST NOTIFICATIONS
# ==============================================================================

@dataclass
class ToastEvent:
    message: str
    level: str = "INFO"  # INFO, SUCCESS, WARN, ERROR
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()
        self.message = redact_proxy_string(self.message)


class ToastManager:
    """Quản lý hiển thị tối đa 3 Toasts thông báo nổi góc màn hình.
    
    An toàn đa luồng:
    - Worker threads gọi `enqueue(message, level)`.
    - Main thread gọi `poll_queue()` định kỳ qua `root.after()`.
    """

    def __init__(self, root: Any, max_toasts: int = 3, toast_duration_sec: float = 3.5):
        self.root = root
        self.max_toasts = max_toasts
        self.toast_duration_sec = toast_duration_sec
        self.event_queue: queue.Queue[ToastEvent] = queue.Queue()
        self._active_toasts: List[Dict[str, Any]] = []
        self._last_event_text = ""
        self._last_event_time = 0.0

        # Container frame for floating toasts
        self.container = ctk.CTkFrame(root, fg_color="transparent")
        self.container.place(relx=0.98, rely=0.92, anchor="se")

    def enqueue(self, message: str, level: str = "INFO") -> None:
        """Called by worker threads or main thread to push an event."""
        evt = ToastEvent(message=message, level=level)
        self.event_queue.put(evt)

    def poll_queue(self) -> None:
        """Must be called on Tk main thread periodically."""
        now = time.time()
        # Clean expired toasts
        expired = [t for t in self._active_toasts if now - t["created_at"] > self.toast_duration_sec]
        for t in expired:
            t["frame"].destroy()
            self._active_toasts.remove(t)

        # Consume pending queue items
        while not self.event_queue.empty() and len(self._active_toasts) < self.max_toasts:
            try:
                evt = self.event_queue.get_nowait()
            except queue.Empty:
                break

            # Deduplication: ignore same event within 2.0 seconds
            if evt.message == self._last_event_text and (now - self._last_event_time) < 2.0:
                continue

            self._last_event_text = evt.message
            self._last_event_time = now
            self._render_toast(evt)

    def _render_toast(self, evt: ToastEvent) -> None:
        color_map = {
            "SUCCESS": (UIThemeTokens.STATUS_LIVE_BG, UIThemeTokens.STATUS_LIVE, "✔️"),
            "WARN": (UIThemeTokens.STATUS_WARN_BG, UIThemeTokens.STATUS_WARN, "⚠️"),
            "ERROR": (UIThemeTokens.STATUS_ERROR_BG, UIThemeTokens.STATUS_ERROR, "❌"),
            "INFO": ("#f1f5f9", UIThemeTokens.TEXT_PRIMARY, "ℹ️"),
        }
        bg, text_color, icon = color_map.get(evt.level.upper(), color_map["INFO"])

        toast_frame = ctk.CTkFrame(
            self.container,
            corner_radius=8,
            fg_color=bg,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        toast_frame.pack(fill="x", pady=2, anchor="e")

        msg_label = ctk.CTkLabel(
            toast_frame,
            text=f"{icon} {evt.message}",
            font=UIThemeTokens.FONT_BODY,
            text_color=text_color,
        )
        msg_label.pack(padx=12, pady=6)

        self._active_toasts.append({
            "frame": toast_frame,
            "created_at": time.time(),
        })


# ==============================================================================
# 9. RESPONSIVE DIALOG GEOMETRY & CENTERING HELPER
# ==============================================================================

def calculate_centered_geometry(
    pref_w: int,
    pref_h: int,
    screen_w: int = 1366,
    screen_h: int = 768,
    min_w: int = 320,
    min_h: int = 200,
    margin_w: int = 60,
    margin_h: int = 96,
) -> Tuple[int, int, int, int, str]:
    """
    Calculates responsive dimensions and centered coordinates for a dialog.
    Ensures dialog never overflows available screen height (subtracting taskbar & titlebar).
    Returns (width, height, x, y, geometry_string).
    """
    avail_w = max(min_w, screen_w - margin_w)
    avail_h = max(min_h, screen_h - margin_h)

    width = min(pref_w, avail_w)
    height = min(pref_h, avail_h)

    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2 - 15)

    geom_str = f"{width}x{height}+{x}+{y}"
    return width, height, x, y, geom_str


def fit_and_center_dialog(
    dlg: Any,
    pref_w: int,
    pref_h: int,
    parent: Optional[Any] = None,
    min_w: int = 320,
    min_h: int = 200,
) -> Tuple[int, int]:
    """
    Dynamically sizes and centers a CTkToplevel dialog on the screen or parent.
    Applies geometry, minsize, application branding icon, and returns (width, height).
    """
    try:
        if parent is not None and hasattr(parent, "winfo_screenwidth"):
            screen_w = parent.winfo_screenwidth()
            screen_h = parent.winfo_screenheight()
        elif hasattr(dlg, "winfo_screenwidth"):
            screen_w = dlg.winfo_screenwidth()
            screen_h = dlg.winfo_screenheight()
        else:
            screen_w, screen_h = 1366, 768
    except Exception:
        screen_w, screen_h = 1366, 768

    width, height, x, y, geom_str = calculate_centered_geometry(
        pref_w, pref_h, screen_w, screen_h, min_w, min_h
    )

    try:
        dlg.geometry(geom_str)
        dlg.minsize(min_w, min_h)
    except Exception:
        pass

    try:
        apply_app_icon(dlg)
    except Exception:
        pass

    return width, height


def resolve_app_icon_path(app_base: Optional[Any] = None) -> Optional[Path]:
    """Find the application icon.ico across root, _internal, assets, or sys._MEIPASS."""
    if getattr(sys, "frozen", False):
        bases = [
            Path(sys.executable).resolve().parent,
            Path(sys.executable).resolve().parent / "_internal",
            getattr(sys, "_MEIPASS", None),
        ]
    else:
        root_dir = Path(app_base or Path(__file__).resolve().parent)
        bases = [
            root_dir,
            root_dir / "_internal",
            root_dir / "assets",
        ]

    for b in bases:
        if b:
            p = Path(b) / "icon.ico"
            if p.exists() and p.is_file() and p.stat().st_size > 0:
                return p.resolve()
    return None


_APP_USER_MODEL_ID_SET = False


def apply_app_icon(window: Any, app_base: Optional[Any] = None) -> bool:
    """Apply icon.ico to the given Tk / CTk window or toplevel, and register Windows AppUserModelID."""
    global _APP_USER_MODEL_ID_SET
    if sys.platform == "win32" and not _APP_USER_MODEL_ID_SET:
        try:
            import ctypes
            app_id = "donglao.tiktok.studio.suite"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
            _APP_USER_MODEL_ID_SET = True
        except Exception:
            pass

    icon_path = resolve_app_icon_path(app_base)
    if not icon_path or not icon_path.exists():
        return False

    def _set_icon():
        try:
            if hasattr(window, "iconbitmap"):
                window.iconbitmap(str(icon_path))
            elif hasattr(window, "wm_iconbitmap"):
                window.wm_iconbitmap(str(icon_path))
        except Exception:
            pass

    _set_icon()
    try:
        if hasattr(window, "after"):
            window.after(100, _set_icon)
    except Exception:
        pass
    return True


# ==============================================================================
# 9. PROFILE SELECTOR COMPONENTS & PURE HELPERS
# ==============================================================================

def normalize_profile_names(raw_profiles: Any) -> List[str]:
    """Pure helper: Trim whitespace, loại bỏ rỗng, deduplicate bảo toàn thứ tự ban đầu."""
    if raw_profiles is None:
        return []
    if isinstance(raw_profiles, (str, bytes, int, float)):
        items = [raw_profiles]
    else:
        try:
            items = list(raw_profiles)
        except Exception:
            items = [raw_profiles]
    return list(dict.fromkeys(
        str(name).strip()
        for name in items
        if name is not None and str(name).strip()
    ))


def normalized_fs_path(path_value: Any) -> str:
    """Chuẩn hóa đường dẫn Windows bằng ntpath (normcase + normpath) an toàn, không chạm filesystem."""
    text = str(path_value or "").strip()
    return ntpath.normcase(ntpath.normpath(text)) if text else ""


def load_live_profile_names(handlers: Dict[str, Any]) -> Tuple[bool, Tuple[str, ...], str]:
    """
    Helper module-level UI-only: tải và chuẩn hóa danh sách live profiles từ handlers.
    Phân định rõ ràng: handler thiếu/lỗi vs trả danh sách thành công.
    """
    if not isinstance(handlers, dict):
        return False, (), "Handlers không hợp lệ."
    handler = handlers.get("get_profiles")
    if not callable(handler):
        return False, (), "Handler get_profiles chưa được cấu hình."
    try:
        raw = handler()
        values = tuple(normalize_profile_names(raw))
    except Exception as exc:
        return False, (), f"Không thể làm mới danh sách profile: {exc}"
    return True, values, ""


class ProfilePickerField(ctk.CTkFrame):
    """
    ProfilePickerField - Component lựa chọn profile readonly presentation-only.
    - Hiển thị giá trị semantic từ variable hoặc placeholder mờ trong display_var riêng.
    - Readonly display ngăn nhập trực tiếp; copy/focus cần runtime smoke test.
    - Tự động hiển thị viền đỏ và nhãn phụ '⚠️ Không còn khả dụng' khi profile bị stale.
    - Quản lý lifecycle trace an toàn: trace_add / trace_remove và chỉ cleanup khi event.widget is self.
    """

    def __init__(
        self,
        master: Any,
        variable: ctk.StringVar,
        command: Callable[[], None],
        placeholder_text: str = "Chưa chọn profile",
        button_text: str = "🔍 Chọn",
        height: int = 30,
        compact_button: bool = False,
    ):
        super().__init__(master, fg_color="transparent")
        self.variable = variable
        self.command = command
        self.placeholder_text = str(placeholder_text or "Chưa chọn profile")
        self.button_text = str(button_text or "🔍 Chọn")
        self.field_height = int(height or 30)
        self.compact_button = bool(compact_button)

        self._profiles: Tuple[str, ...] = ()
        self._profiles_set: frozenset[str] = frozenset()
        self._trace_token: Optional[str] = None
        self._is_destroying: bool = False

        self.display_var = ctk.StringVar(value="")

        self._build_ui()
        self._setup_trace()
        self.refresh_display()

        self.bind("<Destroy>", self._on_destroy_event, add="+")

    @property
    def profiles(self) -> Tuple[str, ...]:
        return self._profiles

    def _build_ui(self):
        # Entry Container Row
        self.grid_columnconfigure(0, weight=1)

        btn_width = 34 if self.compact_button else 65
        self.btn_picker = ctk.CTkButton(
            self,
            text=self.button_text,
            font=UIThemeTokens.FONT_BUTTON,
            width=btn_width,
            height=self.field_height,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self.command,
        )
        self.btn_picker.pack(side="right", padx=(4, 0))

        self.entry = ctk.CTkEntry(
            self,
            textvariable=self.display_var,
            height=self.field_height,
            font=UIThemeTokens.FONT_BODY,
            state="readonly",
        )
        self.entry.pack(side="left", fill="x", expand=True)

        self.lbl_status = ctk.CTkLabel(
            self,
            text="",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.STATUS_ERROR,
        )

    def _setup_trace(self):
        if self.variable and hasattr(self.variable, "trace_add"):
            try:
                self._trace_token = self.variable.trace_add("write", self._on_var_changed)
            except Exception:
                self._trace_token = None

    def _cleanup_trace(self):
        if self._trace_token and self.variable and hasattr(self.variable, "trace_remove"):
            try:
                self.variable.trace_remove("write", self._trace_token)
            except Exception:
                pass
            self._trace_token = None

    def _on_destroy_event(self, event: Any):
        if getattr(event, "widget", None) is self:
            self._cleanup_trace()

    def destroy(self):
        self._is_destroying = True
        self._cleanup_trace()
        super().destroy()

    def _on_var_changed(self, *args):
        if self._is_destroying:
            return
        try:
            self.refresh_display()
        except (tk.TclError, RuntimeError):
            pass

    def set_profiles(self, profiles: Sequence[str]) -> bool:
        normalized = tuple(normalize_profile_names(profiles))
        changed = normalized != self._profiles
        self._profiles = normalized
        self._profiles_set = frozenset(normalized)
        self.refresh_display()
        return changed

    def is_valid(self) -> bool:
        val = self.get()
        return bool(val and val in self._profiles_set)

    def is_empty(self) -> bool:
        return not bool(self.get())

    def is_stale(self) -> bool:
        val = self.get()
        return bool(val and val not in self._profiles_set)

    def get(self) -> str:
        if self.variable and hasattr(self.variable, "get"):
            return str(self.variable.get() or "").strip()
        return ""

    def refresh_display(self) -> None:
        if self._is_destroying:
            return
        val = self.get()
        try:
            if not val:
                # Empty presentation
                self.display_var.set(self.placeholder_text)
                self.entry.configure(
                    text_color=UIThemeTokens.TEXT_MUTED,
                    border_color=UIThemeTokens.BORDER_LIGHT,
                )
                if self.lbl_status.winfo_ismapped():
                    self.lbl_status.pack_forget()
            elif val in self._profiles_set:
                # Valid presentation
                self.display_var.set(val)
                self.entry.configure(
                    text_color=UIThemeTokens.TEXT_PRIMARY,
                    border_color=UIThemeTokens.BORDER_LIGHT,
                )
                if self.lbl_status.winfo_ismapped():
                    self.lbl_status.pack_forget()
            else:
                # Stale presentation
                self.display_var.set(val)
                self.entry.configure(
                    text_color=UIThemeTokens.STATUS_ERROR,
                    border_color=UIThemeTokens.STATUS_ERROR,
                )
                self.lbl_status.configure(text="⚠️ Không còn khả dụng")
                if not self.lbl_status.winfo_ismapped():
                    self.lbl_status.pack(side="bottom", anchor="w", pady=(2, 0))
        except (tk.TclError, RuntimeError):
            pass

    def focus_picker(self) -> None:
        try:
            if self.btn_picker.winfo_exists():
                self.btn_picker.focus_set()
        except Exception:
            pass
