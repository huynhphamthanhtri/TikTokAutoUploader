"""
schedule_ui.py - Giao diện Lên lịch đăng TikTok (Auto-Scheduler Views & Modals).

Bao gồm:
1. ScheduleConflictModal: Hộp thoại giải quyết xung đột khi profile đã có lịch chờ.
2. BulkScheduleModal: Hộp thoại 1-Click to Schedule phân bổ và lên lịch cho nhiều profile.
3. AutoScheduleView: Tab Cấp 1 Quản lý Lịch Đăng Tự Động trên giao diện chính.
"""

from __future__ import annotations

import datetime
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk

from schedule_calculator import (
    DEFAULT_GOLDEN_HOUR_SLOTS,
    distribute_videos_round_robin,
    generate_schedule_timeline,
    generate_title_preview,
    parse_naming_formula,
    resolve_profile_timezone,
    scan_video_files,
)
from schedule_contracts import (
    ConflictSnapshot,
    InvalidPlanCommitError,
    PlannedTask,
    PlanWarning,
    ScheduleCommitError,
    ScheduleCommitResult,
    SchedulePlan,
    ScheduleRequest,
    StalePlanError,
    UnassignedVideo,
)
from schedule_manager import ScheduleManager
from schedule_service import (
    commit_schedule_plan,
    prepare_schedule_plan,
    summarize_schedule_plan,
)
from ui_components import UIThemeTokens, fit_and_center_dialog
from ui_dialogs import SafeCTkToplevel


# ==============================================================================
# 1. SCHEDULE CONFLICT MODAL
# ==============================================================================

