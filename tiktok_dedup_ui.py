"""
tiktok_dedup_ui.py - Giao diện quản trị Dedup Watchdog Engine (Phát hiện video trùng lặp, shadowban).

Bao gồm:
- KPI Banner: Số video trùng phát hiện, số video đã xử lý, trạng thái Watchdog.
- Control Card: Bật/Tắt Watchdog, chọn chế độ (Dry-Run / Ẩn Private / Xóa), chu kỳ quét, giãn cách profile.
- Bảng Nhật ký Audit Log (Treeview): Chi tiết video trùng, ID video gốc, đối chiếu link, trạng thái xử lý.
- Bảng Danh sách Watchlist: Quản lý bật/tắt theo dõi từng profile, quét tức thì theo yêu cầu.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import threading
import time
import webbrowser
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog

from ui_components import UIThemeTokens
from tiktok_dedup_engine import (
    DedupConfig,
    DedupDatabase,
    DedupWatchdogWorker,
    ProfileContext,
    TikTokDedupApiClient,
    analyze_tiktok_video,
    VideoItemAnalysis,
)

logger = logging.getLogger("DedupUI")


class DedupWatchdogView(ctk.CTkFrame):
    """Giao diện chính của module Dedup Watchdog Engine."""

    def __init__(
        self,
        parent,
        db: DedupDatabase,
        worker: DedupWatchdogWorker,
        get_profiles_func: Callable[[], Sequence[ProfileContext]],
        *args,
        **kwargs,
    ):
        super().__init__(parent, fg_color="transparent", *args, **kwargs)
        self.db = db
        self.worker = worker
        self.get_profiles_func = get_profiles_func

        self._active_subtab = "logs"  # 'logs' | 'watchlist' | 'guide'

        self._build_ui()
        self.refresh_data()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ----------------------------------------------------------------------
        # 1. HEADER & KPI BANNER
        # ----------------------------------------------------------------------
        header_frame = ctk.CTkFrame(self, fg_color=UIThemeTokens.BG_CARD, corner_radius=8)
        header_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        header_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # KPI 1: Trạng thái Worker
        self.kpi_status = ctk.CTkFrame(header_frame, fg_color="transparent")
        self.kpi_status.grid(row=0, column=0, padx=12, pady=10, sticky="w")
        ctk.CTkLabel(self.kpi_status, text="Trạng thái Watchdog", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        self.lbl_status_val = ctk.CTkLabel(self.kpi_status, text="Đang dừng", font=("Segoe UI Bold", 14), text_color="#ef4444")
        self.lbl_status_val.pack(anchor="w")

        # KPI 2: Chế độ hành động
        self.kpi_action = ctk.CTkFrame(header_frame, fg_color="transparent")
        self.kpi_action.grid(row=0, column=1, padx=12, pady=10, sticky="w")
        ctk.CTkLabel(self.kpi_action, text="Chế độ xử lý", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        self.lbl_action_val = ctk.CTkLabel(self.kpi_action, text="Chạy thử (Dry-Run)", font=("Segoe UI Bold", 14), text_color="#f59e0b")
        self.lbl_action_val.pack(anchor="w")

        # KPI 3: Tổng phát hiện trùng
        self.kpi_detected = ctk.CTkFrame(header_frame, fg_color="transparent")
        self.kpi_detected.grid(row=0, column=2, padx=12, pady=10, sticky="w")
        ctk.CTkLabel(self.kpi_detected, text="Video trùng phát hiện", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        self.lbl_detected_val = ctk.CTkLabel(self.kpi_detected, text="0", font=("Segoe UI Bold", 14), text_color=UIThemeTokens.TEXT_PRIMARY)
        self.lbl_detected_val.pack(anchor="w")

        # KPI 4: Đã xử lý (Xóa / Ẩn)
        self.kpi_handled = ctk.CTkFrame(header_frame, fg_color="transparent")
        self.kpi_handled.grid(row=0, column=3, padx=12, pady=10, sticky="w")
        ctk.CTkLabel(self.kpi_handled, text="Đã Xóa / Ẩn bảo vệ kênh", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        self.lbl_handled_val = ctk.CTkLabel(self.kpi_handled, text="0", font=("Segoe UI Bold", 14), text_color="#10b981")
        self.lbl_handled_val.pack(anchor="w")

        # ----------------------------------------------------------------------
        # 2. CONTROL BAR & SETTINGS
        # ----------------------------------------------------------------------
        control_frame = ctk.CTkFrame(self, fg_color=UIThemeTokens.BG_CARD, corner_radius=8)
        control_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=4)

        ctrl_inner = ctk.CTkFrame(control_frame, fg_color="transparent")
        ctrl_inner.pack(fill="x", padx=12, pady=8)

        # Nút Bật/Tắt Watchdog
        self.btn_toggle = ctk.CTkButton(
            ctrl_inner,
            text="▶ Bắt Đầu Giám Sát",
            font=("Segoe UI Semibold", 12),
            width=160,
            height=32,
            fg_color="#10b981",
            hover_color="#059669",
            command=self._toggle_watchdog,
        )
        self.btn_toggle.pack(side="left", padx=(0, 10))

        # Chọn hành động (Action)
        ctk.CTkLabel(ctrl_inner, text="Hành động:", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 4))
        self.action_var = ctk.StringVar(value="dry_run")
        self.combo_action = ctk.CTkComboBox(
            ctrl_inner,
            values=["Chạy thử (Dry-Run)", "Ẩn Private (Chỉ mình tôi)", "Xóa vĩnh viễn (Delete)"],
            variable=self.action_var,
            width=190,
            height=32,
            command=self._on_action_changed,
        )
        self.combo_action.pack(side="left", padx=(0, 10))

        # Chu kỳ quét (Interval)
        ctk.CTkLabel(ctrl_inner, text="Chu kỳ:", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 4))
        self.interval_var = ctk.StringVar(value="60 phút")
        self.combo_interval = ctk.CTkComboBox(
            ctrl_inner,
            values=["30 phút", "60 phút", "120 phút", "360 phút", "720 phút"],
            variable=self.interval_var,
            width=100,
            height=32,
            command=self._on_interval_changed,
        )
        self.combo_interval.pack(side="left", padx=(0, 10))

        # Nút Quét ngay (Scan All Now)
        self.btn_scan_all = ctk.CTkButton(
            ctrl_inner,
            text="⚡ Quét Ngay Tất Cả",
            font=("Segoe UI Semibold", 12),
            width=140,
            height=32,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._scan_all_profiles_now,
        )
        self.btn_scan_all.pack(side="left", padx=(0, 10))

        # Nút Làm mới dữ liệu
        self.btn_refresh = ctk.CTkButton(
            ctrl_inner,
            text="🔄 Làm Mới",
            font=("Segoe UI Semibold", 12),
            width=90,
            height=32,
            fg_color="#64748b",
            hover_color="#475569",
            command=self.refresh_data,
        )
        self.btn_refresh.pack(side="right")

        # ----------------------------------------------------------------------
        # 3. CONTENT AREA (SUBTABS & DATA TABLES)
        # ----------------------------------------------------------------------
        content_card = ctk.CTkFrame(self, fg_color=UIThemeTokens.BG_CARD, corner_radius=8)
        content_card.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        content_card.grid_columnconfigure(0, weight=1)
        content_card.grid_rowconfigure(1, weight=1)

        # Thanh chọn Subtab
        subtab_bar = ctk.CTkFrame(content_card, fg_color="transparent")
        subtab_bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))

        self.btn_subtab_logs = ctk.CTkButton(
            subtab_bar,
            text="📋 Nhật Ký Audit Log",
            width=160,
            height=30,
            font=("Segoe UI Semibold", 11),
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            command=lambda: self._switch_subtab("logs"),
        )
        self.btn_subtab_logs.pack(side="left", padx=(0, 6))

        self.btn_subtab_watch = ctk.CTkButton(
            subtab_bar,
            text="👥 Danh Sách Kênh (Watchlist)",
            width=190,
            height=30,
            font=("Segoe UI Semibold", 11),
            fg_color="#e2e8f0",
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color="#cbd5e1",
            command=lambda: self._switch_subtab("watchlist"),
        )
        self.btn_subtab_watch.pack(side="left", padx=(0, 6))

        self.btn_subtab_channel = ctk.CTkButton(
            subtab_bar,
            text="🔍 Check Trùng Kênh Bất Kỳ",
            width=190,
            height=30,
            font=("Segoe UI Semibold", 11),
            fg_color="#e2e8f0",
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color="#cbd5e1",
            command=lambda: self._switch_subtab("channel"),
        )
        self.btn_subtab_channel.pack(side="left", padx=(0, 6))

        # --- SUBTAB 1: AUDIT LOGS CONTAINER ---
        self.logs_container = ctk.CTkFrame(content_card, fg_color="transparent")
        self.logs_container.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.logs_container.grid_columnconfigure(0, weight=1)
        self.logs_container.grid_rowconfigure(0, weight=1)

        # Treeview cho Audit Log
        log_cols = ("channel", "aweme_id", "group_id", "desc", "create_time", "detected_at", "action", "status", "error")
        self.log_tree = ttk.Treeview(
            self.logs_container,
            style="Modern.Treeview",
            columns=log_cols,
            show="headings",
            selectmode="browse",
        )
        self.log_tree.heading("channel", text="Kênh / Profile")
        self.log_tree.heading("aweme_id", text="Aweme ID (Trùng)")
        self.log_tree.heading("group_id", text="Group ID (Gốc)")
        self.log_tree.heading("desc", text="Tiêu Đề Video")
        self.log_tree.heading("create_time", text="Thời Điểm Đăng")
        self.log_tree.heading("detected_at", text="Phát Hiện Lúc")
        self.log_tree.heading("action", text="Cấu Hình")
        self.log_tree.heading("status", text="Trạng Thái")
        self.log_tree.heading("error", text="Chi Tiết Lỗi")

        self.log_tree.column("channel", width=120, anchor="w")
        self.log_tree.column("aweme_id", width=140, anchor="center")
        self.log_tree.column("group_id", width=140, anchor="center")
        self.log_tree.column("desc", width=220, anchor="w")
        self.log_tree.column("create_time", width=120, anchor="center")
        self.log_tree.column("detected_at", width=120, anchor="center")
        self.log_tree.column("action", width=80, anchor="center")
        self.log_tree.column("status", width=90, anchor="center")
        self.log_tree.column("error", width=150, anchor="w")

        self.log_tree.grid(row=0, column=0, sticky="nsew")
        log_vsb = ttk.Scrollbar(self.logs_container, orient="vertical", command=self.log_tree.yview)
        log_vsb.grid(row=0, column=1, sticky="ns")
        self.log_tree.configure(yscrollcommand=log_vsb.set)

        # Audit Log Action Buttons
        log_actions = ctk.CTkFrame(self.logs_container, fg_color="transparent")
        log_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.btn_open_original = ctk.CTkButton(
            log_actions,
            text="🔗 Mở Video Gốc Đối Chiếu",
            font=("Segoe UI Semibold", 11),
            width=180,
            height=28,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._open_selected_original_video,
        )
        self.btn_open_original.pack(side="left", padx=(0, 8))

        # --- SUBTAB 2: WATCHLIST CONTAINER ---
        self.watch_container = ctk.CTkFrame(content_card, fg_color="transparent")
        self.watch_container.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.watch_container.grid_columnconfigure(0, weight=1)
        self.watch_container.grid_rowconfigure(0, weight=1)
        self.watch_container.grid_remove()  # Mặc định ẩn

        watch_cols = ("profile_id", "channel", "added_at", "next_check", "status")
        self.watch_tree = ttk.Treeview(
            self.watch_container,
            style="Modern.Treeview",
            columns=watch_cols,
            show="headings",
            selectmode="browse",
        )
        self.watch_tree.heading("profile_id", text="Profile ID")
        self.watch_tree.heading("channel", text="Tài Khoản TikTok")
        self.watch_tree.heading("added_at", text="Ngày Thêm")
        self.watch_tree.heading("next_check", text="Lần Quét Tới")
        self.watch_tree.heading("status", text="Trạng Thái")

        self.watch_tree.column("profile_id", width=140, anchor="w")
        self.watch_tree.column("channel", width=160, anchor="w")
        self.watch_tree.column("added_at", width=130, anchor="center")
        self.watch_tree.column("next_check", width=130, anchor="center")
        self.watch_tree.column("status", width=110, anchor="center")

        self.watch_tree.grid(row=0, column=0, sticky="nsew")
        watch_vsb = ttk.Scrollbar(self.watch_container, orient="vertical", command=self.watch_tree.yview)
        watch_vsb.grid(row=0, column=1, sticky="ns")
        self.watch_tree.configure(yscrollcommand=watch_vsb.set)

        watch_actions = ctk.CTkFrame(self.watch_container, fg_color="transparent")
        watch_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        self.btn_pause_profile = ctk.CTkButton(
            watch_actions,
            text="⏸️ Tạm Dừng / Tiếp Tục",
            font=("Segoe UI Semibold", 11),
            width=160,
            height=28,
            fg_color="#64748b",
            hover_color="#475569",
            command=self._toggle_selected_profile_pause,
        )
        self.btn_pause_profile.pack(side="left", padx=(0, 8))

        self.btn_scan_single = ctk.CTkButton(
            watch_actions,
            text="⚡ Quét Ngay Profile Này",
            font=("Segoe UI Semibold", 11),
            width=170,
            height=28,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._scan_selected_profile_now,
        )
        self.btn_scan_single.pack(side="left")

        # --- SUBTAB 3: CHANNEL CHECK CONTAINER ---
        self.channel_container = ctk.CTkFrame(content_card, fg_color="transparent")
        self.channel_container.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.channel_container.grid_columnconfigure(0, weight=1)
        self.channel_container.grid_rowconfigure(2, weight=1)
        self.channel_container.grid_remove()  # Mặc định ẩn

        # 3.1 Input Search Bar
        channel_bar = ctk.CTkFrame(self.channel_container, fg_color="transparent")
        channel_bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        channel_bar.grid_columnconfigure(0, weight=1)

        self.channel_input_var = ctk.StringVar(value="@komugi_no_daidokoro")
        self.entry_channel = ctk.CTkEntry(
            channel_bar,
            textvariable=self.channel_input_var,
            placeholder_text="Nhập @username hoặc link profile TikTok (VD: @komugi_no_daidokoro)...",
            height=32,
            font=("Segoe UI", 11),
        )
        self.entry_channel.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.btn_check_channel = ctk.CTkButton(
            channel_bar,
            text="🔍 Kiểm Tra Ngay",
            font=("Segoe UI Semibold", 11),
            width=130,
            height=32,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._on_check_channel_clicked,
        )
        self.btn_check_channel.grid(row=0, column=1, padx=(0, 6))

        self.btn_load_sample = ctk.CTkButton(
            channel_bar,
            text="📁 Tải Mẫu DataRes",
            font=("Segoe UI Semibold", 11),
            width=150,
            height=32,
            fg_color="#64748b",
            hover_color="#475569",
            command=self._load_sample_datares,
        )
        self.btn_load_sample.grid(row=0, column=2)

        # 3.2 Channel Profile Summary Card
        self.channel_meta_frame = ctk.CTkFrame(self.channel_container, fg_color="#f8fafc", corner_radius=6, border_width=1, border_color="#e2e8f0")
        self.channel_meta_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.channel_meta_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.lbl_channel_title = ctk.CTkLabel(
            self.channel_meta_frame,
            text="Kênh: Chưa tải dữ liệu",
            font=("Segoe UI Bold", 12),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.lbl_channel_title.grid(row=0, column=0, padx=12, pady=8, sticky="w")

        self.lbl_channel_stats = ctk.CTkLabel(
            self.channel_meta_frame,
            text="Followers: 0  |  Tổng bài: 0",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_channel_stats.grid(row=0, column=1, padx=8, pady=8, sticky="w")

        self.lbl_channel_loaded = ctk.CTkLabel(
            self.channel_meta_frame,
            text="Đã tải: 0 bài",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_channel_loaded.grid(row=0, column=2, padx=8, pady=8, sticky="w")

        self.lbl_channel_dup_badge = ctk.CTkLabel(
            self.channel_meta_frame,
            text="Chưa kiểm tra",
            font=("Segoe UI Bold", 11),
            text_color="#64748b",
        )
        self.lbl_channel_dup_badge.grid(row=0, column=3, padx=12, pady=8, sticky="e")

        # 3.3 Treeview Table for Channel Posts
        channel_tree_frame = ctk.CTkFrame(self.channel_container, fg_color="transparent")
        channel_tree_frame.grid(row=2, column=0, sticky="nsew")
        channel_tree_frame.grid_columnconfigure(0, weight=1)
        channel_tree_frame.grid_rowconfigure(0, weight=1)

        ch_cols = ("idx", "aweme_id", "group_id", "desc", "views", "create_time", "shadow", "dup_status")
        self.channel_tree = ttk.Treeview(
            channel_tree_frame,
            style="Modern.Treeview",
            columns=ch_cols,
            show="headings",
            selectmode="browse",
        )
        self.channel_tree.heading("idx", text="#")
        self.channel_tree.heading("aweme_id", text="Aweme ID (Bài Viết)")
        self.channel_tree.heading("group_id", text="Group ID (Gốc)")
        self.channel_tree.heading("desc", text="Tiêu Đề / Mô Tả Video")
        self.channel_tree.heading("views", text="Lượt Xem")
        self.channel_tree.heading("create_time", text="Ngày Đăng")
        self.channel_tree.heading("shadow", text="Shadowban / Kiểm Duyệt")
        self.channel_tree.heading("dup_status", text="Trạng Thái Trùng")

        self.channel_tree.column("idx", width=40, anchor="center")
        self.channel_tree.column("aweme_id", width=140, anchor="center")
        self.channel_tree.column("group_id", width=140, anchor="center")
        self.channel_tree.column("desc", width=260, anchor="w")
        self.channel_tree.column("views", width=80, anchor="center")
        self.channel_tree.column("create_time", width=120, anchor="center")
        self.channel_tree.column("shadow", width=150, anchor="w")
        self.channel_tree.column("dup_status", width=180, anchor="center")

        self.channel_tree.grid(row=0, column=0, sticky="nsew")
        ch_vsb = ttk.Scrollbar(channel_tree_frame, orient="vertical", command=self.channel_tree.yview)
        ch_vsb.grid(row=0, column=1, sticky="ns")
        self.channel_tree.configure(yscrollcommand=ch_vsb.set)

        # 3.4 Action Buttons for Channel Check
        ch_actions = ctk.CTkFrame(self.channel_container, fg_color="transparent")
        ch_actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))

        self.btn_channel_open_original = ctk.CTkButton(
            ch_actions,
            text="🔗 Mở Video Gốc Đối Chiếu (TikTok)",
            font=("Segoe UI Semibold", 11),
            width=210,
            height=28,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._open_channel_selected_original,
        )
        self.btn_channel_open_original.pack(side="left", padx=(0, 8))

        self.btn_channel_open_current = ctk.CTkButton(
            ch_actions,
            text="🌐 Mở Video Này (TikTok)",
            font=("Segoe UI Semibold", 11),
            width=160,
            height=28,
            fg_color="#64748b",
            hover_color="#475569",
            command=self._open_channel_selected_current,
        )
        self.btn_channel_open_current.pack(side="left", padx=(0, 8))

        self.btn_channel_save_audit = ctk.CTkButton(
            ch_actions,
            text="💾 Lưu Video Trùng Vào Nhật Ký Audit Log",
            font=("Segoe UI Semibold", 11),
            width=240,
            height=28,
            fg_color="#10b981",
            hover_color="#059669",
            command=self._save_channel_duplicates_to_db,
        )
        self.btn_channel_save_audit.pack(side="left")

        # Lưu cache kết quả phân tích kênh hiện tại
        self._current_channel_analysis: List[VideoItemAnalysis] = []
        self._current_channel_username: str = ""

    # --------------------------------------------------------------------------
    # CONTROLLER LOGIC
    # --------------------------------------------------------------------------

    def _switch_subtab(self, subtab_name: str) -> None:
        self._active_subtab = subtab_name
        self.btn_subtab_logs.configure(fg_color="#e2e8f0", text_color=UIThemeTokens.TEXT_PRIMARY)
        self.btn_subtab_watch.configure(fg_color="#e2e8f0", text_color=UIThemeTokens.TEXT_PRIMARY)
        self.btn_subtab_channel.configure(fg_color="#e2e8f0", text_color=UIThemeTokens.TEXT_PRIMARY)

        self.logs_container.grid_remove()
        self.watch_container.grid_remove()
        self.channel_container.grid_remove()

        if subtab_name == "logs":
            self.btn_subtab_logs.configure(fg_color=UIThemeTokens.ACCENT_PRIMARY, text_color="#ffffff")
            self.logs_container.grid()
        elif subtab_name == "watchlist":
            self.btn_subtab_watch.configure(fg_color=UIThemeTokens.ACCENT_PRIMARY, text_color="#ffffff")
            self.watch_container.grid()
        elif subtab_name == "channel":
            self.btn_subtab_channel.configure(fg_color=UIThemeTokens.ACCENT_PRIMARY, text_color="#ffffff")
            self.channel_container.grid()

        self.refresh_data()

    def _toggle_watchdog(self) -> None:
        if self.worker.is_running():
            self.worker.stop()
            self.worker.config.enabled = False
        else:
            self.worker.config.enabled = True
            self.worker.start()
        self.refresh_data()

    def _on_action_changed(self, choice: str) -> None:
        if "Xóa" in choice:
            self.worker.config.action = "delete"
        elif "Ẩn" in choice:
            self.worker.config.action = "hide"
        else:
            self.worker.config.action = "dry_run"
        self.refresh_data()

    def _on_interval_changed(self, choice: str) -> None:
        minutes = int(choice.replace("phút", "").strip())
        self.worker.config.interval_minutes = minutes
        self.refresh_data()

    def refresh_data(self) -> None:
        """Tải lại trạng thái KPI, Audit Log và Watchlist."""
        # 1. Cập nhật KPI
        is_running = self.worker.is_running()
        if is_running:
            self.lbl_status_val.configure(text="🟢 Đang Chạy", text_color="#10b981")
            self.btn_toggle.configure(text="⏹ Dừng Giám Sát", fg_color="#ef4444", hover_color="#dc2626")
        else:
            self.lbl_status_val.configure(text="🔴 Đang Dừng", text_color="#ef4444")
            self.btn_toggle.configure(text="▶ Bắt Đầu Giám Sát", fg_color="#10b981", hover_color="#059669")

        action = self.worker.config.action
        if action == "delete":
            self.lbl_action_val.configure(text="Xóa Vĩnh Viễn", text_color="#ef4444")
        elif action == "hide":
            self.lbl_action_val.configure(text="Ẩn Private", text_color="#3b82f6")
        else:
            self.lbl_action_val.configure(text="Chạy Thử (Dry-Run)", text_color="#f59e0b")

        # 2. Cập nhật Audit Log Treeview
        logs = self.db.get_audit_logs(limit=200)
        self.lbl_detected_val.configure(text=str(len(logs)))
        handled_count = sum(1 for item in logs if item.get("status") in ("deleted", "hidden"))
        self.lbl_handled_val.configure(text=str(handled_count))

        for row in self.log_tree.get_children():
            self.log_tree.delete(row)

        for l in logs:
            c_time_s = l.get("create_time") or 0
            c_time_str = datetime.fromtimestamp(c_time_s).strftime("%d/%m %H:%M") if c_time_s else "N/A"
            d_time_ms = l.get("detected_at") or 0
            d_time_str = datetime.fromtimestamp(d_time_ms / 1000.0).strftime("%d/%m %H:%M") if d_time_ms else "N/A"

            self.log_tree.insert(
                "",
                "end",
                values=(
                    l.get("channel") or l.get("profile_id"),
                    l.get("aweme_id"),
                    l.get("group_id"),
                    (l.get("desc") or "")[:40],
                    c_time_str,
                    d_time_str,
                    l.get("action"),
                    l.get("status"),
                    l.get("error") or "",
                ),
            )

        # 3. Đồng bộ và cập nhật Watchlist Treeview
        # Đảm bảo các profile trong configs.json được đăng ký vào dedup_watch
        try:
            current_profiles = self.get_profiles_func()
            for p in current_profiles:
                self.db.register_profile(p.profile_id, p.tiktok_account)
        except Exception:
            pass

        watchlist = self.db.get_all_watchlist()
        for row in self.watch_tree.get_children():
            self.watch_tree.delete(row)

        for w in watchlist:
            add_s = (w.get("added_at") or 0) / 1000.0
            add_str = datetime.fromtimestamp(add_s).strftime("%d/%m %H:%M") if add_s else "N/A"
            next_s = (w.get("next_check_at") or 0) / 1000.0
            next_str = datetime.fromtimestamp(next_s).strftime("%d/%m %H:%M") if next_s else "N/A"
            paused = bool(w.get("paused"))
            status_text = "Tạm dừng" if paused else "Đang theo dõi"

            self.watch_tree.insert(
                "",
                "end",
                values=(
                    w.get("profile_id"),
                    w.get("channel") or "N/A",
                    add_str,
                    next_str,
                    status_text,
                ),
            )

    def _open_selected_original_video(self) -> None:
        sel = self.log_tree.selection()
        if not sel:
            messagebox.showinfo("Thông báo", "Vui lòng chọn một dòng video trong bảng nhật ký.")
            return
        vals = self.log_tree.item(sel[0], "values")
        group_id = vals[2] if len(vals) > 2 else ""
        if not group_id:
            messagebox.showwarning("Lỗi", "Không tìm thấy Group ID của video gốc.")
            return
        url = f"https://www.tiktok.com/@tiktok/video/{group_id}"
        webbrowser.open(url)

    def _toggle_selected_profile_pause(self) -> None:
        sel = self.watch_tree.selection()
        if not sel:
            messagebox.showinfo("Thông báo", "Vui lòng chọn một profile trong bảng Watchlist.")
            return
        vals = self.watch_tree.item(sel[0], "values")
        pid = vals[0]
        status_text = vals[4]
        is_paused = (status_text == "Tạm dừng")
        self.db.set_profile_paused(pid, not is_paused)
        self.refresh_data()

    def _scan_selected_profile_now(self) -> None:
        sel = self.watch_tree.selection()
        if not sel:
            messagebox.showinfo("Thông báo", "Vui lòng chọn một profile trong bảng Watchlist.")
            return
        vals = self.watch_tree.item(sel[0], "values")
        pid = vals[0]

        profiles = {p.profile_id: p for p in self.get_profiles_func()}
        target_profile = profiles.get(pid)
        if not target_profile:
            messagebox.showerror("Lỗi", f"Không tìm thấy dữ liệu profile: {pid}")
            return

        def _worker():
            count = self.worker.scan_profile_now(target_profile)
            self.after(0, lambda: self._on_scan_finished(f"Quét hoàn tất: phát hiện {count} video trùng."))

        threading.Thread(target=_worker, daemon=True).start()
        messagebox.showinfo("Đang quét", f"Đang tiến hành quét bài đăng cho profile: {target_profile.tiktok_account}...")

    def _scan_all_profiles_now(self) -> None:
        profiles = list(self.get_profiles_func())
        if not profiles:
            messagebox.showinfo("Thông báo", "Không có profile nào để quét.")
            return

        def _worker():
            total = 0
            for p in profiles:
                total += self.worker.scan_profile_now(p)
                time.sleep(1.0)
            self.after(0, lambda: self._on_scan_finished(f"Quét toàn bộ hoàn tất: phát hiện {total} video trùng."))

        threading.Thread(target=_worker, daemon=True).start()
        messagebox.showinfo("Đang quét", f"Bắt đầu quét {len(profiles)} profile...")

    def _on_scan_finished(self, msg: str) -> None:
        self.refresh_data()
        messagebox.showinfo("Hoàn tất", msg)

    # --------------------------------------------------------------------------
    # SUBTAB 3 CONTROLLER METHODS: CHECK TRÙNG THEO KÊNH
    # --------------------------------------------------------------------------

    def _on_check_channel_clicked(self) -> None:
        input_val = self.channel_input_var.get().strip()
        if not input_val:
            messagebox.showwarning("Cảnh báo", "Vui lòng nhập @username hoặc liên kết trang cá nhân TikTok.")
            return

        self.btn_check_channel.configure(state="disabled", text="⏳ Đang Quét...")
        self.lbl_channel_title.configure(text=f"Đang phân tích kênh: {input_val}...")
        self.lbl_channel_dup_badge.configure(text="Đang xử lý...", text_color="#f59e0b")

        def _worker():
            try:
                profiles = list(self.get_profiles_func())
                ctx = None
                clean_input = input_val.lstrip("@").lower()
                for p in profiles:
                    if p.profile_id.lower() == clean_input or (p.tiktok_account and p.tiktok_account.lstrip("@").lower() == clean_input):
                        ctx = p
                        break
                if not ctx:
                    ctx = profiles[0] if profiles else ProfileContext(profile_id="manual", tiktok_account="")

                client = TikTokDedupApiClient(ctx)
                channel_info, posts = client.fetch_channel_posts(input_val, count=35)
                self.after(0, lambda: self._on_channel_scan_completed(channel_info, posts, input_val))
            except Exception as e:
                logger.exception("Lỗi quét kênh")
                self.after(0, lambda: self._on_channel_scan_failed(str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_channel_scan_failed(self, err_msg: str) -> None:
        self.btn_check_channel.configure(state="normal", text="🔍 Kiểm Tra Ngay")
        self.lbl_channel_title.configure(text="Lỗi khi tải dữ liệu kênh")
        self.lbl_channel_dup_badge.configure(text="Lỗi", text_color="#ef4444")
        messagebox.showerror("Lỗi", f"Không thể lấy dữ liệu kênh: {err_msg}")

    def _on_channel_scan_completed(self, channel_info: Dict[str, Any], posts: List[Dict[str, Any]], input_val: str) -> None:
        self.btn_check_channel.configure(state="normal", text="🔍 Kiểm Tra Ngay")
        self._populate_channel_table(channel_info, posts, input_val)

    def _load_sample_datares(self) -> None:
        """Tải dữ liệu mẫu từ datares.txt để kiểm tra tức thì."""
        sample_path = Path(__file__).resolve().parent / "datares.txt"
        if not sample_path.exists():
            sample_path = Path(r"C:\Users\huynh\AppData\Local\Programs\tiktokmanager\_decompiled\datares.txt")

        if not sample_path.exists():
            chosen = filedialog.askopenfilename(
                title="Chọn file dữ liệu mẫu TikTok (datares.txt hoặc JSON)",
                filetypes=[("Text/JSON files", "*.txt *.json"), ("All files", "*.*")],
            )
            if not chosen:
                return
            sample_path = Path(chosen)

        try:
            with open(sample_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            posts = data.get("aweme_list") or (data if isinstance(data, list) else data.get("itemList") or [])
            if not posts:
                messagebox.showwarning("Thông báo", "File không chứa danh sách aweme_list hoặc itemList hợp lệ.")
                return

            # Trích xuất metadata từ bài viết đầu tiên
            first_author = posts[0].get("author", {}) if isinstance(posts[0], dict) else {}
            channel_info = {
                "uniqueId": first_author.get("unique_id") or "komugi_no_daidokoro",
                "nickname": first_author.get("nickname") or "Tiểu Mạch Đồng Học",
                "followers": first_author.get("follower_count") or 10620,
                "videoCount": first_author.get("aweme_count") or len(posts),
            }
            self.channel_input_var.set(f"@{channel_info['uniqueId']}")
            self._populate_channel_table(channel_info, posts, f"@{channel_info['uniqueId']}")
            messagebox.showinfo("Thành công", f"Đã tải {len(posts)} bài viết từ file {sample_path.name}!")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể đọc file dữ liệu mẫu: {e}")

    def _populate_channel_table(self, channel_info: Dict[str, Any], posts: List[Dict[str, Any]], input_val: str) -> None:
        """Phân tích danh sách bài viết, cập nhật thẻ tổng kết và Treeview."""
        uname = channel_info.get("uniqueId") or TikTokDedupApiClient.extract_username(input_val)
        nick = channel_info.get("nickname") or uname
        f_count = channel_info.get("followers", 0)
        v_count = channel_info.get("videoCount", 0)

        self._current_channel_username = uname
        self.lbl_channel_title.configure(text=f"{nick} (@{uname})")
        self.lbl_channel_stats.configure(text=f"Followers: {f_count:,}  |  Tổng video kênh: {v_count}")
        self.lbl_channel_loaded.configure(text=f"Đã tải: {len(posts)} bài")

        # Phân tích từng video
        analyzed_list: List[VideoItemAnalysis] = [analyze_tiktok_video(p) for p in posts]
        self._current_channel_analysis = analyzed_list

        dup_items = [v for v in analyzed_list if v.is_dup]
        dup_count = len(dup_items)

        if dup_count > 0:
            self.lbl_channel_dup_badge.configure(
                text=f"⚠️ PHÁT HIỆN {dup_count} VIDEO TRÙNG LẶP!",
                text_color="#dc2626",
            )
        else:
            self.lbl_channel_dup_badge.configure(
                text="✅ KÊNH SẠCH - KHÔNG CÓ VIDEO TRÙNG",
                text_color="#16a34a",
            )

        for row in self.channel_tree.get_children():
            self.channel_tree.delete(row)

        for idx, item in enumerate(analyzed_list, start=1):
            c_time_s = item.create_time
            c_time_str = datetime.fromtimestamp(c_time_s).strftime("%d/%m/%Y %H:%M") if c_time_s else "N/A"

            if item.is_dup:
                dup_text = f"⚠️ TRÙNG LẶP (Gốc: {item.group_id})"
                tag = "dup"
            else:
                dup_text = "✅ Bản gốc"
                tag = "clean"

            if item.is_shadow:
                shadow_text = f"⚠️ {item.shadow_reason or 'Bị bóp tương tác'}"
            else:
                shadow_text = "Bình thường"

            self.channel_tree.insert(
                "",
                "end",
                values=(
                    idx,
                    item.aweme_id,
                    item.group_id if item.is_dup else "-",
                    (item.desc or "")[:50],
                    f"{item.play_count:,}" if item.play_count else "0",
                    c_time_str,
                    shadow_text,
                    dup_text,
                ),
                tags=(tag,),
            )

        self.channel_tree.tag_configure("dup", background="#fee2e2", foreground="#991b1b")
        self.channel_tree.tag_configure("clean", background="#ffffff", foreground="#0f172a")
        self.channel_tree.bind("<Double-1>", lambda e: self._on_channel_tree_double_click())

    def _on_channel_tree_double_click(self) -> None:
        """Nhấp đúp chuột vào dòng video: mở video gốc nếu trùng, hoặc mở bài đăng."""
        sel = self.channel_tree.selection()
        if not sel:
            return
        vals = self.channel_tree.item(sel[0], "values")
        group_id = vals[2] if len(vals) > 2 else ""
        if group_id and group_id != "-":
            webbrowser.open(f"https://www.tiktok.com/@tiktok/video/{group_id}")
        else:
            self._open_channel_selected_current()

    def _open_channel_selected_original(self) -> None:
        sel = self.channel_tree.selection()
        if not sel:
            messagebox.showinfo("Thông báo", "Vui lòng chọn một dòng video trong bảng.")
            return
        vals = self.channel_tree.item(sel[0], "values")
        group_id = vals[2] if len(vals) > 2 else ""
        if not group_id or group_id == "-":
            messagebox.showinfo("Thông báo", "Video này là bản gốc, không có Group ID trùng lặp.")
            return
        url = f"https://www.tiktok.com/@tiktok/video/{group_id}"
        webbrowser.open(url)

    def _open_channel_selected_current(self) -> None:
        sel = self.channel_tree.selection()
        if not sel:
            messagebox.showinfo("Thông báo", "Vui lòng chọn một dòng video trong bảng.")
            return
        vals = self.channel_tree.item(sel[0], "values")
        aweme_id = vals[1] if len(vals) > 1 else ""
        if not aweme_id:
            return
        uname = self._current_channel_username or "tiktok"
        url = f"https://www.tiktok.com/@{uname}/video/{aweme_id}"
        webbrowser.open(url)

    def _save_channel_duplicates_to_db(self) -> None:
        dup_items = [v for v in self._current_channel_analysis if v.is_dup]
        if not dup_items:
            messagebox.showinfo("Thông báo", "Không có video trùng nào để lưu.")
            return

        added = 0
        channel_name = self._current_channel_username or "unknown_channel"
        for v in dup_items:
            ok = self.db.log_detected(
                profile_id=f"channel_{channel_name}",
                channel=f"@{channel_name}",
                aweme_id=v.aweme_id,
                group_id=v.group_id,
                desc=v.desc,
                create_time=v.create_time,
                action=self.worker.config.action,
                status="detected",
            )
            if ok:
                added += 1

        self.refresh_data()
        messagebox.showinfo(
            "Đã Lưu",
            f"Đã lưu thành công {added} video trùng lặp vào Nhật Ký Audit Log và cập nhật bộ đếm KPI!",
        )


# ============================================================================
# 5. HỘP THOẠI KIỂM TRA BÀI ĐĂNG (CHECK POST DIALOG THEO CHUẨN TIKTOKMANAGER)
# ============================================================================

class CheckPostDialog(ctk.CTkToplevel):
    """
    Hộp thoại Kiểm Tra Bài Đăng / Check Post chuyên dụng cho từng Profile.
    Được thiết kế và hoạt động 100% theo chuẩn CheckPostDialog của TikTokManager:
    - Hiển thị danh sách video đã đăng trên Profile.
    - Cột Trạng thái Trùng: 🔴 Trùng (kèm Group ID gốc) / 🟢 Gốc.
    - Cột Shadowban / Bóp reach / Giam duyệt: ⛔ Có (lý do cụ thể) / ✅ Không.
    - Cột Nguồn: CapCut / Upload / Quay in-app.
    - Cột Quyền xem: Công khai / Riêng tư / Đã xóa.
    - Nút tác vụ nhanh: Mở video gốc, Mở bài đăng, Ẩn private, Xóa vĩnh viễn, Copy ID, Lưu Audit Log.
    """

    def __init__(
        self,
        parent,
        profile: ProfileContext,
        db: Optional[DedupDatabase] = None,
        autoload: bool = True,
        *args,
        **kwargs,
    ):
        super().__init__(parent, *args, **kwargs)
        self.profile = profile
        self.db = db
        self.client = TikTokDedupApiClient(profile)
        self._is_loading = False

        profile_display = profile.tiktok_account or profile.profile_id
        self.title(f"Kiểm tra bài đăng - {profile_display}")
        self.geometry("1140x680")
        self.minsize(960, 560)

        # Center on parent window
        try:
            self.transient(parent)
        except Exception:
            pass

        self._posts_raw: List[Dict[str, Any]] = []
        self._analyzed_posts: List[VideoItemAnalysis] = []
        self._username = profile.tiktok_account.lstrip("@") if profile.tiktok_account else profile.profile_id

        self._build_ui()
        if autoload:
            self.after(150, self.load_posts)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- 1. HEADER SUMMARY CARD ---
        header_card = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        header_card.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header_card.grid_columnconfigure(0, weight=1)

        header_top = ctk.CTkFrame(header_card, fg_color="transparent")
        header_top.grid(row=0, column=0, sticky="ew", padx=16, pady=12)
        header_top.grid_columnconfigure(0, weight=1)

        # Profile meta labels
        meta_box = ctk.CTkFrame(header_top, fg_color="transparent")
        meta_box.grid(row=0, column=0, sticky="w")

        p_name = self.profile.profile_id
        uname = f"@{self.profile.tiktok_account}" if self.profile.tiktok_account else ""
        self.lbl_profile_title = ctk.CTkLabel(
            meta_box,
            text=f"🔁 Bài đăng Profile: {p_name} {uname}",
            font=("Segoe UI Bold", 15),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.lbl_profile_title.pack(anchor="w")

        self.lbl_meta_sub = ctk.CTkLabel(
            meta_box,
            text="Đang kết nối lấy dữ liệu bài viết...",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_meta_sub.pack(anchor="w", pady=(2, 0))

        # Header action buttons
        hdr_actions = ctk.CTkFrame(header_top, fg_color="transparent")
        hdr_actions.grid(row=0, column=1, sticky="e")

        self.btn_refresh = ctk.CTkButton(
            hdr_actions,
            text="🔄 Tải Lại",
            font=("Segoe UI Semibold", 11),
            width=100,
            height=30,
            fg_color="#334155",
            hover_color="#1e293b",
            command=self.load_posts,
        )
        self.btn_refresh.pack(side="left", padx=(0, 8))

        self.btn_save_audit = ctk.CTkButton(
            hdr_actions,
            text="💾 Lưu Video Trùng",
            font=("Segoe UI Semibold", 11),
            width=140,
            height=30,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._save_duplicates_to_audit,
        )
        self.btn_save_audit.pack(side="left")

        # --- 2. TABLE CONTAINER ---
        table_container = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        table_container.grid(row=1, column=0, sticky="nsew", padx=16, pady=0)
        table_container.grid_columnconfigure(0, weight=1)
        table_container.grid_rowconfigure(0, weight=1)

        cols = ("idx", "aweme_id", "group_id", "desc", "views", "create_time", "source", "shadow", "dup_status", "visibility")
        self.tree = ttk.Treeview(
            table_container,
            style="Modern.Treeview",
            columns=cols,
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("idx", text="#")
        self.tree.heading("aweme_id", text="Aweme ID (Video)")
        self.tree.heading("group_id", text="Group ID (Gốc)")
        self.tree.heading("desc", text="Tiêu Đề / Mô Tả Video")
        self.tree.heading("views", text="Lượt Xem")
        self.tree.heading("create_time", text="Ngày Đăng")
        self.tree.heading("source", text="Nguồn")
        self.tree.heading("shadow", text="Shadowban")
        self.tree.heading("dup_status", text="Trạng Thái Trùng")
        self.tree.heading("visibility", text="Quyền Xem")

        self.tree.column("idx", width=40, anchor="center")
        self.tree.column("aweme_id", width=140, anchor="center")
        self.tree.column("group_id", width=140, anchor="center")
        self.tree.column("desc", width=260, anchor="w")
        self.tree.column("views", width=75, anchor="center")
        self.tree.column("create_time", width=110, anchor="center")
        self.tree.column("source", width=90, anchor="center")
        self.tree.column("shadow", width=130, anchor="center")
        self.tree.column("dup_status", width=170, anchor="center")
        self.tree.column("visibility", width=85, anchor="center")

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        vsb = ttk.Scrollbar(table_container, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.tag_configure("dup", background="#fee2e2", foreground="#991b1b")
        self.tree.tag_configure("clean", background="#ffffff", foreground="#0f172a")
        self.tree.tag_configure("shadow", background="#fef3c7", foreground="#92400e")
        self.tree.bind("<Double-1>", lambda e: self._on_tree_double_click())

        # --- 3. BOTTOM ACTION BAR ---
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=12)
        action_bar.grid_columnconfigure(0, weight=1)

        self.lbl_status_summary = ctk.CTkLabel(
            action_bar,
            text="Đang chuẩn bị dữ liệu...",
            font=("Segoe UI Semibold", 11),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.lbl_status_summary.grid(row=0, column=0, sticky="w")

        btn_box = ctk.CTkFrame(action_bar, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        self.btn_open_original = ctk.CTkButton(
            btn_box,
            text="🔗 Mở Video Gốc Đối Chiếu",
            font=("Segoe UI Semibold", 11),
            width=180,
            height=30,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._open_selected_original,
        )
        self.btn_open_original.pack(side="left", padx=(0, 6))

        self.btn_open_current = ctk.CTkButton(
            btn_box,
            text="🌐 Mở Bài Đăng TikTok",
            font=("Segoe UI Semibold", 11),
            width=150,
            height=30,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._open_selected_current,
        )
        self.btn_open_current.pack(side="left", padx=(0, 6))

        self.btn_hide_private = ctk.CTkButton(
            btn_box,
            text="🔒 Ẩn Video (Chỉ Mình Xem)",
            font=("Segoe UI Semibold", 11),
            width=170,
            height=30,
            fg_color="#475569",
            hover_color="#334155",
            command=self._hide_selected_private,
        )
        self.btn_hide_private.pack(side="left", padx=(0, 6))

        self.btn_delete_post = ctk.CTkButton(
            btn_box,
            text="🗑️ Xóa Vĩnh Viễn",
            font=("Segoe UI Semibold", 11),
            width=130,
            height=30,
            fg_color="#b91c1c",
            hover_color="#991b1b",
            command=self._delete_selected_post,
        )
        self.btn_delete_post.pack(side="left", padx=(0, 6))

        self.btn_copy_id = ctk.CTkButton(
            btn_box,
            text="📋 Copy ID",
            font=("Segoe UI Semibold", 11),
            width=80,
            height=30,
            fg_color="#64748b",
            hover_color="#475569",
            command=self._copy_selected_id,
        )
        self.btn_copy_id.pack(side="left")

    def load_posts(self) -> None:
        """Tải danh sách bài viết trong luồng phụ."""
        if self._is_loading:
            return
        self._is_loading = True
        self.btn_refresh.configure(state="disabled", text="⏳ Đang tải...")
        self.lbl_meta_sub.configure(text="Đang kết nối lấy dữ liệu bài viết từ TikTok...")

        def _worker():
            try:
                posts = self.client.fetch_profile_posts(count=50)
                self.after(0, lambda: self._on_posts_loaded(posts))
            except Exception as e:
                logger.exception("Lỗi khi fetch profile posts")
                self.after(0, lambda: self._on_posts_error(str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_posts_error(self, err_msg: str) -> None:
        self._is_loading = False
        self.btn_refresh.configure(state="normal", text="🔄 Tải Lại")
        self.lbl_meta_sub.configure(text=f"Lỗi: {err_msg}")
        messagebox.showerror("Lỗi", f"Không thể lấy bài viết: {err_msg}")

    def _on_posts_loaded(self, posts: List[Dict[str, Any]]) -> None:
        self._is_loading = False
        self.btn_refresh.configure(state="normal", text="🔄 Tải Lại")
        self._posts_raw = posts
        self._analyzed_posts = [analyze_tiktok_video(p) for p in posts]

        # Xóa Treeview cũ
        for row in self.tree.get_children():
            self.tree.delete(row)

        dup_count = sum(1 for v in self._analyzed_posts if v.is_dup)
        shadow_count = sum(1 for v in self._analyzed_posts if v.is_shadow)
        total = len(self._analyzed_posts)

        # Cập nhật thông tin tiêu đề
        if posts and isinstance(posts[0], dict):
            first_auth = posts[0].get("author") or {}
            if first_auth.get("unique_id"):
                self._username = first_auth.get("unique_id")
                nickname = first_auth.get("nickname") or self._username
                self.lbl_profile_title.configure(text=f"🔁 Bài đăng Profile: {self.profile.profile_id} ({nickname} @{self._username})")

        self.lbl_meta_sub.configure(
            text=f"Tổng video: {total} bài  |  Phát hiện: {dup_count} video trùng lặp  |  {shadow_count} video có dấu hiệu shadowban"
        )

        # Chèn từng video vào Treeview
        for idx, item in enumerate(self._analyzed_posts, start=1):
            c_time_s = item.create_time
            c_time_str = datetime.fromtimestamp(c_time_s).strftime("%d/%m/%Y %H:%M") if c_time_s else "—"

            if item.is_dup:
                dup_text = f"🔴 TRÙNG LẶP (Gốc: {item.group_id})"
                tag = "dup"
            else:
                dup_text = "🟢 BẢN GỐC"
                tag = "clean"

            if item.is_shadow:
                shadow_text = f"⛔ {item.shadow_reason or 'Bị hạn chế'}"
                if tag != "dup":
                    tag = "shadow"
            else:
                shadow_text = "✅ Không"

            vis_text = "Đã xoá" if item.is_delete else ("Riêng tư" if item.is_private else "Công khai")

            self.tree.insert(
                "",
                "end",
                values=(
                    idx,
                    item.aweme_id,
                    item.group_id if item.is_dup else "—",
                    (item.desc or "(không có mô tả)")[:50],
                    f"{item.play_count:,}" if item.play_count else "0",
                    c_time_str,
                    item.source,
                    shadow_text,
                    dup_text,
                    vis_text,
                ),
                tags=(tag,),
            )

        # Cập nhật thanh trạng thái dưới cùng
        if dup_count > 0:
            self.lbl_status_summary.configure(
                text=f"⚠️ CẢNH BÁO: Phát hiện {dup_count} bài viết trùng lặp trên tài khoản này!",
                text_color="#dc2626",
            )
        else:
            self.lbl_status_summary.configure(
                text=f"✅ TÀI KHOẢN SẠCH: Toàn bộ {total} bài đăng đều là nội dung gốc chuẩn.",
                text_color="#16a34a",
            )

    def _get_selected_item(self) -> Optional[VideoItemAnalysis]:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Thông báo", "Vui lòng chọn một dòng video trong bảng.")
            return None
        vals = self.tree.item(sel[0], "values")
        aweme_id = vals[1] if len(vals) > 1 else ""
        for item in self._analyzed_posts:
            if str(item.aweme_id) == str(aweme_id):
                return item
        return None

    def _on_tree_double_click(self) -> None:
        item = self._get_selected_item()
        if not item:
            return
        if item.is_dup and item.group_id:
            webbrowser.open(f"https://www.tiktok.com/@tiktok/video/{item.group_id}")
        else:
            self._open_selected_current()

    def _open_selected_original(self) -> None:
        item = self._get_selected_item()
        if not item:
            return
        if not item.is_dup or not item.group_id or item.group_id == item.aweme_id:
            messagebox.showinfo("Thông báo", "Video này là bản gốc, không có Group ID đối chiếu.")
            return
        url = f"https://www.tiktok.com/@tiktok/video/{item.group_id}"
        webbrowser.open(url)

    def _open_selected_current(self) -> None:
        item = self._get_selected_item()
        if not item:
            return
        uname = self._username or "tiktok"
        url = f"https://www.tiktok.com/@{uname}/video/{item.aweme_id}"
        webbrowser.open(url)

    def _hide_selected_private(self) -> None:
        item = self._get_selected_item()
        if not item:
            return

        confirm = messagebox.askyesno(
            "Xác nhận",
            f"Bạn có chắc chắn muốn ẨN video {item.aweme_id} (chuyển quyền riêng tư sang 'Chỉ mình tôi')?",
        )
        if not confirm:
            return

        ok = self.client.set_post_private(item.aweme_id)
        if ok:
            messagebox.showinfo("Thành công", f"Đã chuyển video {item.aweme_id} sang trạng thái Riêng tư.")
            item.is_private = True
            # Cập nhật UI Treeview
            sel = self.tree.selection()
            if sel:
                vals = list(self.tree.item(sel[0], "values"))
                if len(vals) >= 10:
                    vals[9] = "Riêng tư"
                    self.tree.item(sel[0], values=vals)
        else:
            messagebox.showerror("Thất bại", f"Không thể đổi quyền riêng tư video {item.aweme_id}. Kiểm tra lại Cookie/Proxy.")

    def _delete_selected_post(self) -> None:
        item = self._get_selected_item()
        if not item:
            return

        confirm = messagebox.askyesno(
            "CẢNH BÁO XÓA",
            f"HÀNH ĐỘNG NÀY KHÔNG THỂ HOÀN TÁC!\nBạn có chắc chắn muốn XÓA VĨNH VIỄN video {item.aweme_id} khỏi kênh TikTok?",
        )
        if not confirm:
            return

        ok = self.client.delete_post(item.aweme_id)
        if ok:
            messagebox.showinfo("Đã Xóa", f"Đã xóa vĩnh viễn video {item.aweme_id} khỏi kênh TikTok.")
            item.is_delete = True
            # Cập nhật UI Treeview
            sel = self.tree.selection()
            if sel:
                vals = list(self.tree.item(sel[0], "values"))
                if len(vals) >= 10:
                    vals[9] = "Đã xoá"
                    self.tree.item(sel[0], values=vals)
        else:
            messagebox.showerror("Thất bại", f"Không thể xóa video {item.aweme_id}. Vui lòng kiểm tra lại phiên đăng nhập.")

    def _copy_selected_id(self) -> None:
        item = self._get_selected_item()
        if not item:
            return
        self.clipboard_clear()
        self.clipboard_append(item.aweme_id)
        messagebox.showinfo("Đã Sao Chép", f"Đã sao chép Aweme ID: {item.aweme_id}")

    def _save_duplicates_to_audit(self) -> None:
        if not self.db:
            messagebox.showwarning("Cảnh báo", "Không tìm thấy kết nối Cơ sở dữ liệu Audit Log.")
            return

        dup_items = [v for v in self._analyzed_posts if v.is_dup]
        if not dup_items:
            messagebox.showinfo("Thông báo", "Không có video trùng nào để lưu.")
            return

        saved_count = 0
        p_name = self.profile.profile_id
        chan_name = f"@{self._username}" if self._username else p_name
        for v in dup_items:
            ok = self.db.log_detected(
                profile_id=p_name,
                channel=chan_name,
                aweme_id=v.aweme_id,
                group_id=v.group_id,
                desc=v.desc,
                create_time=v.create_time,
                action="dry_run",
                status="detected",
            )
            if ok:
                saved_count += 1

        messagebox.showinfo(
            "Đã Lưu",
            f"Đã lưu thành công {saved_count} video trùng lặp vào Nhật Ký Audit Log!",
        )

