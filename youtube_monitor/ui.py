import os
import re
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import Menu as TkMenu
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk
from ui_components import (
    UIThemeTokens,
    ProfilePickerField,
    load_live_profile_names,
    normalize_profile_names,
)


class YouTubeMonitorView(ctk.CTkFrame):
    """
    YouTubeMonitorView - Giao diện Giám Sát Tự Động YouTube Studio.
    Bố cục 2 cột Responsive, áp dụng Design System UIThemeTokens,
    Bảng kênh mở rộng với Context Menu, Searchable Profile Picker Modal và View Mode (Grouped / Flat).
    """

    def __init__(self, parent, handlers):
        super().__init__(parent, fg_color="transparent")
        self.handlers = handlers or {}
        self.selected_channel_id = None
        self.profile_names: List[str] = []
        self._channels_data: List[Dict[str, Any]] = []
        self._channels_snapshot = None
        self._first_channel_render = True
        self._is_stacked_layout = False
        self._context_channel_id: Optional[str] = None

        # State Variables
        self.status_var = ctk.StringVar(value="Monitor: Chưa chạy")
        self.health_var = ctk.StringVar(value="Health: -")
        self.callback_var = ctk.StringVar(value="Callback: -")
        self.stats_var = ctk.StringVar(value="Kênh: 0 | Hàng chờ: 0 | Worker: 0 | Hôm nay: 0")
        self.api_status_var = ctk.StringVar(value="API: -")
        self.cookie_var = ctk.StringVar(value="")
        self.cookie_display_var = ctk.StringVar(value="Chưa chọn cookie")
        self.max_minutes_var = ctk.StringVar(value="0")
        self.channel_filter_var = ctk.StringVar(value="Tất cả")
        self.search_var = ctk.StringVar(value="")
        self.channel_view_mode_var = ctk.StringVar(value="grouped")

        self._build()
        self.refresh_profiles()
        self._load_max_minutes()

        # Bind responsive layout handler
        self.bind("<Configure>", self._on_configure)

    def _build(self):
        # 1. Top Summary Card (Header Status Bar)
        top_card = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        top_card.pack(fill="x", pady=(0, 6))

        top_inner = ctk.CTkFrame(top_card, fg_color="transparent")
        top_inner.pack(fill="x", padx=12, pady=8)

        self.status_label = ctk.CTkLabel(
            top_inner,
            textvariable=self.status_var,
            text_color=UIThemeTokens.STATUS_ERROR,
            font=UIThemeTokens.FONT_TITLE,
        )
        self.status_label.pack(side="left", padx=(0, 14))

        ctk.CTkLabel(
            top_inner,
            textvariable=self.stats_var,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            font=UIThemeTokens.FONT_BODY,
        ).pack(side="left", padx=10)

        ctk.CTkLabel(
            top_inner,
            textvariable=self.health_var,
            text_color=UIThemeTokens.TEXT_MUTED,
            font=UIThemeTokens.FONT_SUBTITLE,
        ).pack(side="left", padx=10)

        ctk.CTkLabel(
            top_inner,
            textvariable=self.api_status_var,
            text_color=UIThemeTokens.ACCENT_PRIMARY,
            font=UIThemeTokens.FONT_BUTTON,
        ).pack(side="right", padx=6)

        # 2. Main Content Area (2-Column Responsive Layout)
        self.main_split = ctk.CTkFrame(self, fg_color="transparent")
        self.main_split.pack(fill="both", expand=True, pady=(0, 6))
        self.main_split.grid_rowconfigure(0, weight=1)
        self.main_split.grid_columnconfigure(0, weight=1)
        self.main_split.grid_columnconfigure(1, weight=0)

        # 2A. LEFT COLUMN: Channel Table & Search Filter Toolbar
        self.left_col = ctk.CTkFrame(self.main_split, fg_color="transparent")
        self.left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.left_col.grid_rowconfigure(1, weight=1)
        self.left_col.grid_columnconfigure(0, weight=1)

        # Filter & Search Bar
        filter_card = ctk.CTkFrame(
            self.left_col,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            height=38,
        )
        filter_card.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        filter_inner = ctk.CTkFrame(filter_card, fg_color="transparent")
        filter_inner.pack(fill="x", padx=8, pady=5)

        # View Mode Segmented Switch
        self.view_mode_seg = ctk.CTkSegmentedButton(
            filter_inner,
            values=["📁 Nhóm", "📄 Phẳng"],
            width=115,
            height=28,
            font=UIThemeTokens.FONT_BUTTON,
            command=self._on_view_mode_change,
        )
        self.view_mode_seg.set("📁 Nhóm")
        self.view_mode_seg.pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            filter_inner,
            text="Profile:",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left", padx=(0, 4))

        self.channel_filter_combo = ctk.CTkComboBox(
            filter_inner,
            variable=self.channel_filter_var,
            values=["Tất cả"],
            width=135,
            height=28,
            font=UIThemeTokens.FONT_BODY,
            command=self._on_channel_filter_change,
        )
        self.channel_filter_combo.pack(side="left", padx=(0, 8))

        self.search_entry = ctk.CTkEntry(
            filter_inner,
            textvariable=self.search_var,
            placeholder_text="Tìm kênh, Channel ID hoặc Profile...",
            height=28,
            font=UIThemeTokens.FONT_BODY,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.search_var.trace_add("write", lambda *_: self._on_search_change())

        ctk.CTkLabel(
            filter_inner,
            textvariable=self.callback_var,
            text_color=UIThemeTokens.TEXT_MUTED,
            font=UIThemeTokens.FONT_BADGE,
        ).pack(side="right", padx=(4, 0))

        # Channel Treeview Table Card
        table_card = ctk.CTkFrame(
            self.left_col,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        table_card.grid(row=1, column=0, sticky="nsew")
        table_card.grid_rowconfigure(0, weight=1)
        table_card.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            table_card,
            style="Modern.Treeview",
            columns=("channel", "channel_id", "profile", "active", "short", "seen", "folder"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Cấu Trúc")
        self.tree.column("#0", width=140, minwidth=90, stretch=False)

        cols_def = (
            ("channel", "Tên Kênh / Handle", 210, True),
            ("channel_id", "Channel ID", 145, False),
            ("profile", "Profile Đích", 110, False),
            ("active", "Trạng Thái", 105, False),
            ("short", "Xử Lý Shorts", 125, False),
            ("seen", "Đã Tải", 65, False),
            ("folder", "Thư Mục Lưu", 200, True),
        )
        for col, text, width, stretch in cols_def:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=55, stretch=stretch)

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)

        vsb = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        hsb = ttk.Scrollbar(table_card, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky="ew", padx=(6, 0), pady=(0, 6))
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._open_channel_link)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # Configure Row Tags for styling
        self.tree.tag_configure("tag_active", foreground="#16a34a")
        self.tree.tag_configure("tag_inactive", foreground="#64748b")
        self.tree.tag_configure("tag_group", font=("Segoe UI Semibold", 10))

        # Context Menu for Channel Rows (single instance, commands read _context_channel_id)
        self.ctx_menu = TkMenu(self, tearoff=0, font=("Segoe UI", 9))
        self.ctx_menu.add_command(label="🌐 Mở kênh trên YouTube", command=lambda: self._open_channel_link())
        self.ctx_menu.add_command(label="📁 Mở thư mục lưu video", command=lambda: self._open_folder())
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="🔄 Đổi Profile đích...", command=lambda: self._open_profile_picker())
        self.ctx_menu.add_command(label="⚡ Bật/Tắt theo dõi kênh", command=lambda: self._toggle_active())
        self.ctx_menu.add_command(label="✂️ Bật/Tắt điều chỉnh 40-60s", command=lambda: self._toggle_short())
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="🗑️ Xóa kênh khỏi danh sách", command=lambda: self._remove_with_confirm())

        # 2B. RIGHT COLUMN: Control Panel Cards
        self.right_col = ctk.CTkScrollableFrame(
            self.main_split,
            width=340,
            corner_radius=10,
            fg_color="transparent",
        )
        self.right_col.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        # CARD 1: Vận Hành Hệ Thống (System Operations)
        card_ops = ctk.CTkFrame(
            self.right_col,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        card_ops.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            card_ops,
            text="⚡ VẬN HÀNH HỆ THỐNG",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", padx=12, pady=(10, 6))

        ops_btn_row = ctk.CTkFrame(card_ops, fg_color="transparent")
        ops_btn_row.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkButton(
            ops_btn_row,
            text="▶ Bắt Đầu",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.STATUS_LIVE,
            hover_color="#15803d",
            command=self._start,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            ops_btn_row,
            text="⏹ Dừng",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            command=self._stop,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        ctk.CTkButton(
            ops_btn_row,
            text="🔄 Retry Ngrok",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.STATUS_WARN_BG,
            hover_color="#92400e",
            command=self._retry_ngrok,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        # CARD 2: Thêm Kênh Nhanh (Add Channel)
        card_add = ctk.CTkFrame(
            self.right_col,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        card_add.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            card_add,
            text="➕ THÊM KÊNH THEO DÕI",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", padx=12, pady=(10, 6))

        self.channel_entry = ctk.CTkEntry(
            card_add,
            placeholder_text="URL kênh / @handle / Channel ID",
            height=30,
            font=UIThemeTokens.FONT_BODY,
        )
        self.channel_entry.pack(fill="x", padx=10, pady=(0, 6))

        add_profile_row = ctk.CTkFrame(card_add, fg_color="transparent")
        add_profile_row.pack(fill="x", padx=10, pady=(0, 6))

        ctk.CTkLabel(
            add_profile_row,
            text="Gán Profile:",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left", padx=(0, 4))

        self.profile_var = ctk.StringVar(value="")
        self.add_profile_picker_field = ProfilePickerField(
            add_profile_row,
            variable=self.profile_var,
            command=self._open_add_channel_profile_picker,
            placeholder_text="Chưa chọn profile",
            button_text="🔍 Chọn",
            height=30,
        )
        self.add_profile_picker_field.pack(side="left", fill="x", expand=True)
        self.add_profile_picker_btn = self.add_profile_picker_field.btn_picker

        ctk.CTkButton(
            card_add,
            text="+ Thêm Kênh Vào Hệ Thống",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._add_channel,
        ).pack(fill="x", padx=10, pady=(4, 10))

        # CARD 3: Cấu Hình & Tiện Ích (Config & Tools)
        card_cfg = ctk.CTkFrame(
            self.right_col,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        card_cfg.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            card_cfg,
            text="⚙️ CẤU HÌNH & TIỆN ÍCH",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", padx=12, pady=(10, 6))

        # API Key Row
        ctk.CTkLabel(
            card_cfg,
            text="YouTube Data API v3 Key:",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", padx=10, pady=(2, 2))

        api_row = ctk.CTkFrame(card_cfg, fg_color="transparent")
        api_row.pack(fill="x", padx=10, pady=(0, 6))

        self.api_key_entry = ctk.CTkEntry(
            api_row,
            placeholder_text="Nhập API Key YouTube",
            show="*",
            height=28,
            font=UIThemeTokens.FONT_BODY,
        )
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            api_row,
            text="Lưu/Kiểm",
            font=UIThemeTokens.FONT_BUTTON,
            width=80,
            height=28,
            command=self._save_api_key,
        ).pack(side="right")

        # Cookie File Row (Hiển thị filename, giữ full path)
        ctk.CTkLabel(
            card_cfg,
            text="File Cookie YouTube (cookies.txt):",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", padx=10, pady=(2, 2))

        cookie_row = ctk.CTkFrame(card_cfg, fg_color="transparent")
        cookie_row.pack(fill="x", padx=10, pady=(0, 6))

        self.cookie_entry = ctk.CTkEntry(
            cookie_row,
            textvariable=self.cookie_display_var,
            state="disabled",
            height=28,
            font=UIThemeTokens.FONT_BODY,
        )
        self.cookie_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))

        ctk.CTkButton(
            cookie_row,
            text="Chọn",
            font=UIThemeTokens.FONT_BUTTON,
            width=50,
            height=28,
            command=self._choose_cookie_file,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            cookie_row,
            text="Lưu",
            font=UIThemeTokens.FONT_BUTTON,
            width=45,
            height=28,
            command=self._save_cookie_file,
        ).pack(side="left", padx=2)

        ctk.CTkButton(
            cookie_row,
            text="Xóa",
            font=UIThemeTokens.FONT_BUTTON,
            width=40,
            height=28,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            command=self._clear_cookie_file,
        ).pack(side="left")

        # Duration Limit Row
        dur_row = ctk.CTkFrame(card_cfg, fg_color="transparent")
        dur_row.pack(fill="x", padx=10, pady=(0, 6))

        ctk.CTkLabel(
            dur_row,
            text="Giới hạn video (phút):",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(side="left", padx=(0, 6))

        self.max_minutes_entry = ctk.CTkEntry(
            dur_row,
            textvariable=self.max_minutes_var,
            placeholder_text="0 = tất cả",
            width=65,
            height=28,
            font=UIThemeTokens.FONT_BODY,
        )
        self.max_minutes_entry.pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            dur_row,
            text="Lưu",
            font=UIThemeTokens.FONT_BUTTON,
            width=50,
            height=28,
            command=self._save_max_minutes,
        ).pack(side="left")

        # Diagnostics & Status Badges
        diag_frame = ctk.CTkFrame(card_cfg, corner_radius=6, fg_color=UIThemeTokens.BG_HOVER)
        diag_frame.pack(fill="x", padx=10, pady=(0, 6))

        self.ffmpeg_status_label = ctk.CTkLabel(
            diag_frame,
            text="FFmpeg: Đang kiểm tra...",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.ffmpeg_status_label.pack(anchor="w", padx=8, pady=(4, 2))

        self.ytdlp_label = ctk.CTkLabel(
            diag_frame,
            text="yt-dlp: ?",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.ytdlp_label.pack(anchor="w", padx=8, pady=(0, 4))

        tool_btn_row = ctk.CTkFrame(card_cfg, fg_color="transparent")
        tool_btn_row.pack(fill="x", padx=10, pady=(0, 6))

        ctk.CTkButton(
            tool_btn_row,
            text="Cài FFmpeg",
            font=UIThemeTokens.FONT_BUTTON,
            height=26,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._install_ffmpeg,
        ).pack(side="left", fill="x", expand=True, padx=(0, 3))

        ctk.CTkButton(
            tool_btn_row,
            text="Cập nhật yt-dlp",
            font=UIThemeTokens.FONT_BUTTON,
            height=26,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._update_ytdlp,
        ).pack(side="left", fill="x", expand=True, padx=(3, 0))

        # Test Download Area
        ctk.CTkLabel(
            card_cfg,
            text="⚡ TẢI THỬ NGHIỆM 1 VIDEO",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", padx=12, pady=(10, 6))

        self.test_entry = ctk.CTkEntry(
            card_cfg,
            placeholder_text="URL hoặc Video ID",
            height=28,
            font=UIThemeTokens.FONT_BODY,
        )
        self.test_entry.pack(fill="x", padx=10, pady=(0, 4))

        test_act_row = ctk.CTkFrame(card_cfg, fg_color="transparent")
        test_act_row.pack(fill="x", padx=10, pady=(0, 10))

        self.test_profile_var = ctk.StringVar(value="")
        self.test_profile_picker_field = ProfilePickerField(
            test_act_row,
            variable=self.test_profile_var,
            command=self._open_test_download_profile_picker,
            placeholder_text="Chưa chọn profile",
            button_text="🔍",
            height=28,
            compact_button=True,
        )
        self.test_profile_picker_field.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.test_profile_picker_btn = self.test_profile_picker_field.btn_picker

        ctk.CTkButton(
            test_act_row,
            text="Tải Thử",
            font=UIThemeTokens.FONT_BUTTON,
            height=28,
            width=75,
            command=self._download_test,
        ).pack(side="right")

        # Compatibility labels for test contracts
        self.quality_label = ctk.CTkLabel(self, text="")
        self.dl_workers_label = ctk.CTkLabel(self, text="")

        # 3. Bottom Operation Log Area
        log_card = ctk.CTkFrame(
            self,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        log_card.pack(fill="x", pady=(0, 0))

        log_header = ctk.CTkFrame(log_card, fg_color="transparent", height=24)
        log_header.pack(fill="x", padx=8, pady=(4, 2))
        ctk.CTkLabel(
            log_header,
            text="📋 NHẬT KÝ VẬN HÀNH MONITOR",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left")

        self.log_text = ScrolledText(
            log_card,
            height=4,
            state="disabled",
            font=("Consolas", 9),
            relief="flat",
            bd=0,
            bg="#ffffff",
            fg="#0f172a",
        )
        self.log_text.pack(fill="x", padx=8, pady=(0, 6))
        self.log_text.tag_configure("ERROR", foreground="#b91c1c")
        self.log_text.tag_configure("WARN", foreground="#b45309")
        self.log_text.tag_configure("INFO", foreground="#0f172a")
        self.log_text.tag_configure("SUCCESS", foreground="#16a34a")

    def _on_configure(self, event=None):
        """Responsive breakpoint switching between 2-column and 1-column layout."""
        if not event:
            return
        width = event.width
        if width < 880 and not self._is_stacked_layout:
            self._is_stacked_layout = True
            self.left_col.grid(row=0, column=0, sticky="nsew", padx=0, pady=(0, 6))
            self.right_col.grid(row=1, column=0, sticky="nsew", padx=0, pady=(6, 0))
            self.main_split.grid_columnconfigure(1, weight=0)
            self.main_split.grid_rowconfigure(1, weight=0)
        elif width >= 880 and self._is_stacked_layout:
            self._is_stacked_layout = False
            self.left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=0)
            self.right_col.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=0)
            self.main_split.grid_columnconfigure(1, weight=0)
            self.main_split.grid_rowconfigure(1, weight=0)

    def _on_view_mode_change(self, value=None):
        mode = "flat" if value == "📄 Phẳng" else "grouped"
        if mode != self.channel_view_mode_var.get():
            self.channel_view_mode_var.set(mode)
            self._channels_snapshot = None
            self._render_current_channels()

    def _run_handler(self, name, *args):
        fn = self.handlers.get(name)
        if not fn:
            return False, f"Handler {name} chưa được cấu hình"
        try:
            return fn(*args)
        except Exception as e:
            self.append_log(f"Lỗi {name}: {e}", error=True)
            return False, str(e)

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel or sel[0].startswith("__group__"):
            self.selected_channel_id = None
        else:
            self.selected_channel_id = sel[0]

    def _validate_channel_live_fail_closed(self, cid: str) -> Tuple[bool, str]:
        if not cid or cid.startswith("__group__"):
            return False, "ID kênh không hợp lệ."
        get_ch_fn = self.handlers.get("get_channels")
        if not get_ch_fn or not callable(get_ch_fn):
            return False, "Handler 'get_channels' chưa được cấu hình; không thể xác thực kênh live."
        try:
            live_channels = get_ch_fn()
        except Exception as e:
            return False, f"Lỗi khi xác thực danh sách kênh: {e}"

        if not hasattr(live_channels, "__iter__") or isinstance(live_channels, (str, bytes)):
            return False, "Dữ liệu danh sách kênh không hợp lệ (không thể duyệt)."

        found = False
        for c in live_channels:
            if isinstance(c, dict) and c.get("channel_id") == cid:
                found = True
                break
            elif hasattr(c, "get") and callable(getattr(c, "get", None)):
                try:
                    if c.get("channel_id") == cid:
                        found = True
                        break
                except Exception:
                    continue

        if not found:
            return False, f"Kênh '{cid}' không còn tồn tại trong danh sách theo dõi."
        return True, ""

    def _resolve_target_channel(self, cid: Optional[str] = None, require_tree: bool = False) -> Optional[str]:
        """Resolve the target channel ID from explicit argument, context menu click, selection, or tree selection.

        Args:
            cid: Explicit channel ID. If provided, used directly (optionally validated against tree).
            require_tree: If True, require the channel to exist in the tree. Only applies when cid is NOT explicitly provided.
        """
        explicit_cid = cid is not None
        if explicit_cid:
            target = cid
        elif self._context_channel_id:
            target = self._context_channel_id
        elif self.selected_channel_id:
            target = self.selected_channel_id
        else:
            sel = self.tree.selection()
            target = sel[0] if sel else None

        if not target or target.startswith("__group__"):
            return None
        if require_tree and not explicit_cid and not self.tree.exists(target):
            return None
        return target

    def _refresh_channels_after_action(self, preferred_cid: Optional[str] = None) -> bool:
        """Refresh channel data from live handler and re-render. Returns True if refresh succeeded."""
        get_ch_fn = self.handlers.get("get_channels")
        if not get_ch_fn or not callable(get_ch_fn):
            self.append_log("Không thể làm mới danh sách kênh: handler 'get_channels' chưa cấu hình", error=True)
            return False
        try:
            live_channels = list(get_ch_fn() or [])
        except Exception as e:
            self.append_log(f"Lỗi khi tải danh sách kênh: {e}", error=True)
            return False

        if not hasattr(live_channels, "__iter__") or isinstance(live_channels, (str, bytes)):
            self.append_log("Dữ liệu danh sách kênh không hợp lệ", error=True)
            return False

        self._channels_data = [c for c in live_channels if isinstance(c, dict)]
        self._channels_snapshot = None
        self._render_current_channels()

        # Restore selection if preferred_cid still exists
        if preferred_cid and self.tree.exists(preferred_cid):
            self.tree.selection_set(preferred_cid)
            self.tree.focus(preferred_cid)
            self.selected_channel_id = preferred_cid
        return True

    def _show_context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or iid.startswith("__group__"):
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.selected_channel_id = iid
        self._context_channel_id = iid
        try:
            self.ctx_menu.post(event.x_root, event.y_root)
        finally:
            try:
                self.ctx_menu.grab_release()
            except (tk.TclError, Exception):
                pass

    def refresh_profiles(self):
        """Dirty checking: Cập nhật độc lập field và combo khi danh sách profile thay đổi."""
        ok, live_profiles, _err = load_live_profile_names(self.handlers)
        if not ok:
            # Lỗi fetch tạm thời: giữ nguyên last-known-good profile_names và UI state
            return

        profiles = list(live_profiles)
        self.profile_names = profiles

        # Cập nhật validity và danh sách cho ProfilePickerFields
        if hasattr(self, "add_profile_picker_field"):
            self.add_profile_picker_field.set_profiles(self.profile_names)
        if hasattr(self, "test_profile_picker_field"):
            self.test_profile_picker_field.set_profiles(self.profile_names)

        # Cập nhật filter combo trên bảng kênh
        filter_values = ["Tất cả"] + self.profile_names + (["Chưa gán"] if "Chưa gán" not in self.profile_names else [])
        if hasattr(self, "channel_filter_combo"):
            self.channel_filter_combo.configure(values=filter_values)

        curr_filter = self.channel_filter_var.get()
        if curr_filter not in filter_values:
            self.channel_filter_var.set("Tất cả")
            self._channels_snapshot = None

    def refresh_data(self):
        self.refresh_profiles()
        status = self.handlers.get("get_status", lambda: {})()
        running = "Đang chạy" if status.get("running") else "Đã dừng"
        color = UIThemeTokens.STATUS_LIVE if status.get("running") else UIThemeTokens.STATUS_ERROR
        self.status_var.set(f"Monitor: {running}")
        self.status_label.configure(text_color=color)

        healthy = status.get("healthy", False) if status.get("running") else False
        if status.get("running"):
            mon_state = status.get("monitor_state", "RUNNING")
            if mon_state == "DEGRADED":
                health_text = "DEGRADED - cần Retry ngrok"
            elif mon_state == "RECOVERING":
                attempt = status.get("recovery_attempt", 0)
                health_text = f"Đang khôi phục ngrok (lần {attempt})"
            elif healthy:
                ngrok_ok = status.get("callback_verified", False)
                subs_total = status.get("subscriptions_total", 0)
                subs_ok = status.get("subscriptions_ok", 0)
                degraded = subs_total - subs_ok
                if degraded > 0:
                    health_text = f"Suy giảm ({subs_ok}/{subs_total} WebSub)"
                elif not ngrok_ok:
                    health_text = "Chưa có Ngrok"
                else:
                    health_text = "OK (WebSub Live)"
            else:
                health_text = f"Lỗi: {status.get('health_msg', '?')}"
        else:
            health_text = "-"
        self.health_var.set(f"Sức khỏe: {health_text}")

        self.stats_var.set(
            f"Kênh: {status.get('channels', 0)} | Hàng chờ: {status.get('queue', 0)} | Worker: {status.get('workers', 0)} | Hôm nay: {status.get('downloaded_today', 0)}"
        )

        cookie_status = status.get("cookies_status", "missing")
        if cookie_status == "ok":
            cookie_state = "Có (chưa xác minh live)"
        elif cookie_status == "invalid":
            cookie_state = "File không hợp lệ"
        else:
            cookie_state = "Chưa cấu hình"
        self.api_status_var.set(
            ("API: OK" if status.get("api_key_set") else "API: Chưa nhập") + f" | Cookie: {cookie_state}"
        )

        port = status.get("callback_port", "")
        cb_url = status.get("callback_url", "") or ""
        last_post = status.get("last_callback_post", "")
        cb_parts = [f"Port: {port}" if port else ""]
        if cb_url:
            cb_parts.append(f"Ngrok: {'OK' if status.get('callback_verified') else '?'}")
        else:
            auth_status = status.get("ngrok_auth_status", "unknown")
            if auth_status != "ready":
                cb_parts.append("Ngrok: cần authtoken")
        if last_post:
            cb_parts.append(f"POST: {last_post[11:19] if len(last_post) > 19 else last_post}")
        self.callback_var.set(f"Callback: {' | '.join(cb_parts) if cb_parts else '-'}")

        cookie_path = self.handlers.get("get_cookies_file", lambda: "")()
        if cookie_path and self.cookie_var.get() != cookie_path:
            self.cookie_var.set(cookie_path)
            self.cookie_display_var.set(Path(cookie_path).name)

        try:
            self._channels_data = list(self.handlers.get("get_channels", lambda: [])() or [])
        except Exception:
            self._channels_data = []

        snapshot = self._build_channels_snapshot(self._channels_data)
        if snapshot != self._channels_snapshot:
            self._channels_snapshot = snapshot
            self._render_current_channels()

        logs = self.handlers.get("get_logs", lambda: [])()
        for line in logs:
            self.append_log(line, error=("lỗi" in line.lower() or "error" in line.lower() or "fail" in line.lower()))

        self._update_ffmpeg_status()
        self._refresh_ytdlp_status()

    def _update_ffmpeg_status(self):
        try:
            from . import ffmpeg_helper
            ok, msg, src = ffmpeg_helper.check_ffmpeg()
            if ok:
                label = f"FFmpeg: Sẵn sàng ({src})" if src else "FFmpeg: Sẵn sàng"
                self.ffmpeg_status_label.configure(text=label, text_color=UIThemeTokens.STATUS_LIVE)
            else:
                self.ffmpeg_status_label.configure(text="FFmpeg: Chưa cài", text_color=UIThemeTokens.STATUS_ERROR)
        except Exception:
            self.ffmpeg_status_label.configure(text="FFmpeg: ?", text_color=UIThemeTokens.TEXT_MUTED)

    def append_log(self, line, error=False):
        self.log_text.configure(state="normal")
        tag = "ERROR" if error else ("SUCCESS" if "thành công" in line.lower() else "INFO")
        self.log_text.insert("end", line + "\n", tag)
        self.log_text.see("end")
        lines = int(float(self.log_text.index("end-1c").split(".")[0]))
        if lines > 300:
            self.log_text.delete("1.0", "50.0")
        self.log_text.configure(state="disabled")

    def _append_threadsafe(self, line, error=False):
        try:
            self.after(0, lambda: self.append_log(line, error=error))
        except Exception:
            pass

    def _load_max_minutes(self):
        try:
            self.max_minutes_var.set(str(self.handlers.get("get_max_video_minutes", lambda: 0)()))
        except Exception:
            self.max_minutes_var.set("0")

    def _on_channel_filter_change(self, _value=None):
        self._channels_snapshot = None
        self._render_current_channels()

    def _on_search_change(self):
        self._channels_snapshot = None
        self._render_current_channels()

    def _filtered_channels(self, channels: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        selected = self.channel_filter_var.get()
        query = self.search_var.get().strip().casefold()

        filtered = []
        for item in channels or []:
            # 1. Profile filter
            if selected != "Tất cả":
                if selected == "Chưa gán" and item.get("profile_name"):
                    continue
                if selected != "Chưa gán" and item.get("profile_name", "") != selected:
                    continue

            # 2. Text search query filter (channel_id, title, handle, profile_name)
            if query:
                cid = item.get("channel_id", "").casefold()
                title = (item.get("title") or "").casefold()
                handle = (item.get("handle") or "").casefold()
                pname = (item.get("profile_name") or "").casefold()
                if query not in cid and query not in title and query not in handle and query not in pname:
                    continue

            filtered.append(item)
        return filtered

    def _build_channels_snapshot(self, channels: Sequence[Dict[str, Any]]) -> Tuple:
        rows = []
        for item in channels or []:
            rows.append((
                item.get("channel_id", ""),
                item.get("title", ""),
                item.get("handle", ""),
                item.get("profile_name", ""),
                bool(item.get("active")),
                bool(item.get("process_short")),
                int(item.get("seen_count", 0) or 0),
                item.get("folder", ""),
            ))
        return tuple(sorted(rows))

    def _group_iid(self, index, profile_name):
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", profile_name or "Chua_gan").strip("_") or "Chua_gan"
        return f"__group__{safe}"

    def _render_current_channels(self):
        """Điều phối render channels theo view mode (Grouped / Flat) và dọn dẹp selection stale."""
        channels = self._filtered_channels(self._channels_data)
        mode = self.channel_view_mode_var.get()

        if mode == "flat":
            self._render_flat(channels)
        else:
            self._render_grouped(channels)

        # Dọn dẹp selection stale nếu kênh không còn hiển thị trong bảng
        visible_cids = {item.get("channel_id", "") for item in channels if item.get("channel_id")}
        if self.selected_channel_id:
            if self.selected_channel_id in visible_cids and self.tree.exists(self.selected_channel_id):
                self.tree.selection_set(self.selected_channel_id)
            else:
                self.selected_channel_id = None

    def _render_grouped(self, channels: List[Dict[str, Any]]):
        open_groups = {
            iid for iid in self.tree.get_children("")
            if iid.startswith("__group__") and self.tree.item(iid, "open")
        }
        self.tree.delete(*self.tree.get_children(""))
        grouped = {}
        for item in channels:
            profile = item.get("profile_name") or "Chưa gán"
            grouped.setdefault(profile, []).append(item)
        used_group_iids = set()
        for index, profile in enumerate(sorted(grouped.keys(), key=str.casefold)):
            items = sorted(grouped[profile], key=lambda x: (x.get("title") or x.get("channel_id", "")).casefold())
            group_iid = self._group_iid(index, profile)
            base_group_iid = group_iid
            suffix = 2
            while group_iid in used_group_iids:
                group_iid = f"{base_group_iid}_{suffix}"
                suffix += 1
            used_group_iids.add(group_iid)
            group_text = f"📁 [Profile] {profile} ({len(items)} kênh)"
            self.tree.insert(
                "",
                "end",
                iid=group_iid,
                text=group_text,
                values=("", "", "", "", "", "", ""),
                open=(self._first_channel_render or group_iid in open_groups),
                tags=("tag_group",),
            )
            for item in items:
                cid = item.get("channel_id", "")
                if not cid:
                    continue
                title = (item.get("title") or "").strip()
                display_name = title if title else cid
                status_text = "● Hoạt động" if item.get("active") else "Ⅱ Tạm dừng"
                short_text = "⚡ 40–60s -> 61s" if item.get("process_short") else "⚪ Giữ nguyên"
                tag = "tag_active" if item.get("active") else "tag_inactive"

                values = (
                    display_name,
                    cid,
                    item.get("profile_name", ""),
                    status_text,
                    short_text,
                    item.get("seen_count", 0),
                    item.get("folder", ""),
                )
                self.tree.insert(group_iid, "end", iid=cid, text="", values=values, tags=(tag,))
        self._first_channel_render = False

    def _render_flat(self, channels: List[Dict[str, Any]]):
        self.tree.delete(*self.tree.get_children(""))
        # Sort flat: Profile name -> Channel title -> Channel ID
        sorted_channels = sorted(
            channels,
            key=lambda x: (
                (x.get("profile_name") or "zzz_chua_gan").casefold(),
                (x.get("title") or "").casefold(),
                x.get("channel_id", "").casefold(),
            ),
        )
        for item in sorted_channels:
            cid = item.get("channel_id", "")
            if not cid:
                continue
            title = (item.get("title") or "").strip()
            display_name = title if title else cid
            status_text = "● Hoạt động" if item.get("active") else "Ⅱ Tạm dừng"
            short_text = "⚡ 40–60s -> 61s" if item.get("process_short") else "⚪ Giữ nguyên"
            tag = "tag_active" if item.get("active") else "tag_inactive"

            values = (
                display_name,
                cid,
                item.get("profile_name", "") or "Chưa gán",
                status_text,
                short_text,
                item.get("seen_count", 0),
                item.get("folder", ""),
            )
            self.tree.insert("", "end", iid=cid, text="", values=values, tags=(tag,))
        self._first_channel_render = False

    def _open_channel_link(self, event=None, cid: Optional[str] = None):
        target_cid = self._resolve_target_channel(cid, require_tree=True)
        if not target_cid:
            return

        # Get channel metadata from live data first, then cache
        channel_item = next((c for c in self._channels_data if c.get("channel_id") == target_cid), {})
        channel_url = channel_item.get("channel_url", "").strip()
        if channel_url:
            url = channel_url
        else:
            url = f"https://www.youtube.com/channel/{target_cid}"

        try:
            ok = webbrowser.open(url)
            if not ok:
                self.append_log(f"Không mở được kênh YouTube: {url} (trình duyệt mặc định không phản hồi)", error=True)
        except Exception as e:
            self.append_log(f"Lỗi khi mở kênh YouTube: {e}", error=True)

    def _open_folder(self, cid: Optional[str] = None):
        target_cid = self._resolve_target_channel(cid, require_tree=True)
        if not target_cid:
            return

        # Get folder from channel metadata first (may have full path), fallback to tree
        channel_item = next((c for c in self._channels_data if c.get("channel_id") == target_cid), {})
        folder = channel_item.get("folder", "").strip()
        if not folder:
            folder = self.tree.set(target_cid, "folder") if self.tree.exists(target_cid) else ""

        if not folder:
            self.append_log(f"Kênh {target_cid} chưa có thư mục lưu video được cấu hình", error=True)
            return

        folder = os.path.normpath(folder)
        if not os.path.exists(folder):
            self.append_log(f"Thư mục không tồn tại: {folder}", error=True)
            return

        try:
            os.startfile(folder)
        except Exception as e:
            self.append_log(f"Lỗi khi mở thư mục: {e}", error=True)

    def _open_profile_picker(self, cid: Optional[str] = None):
        """Mở Searchable Profile Picker Modal để đổi Profile đích cho kênh đang chọn."""
        target_cid = self._resolve_target_channel(cid, require_tree=True)
        if not target_cid:
            return

        # Tra cứu metadata kênh từ cache an toàn
        channel_item = next((c for c in self._channels_data if c.get("channel_id") == target_cid), {})
        title = channel_item.get("title") or (self.tree.set(target_cid, "channel") if self.tree.exists(target_cid) else "") or target_cid
        current_prof = channel_item.get("profile_name") or (self.tree.set(target_cid, "profile") if self.tree.exists(target_cid) else "") or ""

        from ui_dialogs import SearchableProfilePickerModal

        def _on_confirm_profile(new_profile: str) -> Tuple[bool, str]:
            # 1. Revalidate channel exists live before dispatch (fail-closed)
            valid, err = self._validate_channel_live_fail_closed(target_cid)
            if not valid:
                return False, err

            # 2. Revalidate profile live (fail-closed)
            ok_live, live_profiles, msg_live = load_live_profile_names(self.handlers)
            if not ok_live:
                return False, msg_live
            if not live_profiles:
                return False, "Hệ thống hiện không có profile nào khả dụng."
            if new_profile not in live_profiles:
                return False, "Profile không còn tồn tại trong hệ thống; vui lòng chọn lại."

            ok, msg = self._run_handler("set_profile", target_cid, new_profile)
            if ok:
                self.append_log(f"Đã gán profile '{new_profile}' cho kênh {target_cid}", error=False)
                # Refresh từ live data để lấy cả folder/profile mới
                self._refresh_channels_after_action(preferred_cid=target_cid)
            return ok, msg

        SearchableProfilePickerModal(
            parent=self,
            profiles=self.profile_names,
            current_profile=current_prof,
            channel_title=title,
            channel_id=target_cid,
            return_focus_to=self.tree,
            on_confirm=_on_confirm_profile,
        )

    def _open_add_channel_profile_picker(self):
        """Mở Searchable Profile Picker để chọn nhanh Profile gán cho kênh mới."""
        from ui_dialogs import SearchableProfilePickerModal

        def _on_confirm(selected_profile: str) -> Tuple[bool, str]:
            ok, live_profiles, msg = load_live_profile_names(self.handlers)
            if not ok:
                return False, msg
            if not live_profiles:
                return False, "Hệ thống hiện không có profile nào khả dụng."
            if selected_profile not in live_profiles:
                return False, "Profile không còn tồn tại trong hệ thống; vui lòng chọn lại."
            self.profile_var.set(selected_profile)
            return True, ""

        SearchableProfilePickerModal(
            parent=self,
            profiles=self.profile_names,
            current_profile=self.profile_var.get(),
            title_text="Chọn Profile Cho Kênh Mới",
            header_text="➕ CHỌN PROFILE CHO KÊNH MỚI",
            subject_text="🎯 Chọn profile TikTok sẽ nhận video từ kênh này",
            confirm_text="Chọn Profile",
            return_focus_to=getattr(self, "add_profile_picker_btn", None),
            on_confirm=_on_confirm,
        )

    def _open_test_download_profile_picker(self):
        """Mở Searchable Profile Picker để chọn nhanh Profile cho tải thử nghiệm."""
        from ui_dialogs import SearchableProfilePickerModal

        def _on_confirm(selected_profile: str) -> Tuple[bool, str]:
            ok, live_profiles, msg = load_live_profile_names(self.handlers)
            if not ok:
                return False, msg
            if not live_profiles:
                return False, "Hệ thống hiện không có profile nào khả dụng."
            if selected_profile not in live_profiles:
                return False, "Profile không còn tồn tại trong hệ thống; vui lòng chọn lại."
            self.test_profile_var.set(selected_profile)
            return True, ""

        SearchableProfilePickerModal(
            parent=self,
            profiles=self.profile_names,
            current_profile=self.test_profile_var.get(),
            title_text="Chọn Profile Tải Thử",
            header_text="⚡ CHỌN PROFILE TẢI THỬ NGHIỆM",
            subject_text="🎯 Chọn profile TikTok nhận video tải thử",
            confirm_text="Chọn Profile",
            return_focus_to=getattr(self, "test_profile_picker_btn", None),
            on_confirm=_on_confirm,
        )

    def _save_api_key(self):
        key = self.api_key_entry.get().strip()
        threading.Thread(
            target=lambda: self._append_threadsafe(self._run_handler("save_api_key", key)[1]),
            daemon=True,
        ).start()

    def _start(self):
        threading.Thread(
            target=lambda: self._append_threadsafe(self._run_handler("start")[1]),
            daemon=True,
        ).start()

    def _stop(self):
        threading.Thread(
            target=lambda: self._append_threadsafe(self._run_handler("stop")[1]),
            daemon=True,
        ).start()

    def _retry_ngrok(self):
        threading.Thread(
            target=lambda: self._append_threadsafe(self._run_handler("retry_ngrok")[1]),
            daemon=True,
        ).start()

    def _add_channel(self):
        channel = self.channel_entry.get().strip()
        if not channel:
            self.append_log("Vui lòng nhập URL / @handle / ID kênh YouTube", error=True)
            return

        profile = self.profile_var.get().strip()
        if not profile:
            self.append_log("Vui lòng chọn một profile TikTok trước khi thêm kênh", error=True)
            return

        ok_live, live_profiles, msg_live = load_live_profile_names(self.handlers)
        if not ok_live:
            self.append_log(f"Không thể xác thực danh sách profile: {msg_live}", error=True)
            return
        if profile not in live_profiles:
            if hasattr(self, "add_profile_picker_field"):
                self.add_profile_picker_field.set_profiles(live_profiles)
            self.append_log("Profile đã chọn không còn tồn tại trong hệ thống; vui lòng chọn lại", error=True)
            return

        threading.Thread(
            target=lambda: self._append_threadsafe(self._run_handler("add_channel", channel, profile)[1]),
            daemon=True,
        ).start()

    def _download_test(self):
        video = self.test_entry.get().strip()
        if not video:
            self.append_log("Vui lòng nhập URL hoặc Video ID để tải thử", error=True)
            return

        profile = self.test_profile_var.get().strip()
        if not profile:
            self.append_log("Vui lòng chọn một profile TikTok trước khi tải thử", error=True)
            return

        ok_live, live_profiles, msg_live = load_live_profile_names(self.handlers)
        if not ok_live:
            self.append_log(f"Không thể xác thực danh sách profile: {msg_live}", error=True)
            return
        if profile not in live_profiles:
            if hasattr(self, "test_profile_picker_field"):
                self.test_profile_picker_field.set_profiles(live_profiles)
            self.append_log("Profile đã chọn không còn tồn tại trong hệ thống; vui lòng chọn lại", error=True)
            return

        self.append_log(self._run_handler("download_test", video, profile)[1])

    def _choose_cookie_file(self):
        path = filedialog.askopenfilename(
            title="Chọn cookies.txt YouTube",
            filetypes=[("Cookies txt", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.cookie_var.set(path)
            self.cookie_display_var.set(Path(path).name)

    def _save_cookie_file(self):
        path = self.cookie_var.get().strip()
        threading.Thread(
            target=lambda: self._append_threadsafe(self._run_handler("set_cookies_file", path)[1]),
            daemon=True,
        ).start()

    def _clear_cookie_file(self):
        self.cookie_var.set("")
        self.cookie_display_var.set("Chưa chọn cookie")
        self.append_log(self._run_handler("set_cookies_file", "")[1])

    def _save_max_minutes(self):
        value = self.max_minutes_var.get().strip()

        def run():
            ok, msg = self._run_handler("set_max_video_minutes", value)
            if ok:
                try:
                    self.after(0, lambda: self.max_minutes_var.set(str(value)))
                except Exception:
                    pass
            self._append_threadsafe(msg, error=not ok)

        threading.Thread(target=run, daemon=True).start()

    def _install_ffmpeg(self):
        def _run():
            try:
                self.after(0, lambda: self.ffmpeg_status_label.configure(
                    text="FFmpeg: Đang tải...",
                    text_color=UIThemeTokens.STATUS_WARN,
                ))
            except Exception:
                pass
            self._append_threadsafe("Đang tải FFmpeg...", error=False)
            from . import ffmpeg_helper
            try:
                ffmpeg_helper.ensure_ffmpeg(progress_callback=lambda msg, pct: self._append_threadsafe(msg, error=False))
                self._append_threadsafe("FFmpeg đã cài đặt thành công", error=False)
                self._update_ffmpeg_status()
            except Exception as e:
                self._append_threadsafe(f"Lỗi cài FFmpeg: {e}", error=True)
                self._update_ffmpeg_status()

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_ytdlp_status(self):
        try:
            from . import ytdlp_updater
            version = ytdlp_updater.get_ytdlp_version()
            text = f"yt-dlp: {version}" if version else "yt-dlp: ?"
            try:
                self.after(0, lambda: self.ytdlp_label.configure(text=text))
            except Exception:
                pass
        except Exception:
            pass

    def _update_ytdlp(self):
        def _run():
            from . import ytdlp_updater
            self._append_threadsafe(f"yt-dlp hiện tại: {ytdlp_updater.get_ytdlp_version()}", error=False)
            ok, msg = ytdlp_updater.update_ytdlp()
            self._append_threadsafe(msg, error=not ok)
            self._refresh_ytdlp_status()

        threading.Thread(target=_run, daemon=True).start()

    def _toggle_active(self, cid: Optional[str] = None):
        target_cid = self._resolve_target_channel(cid, require_tree=True)
        if not target_cid:
            return
        valid, err = self._validate_channel_live_fail_closed(target_cid)
        if not valid:
            self.append_log(f"Không thể đổi trạng thái theo dõi: {err}", error=True)
            return
        ok, msg = self._run_handler("toggle_active", target_cid)
        if ok:
            self.append_log(f"Đã đổi trạng thái theo dõi kênh {target_cid}: {msg}", error=False)
            self._refresh_channels_after_action(preferred_cid=target_cid)
        else:
            self.append_log(f"Lỗi đổi trạng thái theo dõi {target_cid}: {msg}", error=True)

    def _toggle_short(self, cid: Optional[str] = None):
        target_cid = self._resolve_target_channel(cid, require_tree=True)
        if not target_cid:
            return
        valid, err = self._validate_channel_live_fail_closed(target_cid)
        if not valid:
            self.append_log(f"Không thể đổi trạng thái Shorts: {err}", error=True)
            return
        ok, msg = self._run_handler("toggle_short", target_cid)
        if ok:
            self.append_log(f"Đã đổi trạng thái Shorts kênh {target_cid}: {msg}", error=False)
            self._refresh_channels_after_action(preferred_cid=target_cid)
        else:
            self.append_log(f"Lỗi đổi trạng thái Shorts {target_cid}: {msg}", error=True)

    def _remove_with_confirm(self, cid: Optional[str] = None):
        target_cid = self._resolve_target_channel(cid, require_tree=True)
        if not target_cid:
            return
        valid, err = self._validate_channel_live_fail_closed(target_cid)
        if not valid:
            self.append_log(f"Không thể xóa kênh: {err}", error=True)
            return
        # Get channel title for confirmation dialog
        channel_item = next((c for c in self._channels_data if c.get("channel_id") == target_cid), {})
        title = channel_item.get("title", target_cid)
        if messagebox.askyesno("Xác nhận xóa kênh", f"Bạn có chắc muốn xóa kênh '{title}' ({target_cid}) khỏi danh sách theo dõi?"):
            self._remove(target_cid=target_cid)

    def _remove(self, target_cid: Optional[str] = None):
        cid = self._resolve_target_channel(target_cid, require_tree=True)
        if not cid:
            return
        valid, err = self._validate_channel_live_fail_closed(cid)
        if not valid:
            self.append_log(f"Không thể xóa kênh: {err}", error=True)
            return
        ok, msg = self._run_handler("remove_channel", cid)
        if ok:
            self.append_log(f"Đã xóa kênh {cid}: {msg}", error=False)
            # Clear context and selection since channel is removed
            self._context_channel_id = None
            self.selected_channel_id = None
            self._refresh_channels_after_action(preferred_cid=None)
        else:
            self.append_log(f"Lỗi xóa kênh {cid}: {msg}", error=True)
