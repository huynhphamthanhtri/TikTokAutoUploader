# -*- coding: utf-8 -*-
"""
activity_dialog.py - Modal kiểm tra hoạt động kênh & lọc kênh quá 48h chưa ra video.
Áp dụng Design System UIThemeTokens, ThreadPoolExecutor chống nghẽn UI,
quản lý trạng thái an toàn theo AGENTS.md với phản biện kiểm tra chéo từ Codex.
"""
from __future__ import annotations

import copy
import queue
import threading
import time
import uuid
import webbrowser
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import tkinter as tk
from tkinter import messagebox, ttk
import customtkinter as ctk

from ui_components import UIThemeTokens
from .core import (
    audit_channels_activity_data_api,
    evaluate_channel_activity,
    get_channels,
    log,
    remove_channel,
    set_channel_active,
)


def calculate_dialog_center_geometry(
    screen_w: int,
    screen_h: int,
    screen_x: int = 0,
    screen_y: int = 0,
    pref_w: int = 1120,
    pref_h: int = 720,
    min_w: int = 960,
    min_h: int = 600,
    margin_w: int = 60,
    margin_h: int = 80,
) -> Tuple[int, int, int, int, str]:
    """
    Calculates responsive dimensions and true centered coordinates for the dialog.
    Supports multi-monitor screen offsets (screen_x, screen_y) and ensures coordinates
    are integers and strictly centered on the target display monitor or parent window.
    Returns (width, height, x, y, geometry_string) formatted with standard Tk signs (+/-).
    """
    try:
        sw = int(screen_w)
        sh = int(screen_h)
    except (TypeError, ValueError):
        sw, sh = 1366, 768

    try:
        sx = int(screen_x)
    except (TypeError, ValueError):
        sx = 0

    try:
        sy = int(screen_y)
    except (TypeError, ValueError):
        sy = 0

    if sw <= 0 or sh <= 0:
        sw, sh = 1366, 768

    # Adaptive minimum bounds for small/compact screens to avoid overflow
    effective_min_w = min(min_w, max(640, sw - margin_w))
    effective_min_h = min(min_h, max(480, sh - margin_h))

    avail_w = max(effective_min_w, sw - margin_w)
    avail_h = max(effective_min_h, sh - margin_h)

    width = min(pref_w, avail_w)
    height = min(pref_h, avail_h)

    # True center on display monitor / target area
    x = sx + max(0, (sw - width) // 2)
    y = sy + max(0, (sh - height) // 2)

    # Standard Tk geometry format with explicit +/- signs ({x:+d}{y:+d})
    geom_str = f"{width}x{height}{x:+d}{y:+d}"
    return width, height, x, y, geom_str


class ChannelActivityReviewDialog(ctk.CTkToplevel):
    """
    Hộp thoại kiểm tra hoạt động toàn bộ kênh YouTube trong hệ thống:
    - Phân tích thời điểm ra video mới nhất (Data API / Cached)
    - Tự động phát hiện & cảnh báo kênh quá 48h (hoặc ngưỡng tùy chọn) chưa ra video
    - Quét Data API v3 đồng thời có giới hạn worker, xoay key tự động
    - Thao tác hàng loạt: Tạm dừng theo dõi, Bật lại, Xóa vĩnh viễn kênh
    """

    def __init__(self, parent, on_channels_changed: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.transient(parent)
        self.title("🔍 Bảng Kiểm Tra Hoạt Động Kênh & Lọc Kênh Quá 48h")
        self.geometry("1120x720")
        self.configure(fg_color=UIThemeTokens.BG_ROOT)

        self._on_channels_changed = on_channels_changed
        self._selected_cids: Set[str] = set()
        self._cancellation_event: Optional[threading.Event] = None
        self._scan_thread: Optional[threading.Thread] = None
        self._msg_queue: queue.Queue = queue.Queue()
        self._poll_job: Optional[str] = None
        self._active_run_id: Optional[str] = None
        self._is_scanning: bool = False

        # State Variables
        self.threshold_var = ctk.StringVar(value="48.0")
        self.filter_category_var = ctk.StringVar(value="ALL")
        self.search_var = ctk.StringVar(value="")
        self.status_text_var = ctk.StringVar(value="Sẵn sàng quét hoặc lọc kênh")
        self.stat_total_var = ctk.StringVar(value="Tổng: 0")
        self.stat_in_var = ctk.StringVar(value="🟢 Trong hạn: 0")
        self.stat_exceeded_var = ctk.StringVar(value="⚠ Quá 48h: 0")
        self.stat_nodata_var = ctk.StringVar(value="⚪ Chưa có video: 0")
        self.stat_paused_var = ctk.StringVar(value="⏸ Tạm dừng: 0")
        self.selected_count_var = ctk.StringVar(value="Đã chọn: 0 kênh")

        # In-memory channels data & evaluations
        self._channels_list: List[Dict[str, Any]] = []
        self._channels_by_id: Dict[str, Dict[str, Any]] = {}
        self._evaluations: Dict[str, Dict[str, Any]] = {}

        self._build_ui()
        self._load_local_channels_data()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Center on parent and make modal
        self._center_window(parent)
        try:
            self.grab_set()
        except Exception:
            pass

    def _center_window(self, parent=None):
        try:
            self.update_idletasks()
            top = None
            if parent is not None:
                try:
                    top = parent.winfo_toplevel()
                except Exception:
                    top = None

            target_w = 0
            target_h = 0
            target_x = 0
            target_y = 0

            # 1. If parent toplevel exists and has valid rendered size, center relative to parent window
            # which automatically handles multi-monitor placements (Monitor 1, Monitor 2, maximized).
            if top and hasattr(top, "winfo_width") and hasattr(top, "winfo_height"):
                try:
                    pw = int(top.winfo_width())
                    ph = int(top.winfo_height())
                    px = int(top.winfo_rootx())
                    py = int(top.winfo_rooty())
                    if pw > 300 and ph > 300:
                        target_w, target_h = pw, ph
                        target_x, target_y = px, py
                except Exception:
                    pass

            # 2. If parent bounds unavailable or too small, fall back to screen dimensions
            if target_w <= 0 or target_h <= 0:
                try:
                    target_w = int(self.winfo_screenwidth())
                    target_h = int(self.winfo_screenheight())
                except Exception:
                    target_w, target_h = 1366, 768
                target_x, target_y = 0, 0

            w, h, x, y, geom_str = calculate_dialog_center_geometry(
                screen_w=target_w,
                screen_h=target_h,
                screen_x=target_x,
                screen_y=target_y,
                pref_w=1120,
                pref_h=720,
                min_w=960,
                min_h=600,
            )
            self.geometry(geom_str)
            self.minsize(min(960, w), min(600, h))
        except Exception as exc:
            log(f"Warning: Dialog centering encountered exception: {exc}")

    def _build_ui(self):
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # -------------------------------------------------------------
        # 1. TOP HEADER & STAT CARDS
        # -------------------------------------------------------------
        header_card = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        header_card.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        top_row = ctk.CTkFrame(header_card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(10, 6))

        # Title block
        title_block = ctk.CTkFrame(top_row, fg_color="transparent")
        title_block.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            title_block,
            text="📊 BẢNG THEO DÕI HOẠT ĐỘNG KÊNH YOUTUBE",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w")

        ctk.CTkLabel(
            title_block,
            text="Kiểm tra thời điểm ra video mới nhất, phát hiện các kênh quá 48 giờ chưa ra video để tạm dừng hoặc loại bỏ.",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        # Top Action Buttons
        btn_box = ctk.CTkFrame(top_row, fg_color="transparent")
        btn_box.pack(side="right")

        self.btn_scan = ctk.CTkButton(
            btn_box,
            text="⚡ Quét Data API",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._start_api_scan,
        )
        self.btn_scan.pack(side="left", padx=(0, 6))

        self.btn_cancel = ctk.CTkButton(
            btn_box,
            text="⏹ Dừng Quét",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            state="disabled",
            command=self._cancel_api_scan,
        )
        self.btn_cancel.pack(side="left")

        # Progress bar row (hidden when idle)
        self.progress_frame = ctk.CTkFrame(header_card, fg_color="transparent")
        self.progress_frame.pack(fill="x", padx=14, pady=(0, 8))

        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=6)
        self.progress_bar.pack(fill="x", pady=(2, 4))
        self.progress_bar.set(0.0)

        status_lbl_row = ctk.CTkFrame(self.progress_frame, fg_color="transparent")
        status_lbl_row.pack(fill="x")

        self.lbl_status = ctk.CTkLabel(
            status_lbl_row,
            textvariable=self.status_text_var,
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_status.pack(side="left")

        # Summary Badges Row
        stat_row = ctk.CTkFrame(header_card, fg_color="transparent")
        stat_row.pack(fill="x", padx=14, pady=(0, 10))

        badges = [
            (self.stat_total_var, UIThemeTokens.BG_HOVER, UIThemeTokens.TEXT_PRIMARY),
            (self.stat_in_var, UIThemeTokens.STATUS_LIVE_BG, UIThemeTokens.STATUS_LIVE),
            (self.stat_exceeded_var, UIThemeTokens.STATUS_WARN_BG, UIThemeTokens.STATUS_WARN),
            (self.stat_nodata_var, UIThemeTokens.BG_HOVER, UIThemeTokens.TEXT_MUTED),
        ]
        for var, bg_color, fg_color in badges:
            lbl = ctk.CTkLabel(
                stat_row,
                textvariable=var,
                font=UIThemeTokens.FONT_BADGE,
                fg_color=bg_color,
                text_color=fg_color,
                corner_radius=6,
                padx=10,
                pady=3,
            )
            lbl.pack(side="left", padx=(0, 8))

        # Vertical separator to visually decouple operational paused state from activity categories
        ctk.CTkLabel(
            stat_row,
            text="|",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.BORDER_LIGHT,
        ).pack(side="left", padx=(0, 8))

        lbl_paused = ctk.CTkLabel(
            stat_row,
            textvariable=self.stat_paused_var,
            font=UIThemeTokens.FONT_BADGE,
            fg_color=UIThemeTokens.STATUS_ERROR_BG,
            text_color=UIThemeTokens.STATUS_ERROR,
            corner_radius=6,
            padx=10,
            pady=3,
        )
        lbl_paused.pack(side="left", padx=(0, 8))

        # -------------------------------------------------------------
        # 2. FILTER & TOOLBAR CARD
        # -------------------------------------------------------------
        filter_card = ctk.CTkFrame(
            self,
            corner_radius=8,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        filter_card.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))

        filter_inner = ctk.CTkFrame(filter_card, fg_color="transparent")
        filter_inner.pack(fill="x", padx=12, pady=8)

        # Category Filter Segmented Button
        self.seg_filter = ctk.CTkSegmentedButton(
            filter_inner,
            values=["Tất Cả", "⚠ Quá Ngưỡng", "🟢 Trong Ngưỡng", "⚪ Chưa Có Video", "⏸ Đang Tạm Dừng"],
            font=UIThemeTokens.FONT_BUTTON,
            height=28,
            command=self._on_filter_changed,
        )
        self.seg_filter.set("Tất Cả")
        self.seg_filter.pack(side="left", padx=(0, 12))

        # Threshold Selector
        ctk.CTkLabel(
            filter_inner,
            text="Ngưỡng:",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left", padx=(0, 4))

        self.combo_threshold = ctk.CTkComboBox(
            filter_inner,
            values=["24 giờ", "48 giờ (Mặc định)", "72 giờ", "7 ngày (168h)"],
            width=140,
            height=28,
            font=UIThemeTokens.FONT_BODY,
            command=self._on_threshold_changed,
        )
        self.combo_threshold.set("48 giờ (Mặc định)")
        self.combo_threshold.pack(side="left", padx=(0, 12))

        # Search Box
        self.entry_search = ctk.CTkEntry(
            filter_inner,
            textvariable=self.search_var,
            placeholder_text="🔍 Tìm kiếm kênh, Channel ID, Profile, Video...",
            height=28,
            font=UIThemeTokens.FONT_BODY,
        )
        self.entry_search.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.search_var.trace_add("write", lambda *_: self._render_table())

        # -------------------------------------------------------------
        # 3. CHANNEL TABLE (Treeview)
        # -------------------------------------------------------------
        table_card = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        table_card.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 6))
        table_card.grid_rowconfigure(0, weight=1)
        table_card.grid_columnconfigure(0, weight=1)

        cols = ("select", "profile", "channel", "latest_video", "age", "status", "audit")
        self.tree = ttk.Treeview(
            table_card,
            style="Modern.Treeview",
            columns=cols,
            show="headings",
            selectmode="extended",
        )

        cols_cfg = [
            ("select", "Chọn", 46, "center", False),
            ("profile", "Profile", 110, "w", False),
            ("channel", "Kênh YouTube (Tên / ID)", 250, "w", True),
            ("latest_video", "Video Mới Nhất", 340, "w", True),
            ("age", "Thời Điểm Ra Video", 160, "center", False),
            ("status", "Theo Dõi", 95, "center", False),
            ("audit", "API Quét", 85, "center", False),
        ]
        for col_id, col_text, col_w, col_anchor, col_stretch in cols_cfg:
            self.tree.heading(col_id, text=col_text, anchor=col_anchor)
            self.tree.column(col_id, width=col_w, minwidth=40, anchor=col_anchor, stretch=col_stretch)

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)

        vsb = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        hsb = ttk.Scrollbar(table_card, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky="ew", padx=(6, 0), pady=(0, 6))
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        # Tag Styles
        self.tree.tag_configure("tag_exceeded", foreground="#b45309", background="#fffbeb")  # Amber/Yellow warn
        self.tree.tag_configure("tag_in_threshold", foreground="#15803d")                     # Green normal
        self.tree.tag_configure("tag_nodata", foreground="#64748b")                           # Slate muted
        self.tree.tag_configure("tag_paused", foreground="#94a3b8")                           # Disabled muted

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<space>", self._on_tree_space)

        # -------------------------------------------------------------
        # 4. ACTION FOOTER BAR
        # -------------------------------------------------------------
        footer_card = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            height=46,
        )
        footer_card.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))

        footer_inner = ctk.CTkFrame(footer_card, fg_color="transparent")
        footer_inner.pack(fill="x", padx=12, pady=6)

        # Left action buttons
        ctk.CTkButton(
            footer_inner,
            text="☑ Chọn Tất Cả Quá Ngưỡng Đang Hiện",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            fg_color=UIThemeTokens.BG_HOVER,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self._select_all_exceeded,
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(
            footer_inner,
            text="☐ Bỏ Chọn",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            fg_color=UIThemeTokens.BG_HOVER,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self._deselect_all,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkLabel(
            footer_inner,
            textvariable=self.selected_count_var,
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left")

        # Right action buttons
        ctk.CTkButton(
            footer_inner,
            text="Đóng",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            width=80,
            fg_color=UIThemeTokens.BG_HOVER,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self._on_close,
        ).pack(side="right")

        self.btn_bulk_remove = ctk.CTkButton(
            footer_inner,
            text="🗑 Xóa Khỏi Hệ Thống",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            command=self._bulk_remove_channels,
        )
        self.btn_bulk_remove.pack(side="right", padx=(0, 6))

        self.btn_bulk_resume = ctk.CTkButton(
            footer_inner,
            text="▶ Bật Lại Kênh",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            fg_color=UIThemeTokens.STATUS_LIVE,
            hover_color="#15803d",
            command=lambda: self._bulk_set_active(True),
        )
        self.btn_bulk_resume.pack(side="right", padx=(0, 6))

        self.btn_bulk_pause = ctk.CTkButton(
            footer_inner,
            text="⏸ Tạm Dừng Theo Dõi",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: self._bulk_set_active(False),
        )
        self.btn_bulk_pause.pack(side="right", padx=(0, 6))

    # -----------------------------------------------------------------
    # DATA LOADING & EVALUATION LOGIC
    # -----------------------------------------------------------------
    def _get_current_threshold_hours(self) -> float:
        val = self.combo_threshold.get()
        if "24" in val:
            return 24.0
        if "72" in val:
            return 72.0
        if "7 ngày" in val or "168" in val:
            return 168.0
        return 48.0

    def _load_local_channels_data(self):
        """Tải dữ liệu kênh hiện tại, lập chỉ mục O(1) và tính toán evaluation."""
        self._channels_list = get_channels()
        self._channels_by_id = {c["channel_id"]: c for c in self._channels_list if c.get("channel_id")}

        # Prune obsolete selection IDs to prevent stale actions
        valid_cids = set(self._channels_by_id.keys())
        self._selected_cids &= valid_cids

        # Rebuild evaluations cleanly for active channels only
        th_hours = self._get_current_threshold_hours()
        now = time.time()
        new_evals = {}
        for cid, ch in self._channels_by_id.items():
            new_evals[cid] = evaluate_channel_activity(ch, now_utc=now, threshold_hours=th_hours)
        self._evaluations = new_evals

        total = len(self._channels_list)
        if hasattr(self, "btn_scan") and self.btn_scan:
            self.btn_scan.configure(text=f"⚡ Quét Data API (~{total} Quota)")

        self._render_table()
        self._update_stat_badges()

    def _update_stat_badges(self):
        total = len(self._channels_list)
        th_hours = self._get_current_threshold_hours()
        now = time.time()

        in_cnt = 0
        exceeded_cnt = 0
        nodata_cnt = 0
        paused_cnt = 0

        for ch in self._channels_list:
            cid = ch.get("channel_id")
            ev = self._evaluations.get(cid) or evaluate_channel_activity(ch, now_utc=now, threshold_hours=th_hours)
            if not ch.get("active", True):
                paused_cnt += 1

            cat = ev.get("category")
            if cat == "EXCEEDED_THRESHOLD":
                exceeded_cnt += 1
            elif cat == "IN_THRESHOLD":
                in_cnt += 1
            else:
                nodata_cnt += 1

        self.stat_total_var.set(f"Tổng: {total}")
        self.stat_in_var.set(f"🟢 Trong hạn: {in_cnt}")
        self.stat_exceeded_var.set(f"⚠ Quá {th_hours:g}h: {exceeded_cnt}")
        self.stat_nodata_var.set(f"⚪ Chưa rõ: {nodata_cnt}")
        self.stat_paused_var.set(f"⏸ Tạm dừng: {paused_cnt}")
        self.selected_count_var.set(f"Đã chọn: {len(self._selected_cids)} kênh")

    def _matches_current_filter(self, ch: dict, ev: dict) -> bool:
        """Kiểm tra một kênh có thỏa mãn bộ lọc danh mục và từ khóa tìm kiếm hiện tại hay không."""
        filter_cat = self.seg_filter.get()
        search_kw = (self.search_var.get() or "").strip().lower()
        cat = ev.get("category", "NO_DATA")
        is_active = bool(ch.get("active", True))

        if filter_cat == "⚠ Quá Ngưỡng" and cat != "EXCEEDED_THRESHOLD":
            return False
        if filter_cat == "🟢 Trong Ngưỡng" and cat != "IN_THRESHOLD":
            return False
        if filter_cat == "⚪ Chưa Có Video" and cat != "NO_DATA":
            return False
        if filter_cat == "⏸ Đang Tạm Dừng" and is_active:
            return False

        if search_kw:
            title = str(ch.get("title") or "").lower()
            cid = str(ch.get("channel_id") or "").lower()
            prof = str(ch.get("profile_name") or "").lower()
            vtitle = str(ev.get("latest_video_title") or "").lower()
            if (
                search_kw not in title
                and search_kw not in cid
                and search_kw not in prof
                and search_kw not in vtitle
            ):
                return False

        return True

    def _render_table(self):
        """Render các hàng trong Treeview dựa trên bộ lọc danh mục và từ khóa tìm kiếm."""
        prev_focused = self.tree.focus()

        for item_id in self.tree.get_children():
            self.tree.delete(item_id)

        th_hours = self._get_current_threshold_hours()
        now = time.time()

        for ch in self._channels_list:
            cid = ch.get("channel_id", "")
            if not cid:
                continue

            ev = self._evaluations.get(cid) or evaluate_channel_activity(ch, now_utc=now, threshold_hours=th_hours)
            if not self._matches_current_filter(ch, ev):
                continue

            cat = ev.get("category", "NO_DATA")
            is_active = bool(ch.get("active", True))

            is_sel = cid in self._selected_cids
            sel_mark = "☑" if is_sel else "☐"

            if not is_active:
                tag = "tag_paused"
                status_display = "⏸ Tạm dừng"
            elif cat == "EXCEEDED_THRESHOLD":
                tag = "tag_exceeded"
                status_display = "🟢 Đang chạy"
            elif cat == "IN_THRESHOLD":
                tag = "tag_in_threshold"
                status_display = "🟢 Đang chạy"
            else:
                tag = "tag_nodata"
                status_display = "🟢 Đang chạy"

            ast = ev.get("audit_status") or "CACHED"
            if ast == "OK":
                audit_display = "✓ OK"
            elif ast == "EMPTY":
                audit_display = "Trống"
            elif ast == "ERROR":
                audit_display = "⚠ Lỗi"
            else:
                audit_display = "Bộ nhớ đệm"

            channel_display = f"{ch.get('title') or cid} ({cid})"
            video_display = ev.get("latest_video_title") or "(Chưa có video)"

            self.tree.insert(
                "",
                "end",
                iid=cid,
                values=(
                    sel_mark,
                    ch.get("profile_name") or "-",
                    channel_display,
                    video_display,
                    ev.get("age_display") or "Chưa có dữ liệu",
                    status_display,
                    audit_display,
                ),
                tags=(tag,),
            )

        if prev_focused and self.tree.exists(prev_focused):
            self.tree.focus(prev_focused)

    # -----------------------------------------------------------------
    # EVENTS & INTERACTION HANDLERS
    # -----------------------------------------------------------------
    def _on_filter_changed(self, value):
        self._render_table()

    def _on_threshold_changed(self, value):
        th_hours = self._get_current_threshold_hours()
        now = time.time()
        for ch in self._channels_list:
            cid = ch.get("channel_id")
            if cid:
                self._evaluations[cid] = evaluate_channel_activity(ch, now_utc=now, threshold_hours=th_hours)
        self._update_stat_badges()
        self._render_table()

    def _on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        column = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)

        if not row_id:
            return

        if column == "#1" or region == "cell":
            if column == "#1":
                self._toggle_selection(row_id)
                return "break"

    def _on_tree_space(self, event):
        focused = self.tree.focus()
        if focused:
            self._toggle_selection(focused)
            return "break"

    def _toggle_selection(self, cid: str):
        if cid in self._selected_cids:
            self._selected_cids.remove(cid)
        else:
            self._selected_cids.add(cid)

        if self.tree.exists(cid):
            curr_vals = list(self.tree.item(cid, "values"))
            curr_vals[0] = "☑" if cid in self._selected_cids else "☐"
            self.tree.item(cid, values=curr_vals)

        self.selected_count_var.set(f"Đã chọn: {len(self._selected_cids)} kênh")

    def _select_all_exceeded(self):
        """Chọn tất cả kênh quá ngưỡng đang hiển thị trong bảng."""
        count_added = 0
        for item_id in self.tree.get_children():
            ev = self._evaluations.get(item_id) or {}
            if ev.get("category") == "EXCEEDED_THRESHOLD":
                self._selected_cids.add(item_id)
                count_added += 1

        self._render_table()
        self.selected_count_var.set(f"Đã chọn: {len(self._selected_cids)} kênh")

    def _deselect_all(self):
        self._selected_cids.clear()
        self._render_table()
        self.selected_count_var.set("Đã chọn: 0 kênh")

    def _on_tree_double_click(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        ev = self._evaluations.get(row_id) or {}
        vid = ev.get("latest_video_id")
        if vid:
            url = f"https://www.youtube.com/watch?v={vid}"
        else:
            ch_item = self._channels_by_id.get(row_id)
            url = ch_item.get("channel_url") if ch_item else f"https://www.youtube.com/channel/{row_id}"

        if url:
            webbrowser.open(url)

    # -----------------------------------------------------------------
    # BATCH ACTIONS: BULK PAUSE, RESUME, REMOVE (WITH RACE GUARDS)
    # -----------------------------------------------------------------
    def _bulk_set_active(self, active: bool):
        if self._is_scanning:
            messagebox.showwarning(
                "Đang Quét",
                "Hệ thống đang quét Data API. Vui lòng dừng quét hoặc đợi hoàn tất trước khi thao tác.",
                parent=self,
            )
            return

        if not self._selected_cids:
            messagebox.showinfo("Thông báo", "Vui lòng chọn ít nhất một kênh để thao tác.", parent=self)
            return

        action_name = "tiếp tục theo dõi" if active else "tạm dừng theo dõi"
        confirm = messagebox.askyesno(
            "Xác nhận",
            f"Bạn có muốn {action_name} cho {len(self._selected_cids)} kênh đã chọn không?",
            parent=self,
        )
        if not confirm:
            return

        updated_count = 0
        failed_count = 0
        try:
            for cid in list(self._selected_cids):
                try:
                    ok, msg, status_code = set_channel_active(cid, active)
                    if ok:
                        updated_count += 1
                    else:
                        failed_count += 1
                except Exception as op_err:
                    log(f"[BulkAction] Lỗi set_active kênh {cid}: {op_err}")
                    failed_count += 1
        finally:
            try:
                self._load_local_channels_data()
            except Exception as load_err:
                log(f"[ActivityDialog] Lỗi làm mới dữ liệu cục bộ sau bulk action: {load_err}")

            if self._on_channels_changed and callable(self._on_channels_changed):
                try:
                    self._on_channels_changed()
                except Exception as cb_err:
                    log(f"[ActivityDialog] Callback error: {cb_err}")

        res_msg = f"Đã {action_name} {updated_count} kênh."
        if failed_count > 0:
            res_msg += f" (Gặp lỗi {failed_count} kênh)"
        messagebox.showinfo("Kết quả", res_msg, parent=self)

    def _bulk_remove_channels(self):
        if self._is_scanning:
            messagebox.showwarning(
                "Đang Quét",
                "Hệ thống đang quét Data API. Vui lòng dừng quét hoặc đợi hoàn tất trước khi xóa kênh.",
                parent=self,
            )
            return

        if not self._selected_cids:
            messagebox.showinfo("Thông báo", "Vui lòng chọn ít nhất một kênh để xóa.", parent=self)
            return

        sample_titles = []
        for cid in list(self._selected_cids)[:5]:
            ch = self._channels_by_id.get(cid)
            ch_title = ch.get("title") if ch else cid
            sample_titles.append(f"- {ch_title or cid}")

        preview = "\n".join(sample_titles)
        if len(self._selected_cids) > 5:
            preview += f"\n... và {len(self._selected_cids) - 5} kênh khác"

        confirm = messagebox.askyesno(
            "⚠ XÁC NHẬN XÓA KÊNH KHỎI HỆ THỐNG",
            f"Bạn có chắc chắn muốn XÓA VĨNH VIỄN {len(self._selected_cids)} kênh sau khỏi hệ thống giám sát tự động?\n\n{preview}\n\nThao tác này KHÔNG THỂ hoàn tác!",
            icon="warning",
            parent=self,
        )
        if not confirm:
            return

        removed_count = 0
        failed_count = 0
        try:
            for cid in list(self._selected_cids):
                try:
                    ok, msg = remove_channel(cid)
                    if ok:
                        removed_count += 1
                        self._selected_cids.discard(cid)
                    else:
                        failed_count += 1
                except Exception as op_err:
                    log(f"[BulkAction] Lỗi remove_channel {cid}: {op_err}")
                    failed_count += 1
        finally:
            try:
                self._load_local_channels_data()
            except Exception as load_err:
                log(f"[ActivityDialog] Lỗi làm mới dữ liệu cục bộ sau bulk action: {load_err}")

            if self._on_channels_changed and callable(self._on_channels_changed):
                try:
                    self._on_channels_changed()
                except Exception as cb_err:
                    log(f"[ActivityDialog] Callback error: {cb_err}")

        res_msg = f"Đã xóa vĩnh viễn {removed_count} kênh khỏi hệ thống."
        if failed_count > 0:
            res_msg += f" (Gặp lỗi {failed_count} kênh)"
        messagebox.showinfo("Kết quả", res_msg, parent=self)

    # -----------------------------------------------------------------
    # PHASE 4: BACKGROUND DATA API SWEEP & QUEUE POLLING
    # -----------------------------------------------------------------
    def _set_bulk_buttons_state(self, state: str):
        self.btn_bulk_remove.configure(state=state)
        self.btn_bulk_resume.configure(state=state)
        self.btn_bulk_pause.configure(state=state)

    def _start_api_scan(self):
        if self._is_scanning:
            return

        if not self._channels_list:
            messagebox.showinfo("Thông báo", "Hệ thống chưa có kênh nào để quét.", parent=self)
            return

        self._is_scanning = True
        self._active_run_id = uuid.uuid4().hex
        self._cancellation_event = threading.Event()
        self.btn_scan.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self._set_bulk_buttons_state("disabled")
        self.progress_bar.set(0.0)
        self.status_text_var.set(f"Đang chuẩn bị quét {len(self._channels_list)} kênh qua YouTube Data API v3...")

        channels_snapshot = copy.deepcopy(self._channels_list)
        run_id = self._active_run_id

        self._scan_thread = threading.Thread(
            target=self._run_scan_worker,
            args=(channels_snapshot, self._cancellation_event, run_id),
            daemon=True,
        )
        self._scan_thread.start()

        self._poll_job = self.after(50, self._poll_queue)

    def _cancel_api_scan(self):
        if self._cancellation_event:
            self._cancellation_event.set()
            self.status_text_var.set("Đang gửi tín hiệu hủy quét...")
            self.btn_cancel.configure(state="disabled")

    def _run_scan_worker(self, channels_snapshot, cancel_evt, run_id):
        try:
            def _progress_cb(completed, total, res_snapshot, active_run_id=None):
                self._msg_queue.put(("PROGRESS", completed, total, res_snapshot, active_run_id or run_id))

            summary = audit_channels_activity_data_api(
                channels_list=channels_snapshot,
                progress_callback=_progress_cb,
                cancellation_event=cancel_evt,
                max_workers=4,
                run_id=run_id,
            )
            self._msg_queue.put(("DONE", summary, run_id))
        except Exception as err:
            self._msg_queue.put(("ERROR", str(err), run_id))

    def _poll_queue(self):
        drained_count = 0
        max_batch = 20
        badge_dirty = False

        try:
            while not self._msg_queue.empty() and drained_count < max_batch:
                drained_count += 1
                msg = self._msg_queue.get_nowait()
                msg_type = msg[0]

                if msg_type == "PROGRESS":
                    _, completed, total, res_item, msg_run_id = msg
                    if not self._active_run_id or msg_run_id != self._active_run_id:
                        continue

                    progress_pct = completed / max(1, total)
                    self.progress_bar.set(progress_pct)
                    cid = res_item.get("channel_id")
                    st = res_item.get("status")
                    self.status_text_var.set(
                        f"Đang quét: {completed}/{total} kênh ({int(progress_pct * 100)}%) - Kênh: {cid} [{st}]"
                    )

                    if cid:
                        th_hours = self._get_current_threshold_hours()
                        ch = self._channels_by_id.get(cid)
                        if ch is None:
                            continue

                        merged_meta = dict(ch)
                        if st == "EMPTY":
                            merged_meta["latest_observed_video_id"] = ""
                            merged_meta["latest_observed_video_title"] = ""
                            merged_meta["latest_observed_pub_utc"] = None
                        elif res_item.get("video_id"):
                            merged_meta["latest_observed_video_id"] = res_item.get("video_id")
                            merged_meta["latest_observed_video_title"] = res_item.get("video_title", "")
                            merged_meta["latest_observed_pub_utc"] = res_item.get("published_utc")

                        merged_meta["last_api_audit_status"] = st
                        merged_meta["last_api_audit_error"] = res_item.get("error", "")
                        if res_item.get("last_api_audit_at") is not None:
                            merged_meta["last_api_audit_at"] = res_item.get("last_api_audit_at")
                        if res_item.get("last_api_audit_success_at") is not None:
                            merged_meta["last_api_audit_success_at"] = res_item.get("last_api_audit_success_at")

                        # In-place update so both _channels_by_id and _channels_list reference fresh data
                        ch.update(merged_meta)

                        fresh_eval = evaluate_channel_activity(ch, now_utc=time.time(), threshold_hours=th_hours)
                        self._evaluations[cid] = fresh_eval
                        self._sync_single_row_with_filter(cid, fresh_eval)
                        badge_dirty = True

                elif msg_type == "DONE":
                    _, summary, msg_run_id = msg
                    if not self._active_run_id or msg_run_id != self._active_run_id:
                        continue
                    if badge_dirty:
                        self._update_stat_badges()
                        badge_dirty = False
                    self._on_scan_completed(summary)
                    return

                elif msg_type == "ERROR":
                    _, err_msg, msg_run_id = msg
                    if not self._active_run_id or msg_run_id != self._active_run_id:
                        continue
                    if badge_dirty:
                        self._update_stat_badges()
                        badge_dirty = False
                    self._on_scan_error(err_msg)
                    return

        except Exception as poll_err:
            log(f"[ActivityDialog] Lỗi poll queue: {poll_err}")

        if badge_dirty:
            self._update_stat_badges()

        if self._is_scanning:
            self._poll_job = self.after(50, self._poll_queue)

    def _sync_single_row_with_filter(self, cid: str, ev: dict):
        """Đồng bộ một hàng vào bảng theo bộ lọc hiện tại (insert, update, hoặc remove nếu không còn match)."""
        ch = self._channels_by_id.get(cid)
        if not ch:
            if self.tree.exists(cid):
                self.tree.delete(cid)
            return

        matches = self._matches_current_filter(ch, ev)
        if not matches:
            if self.tree.exists(cid):
                self.tree.delete(cid)
            return

        cat = ev.get("category", "NO_DATA")
        is_active = bool(ch.get("active", True))

        if not is_active:
            tag = "tag_paused"
            status_display = "⏸ Tạm dừng"
        elif cat == "EXCEEDED_THRESHOLD":
            tag = "tag_exceeded"
            status_display = "🟢 Đang chạy"
        elif cat == "IN_THRESHOLD":
            tag = "tag_in_threshold"
            status_display = "🟢 Đang chạy"
        else:
            tag = "tag_nodata"
            status_display = "🟢 Đang chạy"

        ast = ev.get("audit_status") or "OK"
        if ast == "OK":
            audit_display = "✓ OK"
        elif ast == "EMPTY":
            audit_display = "Trống"
        elif ast == "ERROR":
            audit_display = "⚠ Lỗi"
        else:
            audit_display = "Bộ nhớ đệm"

        is_sel = cid in self._selected_cids
        sel_mark = "☑" if is_sel else "☐"

        row_vals = (
            sel_mark,
            ch.get("profile_name") or "-",
            f"{ch.get('title') or cid} ({cid})",
            ev.get("latest_video_title") or "(Chưa có video)",
            ev.get("age_display") or "Chưa có dữ liệu",
            status_display,
            audit_display,
        )

        if self.tree.exists(cid):
            self.tree.item(cid, values=row_vals, tags=(tag,))
        else:
            self.tree.insert("", "end", iid=cid, values=row_vals, tags=(tag,))

    def _on_scan_completed(self, summary: dict):
        self._is_scanning = False
        self.btn_scan.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self._set_bulk_buttons_state("normal")
        self.progress_bar.set(1.0)

        tot = summary.get("total", 0)
        ok_cnt = summary.get("ok", 0)
        empty_cnt = summary.get("empty", 0)
        err_cnt = summary.get("error", 0)
        is_cancelled = summary.get("cancelled", False)

        if is_cancelled:
            self.status_text_var.set(
                f"⏹ Đã dừng quét: Hoàn thành {summary.get('completed', 0)}/{tot} kênh ({ok_cnt} OK, {err_cnt} lỗi)."
            )
        else:
            self.status_text_var.set(
                f"✓ Hoàn tất quét Data API: {ok_cnt} OK, {empty_cnt} kênh trống, {err_cnt} lỗi ({tot} kênh)."
            )

        self._load_local_channels_data()

        if self._on_channels_changed and callable(self._on_channels_changed):
            try:
                self._on_channels_changed()
            except Exception:
                pass

        if not is_cancelled:
            th_hours = self._get_current_threshold_hours()
            exceeded_cnt = sum(
                1 for ev in self._evaluations.values() if ev.get("category") == "EXCEEDED_THRESHOLD"
            )
            if exceeded_cnt > 0:
                messagebox.showwarning(
                    "Cảnh Báo Kênh Ngừng Ra Video",
                    f"Phát hiện {exceeded_cnt} kênh đã quá {th_hours:g} giờ không ra video nào!\n\nBạn có thể nhấn nút 'Chọn Tất Cả Quá Ngưỡng' để tạm dừng hoặc xóa các kênh này.",
                    parent=self,
                )

    def _on_scan_error(self, err_msg: str):
        self._is_scanning = False
        self.btn_scan.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self._set_bulk_buttons_state("normal")
        self.status_text_var.set(f"⚠ Lỗi trong quá trình quét: {err_msg}")
        messagebox.showerror("Lỗi Quét Kênh", f"Đã xảy ra lỗi khi quét: {err_msg}", parent=self)

    def _on_close(self):
        if self._is_scanning and self._cancellation_event:
            self._cancellation_event.set()

        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass

        self.destroy()