class ScheduleConflictModal(SafeCTkToplevel):
    """Hộp thoại giải quyết xung đột khi phát hiện profile đã có video chờ đăng."""

    def __init__(
        self,
        parent: Any,
        conflicts: Dict[str, Dict[str, Any]],
        on_resolve: Callable[[str], None],
    ):
        toplevel_parent = parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent
        super().__init__(toplevel_parent)
        self.title("Phát Hiện Trùng Lịch Đăng")
        fit_and_center_dialog(self, 560, 480, parent=toplevel_parent, min_w=480, min_h=400)
        self.transient(toplevel_parent)
        self.grab_set()

        self.conflicts = conflicts
        self.on_resolve = on_resolve
        self.resolved_mode: Optional[str] = None

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=14)

        # Header
        ctk.CTkLabel(
            container,
            text="⚠️ Phát Hiện Profile Đang Có Lịch Chờ Đăng",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.STATUS_WARN,
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        count_p = len(conflicts)
        total_pending = sum(v.get("pending_count", 0) for v in conflicts.values())
        ctk.CTkLabel(
            container,
            text=f"Có {count_p} profile đang có tổng cộng {total_pending} video chờ đăng. "
                 "Vui lòng chọn cách xử lý để tránh spam hoặc trùng lặp bài đăng:",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
            wraplength=520,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        # Conflict Table / List
        list_frame = ctk.CTkFrame(
            container,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        list_frame.pack(fill="both", expand=True, pady=(0, 12))

        cols = ("profile", "pending", "latest_time")
        tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=5, selectmode="none")
        tree.heading("profile", text="Profile", anchor="w")
        tree.column("profile", width=180, stretch=True)
        tree.heading("pending", text="Video chờ", anchor="center")
        tree.column("pending", width=80, stretch=False)
        tree.heading("latest_time", text="Ngày kết thúc lịch cũ", anchor="w")
        tree.column("latest_time", width=180, stretch=True)

        for p_name, info in conflicts.items():
            latest = info.get("latest_scheduled_time")
            latest_str = latest.strftime("%H:%M  %d/%m/%Y") if isinstance(latest, datetime.datetime) else "Chưa rõ"
            server_cnt = info.get("server_scheduled_count", 0)
            pending_cnt = info.get("pending_count", 0)
            cnt_str = f"⏳ {pending_cnt}"
            if server_cnt > 0:
                cnt_str += f" ({server_cnt} trên server)"
            tree.insert("", "end", values=(p_name, cnt_str, latest_str))

        tree.pack(fill="both", expand=True, padx=6, pady=6)

        # Resolution Options Buttons
        btn_frame = ctk.CTkFrame(container, fg_color="transparent")
        btn_frame.pack(fill="x", pady=(0, 4))

        # Option 1: APPEND
        btn_append = ctk.CTkButton(
            btn_frame,
            text="➕ 1. Nối Tiếp Lịch Mới Vào Sau Lịch Cũ (Khuyên dùng)\n"
                 "   Tự động xếp video mới bắt đầu từ ngày hôm sau của lịch cũ.",
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            text_color="#FFFFFF",
            anchor="w",
            height=46,
            command=lambda: self._select_mode("APPEND"),
        )
        btn_append.pack(fill="x", pady=(0, 6))

        # Option 2: OVERWRITE
        btn_overwrite = ctk.CTkButton(
            btn_frame,
            text="↻ 2. Ghi Đè Lịch Cũ\n"
                 "   Hủy toàn bộ video đang chờ của các profile này và áp dụng lịch mới.",
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.BG_CARD,
            hover_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.STATUS_ERROR,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            anchor="w",
            height=44,
            command=lambda: self._select_mode("OVERWRITE"),
        )
        btn_overwrite.pack(fill="x", pady=(0, 6))

        # Option 3: SKIP
        btn_skip = ctk.CTkButton(
            btn_frame,
            text="⏭️ 3. Bỏ Qua Các Profile Đang Bận\n"
                 "   Giữ nguyên lịch cũ, chỉ lên lịch cho các profile còn trống.",
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.BG_CARD,
            hover_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            anchor="w",
            height=44,
            command=lambda: self._select_mode("SKIP"),
        )
        btn_skip.pack(fill="x", pady=(0, 8))

        # Cancel button
        ctk.CTkButton(
            container,
            text="Hủy Thao Tác",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            fg_color="transparent",
            text_color=UIThemeTokens.TEXT_MUTED,
            hover_color=UIThemeTokens.BG_HOVER,
            command=self._cancel,
        ).pack(side="right")

    def _select_mode(self, mode: str):
        if mode == "OVERWRITE":
            server_scheduled_total = sum(v.get("server_scheduled_count", 0) for v in self.conflicts.values())
            if server_scheduled_total > 0:
                from tkinter import messagebox
                confirmed = messagebox.askyesno(
                    "Cảnh Báo Ghi Đè Lịch Server",
                    f"CẢNH BÁO: Đang có {server_scheduled_total} video ĐÃ ĐƯỢC NẠP LÊN MÁY CHỦ TIKTOK (SERVER_SCHEDULED).\n\n"
                    "Việc 'Ghi Đè' trên phần mềm CHỈ HỦY ĐƯỢC LỊCH LƯU TRÊN MÁY TÍNH CỦA BẠN.\n"
                    "Máy chủ TikTok vẫn sẽ tự động phát hành các video đã nạp trước đó!\n\n"
                    "Bạn có chắc chắn muốn tiếp tục ghi đè lịch cục bộ không?",
                    parent=self,
                )
                if not confirmed:
                    return

        self.resolved_mode = mode
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if callable(self.on_resolve):
            self.on_resolve(mode)

    def destroy(self):
        self._is_destroyed = True
        self._current_plan = None
        self._current_snapshot = None
        self._committed_revision = -1
        super().destroy()

    def _cancel(self):
        self._is_destroyed = True
        self._current_plan = None
        self._current_snapshot = None
        self._committed_revision = -1
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

# ==============================================================================
# 2. BULK SCHEDULE MODAL (1-CLICK TO SCHEDULE)
# ==============================================================================

class BulkScheduleModal(SafeCTkToplevel):
    """Modal 1-Click to Schedule phân bổ và lên lịch cho nhiều profile cùng lúc (Phase 3: Live Timeline Preview Matrix & Safe Queue Management)."""

    def __init__(
        self,
        parent: Any,
        selected_profiles: Sequence[str],
        profile_configs: Optional[Dict[str, Any]] = None,
        schedule_manager: Optional[ScheduleManager] = None,
        on_success: Optional[Callable[..., None]] = None,
        on_start_server_upload: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ):
        toplevel_parent = parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent
        super().__init__(toplevel_parent)
        self.title("Lên Lịch Đăng TikTok Tự Động")
        fit_and_center_dialog(self, 820, 840, parent=toplevel_parent, min_w=640, min_h=580)
        self.transient(toplevel_parent)
        self.grab_set()

        self.selected_profiles = [str(p).strip() for p in selected_profiles if str(p).strip()]
        self.profile_configs = profile_configs or {}
        self.schedule_manager = schedule_manager or ScheduleManager()
        self.on_success = on_success
        self.on_start_server_upload = on_start_server_upload

        # Quản trị trạng thái lập lịch và Preview (Phase 3)
        self._input_revision: int = 0
        self._committed_revision: int = -1
        self._current_plan: Optional[SchedulePlan] = None
        self._current_snapshot: Optional[ConflictSnapshot] = None
        self._is_committing: bool = False
        self._is_previewing: bool = False
        self._commit_succeeded: bool = False
        self._is_destroyed: bool = False

        self.scanned_videos: List[Path] = []
        self.formula_var = ctk.StringVar(value="{filename}")
        self.preview_var = ctk.StringVar(value="")
        self.daily_max_var = ctk.StringVar(value="2")
        self.jitter_var = ctk.StringVar(value="10")
        self.exec_mode_var = ctk.StringVar(value="SERVER_SCHEDULE")  # SERVER_SCHEDULE | LOCAL_QUEUE
        self.resolution_mode_var = ctk.StringVar(value="APPEND")    # APPEND | OVERWRITE
        self.order_mode_var = ctk.StringVar(value="ORIGINAL")       # ORIGINAL | SERIES | SHUFFLE
        self.delete_source_var = ctk.BooleanVar(value=True)
        self.status_info_var = ctk.StringVar(value="")

        # Checkboxes cho 4 khung giờ vàng
        self.slot_sang_var = ctk.BooleanVar(value=True)
        self.slot_trua_var = ctk.BooleanVar(value=True)
        self.slot_chieu_var = ctk.BooleanVar(value=True)
        self.slot_toi_var = ctk.BooleanVar(value=True)

        self._build_ui()
        self._update_preview()

        # Đăng ký listener: Mọi thay đổi cấu hình đầu vào đều tự động vô hiệu hóa bản preview cũ
        # Thứ tự: _on_input_changed() chạy trước tiên để xóa sạch plan cũ, sau đó mới cập nhật preview (Codex Round 16 Fix)
        self.formula_var.trace_add("write", lambda *_: (self._on_input_changed(), self._update_preview()))
        self.daily_max_var.trace_add("write", self._on_input_changed)
        self.jitter_var.trace_add("write", self._on_input_changed)
        self.exec_mode_var.trace_add("write", self._on_input_changed)
        self.resolution_mode_var.trace_add("write", self._on_input_changed)
        self.order_mode_var.trace_add("write", self._on_input_changed)
        self.delete_source_var.trace_add("write", self._on_input_changed)
        self.slot_sang_var.trace_add("write", self._on_input_changed)
        self.slot_trua_var.trace_add("write", self._on_input_changed)
        self.slot_chieu_var.trace_add("write", self._on_input_changed)
        self.slot_toi_var.trace_add("write", self._on_input_changed)

    def _on_input_changed(self, *_):
        """Khi bất kỳ cấu hình đầu vào nào thay đổi, vô hiệu hóa quyền commit cho đến khi xem trước lại."""
        if self._commit_succeeded:
            return
        self._input_revision += 1
        self._current_plan = None
        self._current_snapshot = None
        self._committed_revision = -1
        try:
            if hasattr(self, "btn_submit") and self.winfo_exists() and self.btn_submit.winfo_exists():
                self.btn_submit.configure(
                    state="normal",
                    fg_color=UIThemeTokens.ACCENT_PRIMARY,
                    text="🚀 1-Click Lên Lịch Tự Động",
                )
            if hasattr(self, "status_info_var"):
                self.status_info_var.set("⚠️ Cấu hình đã thay đổi. Bấm '🔍 Xem Trước Lịch Đăng' để cập nhật ma trận.")
        except Exception:
            pass

    def _build_ui(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=12)

        # Fixed bottom bar: Luôn ghim cố định ở đáy cửa sổ để nút bấm luôn luôn hiển thị
        bottom_bar = ctk.CTkFrame(container, fg_color="transparent")
        bottom_bar.pack(side="bottom", fill="x", pady=(6, 0))

        info_row = ctk.CTkFrame(bottom_bar, fg_color="transparent")
        info_row.pack(fill="x", pady=(0, 4))

        self.lbl_status = ctk.CTkLabel(
            info_row,
            textvariable=self.status_info_var,
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.STATUS_WARN,
            anchor="w",
        )
        self.lbl_status.pack(side="left")

        btn_row = ctk.CTkFrame(bottom_bar, fg_color="transparent")
        btn_row.pack(fill="x")

        ctk.CTkButton(
            btn_row,
            text="Hủy (Esc)",
            font=UIThemeTokens.FONT_BUTTON,
            height=34,
            width=100,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._cancel,
        ).pack(side="left")

        self.btn_preview = ctk.CTkButton(
            btn_row,
            text="🔍 Xem Trước Lịch Đăng (Preview)",
            font=UIThemeTokens.FONT_BUTTON,
            height=34,
            width=210,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._generate_preview,
        )
        self.btn_preview.pack(side="right", padx=(8, 0))

        self.btn_submit = ctk.CTkButton(
            btn_row,
            text="🚀 1-Click Lên Lịch Tự Động",
            font=UIThemeTokens.FONT_BUTTON,
            height=34,
            width=200,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            text_color="#FFFFFF",
            command=self._do_schedule,
        )
        self.btn_submit.pack(side="right")

        # Scrollable content area: Chứa toàn bộ các thẻ cấu hình
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll

        # 1. Header & Profiles Selected Card
        header_card = ctk.CTkFrame(
            scroll,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        header_card.pack(fill="x", pady=(0, 6))

        h_inner = ctk.CTkFrame(header_card, fg_color="transparent")
        h_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(
            h_inner,
            text=f"📅 Lên Lịch Đăng Cho {len(self.selected_profiles)} Profile Đã Chọn",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w")

        prof_badges = []
        for p in self.selected_profiles[:4]:
            cfg = self.profile_configs.get(p, {}) if isinstance(self.profile_configs, dict) else {}
            acc = cfg.get("tiktok_account") or ""
            acc_str = f" (@{acc})" if acc else ""
            prof_badges.append(f"👤 {p}{acc_str}")
        if len(self.selected_profiles) > 4:
            prof_badges.append(f"+{len(self.selected_profiles) - 4} profile khác")

        ctk.CTkLabel(
            h_inner,
            text="  •  ".join(prof_badges),
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        # 2. Nạp Video Card
        video_card = ctk.CTkFrame(
            scroll,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        video_card.pack(fill="x", pady=(0, 6))

        v_inner = ctk.CTkFrame(video_card, fg_color="transparent")
        v_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(
            v_inner,
            text="📁 Nạp Video Nguồn (.mp4)",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 4))

        v_btn_row = ctk.CTkFrame(v_inner, fg_color="transparent")
        v_btn_row.pack(fill="x", pady=(0, 4))

        ctk.CTkButton(
            v_btn_row,
            text="📂 Chọn Thư Mục Video",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            width=150,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._pick_folder,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            v_btn_row,
            text="➕ Thêm File Video",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            width=130,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._pick_files,
        ).pack(side="left")

        self.lbl_video_count = ctk.CTkLabel(
            v_inner,
            text="⚠️ Chưa nạp video nào. Vui lòng chọn thư mục chứa video.",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        )
        self.lbl_video_count.pack(fill="x", pady=(2, 0))

        # 3. Cấu hình Công thức Tiêu đề
        formula_card = ctk.CTkFrame(
            scroll,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        formula_card.pack(fill="x", pady=(0, 6))

        f_inner = ctk.CTkFrame(formula_card, fg_color="transparent")
        f_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(
            f_inner,
            text="🏷️ Công Thức Đặt Tiêu Đề Bài Đăng",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 2))

        ctk.CTkLabel(
            f_inner,
            text="Hỗ trợ các biến: {filename} (tên file sạch), {n} (số thứ tự), {profile}, {account}, {date}",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 4))

        f_entry = ctk.CTkEntry(
            f_inner,
            textvariable=self.formula_var,
            height=32,
            font=UIThemeTokens.FONT_BODY,
        )
        f_entry.pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            f_inner,
            textvariable=self.preview_var,
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.STATUS_RUNNING,
            anchor="w",
        ).pack(fill="x")

        # 4. Cấu hình Khung Giờ & Tần Suất
        schedule_card = ctk.CTkFrame(
            scroll,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        schedule_card.pack(fill="x", pady=(0, 6))

        s_inner = ctk.CTkFrame(schedule_card, fg_color="transparent")
        s_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(
            s_inner,
            text="⏰ Cấu Hình Khung Giờ Vàng & Tần Suất Đăng",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 4))

        row1 = ctk.CTkFrame(s_inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(row1, text="Tối đa:", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 4))
        self.daily_cb = ctk.CTkComboBox(
            row1,
            values=["1", "2", "3", "4", "5"],
            variable=self.daily_max_var,
            width=65,
            height=28,
        )
        self.daily_cb.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(row1, text="video / ngày / profile", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(row1, text="Độ lệch ngẫu nhiên (Jitter): ±", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 4))
        self.jitter_entry = ctk.CTkEntry(row1, textvariable=self.jitter_var, width=45, height=28)
        self.jitter_entry.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(row1, text="phút", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left")

        # Khung giờ checkboxes
        slots_row = ctk.CTkFrame(s_inner, fg_color="transparent")
        slots_row.pack(fill="x", pady=(2, 0))

        ctk.CTkCheckBox(slots_row, text="Sáng (8h-9h15)", variable=self.slot_sang_var, font=UIThemeTokens.FONT_BADGE).pack(side="left", padx=(0, 12))
        ctk.CTkCheckBox(slots_row, text="Trưa (11h45-13h)", variable=self.slot_trua_var, font=UIThemeTokens.FONT_BADGE).pack(side="left", padx=(0, 12))
        ctk.CTkCheckBox(slots_row, text="Chiều (17h30-18h45)", variable=self.slot_chieu_var, font=UIThemeTokens.FONT_BADGE).pack(side="left", padx=(0, 12))
        ctk.CTkCheckBox(slots_row, text="Tối (20h-21h30)", variable=self.slot_toi_var, font=UIThemeTokens.FONT_BADGE).pack(side="left")

        # 5. Phương Thức Thực Thi & Thứ Tự & Giải Quyết Xung Đột
        opt_card = ctk.CTkFrame(
            scroll,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        opt_card.pack(fill="x", pady=(0, 6))

        opt_inner = ctk.CTkFrame(opt_card, fg_color="transparent")
        opt_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(
            opt_inner,
            text="⚙️ Tùy Chọn Thực Thi & Thứ Tự Phân Bổ",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkRadioButton(
            opt_inner,
            text="☁️ Lên lịch trực tiếp lên Server TikTok qua API (TẮT MÁY / TẮT TOOL VẪN ĐĂNG ĐÚNG GIỜ 100%)",
            variable=self.exec_mode_var,
            value="SERVER_SCHEDULE",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.ACCENT_PRIMARY,
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkRadioButton(
            opt_inner,
            text="💻 Quản lý hàng đợi trên máy tính (Lưu trữ trên máy, đến giờ hẹn Tool mới gọi API đẩy lên)",
            variable=self.exec_mode_var,
            value="LOCAL_QUEUE",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 6))

        # Hàng chọn thứ tự video & giải quyết xung đột
        mode_row = ctk.CTkFrame(opt_inner, fg_color="transparent")
        mode_row.pack(fill="x", pady=(2, 6))

        ctk.CTkLabel(mode_row, text="Thứ tự phân bổ:", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 4))
        ctk.CTkComboBox(
            mode_row,
            values=["ORIGINAL", "SERIES", "SHUFFLE"],
            variable=self.order_mode_var,
            width=110,
            height=28,
        ).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(mode_row, text="Xử lý lịch cũ:", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 4))
        ctk.CTkComboBox(
            mode_row,
            values=["APPEND", "OVERWRITE"],
            variable=self.resolution_mode_var,
            width=110,
            height=28,
        ).pack(side="left")

        ctk.CTkCheckBox(
            opt_inner,
            text="🗑️ Tự động xóa file video gốc sau khi lên lịch/đăng thành công (giải phóng dung lượng)",
            variable=self.delete_source_var,
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        # 6. Thẻ Ma Trận Xem Trước Lịch Đăng (Live Timeline Preview Matrix - Phase 3)
        preview_card = ctk.CTkFrame(
            scroll,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        preview_card.pack(fill="both", expand=True, pady=(0, 6))

        p_inner = ctk.CTkFrame(preview_card, fg_color="transparent")
        p_inner.pack(fill="both", expand=True, padx=12, pady=10)

        p_header = ctk.CTkFrame(p_inner, fg_color="transparent")
        p_header.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            p_header,
            text="📊 Ma Trận Xem Trước Lịch Đăng (Live Timeline Preview)",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(side="left")

        self.lbl_preview_summary = ctk.CTkLabel(
            p_inner,
            text="ℹ️ Chưa có bản xem trước. Bấm nút '🔍 Xem Trước Lịch Đăng' để phân bổ và tính toán thời gian.",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        )
        self.lbl_preview_summary.pack(fill="x", pady=(0, 4))

        self.lbl_conflict_badge = ctk.CTkLabel(
            p_inner,
            text="",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.STATUS_WARN,
            anchor="w",
        )
        self.lbl_conflict_badge.pack(fill="x", pady=(0, 4))

        # Bảng Treeview ma trận xem trước
        table_frame = ctk.CTkFrame(p_inner, fg_color="transparent")
        table_frame.pack(fill="both", expand=True, pady=(0, 4))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        cols = ("profile", "sched_time", "slot", "video", "title")
        self.preview_tree = ttk.Treeview(
            table_frame,
            columns=cols,
            show="headings",
            selectmode="extended",
            style="Modern.Treeview",
            height=7,
        )
        self.preview_tree.heading("profile", text="Profile", anchor="w")
        self.preview_tree.column("profile", width=120, stretch=False)
        self.preview_tree.heading("sched_time", text="Thời Gian Đăng (Local)", anchor="w")
        self.preview_tree.column("sched_time", width=150, stretch=False)
        self.preview_tree.heading("slot", text="Khung Giờ", anchor="center")
        self.preview_tree.column("slot", width=85, stretch=False)
        self.preview_tree.heading("video", text="File Video", anchor="w")
        self.preview_tree.column("video", width=140, stretch=False)
        self.preview_tree.heading("title", text="Tiêu Đề Dự Kiến", anchor="w")
        self.preview_tree.column("title", width=220, stretch=True)

        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        tree_sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.preview_tree.yview)
        tree_sb.grid(row=0, column=1, sticky="ns")
        self.preview_tree.configure(yscrollcommand=tree_sb.set)

    def _update_preview(self):
        if self._commit_succeeded:
            return
        try:
            f = self.formula_var.get()
            p = self.selected_profiles[0] if self.selected_profiles else "Profile_01"
            acc = ""
            if isinstance(self.profile_configs, dict) and p in self.profile_configs:
                acc = self.profile_configs[p].get("tiktok_account") or ""
            sample_title = generate_title_preview(f, sample_filename="top_10_clips.mp4", profile_name=p, account_name=acc)
            if hasattr(self, "preview_var"):
                self.preview_var.set(f"🔍 Xem trước tiêu đề: \"{sample_title}\"")
        except Exception:
            pass

    def _pick_folder(self):
        folder = filedialog.askdirectory(title="Chọn thư mục chứa video .mp4")
        if folder:
            videos = scan_video_files(folder)
            self._set_scanned_videos(videos)

    def _pick_files(self):
        files = filedialog.askopenfilenames(
            title="Chọn các file video .mp4",
            filetypes=[("MP4 Videos", "*.mp4"), ("All Files", "*.*")],
        )
        if files:
            videos = scan_video_files(files)
            self._set_scanned_videos(videos)

    def _set_scanned_videos(self, videos: List[Path]):
        self.scanned_videos = videos
        # Vô hiệu hóa plan cũ và nâng revision NGAY LẬP TỨC trước khi cập nhật widget UI (Codex Round 15 Fix)
        self._on_input_changed()
        try:
            count = len(videos)
            if count > 0:
                count_p = max(1, len(self.selected_profiles))
                avg = count / count_p
                if hasattr(self, "lbl_video_count") and self.winfo_exists() and self.lbl_video_count.winfo_exists():
                    self.lbl_video_count.configure(
                        text=f"✅ Đã nạp {count} video hợp lệ (.mp4) -> Trung bình ~{avg:.1f} video / profile.",
                        text_color=UIThemeTokens.STATUS_LIVE,
                    )
                if hasattr(self, "status_info_var"):
                    self.status_info_var.set("ℹ️ Đã nạp video mới. Bấm '🔍 Xem Trước Lịch Đăng' để phân bổ ma trận.")
            else:
                if hasattr(self, "lbl_video_count") and self.winfo_exists() and self.lbl_video_count.winfo_exists():
                    self.lbl_video_count.configure(
                        text="❌ Không tìm thấy video .mp4 hợp lệ nào trong đường dẫn đã chọn.",
                        text_color=UIThemeTokens.STATUS_ERROR,
                    )
        except Exception:
            pass

    def _generate_preview(self):
        """Tạo Kế hoạch Lên lịch chuẩn hóa (SchedulePlan) và hiển thị ma trận xem trước (Phase 3)."""
        if self._commit_succeeded or self._is_committing or self._is_previewing:
            return

        self._is_previewing = True
        try:
            if not self.scanned_videos:
                try:
                    self.status_info_var.set("⚠️ Vui lòng nạp ít nhất 1 video .mp4 trước khi xem trước.")
                except Exception:
                    pass
                try:
                    messagebox.showwarning("Thiếu Video", "Vui lòng chọn thư mục hoặc thêm ít nhất 1 file video .mp4!")
                except Exception:
                    pass
                return

            if not self.selected_profiles:
                try:
                    self.status_info_var.set("⚠️ Không có profile nào được chọn để lên lịch.")
                except Exception:
                    pass
                return

            # Khóa chặt Invariant: Ngay khi bắt đầu tạo preview, lập tức xóa sạch state cũ (Codex Round 3 Fix)
            self._current_plan = None
            self._current_snapshot = None
            self._committed_revision = -1
            try:
                if hasattr(self, "btn_submit") and self.winfo_exists() and self.btn_submit.winfo_exists():
                    self.btn_submit.configure(state="normal", text="🚀 1-Click Lên Lịch Tự Động")
            except Exception:
                pass

            current_rev = self._input_revision
            # Giai đoạn thiết lập UI trước xem trước (bọc phòng vệ an toàn - Codex Round 12 Fix)
            try:
                self.status_info_var.set("⏳ Đang quét kiểm tra video và tính toán ma trận thời gian...")
                self.update_idletasks()
            except Exception as ui_prep_err:
                import logging
                logging.getLogger("BulkScheduleModal").error(f"Lỗi thiết lập giao diện trước xem trước: {ui_prep_err}")
                try:
                    self.status_info_var.set(f"❌ Lỗi giao diện xem trước: {ui_prep_err}")
                except Exception:
                    pass
                try:
                    messagebox.showerror("Lỗi Giao Diện", f"Không thể cập nhật giao diện trước khi xem trước:\n{ui_prep_err}")
                except Exception:
                    pass
                return

            # Kiểm tra modal còn tồn tại và không bị destroy trong quá trình update_idletasks (Codex Round 12 Fix)
            if getattr(self, "_is_destroyed", False) or not self.winfo_exists():
                self._current_plan = None
                self._current_snapshot = None
                self._committed_revision = -1
                return

            try:
                daily_max = max(1, int(self.daily_max_var.get().strip() or "2"))
            except Exception:
                daily_max = 2

            try:
                jitter = max(0, int(self.jitter_var.get().strip() or "10"))
            except Exception:
                jitter = 10

            selected_slots = []
            if self.slot_sang_var.get(): selected_slots.append("SANG")
            if self.slot_trua_var.get(): selected_slots.append("TRUA")
            if self.slot_chieu_var.get(): selected_slots.append("CHIEU")
            if self.slot_toi_var.get(): selected_slots.append("TOI")
            if not selected_slots:
                selected_slots = ["SANG", "TRUA", "CHIEU", "TOI"]

            exec_mode = self.exec_mode_var.get()
            if exec_mode not in ("SERVER_SCHEDULE", "LOCAL_QUEUE"):
                exec_mode = "SERVER_SCHEDULE"

            res_mode = self.resolution_mode_var.get()
            if res_mode not in ("APPEND", "OVERWRITE"):
                res_mode = "APPEND"

            order_mode = self.order_mode_var.get()
            if order_mode not in ("ORIGINAL", "SERIES", "SHUFFLE"):
                order_mode = "ORIGINAL"

            formula = self.formula_var.get().strip() or "{filename}"

            try:
                request = ScheduleRequest(
                    video_paths=tuple(str(p.resolve()) for p in self.scanned_videos),
                    profile_names=tuple(self.selected_profiles),
                    profile_configs=self.profile_configs,
                    formula=formula,
                    daily_max=daily_max,
                    selected_slots=tuple(selected_slots),
                    jitter_minutes=jitter,
                    execution_mode=exec_mode,
                    resolution_mode=res_mode,
                    order_mode=order_mode,
                    delete_source_after_post=self.delete_source_var.get(),
                )

                plan = prepare_schedule_plan(request, manager=self.schedule_manager)

                # Cập nhật state nếu revision vẫn còn hiệu lực
                if self._input_revision != current_rev:
                    for item in self.preview_tree.get_children():
                        self.preview_tree.delete(item)
                    self._current_plan = None
                    self._current_snapshot = None
                    self._committed_revision = -1
                    if hasattr(self, "btn_submit") and self.btn_submit.winfo_exists():
                        self.btn_submit.configure(state="normal", text="🚀 1-Click Lên Lịch Tự Động")
                    self.status_info_var.set("⚠️ Cấu hình đã thay đổi trong lúc tính toán. Vui lòng bấm Xem trước lại.")
                    return

                # Xóa các dòng cũ trong ma trận Treeview
                for item in self.preview_tree.get_children():
                    self.preview_tree.delete(item)

                # Kiểm tra plan rỗng (không có video hợp lệ nào được phân bổ) -> không bật nút Commit (Codex Round 10 Fix)
                if plan.total_planned_tasks == 0:
                    self.lbl_preview_summary.configure(
                        text="⚠️ Không có video nào được phân bổ hợp lệ vào kế hoạch lên lịch.",
                        text_color=UIThemeTokens.STATUS_WARN,
                    )
                    self.status_info_var.set("⚠️ Kế hoạch rỗng: không có video nào được phân bổ.")
                    messagebox.showwarning("Kế Hoạch Rỗng", "Không thể lên lịch vì không có video nào hợp lệ được phân bổ vào các profile đã chọn!")
                    return

                # Đổ dữ liệu các task đã lên kế hoạch vào ma trận
                all_planned = []
                for prof, task_list in plan.profile_assignments.items():
                    for pt in task_list:
                        all_planned.append(pt)

                all_planned.sort(key=lambda t: t.scheduled_epoch)

                for pt in all_planned:
                    self.preview_tree.insert(
                        "",
                        "end",
                        values=(
                            pt.profile_name,
                            pt.scheduled_time_local,
                            pt.slot_id,
                            pt.video_filename,
                            pt.title,
                        ),
                    )

                # Cập nhật thẻ tóm tắt
                summary = summarize_schedule_plan(plan)
                dates_count = len(summary["date_counts"])
                summary_txt = f"✅ Đã phân bổ {plan.total_planned_tasks} video qua {len(plan.profile_assignments)} profile trong {dates_count} ngày."
                if plan.unassigned_videos:
                    summary_txt += f" ⚠️ Bỏ qua {len(plan.unassigned_videos)} video không hợp lệ."
                self.lbl_preview_summary.configure(text=summary_txt, text_color=UIThemeTokens.STATUS_LIVE)

                # Cập nhật Badge xung đột
                snap = plan.conflicts_identified
                if snap and snap.conflicted_profiles:
                    if res_mode == "APPEND":
                        self.lbl_conflict_badge.configure(
                            text=f"ℹ️ Chế độ NỐI TIẾP (APPEND): Phát hiện {len(snap.conflicted_profiles)} profile đang có lịch chờ. Lịch mới tự động dời nối tiếp sau video cuối cùng.",
                            text_color=UIThemeTokens.STATUS_RUNNING,
                        )
                    else:
                        self.lbl_conflict_badge.configure(
                            text=f"⚠️ Chế độ GHI ĐÈ (OVERWRITE): Sẽ HỦY toàn bộ video đang chờ của {len(snap.conflicted_profiles)} profile ({', '.join(snap.conflicted_profiles[:3])}) để thay bằng lịch mới!",
                            text_color=UIThemeTokens.STATUS_ERROR,
                        )
                else:
                    self.lbl_conflict_badge.configure(text="", text_color=UIThemeTokens.TEXT_MUTED)

                # Chỉ công bố plan, snapshot, revision và kích hoạt nút Commit SAU KHI toàn bộ render thành công (P1 Fix)
                self._current_plan = plan
                self._current_snapshot = plan.conflicts_identified
                self._committed_revision = current_rev

                self.btn_submit.configure(
                    state="normal",
                    fg_color=UIThemeTokens.ACCENT_PRIMARY,
                    text="🚀 Xác Nhận Lên Lịch (Commit)",
                )
                self.status_info_var.set("✅ Đã tạo ma trận xem trước thành công. Vui lòng kiểm tra kỹ trước khi bấm Xác Nhận.")

            except Exception as ex:
                # Nhánh lỗi: vô hiệu hóa hoàn toàn plan/snapshot và quyền commit
                self._current_plan = None
                self._current_snapshot = None
                self._committed_revision = -1
                try:
                    if hasattr(self, "btn_submit") and self.winfo_exists() and self.btn_submit.winfo_exists():
                        self.btn_submit.configure(
                            state="normal",
                            fg_color=UIThemeTokens.ACCENT_PRIMARY,
                            text="🚀 1-Click Lên Lịch Tự Động",
                        )
                except Exception:
                    pass
                try:
                    self.status_info_var.set(f"❌ Lỗi khi tính toán xem trước: {ex}")
                except Exception:
                    pass
                try:
                    messagebox.showerror("Lỗi Xem Trước", f"Không thể tính toán kế hoạch lên lịch:\n{ex}")
                except Exception:
                    pass

        finally:
            self._is_previewing = False

    def _do_schedule(self):
        """Thực thi Lên Lịch Tự Động (Commit Plan nguyên tử vào SQLite - Phase 3)."""
        # Double-click debounce guard đặt ở đầu hàm & guard chống commit lặp lại khi đã thành công (Codex Round 6 Fix)
        if self._is_committing or self._commit_succeeded:
            return

        if not self.scanned_videos:
            try:
                self.status_info_var.set("⚠️ Vui lòng nạp ít nhất 1 video .mp4 trước khi lên lịch.")
            except Exception:
                pass
            try:
                messagebox.showwarning("Thiếu Video", "Vui lòng chọn thư mục hoặc thêm ít nhất 1 file video .mp4!")
            except Exception:
                pass
            return

        if not self.selected_profiles:
            try:
                self.status_info_var.set("⚠️ Không có profile nào được chọn để lên lịch.")
            except Exception:
                pass
            return

        # Nếu chưa xem trước hoặc cấu hình vừa bị thay đổi -> Bắt buộc tạo preview trước
        if self._current_plan is None or self._committed_revision != self._input_revision:
            self._generate_preview()
            if self._current_plan is not None and self._committed_revision == self._input_revision:
                try:
                    self.status_info_var.set("ℹ️ Đã tạo bản xem trước mới. Vui lòng kiểm tra lại ma trận rồi bấm 'Xác Nhận Lên Lịch'.")
                except Exception:
                    pass
            return

        self._is_committing = True
        commit_res = None
        should_refresh_stale = False
        try:
            # Chụp snapshot danh tính của plan và revision được người dùng xác nhận trước khi nhường quyền cho idle callbacks (Codex Round 8 Fix)
            plan_to_commit = self._current_plan
            expected_rev = self._committed_revision
            snapshot_to_commit = self._current_snapshot

            # 1. Giai đoạn thiết lập UI trước commit (bọc riêng biệt phòng vệ nhiều tầng - Codex Round 8 Fix)
            try:
                if self.winfo_exists() and hasattr(self, "btn_submit") and self.btn_submit.winfo_exists():
                    self.btn_submit.configure(state="disabled", text="⏳ Đang lưu dữ liệu nguyên tử...")
                self.status_info_var.set("⏳ Đang thực thi giao dịch nguyên tử BEGIN IMMEDIATE...")
                self.update_idletasks()
            except Exception as ui_prep_err:
                import logging
                logging.getLogger("BulkScheduleModal").error(f"Lỗi thiết lập giao diện trước commit: {ui_prep_err}")
                try:
                    self.status_info_var.set(f"❌ Lỗi giao diện: {ui_prep_err}")
                except Exception:
                    pass
                try:
                    messagebox.showerror("Lỗi Giao Diện", f"Không thể cập nhật giao diện trước khi lên lịch:\n{ui_prep_err}")
                except Exception:
                    pass
                return

            # Kiểm tra modal còn tồn tại và không bị destroy trong quá trình update_idletasks (Codex Round 11 Fix)
            if getattr(self, "_is_destroyed", False) or not self.winfo_exists():
                self._current_plan = None
                self._current_snapshot = None
                self._committed_revision = -1
                return

            # Kiểm tra an toàn danh tính Plan (Plan Identity Guard): nếu trong quá trình update_idletasks mà plan hoặc revision bị thay đổi ->
            # Lập tức hủy bỏ toàn bộ plan cũ, reset state về None để không bị lợi dụng commit ở lần bấm sau (Codex Round 9 Fix)
            if (
                self._current_plan is None
                or self._current_plan is not plan_to_commit
                or self._committed_revision != expected_rev
                or self._input_revision != expected_rev
            ):
                self._current_plan = None
                self._current_snapshot = None
                self._committed_revision = -1
                try:
                    if not getattr(self, "_is_destroyed", False) and self.winfo_exists() and hasattr(self, "status_info_var"):
                        self.status_info_var.set("⚠️ Cấu hình hoặc kế hoạch đã bị thay đổi trong lúc chuẩn bị. Vui lòng xem trước lại.")
                except Exception:
                    pass
                return

            # 2. Giai đoạn thực thi transaction commit vào SQLite (Codex Round 7/8/9 Fix)
            try:
                fingerprint = snapshot_to_commit.snapshot_fingerprint if snapshot_to_commit else ""
                commit_res = commit_schedule_plan(
                    plan_to_commit,
                    manager=self.schedule_manager,
                    expected_snapshot_fingerprint=fingerprint,
                )

                # Đánh dấu commit đã hoàn tất thành công và khóa latch vĩnh viễn (Codex Round 7/8 Fix)
                self._commit_succeeded = True
                self._current_plan = None
                self._current_snapshot = None
                self._committed_revision = -1

            except StalePlanError:
                # Xóa sạch plan cũ trước khi refresh để không sót state (P2 Fix)
                self._current_plan = None
                self._current_snapshot = None
                self._committed_revision = -1
                should_refresh_stale = True
                try:
                    messagebox.showwarning(
                        "Lịch Đăng Đã Bị Thay Đổi",
                        "Trạng thái hàng đợi của profile đã thay đổi bởi một tác vụ khác trong lúc bạn xem trước.\n\n"
                        "Hệ thống đã tự động làm mới lại bản xem trước (Preview Timeline). "
                        "Vui lòng kiểm tra lại ma trận trước khi bấm xác nhận lại!"
                    )
                except Exception:
                    pass

            except Exception as db_err:
                # Chỉ thông báo lỗi DB khi transaction commit thực sự thất bại (bọc độc lập - Codex Round 14 Fix)
                try:
                    messagebox.showerror("Lỗi Lưu Dữ Liệu", f"Không thể lưu kế hoạch lên lịch:\n{db_err}")
                except Exception:
                    pass
                try:
                    self.status_info_var.set(f"❌ Lỗi commit: {db_err}")
                except Exception:
                    pass

        finally:
            self._is_committing = False
            try:
                if self.winfo_exists():
                    if self._commit_succeeded:
                        # Tách riêng từng nút để nếu 1 nút lỗi thì nút kia vẫn được disable độc lập (Codex Round 8 Fix)
                        try:
                            if hasattr(self, "btn_submit") and self.btn_submit.winfo_exists():
                                self.btn_submit.configure(state="disabled", text="✅ Đã Lên Lịch Thành Công")
                        except Exception:
                            pass
                        try:
                            if hasattr(self, "btn_preview") and self.btn_preview.winfo_exists():
                                self.btn_preview.configure(state="disabled")
                        except Exception:
                            pass
                    elif hasattr(self, "btn_submit") and self.btn_submit.winfo_exists():
                        if self._current_plan is not None and self._committed_revision == self._input_revision:
                            self.btn_submit.configure(state="normal", text="🚀 Xác Nhận Lên Lịch (Commit)")
                        else:
                            self.btn_submit.configure(state="normal", text="🚀 1-Click Lên Lịch Tự Động")
            except Exception:
                pass

        # Thực hiện làm mới Preview sau StalePlanError sau khi cờ _is_committing đã được giải phóng (Codex Round 9 Fix)
        if should_refresh_stale:
            try:
                self._generate_preview()
            except Exception as ref_err:
                try:
                    self.status_info_var.set(f"❌ Lỗi khi làm mới xem trước: {ref_err}")
                except Exception:
                    pass
            return

        # Giai đoạn sau commit SQLite thành công: Bọc tách biệt hoàn toàn khỏi transaction error handling (Codex Round 5 Fix).
        # Lỗi popup hoặc đóng modal tuyệt đối không kích hoạt popup "Lỗi Lưu Dữ Liệu" gây hiểu nhầm dữ liệu chưa lưu.
        if commit_res is not None:
            try:
                mode_desc = "Server TikTok" if self.exec_mode_var.get() == "SERVER_SCHEDULE" else "Hàng đợi máy tính"
                msg = (
                    f"Đã lên lịch thành công {len(commit_res.created_task_ids)} video vào cơ sở dữ liệu SQLite!\n\n"
                    f"- Chế độ thực thi: {mode_desc}\n"
                    f"- Chế độ xử lý: {commit_res.resolution_mode}\n"
                )
                if commit_res.cancelled_task_ids:
                    msg += f"- Đã hủy {len(commit_res.cancelled_task_ids)} video cũ theo yêu cầu Ghi đè.\n"

                msg += "\nDữ liệu đã sẵn sàng trong Tab 'Lịch Đăng'. Bạn có thể kiểm tra danh sách bất cứ lúc nào."

                messagebox.showinfo("Lên Lịch Thành Công", msg)
            except Exception as ui_msg_err:
                import logging
                logging.getLogger("BulkScheduleModal").warning(f"Lỗi hiển thị thông báo thành công sau commit: {ui_msg_err}")

            try:
                self._cancel()
            except Exception as close_err:
                import logging
                logging.getLogger("BulkScheduleModal").warning(f"Lỗi đóng modal sau commit: {close_err}")

        # Kích hoạt callback an toàn bên ngoài try-except commit, xác định cách gọi bằng Signature.bind (Codex Round 3 Fix)
        if commit_res is not None and callable(self.on_success):
            try:
                import inspect
                sig = inspect.signature(self.on_success)
                created_list = list(commit_res.created_task_ids)
                args_to_pass = ()
                kwargs_to_pass = {}

                # 1. Thử positional argument
                try:
                    sig.bind(created_list)
                    args_to_pass = (created_list,)
                except TypeError:
                    # 2. Thử keyword-only 'created_tasks'
                    try:
                        sig.bind(created_tasks=created_list)
                        kwargs_to_pass = {"created_tasks": created_list}
                    except TypeError:
                        # 3. Thử keyword-only 'created_task_ids'
                        try:
                            sig.bind(created_task_ids=created_list)
                            kwargs_to_pass = {"created_task_ids": created_list}
                        except TypeError:
                            # 4. Thử no-arg
                            sig.bind()

                self.on_success(*args_to_pass, **kwargs_to_pass)
            except Exception as cb_err:
                import logging
                logging.getLogger("BulkScheduleModal").error(f"Lỗi khi thực thi on_success callback sau commit: {cb_err}")

    def destroy(self):
        self._is_destroyed = True
        self._current_plan = None
        self._current_snapshot = None
        self._committed_revision = -1
        super().destroy()

    def _cancel(self):
        self._is_destroyed = True
        self._current_plan = None
        self._current_snapshot = None
        self._committed_revision = -1
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


def make_bulk_schedule_success_callback(
    target_profiles: Sequence[str],
    update_status_fn: Optional[Callable[[str], Any]] = None,
    update_profile_list_fn: Optional[Callable[[], Any]] = None,
    switch_workspace_fn: Optional[Callable[[str], Any]] = None,
    schedule_view_ref: Optional[Any] = None,
) -> Callable[..., None]:
    """Tạo callback xử lý thành công sau commit dùng chung giữa main.py và UI test.

    Đảm bảo 100% call-chain thống nhất, loại trừ phân mảnh giữa production và test suite:
    1. Cập nhật thông báo trạng thái với số lượng target_profiles.
    2. Cập nhật bảng danh sách profile (bọc try/except an toàn).
    3. Chuyển sang workspace 'schedule' hoặc refresh schedule_view.
    """
    def _on_success(created_tasks=None):
        count_p = len(target_profiles)
        if callable(update_status_fn):
            try:
                update_status_fn(f"Đã lên lịch thành công cho {count_p} profile.")
            except Exception:
                pass
        if callable(update_profile_list_fn):
            try:
                update_profile_list_fn()
            except Exception:
                pass
        if callable(switch_workspace_fn):
            try:
                switch_workspace_fn("schedule")
            except Exception:
                pass
        elif schedule_view_ref is not None and hasattr(schedule_view_ref, "_refresh_data"):
            try:
                schedule_view_ref._refresh_data()
            except Exception:
                pass

    return _on_success



# ==============================================================================
# 3. AUTO SCHEDULE VIEW (MAIN LEVEL-1 TAB)
# ==============================================================================

class AutoScheduleView(ctk.CTkFrame):
    """Tab Cấp 1 Quản lý Lịch Đăng Tự Động trên giao diện chính (Phase 4: Real-time Multi-dimensional Filters, Pagination & Batch Operations)."""

    def __init__(
        self,
        parent: Any,
        schedule_manager: Optional[ScheduleManager] = None,
        handlers: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(parent, fg_color="transparent")
        self.schedule_manager = schedule_manager or ScheduleManager()
        self.handlers = handlers or {}

        self.search_var = ctk.StringVar(value="")
        self.status_filter_var = ctk.StringVar(value="Tất cả")
        self.profile_filter_var = ctk.StringVar(value="Tất cả")
        self.mode_filter_var = ctk.StringVar(value="Tất cả chế độ")

        # Metric vars
        self.stat_pending_var = ctk.StringVar(value="0")
        self.stat_server_var = ctk.StringVar(value="0")
        self.stat_today_var = ctk.StringVar(value="0")
        self.stat_failed_var = ctk.StringVar(value="0")

        # Pagination state (Phase 4)
        self.page_size_var = ctk.StringVar(value="50")
        self.current_page: int = 1
        self.total_pages: int = 1
        self.total_count: int = 0

        self.lbl_page_info = None
        self.lbl_current_page = None
        self.btn_prev_page = None
        self.btn_next_page = None
        self.cb_page_size = None

        self._build_ui()
        self._refresh_data()

    def _build_ui(self):
        # 1. Header & Metric Summary Cards
        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.pack(fill="x", padx=16, pady=(12, 8))

        ctk.CTkLabel(
            header_row,
            text="📅 QUẢN LÝ LỊCH ĐĂNG TIKTOK TỰ ĐỘNG",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(side="left")

        # Metrics cards
        metrics_row = ctk.CTkFrame(self, fg_color="transparent")
        metrics_row.pack(fill="x", padx=16, pady=(0, 10))

        self._create_metric_card(metrics_row, "⏳ Đang Chờ Đăng", self.stat_pending_var, UIThemeTokens.STATUS_WARN)
        self._create_metric_card(metrics_row, "☁️ Đã Hẹn TikTok", self.stat_server_var, UIThemeTokens.STATUS_RUNNING)
        self._create_metric_card(metrics_row, "🟢 Đã Đăng Hôm Nay", self.stat_today_var, UIThemeTokens.STATUS_LIVE)
        self._create_metric_card(metrics_row, "❌ Lỗi Cần Thử Lại", self.stat_failed_var, UIThemeTokens.STATUS_ERROR)

        # 2. Filter Bar
        filter_card = ctk.CTkFrame(
            self,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        filter_card.pack(fill="x", padx=16, pady=(0, 8))

        f_inner = ctk.CTkFrame(filter_card, fg_color="transparent")
        f_inner.pack(fill="x", padx=10, pady=8)

        # Search Entry
        search_entry = ctk.CTkEntry(
            f_inner,
            textvariable=self.search_var,
            placeholder_text="🔍 Tìm theo profile, video, tiêu đề...",
            width=220,
            height=30,
            font=UIThemeTokens.FONT_BODY,
        )
        search_entry.pack(side="left", padx=(0, 10))
        self.search_var.trace_add("write", lambda *_: self._refresh_data(keep_page=False))

        # Status filter
        ctk.CTkLabel(f_inner, text="Trạng thái:", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left", padx=(0, 4))
        self.cb_status = ctk.CTkComboBox(
            f_inner,
            values=["Tất cả", "PENDING", "UPLOADING", "SERVER_SCHEDULED", "SUCCESS", "FAILED", "CANCELLED", "MISSED_RESCHEDULED"],
            variable=self.status_filter_var,
            width=130,
            height=30,
            command=lambda _: self._refresh_data(keep_page=False),
        )
        self.cb_status.pack(side="left", padx=(0, 10))

        # Mode filter
        ctk.CTkLabel(f_inner, text="Chế độ:", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left", padx=(0, 4))
        self.cb_mode = ctk.CTkComboBox(
            f_inner,
            values=["Tất cả chế độ", "☁️ Hẹn giờ TikTok", "💻 Hàng đợi máy"],
            variable=self.mode_filter_var,
            width=140,
            height=30,
            command=lambda _: self._refresh_data(keep_page=False),
        )
        self.cb_mode.pack(side="left", padx=(0, 10))

        # Profile filter
        ctk.CTkLabel(f_inner, text="Profile:", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left", padx=(0, 4))
        self.cb_profile = ctk.CTkComboBox(
            f_inner,
            values=["Tất cả"],
            variable=self.profile_filter_var,
            width=120,
            height=30,
            command=lambda _: self._refresh_data(keep_page=False),
        )
        self.cb_profile.pack(side="left", padx=(0, 10))

        # Refresh button
        ctk.CTkButton(
            f_inner,
            text="🔄 Làm Mới",
            font=UIThemeTokens.FONT_BUTTON,
            width=90,
            height=30,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=lambda: self._refresh_data(keep_page=True),
        ).pack(side="right")

        # 3. Queue Table Card
        table_card = ctk.CTkFrame(
            self,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        table_card.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        table_card.grid_rowconfigure(0, weight=1)
        table_card.grid_columnconfigure(0, weight=1)

        columns = ("sched_time", "profile", "account", "video", "title", "mode", "status")
        self.tree = ttk.Treeview(
            table_card,
            columns=columns,
            show="headings",
            selectmode="extended",
            style="Modern.Treeview",
        )
        self.tree.heading("sched_time", text="Thời gian dự kiến", anchor="w")
        self.tree.column("sched_time", width=140, stretch=False)
        self.tree.heading("profile", text="Profile", anchor="w")
        self.tree.column("profile", width=130, stretch=False)
        self.tree.heading("account", text="Tài khoản TikTok", anchor="w")
        self.tree.column("account", width=120, stretch=False)
        self.tree.heading("video", text="Tên Video", anchor="w")
        self.tree.column("video", width=150, stretch=False)
        self.tree.heading("title", text="Tiêu Đề Xuất Bản", anchor="w")
        self.tree.column("title", width=220, stretch=True)
        self.tree.heading("mode", text="Chế Độ", anchor="center")
        self.tree.column("mode", width=95, stretch=False)
        self.tree.heading("status", text="Trạng Thái", anchor="center")
        self.tree.column("status", width=110, stretch=False)

        self.tree.tag_configure("success_tag", foreground=UIThemeTokens.STATUS_LIVE)
        self.tree.tag_configure("pending_tag", foreground=UIThemeTokens.STATUS_WARN)
        self.tree.tag_configure("server_tag", foreground=UIThemeTokens.STATUS_RUNNING)
        self.tree.tag_configure("failed_tag", foreground=UIThemeTokens.STATUS_ERROR)
        self.tree.tag_configure("cancel_tag", foreground=UIThemeTokens.TEXT_MUTED)

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vsb = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.bind("<Double-1>", self._on_double_click_row)

        # 3.5. Pagination Control Bar (Phase 4)
        page_bar = ctk.CTkFrame(self, fg_color="transparent")
        page_bar.pack(fill="x", padx=16, pady=(0, 6))

        self.lbl_page_info = ctk.CTkLabel(
            page_bar,
            text="Hiển thị 0 - 0 / Tổng 0 tác vụ",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_page_info.pack(side="left")

        page_nav_frame = ctk.CTkFrame(page_bar, fg_color="transparent")
        page_nav_frame.pack(side="right")

        ctk.CTkLabel(
            page_nav_frame,
            text="Số bản ghi/trang:",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left", padx=(0, 4))

        self.cb_page_size = ctk.CTkComboBox(
            page_nav_frame,
            values=["25", "50", "100", "Tất cả"],
            variable=self.page_size_var,
            width=90,
            height=28,
            command=self._on_page_size_changed,
        )
        self.cb_page_size.pack(side="left", padx=(0, 10))

        self.btn_prev_page = ctk.CTkButton(
            page_nav_frame,
            text="◀️ Trang trước",
            width=95,
            height=28,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._prev_page,
        )
        self.btn_prev_page.pack(side="left", padx=(0, 4))

        self.lbl_current_page = ctk.CTkLabel(
            page_nav_frame,
            text="Trang 1 / 1",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.lbl_current_page.pack(side="left", padx=4)

        self.btn_next_page = ctk.CTkButton(
            page_nav_frame,
            text="Trang sau ▶️",
            width=95,
            height=28,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._next_page,
        )
        self.btn_next_page.pack(side="left", padx=(4, 0))

        # 4. Bottom Action Bar
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.pack(fill="x", padx=16, pady=(0, 12))

        self.btn_run = ctk.CTkButton(
            action_bar,
            text="▶️ Bắt Đầu Chạy Lịch",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=150,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            text_color="#FFFFFF",
            command=self._do_start_queue,
        )
        self.btn_run.pack(side="left", padx=(0, 6))

        self.btn_pause = ctk.CTkButton(
            action_bar,
            text="⏸️ Tạm Dừng",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=100,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._do_pause_queue,
        )
        self.btn_pause.pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            action_bar,
            text="⚡ Đăng Ngay Lập Tức",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=140,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._do_post_now,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            action_bar,
            text="🔄 Thử Lại Lỗi",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=105,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._do_retry,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            action_bar,
            text="❌ Hủy Lịch",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=95,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.STATUS_ERROR,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._do_cancel,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            action_bar,
            text="🗑️ Xóa Bản Ghi",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=100,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_MUTED,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._do_delete,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            action_bar,
            text="🧹 Dọn Dẹp Đã Xong",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=130,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_MUTED,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._do_clear_finished,
        ).pack(side="right")

    def _create_metric_card(self, parent, title: str, var: ctk.StringVar, color: str):
        card = ctk.CTkFrame(
            parent,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        card.pack(side="left", fill="both", expand=True, padx=(0, 8))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=8)

        ctk.CTkLabel(inner, text=title, font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(inner, textvariable=var, font=UIThemeTokens.FONT_STAT_NUM, text_color=color).pack(anchor="w")

    def _is_alive(self) -> bool:
        try:
            return bool(super().winfo_exists())
        except Exception:
            return False

    @staticmethod
    def _widget_alive(w: Any) -> bool:
        if w is None:
            return False
        try:
            return bool(w.winfo_exists())
        except Exception:
            return False

    def _prev_page(self):
        if not self._is_alive():
            return
        if self.current_page > 1:
            self.current_page -= 1
            self._refresh_data(keep_page=True)

    def _next_page(self):
        if not self._is_alive():
            return
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._refresh_data(keep_page=True)

    def _on_page_size_changed(self, _choice=None):
        if not self._is_alive():
            return
        self.current_page = 1
        self._refresh_data(keep_page=True)

    def _refresh_data(self, keep_page: bool = False):
        """Tải lại danh sách task và cập nhật bảng (hỗ trợ phân trang và bộ lọc thời gian thực)."""
        if not self._is_alive():
            return

        if not keep_page:
            self.current_page = 1

        # 1. Cập nhật danh sách profile và chuẩn hóa profile filter TRƯỚC KHI query (P2-01 & P2-02)
        try:
            all_profs = self.schedule_manager.get_distinct_profiles()
            valid_profs = ["Tất cả"] + all_profs
            if hasattr(self, "cb_profile") and self._widget_alive(self.cb_profile):
                self.cb_profile.configure(values=valid_profs)
            cur_prof = self.profile_filter_var.get()
            if cur_prof not in valid_profs:
                self.profile_filter_var.set("Tất cả")
        except Exception:
            pass

        # 2. Cập nhật metrics
        try:
            stats = self.schedule_manager.get_summary_stats()
            self.stat_pending_var.set(str(stats.get("pending", 0)))
            self.stat_server_var.set(str(stats.get("server_scheduled", 0)))
            self.stat_today_var.set(str(stats.get("posted_today", 0)))
            self.stat_failed_var.set(str(stats.get("failed", 0)))
        except Exception:
            pass

        # 3. Đọc bộ lọc & tách biệt Sentinel 'Tất cả' khỏi profile name thật (P2-01 & Codex Review)
        try:
            cur_st = (self.status_filter_var.get() if self.status_filter_var else "Tất cả").strip()
        except Exception:
            cur_st = "Tất cả"
        st_filter = None if cur_st in ("Tất cả", "tất cả", "") else cur_st

        try:
            cur_prof = self.profile_filter_var.get() if self.profile_filter_var else "Tất cả"
        except Exception:
            cur_prof = "Tất cả"
        prof_filter = None if cur_prof in ("Tất cả", "tất cả", "") else cur_prof

        try:
            mode_filter = self.mode_filter_var.get() if self.mode_filter_var else "Tất cả chế độ"
        except Exception:
            mode_filter = "Tất cả chế độ"

        try:
            search_q = self.search_var.get() if self.search_var else ""
        except Exception:
            search_q = ""

        # 4. Phân trang & parse phòng thủ an toàn (P2-03)
        try:
            raw_ps = (self.page_size_var.get() if self.page_size_var else "50").strip()
        except Exception:
            raw_ps = "50"

        if raw_ps.casefold() in {"tất cả", "tat ca", "all"}:
            limit = None
        else:
            try:
                parsed_val = int(raw_ps)
                if parsed_val <= 0 or parsed_val > 1000:
                    limit = 50
                else:
                    limit = parsed_val
            except (ValueError, TypeError, OverflowError):
                limit = 50

        try:
            self.total_count = self.schedule_manager.count_tasks(
                filter_status=st_filter,
                filter_profile=prof_filter,
                filter_mode=mode_filter,
                search_query=search_q,
            )
        except Exception as ex:
            import logging
            logging.getLogger("AutoScheduleView").error(f"Lỗi count_tasks: {ex}")
            self.total_count = 0
            if self._widget_alive(self.lbl_page_info):
                self.lbl_page_info.configure(text="⚠️ Lỗi kết nối cơ sở dữ liệu")

        if limit:
            import math
            self.total_pages = max(1, math.ceil(self.total_count / limit))
        else:
            self.total_pages = 1

        self.current_page = max(1, min(self.current_page, self.total_pages))
        offset = (self.current_page - 1) * limit if limit else 0

        # 5. Đọc tasks
        try:
            tasks = self.schedule_manager.get_all_tasks(
                filter_status=st_filter,
                filter_profile=prof_filter,
                filter_mode=mode_filter,
                search_query=search_q,
                limit=limit,
                offset=offset,
            )
        except Exception as ex:
            import logging
            logging.getLogger("AutoScheduleView").error(f"Lỗi get_all_tasks: {ex}")
            tasks = []
            if self._widget_alive(self.lbl_page_info):
                self.lbl_page_info.configure(text="⚠️ Lỗi tải dữ liệu lịch đăng")

        # 6. Cập nhật Treeview
        try:
            self.tree.delete(*self.tree.get_children())
            for t in tasks:
                tid = t.get("id", "")
                raw_time = t.get("scheduled_time", "")
                try:
                    dt = datetime.datetime.fromisoformat(raw_time)
                    time_str = dt.strftime("%H:%M  %d/%m/%Y")
                except Exception:
                    time_str = raw_time

                st = t.get("status", "PENDING")
                mode = "☁️ TikTok" if t.get("execution_mode") == "SERVER_SCHEDULE" else "💻 Máy"

                if st == "SUCCESS":
                    status_str = "🟢 Đã đăng"
                    tag = "success_tag"
                elif st == "SERVER_SCHEDULED":
                    status_str = "☁️ Đã hẹn TikTok"
                    tag = "server_tag"
                elif st == "FAILED":
                    status_str = "❌ Thất bại"
                    tag = "failed_tag"
                elif st == "CANCELLED":
                    status_str = "⚪ Đã hủy"
                    tag = "cancel_tag"
                elif st == "MISSED_RESCHEDULED":
                    status_str = "⏳ Dời sang mai"
                    tag = "pending_tag"
                elif st == "UPLOADING":
                    status_str = "⚡ Đang tải..."
                    tag = "server_tag"
                else:
                    status_str = "⏳ Đang chờ"
                    tag = "pending_tag"

                self.tree.insert(
                    "",
                    "end",
                    iid=tid,
                    values=(
                        time_str,
                        t.get("profile_name", ""),
                        f"@{t.get('tiktok_account', '')}" if t.get("tiktok_account") else "-",
                        t.get("video_filename", ""),
                        t.get("title", ""),
                        mode,
                        status_str,
                    ),
                    tags=(tag,),
                )
        except Exception:
            pass

        # 7. Cập nhật pagination widgets (P2-04)
        try:
            if self._widget_alive(self.lbl_page_info):
                if self.total_count == 0:
                    from_i, to_i = 0, 0
                else:
                    from_i = offset + 1
                    to_i = min(offset + (limit if limit else self.total_count), self.total_count)
                self.lbl_page_info.configure(text=f"Hiển thị {from_i} - {to_i} / Tổng {self.total_count} tác vụ")

            if self._widget_alive(self.lbl_current_page):
                self.lbl_current_page.configure(text=f"Trang {self.current_page} / {self.total_pages}")

            if self._widget_alive(self.btn_prev_page):
                self.btn_prev_page.configure(state="normal" if self.current_page > 1 else "disabled")

            if self._widget_alive(self.btn_next_page):
                self.btn_next_page.configure(state="normal" if self.current_page < self.total_pages else "disabled")
        except Exception:
            pass

    def _get_selected_task_ids(self) -> List[str]:
        if not self._is_alive():
            return []
        try:
            return list(self.tree.selection())
        except Exception:
            return []

    def _do_start_queue(self):
        if not self._is_alive():
            return
        selected = self._get_selected_task_ids()
        start_handler = self.handlers.get("start_queue_handler")
        try:
            if callable(start_handler):
                start_handler(selected)
            else:
                messagebox.showinfo("Thông báo", "Đang khởi động hàng đợi đăng bài tự động.")
        except Exception as ex:
            import logging
            logging.getLogger("AutoScheduleView").error(f"Lỗi khởi động hàng đợi: {ex}")
        self._refresh_data(keep_page=True)

    def _do_pause_queue(self):
        if not self._is_alive():
            return
        pause_handler = self.handlers.get("pause_queue_handler")
        try:
            if callable(pause_handler):
                pause_handler()
            else:
                messagebox.showinfo("Thông báo", "Đã tạm dừng hàng đợi đăng bài.")
        except Exception as ex:
            import logging
            logging.getLogger("AutoScheduleView").error(f"Lỗi tạm dừng hàng đợi: {ex}")
        self._refresh_data(keep_page=True)

    def _do_post_now(self):
        if not self._is_alive():
            return
        selected = self._get_selected_task_ids()
        if not selected:
            try:
                messagebox.showwarning("Thông báo", "Vui lòng chọn ít nhất một tác vụ để đăng ngay.")
            except Exception:
                pass
            return

        post_handler = self.handlers.get("post_now_handler")
        try:
            if callable(post_handler):
                post_handler(selected)
            else:
                messagebox.showinfo("Thông báo", f"Đã gửi yêu cầu đăng ngay {len(selected)} video.")
        except Exception as ex:
            import logging
            logging.getLogger("AutoScheduleView").error(f"Lỗi đăng ngay: {ex}")
        self._refresh_data(keep_page=True)

    def _notify_change(self):
        if not self._is_alive():
            return
        handler = self.handlers.get("on_change_handler")
        if callable(handler):
            try:
                handler()
            except Exception:
                pass

    def _do_retry(self):
        if not self._is_alive():
            return
        selected = self._get_selected_task_ids()
        if not selected:
            try:
                messagebox.showwarning("Thông báo", "Vui lòng chọn ít nhất một tác vụ lỗi để thử lại.")
            except Exception:
                pass
            return
        try:
            retried = self.schedule_manager.retry_tasks(selected)
            self._refresh_data(keep_page=True)
            self._notify_change()
            messagebox.showinfo("Thành công", f"Đã đưa {retried} tác vụ về hàng đợi chờ đăng.")
        except Exception as ex:
            try:
                messagebox.showerror("Lỗi", f"Không thể thử lại tác vụ:\n{ex}")
            except Exception:
                pass

    def _do_cancel(self):
        if not self._is_alive():
            return
        selected = self._get_selected_task_ids()
        if not selected:
            try:
                messagebox.showwarning("Thông báo", "Vui lòng chọn ít nhất một tác vụ để hủy.")
            except Exception:
                pass
            return
        try:
            confirmed = messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn hủy {len(selected)} tác vụ đang chọn?")
        except Exception:
            confirmed = False
        if not self._is_alive():
            return
        if confirmed:
            try:
                cancelled = self.schedule_manager.cancel_tasks(selected)
                self._refresh_data(keep_page=True)
                self._notify_change()
                messagebox.showinfo("Thành công", f"Đã hủy thành công {cancelled} tác vụ.")
            except Exception as ex:
                try:
                    messagebox.showerror("Lỗi", f"Không thể hủy tác vụ:\n{ex}")
                except Exception:
                    pass

    def _do_delete(self):
        if not self._is_alive():
            return
        selected = self._get_selected_task_ids()
        if not selected:
            try:
                messagebox.showwarning("Thông báo", "Vui lòng chọn ít nhất một bản ghi để xóa.")
            except Exception:
                pass
            return
        try:
            confirmed = messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn xóa vĩnh viễn {len(selected)} bản ghi này khỏi lịch?")
        except Exception:
            confirmed = False
        if not self._is_alive():
            return
        if confirmed:
            try:
                deleted = self.schedule_manager.delete_tasks(selected)
                self._refresh_data(keep_page=True)
                self._notify_change()
                messagebox.showinfo("Thành công", f"Đã xóa {deleted} bản ghi.")
            except Exception as ex:
                try:
                    messagebox.showerror("Lỗi", f"Không thể xóa bản ghi:\n{ex}")
                except Exception:
                    pass

    def _do_clear_finished(self):
        if not self._is_alive():
            return
        try:
            cleared = self.schedule_manager.clear_finished_tasks()
            self._refresh_data(keep_page=True)
            self._notify_change()
            messagebox.showinfo("Dọn dẹp", f"Đã dọn dẹp {cleared} bản ghi đã hoàn thành hoặc đã hủy.")
        except Exception as ex:
            try:
                messagebox.showerror("Lỗi", f"Không thể dọn dẹp bản ghi:\n{ex}")
            except Exception:
                pass

    def _on_double_click_row(self, _event):
        if not self._is_alive():
            return
        selected = self._get_selected_task_ids()
        if not selected:
            return
        tid = selected[0]
        try:
            task = self.schedule_manager.get_task_by_id(tid)
        except Exception:
            task = None
        if not task:
            return

        err = task.get("error_message")
        msg = (
            f"ID: {task.get('id')}\n"
            f"Profile: {task.get('profile_name')} (@{task.get('tiktok_account')})\n"
            f"Video: {task.get('video_filename')}\n"
            f"Đường dẫn: {task.get('video_path')}\n"
            f"Tiêu đề: {task.get('title')}\n"
            f"Thời gian hẹn: {task.get('scheduled_time')}\n"
            f"Trạng thái: {task.get('status')}\n"
            f"Chế độ: {task.get('execution_mode')}\n"
        )
        if err:
            msg += f"\n❌ Chi tiết lỗi:\n{err}"

        try:
            messagebox.showinfo("Chi Tiết Tác Vụ Lên Lịch", msg)
        except Exception:
            pass
