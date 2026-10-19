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
    scan_video_files,
)
from schedule_manager import ScheduleManager
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

    def _cancel(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


# ==============================================================================
# 2. BULK SCHEDULE MODAL (1-CLICK TO SCHEDULE)
# ==============================================================================

class BulkScheduleModal(SafeCTkToplevel):
    """Modal 1-Click to Schedule phân bổ và lên lịch cho nhiều profile cùng lúc."""

    def __init__(
        self,
        parent: Any,
        selected_profiles: Sequence[str],
        profile_configs: Optional[Dict[str, Any]] = None,
        schedule_manager: Optional[ScheduleManager] = None,
        on_success: Optional[Callable[[], None]] = None,
        on_start_server_upload: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ):
        toplevel_parent = parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent
        super().__init__(toplevel_parent)
        self.title("Lên Lịch Đăng TikTok Tự Động")
        fit_and_center_dialog(self, 720, 780, parent=toplevel_parent, min_w=580, min_h=520)
        self.transient(toplevel_parent)
        self.grab_set()

        self.selected_profiles = [str(p).strip() for p in selected_profiles if str(p).strip()]
        self.profile_configs = profile_configs or {}
        self.schedule_manager = schedule_manager or ScheduleManager()
        self.on_success = on_success
        self.on_start_server_upload = on_start_server_upload

        self.scanned_videos: List[Path] = []
        self.formula_var = ctk.StringVar(value="{filename}")
        self.preview_var = ctk.StringVar(value="")
        self.daily_max_var = ctk.StringVar(value="2")
        self.jitter_var = ctk.StringVar(value="10")
        self.exec_mode_var = ctk.StringVar(value="SERVER_SCHEDULE")  # SERVER_SCHEDULE | LOCAL_QUEUE
        self.status_info_var = ctk.StringVar(value="")

        # Checkboxes cho 4 khung giờ vàng
        self.slot_sang_var = ctk.BooleanVar(value=True)
        self.slot_trua_var = ctk.BooleanVar(value=True)
        self.slot_chieu_var = ctk.BooleanVar(value=True)
        self.slot_toi_var = ctk.BooleanVar(value=True)

        self._build_ui()
        self._update_preview()

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
        self.formula_var.trace_add("write", lambda *_: self._update_preview())

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

        # 5. Phương Thức Thực Thi (Server-Side vs Local-Queue)
        exec_card = ctk.CTkFrame(
            scroll,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        exec_card.pack(fill="x", pady=(0, 6))

        e_inner = ctk.CTkFrame(exec_card, fg_color="transparent")
        e_inner.pack(fill="x", padx=12, pady=10)

        ctk.CTkLabel(
            e_inner,
            text="🚀 Phương Thức Thực Thi Hẹn Giờ",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkRadioButton(
            e_inner,
            text="☁️ Lên lịch trực tiếp lên Server TikTok qua API (TẮT MÁY / TẮT TOOL VẪN ĐĂNG ĐÚNG GIỜ 100%)",
            variable=self.exec_mode_var,
            value="SERVER_SCHEDULE",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.ACCENT_PRIMARY,
        ).pack(anchor="w", pady=(0, 4))

        ctk.CTkRadioButton(
            e_inner,
            text="💻 Quản lý hàng đợi trên máy tính (Lưu trữ trên máy, đến giờ hẹn Tool mới gọi API đẩy lên)",
            variable=self.exec_mode_var,
            value="LOCAL_QUEUE",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 6))

        self.delete_source_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            e_inner,
            text="🗑️ Tự động xóa file video gốc sau khi lên lịch/đăng thành công (giải phóng dung lượng)",
            variable=self.delete_source_var,
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", pady=(0, 2))

    def _update_preview(self):
        f = self.formula_var.get()
        p = self.selected_profiles[0] if self.selected_profiles else "Profile_01"
        acc = ""
        if isinstance(self.profile_configs, dict) and p in self.profile_configs:
            acc = self.profile_configs[p].get("tiktok_account") or ""
        sample_title = generate_title_preview(f, sample_filename="top_10_clips.mp4", profile_name=p, account_name=acc)
        self.preview_var.set(f"🔍 Xem trước: \"{sample_title}\"")

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
        count = len(videos)
        if count > 0:
            count_p = max(1, len(self.selected_profiles))
            avg = count / count_p
            self.lbl_video_count.configure(
                text=f"✅ Đã nạp {count} video hợp lệ (.mp4) -> Trung bình ~{avg:.1f} video / profile.",
                text_color=UIThemeTokens.STATUS_LIVE,
            )
            self.status_info_var.set("")
        else:
            self.lbl_video_count.configure(
                text="❌ Không tìm thấy video .mp4 hợp lệ nào trong đường dẫn đã chọn.",
                text_color=UIThemeTokens.STATUS_ERROR,
            )

    def _do_schedule(self):
        if not self.scanned_videos:
            self.status_info_var.set("⚠️ Vui lòng nạp ít nhất 1 video .mp4 trước khi lên lịch.")
            return

        # 1. Kiểm tra xung đột lịch cũ
        conflicts = self.schedule_manager.check_profile_conflicts(self.selected_profiles)
        if conflicts:
            # Mở modal xử lý xung đột
            ScheduleConflictModal(
                parent=self,
                conflicts=conflicts,
                on_resolve=lambda mode: self._execute_schedule(resolution_mode=mode),
            )
        else:
            self._execute_schedule(resolution_mode="APPEND")

    def _execute_schedule(self, resolution_mode: str = "APPEND"):
        """Thực thi phân bổ và sinh các task lịch đăng."""
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
        formula = self.formula_var.get().strip()

        # Phân bổ Round-Robin
        distribution = distribute_videos_round_robin(self.scanned_videos, self.selected_profiles)
        all_new_tasks: List[Dict[str, Any]] = []

        # Kiểm tra mốc bắt đầu nối tiếp nếu resolution_mode == 'APPEND'
        conflicts = self.schedule_manager.check_profile_conflicts(self.selected_profiles)

        for p_name, v_list in distribution.items():
            if not v_list:
                continue

            start_dt = datetime.datetime.now()
            if resolution_mode == "APPEND" and p_name in conflicts:
                latest = conflicts[p_name].get("latest_scheduled_time")
                if isinstance(latest, datetime.datetime):
                    # Bắt đầu từ ngày kế tiếp của lịch cũ
                    start_dt = datetime.datetime.combine(
                        latest.date() + datetime.timedelta(days=1),
                        datetime.time(hour=8, minute=0),
                    )

            # Sinh timeline các mốc thời gian
            timeline = generate_schedule_timeline(
                count=len(v_list),
                start_date=start_dt,
                max_daily_posts=daily_max,
                selected_slots=selected_slots,
                jitter_minutes=jitter,
            )

            cfg = self.profile_configs.get(p_name, {}) if isinstance(self.profile_configs, dict) else {}
            acc = cfg.get("tiktok_account") or ""

            for idx, v_path in enumerate(v_list):
                sched_dt = timeline[idx] if idx < len(timeline) else datetime.datetime.now()
                post_title = parse_naming_formula(
                    formula=formula,
                    video_path_or_name=v_path,
                    index=idx + 1,
                    profile_name=p_name,
                    account_name=acc,
                    schedule_dt=sched_dt,
                )

                task_data = {
                    "profile_name": p_name,
                    "tiktok_account": acc,
                    "video_path": str(v_path.resolve()),
                    "video_filename": v_path.name,
                    "title": post_title,
                    "scheduled_time": sched_dt.isoformat(),
                    "scheduled_epoch": int(sched_dt.timestamp()),
                    "golden_hour_slot": "AUTO",
                    "execution_mode": exec_mode,
                    "status": "PENDING",
                    "upload_engine": "DIRECT_API",
                    "delete_source_after_post": self.delete_source_var.get(),
                }
                all_new_tasks.append(task_data)

        # Áp dụng giải quyết xung đột (Skip / Overwrite nếu người dùng chọn)
        final_tasks = self.schedule_manager.apply_conflict_resolution(
            resolution_mode=resolution_mode,
            new_tasks=all_new_tasks,
            conflicted_profiles=list(conflicts.keys()),
        )

        if not final_tasks:
            self.status_info_var.set("ℹ️ Không có bài đăng nào cần lên lịch (đã bị bỏ qua theo yêu cầu).")
            return

        # Lưu tasks vào database JSON ở trạng thái PENDING
        created = self.schedule_manager.add_tasks(final_tasks)

        mode_desc = "Server TikTok" if exec_mode == "SERVER_SCHEDULE" else "Hàng đợi máy tính"
        messagebox.showinfo(
            "Đã Lên Lịch Thành Công",
            f"Đã phân bổ thành công {len(created)} video (Chế độ: {mode_desc})!\n\n"
            "Dữ liệu đã được nạp vào Tab 'Lịch Đăng'. Bạn có thể kiểm tra kỹ danh sách, "
            "tiêu đề và khung giờ hẹn.\n"
            "Khi sẵn sàng, hãy bấm nút '▶️ Bắt Đầu Chạy Lịch' để hệ thống tiến hành thực thi.",
        )

        self._cancel()
        if callable(self.on_success):
            self.on_success(created)

    def _cancel(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


# ==============================================================================
# 3. AUTO SCHEDULE VIEW (MAIN LEVEL-1 TAB)
# ==============================================================================

class AutoScheduleView(ctk.CTkFrame):
    """Tab Cấp 1 Quản lý Lịch Đăng Tự Động trên giao diện chính."""

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
        self.search_var.trace_add("write", lambda *_: self._refresh_data())

        # Status filter
        ctk.CTkLabel(f_inner, text="Trạng thái:", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left", padx=(0, 4))
        self.cb_status = ctk.CTkComboBox(
            f_inner,
            values=["Tất cả", "PENDING", "SERVER_SCHEDULED", "SUCCESS", "FAILED", "CANCELLED"],
            variable=self.status_filter_var,
            width=130,
            height=30,
            command=lambda _: self._refresh_data(),
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
            command=lambda _: self._refresh_data(),
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
            command=lambda _: self._refresh_data(),
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
            command=self._refresh_data,
        ).pack(side="right")

        # 3. Queue Table Card
        table_card = ctk.CTkFrame(
            self,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        table_card.pack(fill="both", expand=True, padx=16, pady=(0, 8))
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

    def _refresh_data(self):
        """Tải lại danh sách task và cập nhật bảng."""
        # 1. Cập nhật metrics
        stats = self.schedule_manager.get_summary_stats()
        self.stat_pending_var.set(str(stats.get("pending", 0)))
        self.stat_server_var.set(str(stats.get("server_scheduled", 0)))
        self.stat_today_var.set(str(stats.get("posted_today", 0)))
        self.stat_failed_var.set(str(stats.get("failed", 0)))

        # 2. Đọc tasks
        tasks = self.schedule_manager.get_all_tasks(
            filter_status=self.status_filter_var.get(),
            filter_profile=self.profile_filter_var.get(),
            filter_mode=self.mode_filter_var.get(),
            search_query=self.search_var.get(),
        )

        # Cập nhật danh sách profile cho combobox
        all_unfiltered = self.schedule_manager.get_all_tasks()
        all_profs = sorted(list(set(t.get("profile_name", "") for t in all_unfiltered if t.get("profile_name"))))
        cur_prof = self.profile_filter_var.get()
        self.cb_profile.configure(values=["Tất cả"] + all_profs)
        if cur_prof not in (["Tất cả"] + all_profs):
            self.profile_filter_var.set("Tất cả")

        # Cập nhật Treeview
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

    def _get_selected_task_ids(self) -> List[str]:
        return list(self.tree.selection())

    def _do_start_queue(self):
        selected = self._get_selected_task_ids()
        start_handler = self.handlers.get("start_queue_handler")
        if callable(start_handler):
            start_handler(selected)
        else:
            messagebox.showinfo("Thông báo", "Đang khởi động hàng đợi đăng bài tự động.")
        self._refresh_data()

    def _do_pause_queue(self):
        pause_handler = self.handlers.get("pause_queue_handler")
        if callable(pause_handler):
            pause_handler()
        else:
            messagebox.showinfo("Thông báo", "Đã tạm dừng hàng đợi đăng bài.")
        self._refresh_data()

    def _do_post_now(self):
        selected = self._get_selected_task_ids()
        if not selected:
            messagebox.showwarning("Thông báo", "Vui lòng chọn ít nhất một tác vụ để đăng ngay.")
            return

        post_handler = self.handlers.get("post_now_handler")
        if callable(post_handler):
            post_handler(selected)
        else:
            messagebox.showinfo("Thông báo", f"Đã gửi yêu cầu đăng ngay {len(selected)} video.")
        self._refresh_data()

    def _notify_change(self):
        handler = self.handlers.get("on_change_handler")
        if callable(handler):
            try:
                handler()
            except Exception:
                pass

    def _do_retry(self):
        selected = self._get_selected_task_ids()
        if not selected:
            messagebox.showwarning("Thông báo", "Vui lòng chọn ít nhất một tác vụ lỗi để thử lại.")
            return
        retried = self.schedule_manager.retry_tasks(selected)
        self._refresh_data()
        self._notify_change()
        messagebox.showinfo("Thành công", f"Đã đưa {retried} tác vụ về hàng đợi chờ đăng.")

    def _do_cancel(self):
        selected = self._get_selected_task_ids()
        if not selected:
            messagebox.showwarning("Thông báo", "Vui lòng chọn ít nhất một tác vụ để hủy.")
            return
        if messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn hủy {len(selected)} tác vụ đang chọn?"):
            cancelled = self.schedule_manager.cancel_tasks(selected)
            self._refresh_data()
            self._notify_change()
            messagebox.showinfo("Thành công", f"Đã hủy thành công {cancelled} tác vụ.")

    def _do_delete(self):
        selected = self._get_selected_task_ids()
        if not selected:
            messagebox.showwarning("Thông báo", "Vui lòng chọn ít nhất một bản ghi để xóa.")
            return
        if messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn xóa vĩnh viễn {len(selected)} bản ghi này khỏi lịch?"):
            deleted = self.schedule_manager.delete_tasks(selected)
            self._refresh_data()
            self._notify_change()
            messagebox.showinfo("Thành công", f"Đã xóa {deleted} bản ghi.")

    def _do_clear_finished(self):
        cleared = self.schedule_manager.clear_finished_tasks()
        self._refresh_data()
        self._notify_change()
        messagebox.showinfo("Dọn dẹp", f"Đã dọn dẹp {cleared} bản ghi đã hoàn thành hoặc đã hủy.")

    def _on_double_click_row(self, _event):
        selected = self._get_selected_task_ids()
        if not selected:
            return
        tid = selected[0]
        task = self.schedule_manager.get_task_by_id(tid)
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

        messagebox.showinfo("Chi Tiết Tác Vụ Lên Lịch", msg)
