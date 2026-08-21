"""
ui_statistics.py - Module Thống Kê Hoạt Động & Hiệu Suất Video (YouTube Download & TikTok Upload)
dành cho DONGLAO-TIKTOK Automation & Studio Suite.

Mô-đun được thiết kế phân tầng chuẩn mực:
1. Pure Aggregation Engine: Xử lý dữ liệu thuần túy (O(N) single-pass scan, zero-division safe,
   lọc theo mốc thời gian, dự án, từ khóa tìm kiếm, trích xuất danh sách lỗi theo tài khoản,
   xuất báo cáo CSV UTF-8-sig) - 100% testable độc lập trên CI không cần màn hình.
2. Presentation Layer (StatisticsWorkspaceView): Giao diện CustomTkinter + ttk.Treeview
   chuẩn UIThemeTokens với KPI Cards, Bảng chi tiết từng tài khoản (sortable), và
   Bảng kiểm tra lỗi nhanh (Quick Error Inspector) cho tài khoản được chọn.
"""

from __future__ import annotations

import csv
import io
import os
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import customtkinter as ctk

from ui_components import UIThemeTokens

# ==============================================================================
# 1. CONSTANTS & MAPPINGS
# ==============================================================================

ALL_OPTION = "Tất cả"

TIMEFRAME_OPTIONS: List[Tuple[str, str]] = [
    ("all", "Tất cả"),
    ("today", "Hôm nay"),
    ("yesterday", "Hôm qua"),
    ("7days", "7 ngày qua"),
    ("30days", "30 ngày qua"),
]

ACCOUNT_COLUMNS: List[Tuple[str, str, int, str]] = [
    ("name", "Tên Hồ Sơ", 180, "w"),
    ("project", "Dự Án", 120, "center"),
    ("dl_ok", "Tải OK", 80, "center"),
    ("dl_fail", "Tải Lỗi", 80, "center"),
    ("up_ok", "Đăng OK", 85, "center"),
    ("up_fail", "Đăng Lỗi", 85, "center"),
    ("today", "Hôm Nay", 85, "center"),
    ("yesterday", "Hôm Qua", 85, "center"),
    ("rate", "Tỷ Lệ %", 85, "center"),
    ("last_active", "Hoạt Động Cuối", 140, "center"),
]

ERROR_COLUMNS: List[Tuple[str, str, int, str]] = [
    ("time", "Thời Gian", 130, "center"),
    ("type", "Tác Vụ", 110, "center"),
    ("video_name", "Tên Video", 220, "w"),
    ("detail", "Lý Do Thất Bại / Mã Lỗi", 280, "w"),
    ("video_url", "Link Video", 180, "w"),
    ("file_path", "Đường Dẫn File", 220, "w"),
]


# ==============================================================================
# 2. PURE LOGIC & DATA AGGREGATION ENGINE (100% CI TESTABLE)
# ==============================================================================

def parse_log_datetime(dt_str: Any) -> Optional[datetime]:
    """Phân tích an toàn chuỗi timestamp từ log sang đối tượng datetime."""
    if not dt_str:
        return None
    s = str(dt_str).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except (ValueError, TypeError):
            pass
    return None


def filter_by_timeframe(
    rows: Sequence[Dict[str, Any]],
    timeframe: str = "all",
    now_dt: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Lọc danh sách các dòng activity logs theo mốc thời gian (Pure function).
    Hỗ trợ: 'all', 'today', 'yesterday', '7days', '30days'.
    """
    tf = str(timeframe or "all").strip().lower()
    if tf == "all" or not rows:
        return list(rows)

    now = now_dt or datetime.now()
    today_start = datetime(now.year, now.month, now.day)
    yesterday_start = today_start - timedelta(days=1)
    seven_days_ago = today_start - timedelta(days=7)
    thirty_days_ago = today_start - timedelta(days=30)

    filtered: List[Dict[str, Any]] = []
    for row in rows:
        dt = parse_log_datetime(row.get("time"))
        if not dt:
            continue

        if tf == "today":
            if dt >= today_start:
                filtered.append(row)
        elif tf == "yesterday":
            if yesterday_start <= dt < today_start:
                filtered.append(row)
        elif tf in ("7days", "7_days", "week"):
            if dt >= seven_days_ago:
                filtered.append(row)
        elif tf in ("30days", "30_days", "month"):
            if dt >= thirty_days_ago:
                filtered.append(row)
        else:
            filtered.append(row)

    return filtered


def get_account_recent_errors(
    activity_rows: Sequence[Dict[str, Any]],
    profile_name: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Trích xuất danh sách các sự kiện lỗi gần nhất của một tài khoản cụ thể.
    """
    p_name = str(profile_name or "").strip().lower()
    if not p_name:
        return []

    errors: List[Dict[str, Any]] = []
    for row in reversed(activity_rows):
        row_profile = str(row.get("profile", "")).strip().lower()
        if row_profile != p_name:
            continue
        status = str(row.get("status", "")).strip().lower()
        if status in ("fail", "error", "failed"):
            errors.append(dict(row))
            if len(errors) >= limit:
                break

    return errors


def aggregate_statistics(
    activity_rows: Sequence[Dict[str, Any]],
    profiles_data: Dict[str, Any],
    project_mapping: Dict[str, List[str]],
    project_filter: str = ALL_OPTION,
    timeframe: str = "all",
    search_query: str = "",
    now_dt: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Tổng hợp toàn diện dữ liệu thống kê hệ thống và theo từng tài khoản (Pure Engine).
    Thực hiện quét 1 lượt O(N) duy nhất tối ưu hóa hiệu năng tối đa.
    """
    proj_filter = str(project_filter or ALL_OPTION).strip()
    search_q = str(search_query or "").strip().lower()

    # 1. Xác định tập profile hợp lệ theo bộ lọc dự án
    reverse_proj_map: Dict[str, str] = {}
    for proj, prof_list in (project_mapping or {}).items():
        if isinstance(prof_list, (list, tuple, set)):
            for p in prof_list:
                reverse_proj_map[str(p)] = str(proj)

    all_profile_names: Set[str] = set(str(k) for k in (profiles_data or {}).keys())

    # Thêm các profile có thể đã xuất hiện trong log nhưng chưa có trong profiles_data
    for r in activity_rows:
        prof_in_log = str(r.get("profile", "")).strip()
        if prof_in_log:
            all_profile_names.add(prof_in_log)

    target_profiles: Set[str] = set()
    for prof in all_profile_names:
        prof_proj = reverse_proj_map.get(prof, "Mặc định")
        if proj_filter == ALL_OPTION or prof_proj == proj_filter:
            if not search_q or search_q in prof.lower() or search_q in prof_proj.lower():
                target_profiles.add(prof)

    # 2. Lọc log theo mốc thời gian
    filtered_rows = filter_by_timeframe(activity_rows, timeframe=timeframe, now_dt=now_dt)

    # 3. Khởi tạo cấu trúc thống kê từng profile
    account_stats: Dict[str, Dict[str, Any]] = {}
    for prof in target_profiles:
        p_info = profiles_data.get(prof, {}) if isinstance(profiles_data, dict) else {}
        cfg = p_info.get("config", {}) if isinstance(p_info, dict) else {}
        account_stats[prof] = {
            "name": prof,
            "project": reverse_proj_map.get(prof, "Mặc định"),
            "dl_ok": 0,
            "dl_fail": 0,
            "dl_skipped": 0,
            "up_ok": 0,
            "up_fail": 0,
            "today": int(p_info.get("uploads_today_count", cfg.get("uploads_today_count", 0)) or 0),
            "yesterday": int(p_info.get("uploads_yesterday_count", cfg.get("uploads_yesterday_count", 0)) or 0),
            "last_active": "",
            "last_active_dt": None,
        }

    # 4. Quét 1 lượt duy nhất qua filtered_rows để tổng hợp
    total_dl_ok = 0
    total_dl_fail = 0
    total_dl_skipped = 0
    total_up_ok = 0
    total_up_fail = 0

    recent_errors_map: Dict[str, List[Dict[str, Any]]] = {}

    for row in filtered_rows:
        event_type = str(row.get("type", "")).strip().lower()
        status = str(row.get("status", "")).strip().lower()
        prof_name = str(row.get("profile", "")).strip()
        row_time_str = str(row.get("time", "")).strip()

        # Kiểm tra event thuộc profile đang xét
        is_target_prof = prof_name in account_stats
        acc = account_stats.get(prof_name) if is_target_prof else None

        if event_type == "youtube_download":
            if status in ("success", "ok", "tải thành công"):
                total_dl_ok += 1
                if acc:
                    acc["dl_ok"] += 1
            elif status in ("fail", "error", "failed", "lỗi"):
                total_dl_fail += 1
                if acc:
                    acc["dl_fail"] += 1
            elif status in ("skipped", "bỏ qua"):
                total_dl_skipped += 1
                if acc:
                    acc["dl_skipped"] += 1

        elif event_type == "tiktok_upload":
            if status in ("success", "ok", "đã đăng", "uploaded"):
                total_up_ok += 1
                if acc:
                    acc["up_ok"] += 1
            elif status in ("fail", "error", "failed", "lỗi"):
                total_up_fail += 1
                if acc:
                    acc["up_fail"] += 1

        # Cập nhật thời điểm hoạt động gần nhất
        if acc and row_time_str:
            dt = parse_log_datetime(row_time_str)
            if dt:
                if acc["last_active_dt"] is None or dt > acc["last_active_dt"]:
                    acc["last_active_dt"] = dt
                    acc["last_active"] = row_time_str

        # Lưu lại lỗi gần nhất
        if status in ("fail", "error", "failed"):
            if prof_name not in recent_errors_map:
                recent_errors_map[prof_name] = []
            if len(recent_errors_map[prof_name]) < 50:
                recent_errors_map[prof_name].append(dict(row))

    # 5. Hoàn thiện bảng accounts và tính tỷ lệ thành công % (Zero-Division Safe)
    accounts_list: List[Dict[str, Any]] = []
    for prof, acc in account_stats.items():
        up_ok = acc["up_ok"]
        up_fail = acc["up_fail"]
        total_up = up_ok + up_fail

        if total_up > 0:
            rate = round((up_ok / total_up) * 100.0, 1)
            rate_str = f"{rate:.1f}%"
        else:
            rate = 0.0
            rate_str = "-"

        # Gán nhãn trạng thái trực quan
        if acc["dl_fail"] > 0 or acc["up_fail"] > 0:
            status_tag = "error" if up_fail > up_ok else "warn"
        elif up_ok > 0 or acc["dl_ok"] > 0:
            status_tag = "good"
        else:
            status_tag = "idle"

        acc_record = {
            "name": prof,
            "project": acc["project"],
            "dl_ok": acc["dl_ok"],
            "dl_fail": acc["dl_fail"],
            "dl_skipped": acc["dl_skipped"],
            "up_ok": acc["up_ok"],
            "up_fail": acc["up_fail"],
            "today": acc["today"],
            "yesterday": acc["yesterday"],
            "rate": rate,
            "rate_str": rate_str,
            "last_active": acc["last_active"] or "Chưa có",
            "status_tag": status_tag,
        }
        accounts_list.append(acc_record)

    # Sắp xếp mặc định theo tên hồ sơ A-Z
    accounts_list.sort(key=lambda x: str(x["name"]).lower())

    # 6. Tính toán KPI tổng quan toàn hệ thống
    total_uploads = total_up_ok + total_up_fail
    if total_uploads > 0:
        overall_rate = round((total_up_ok / total_uploads) * 100.0, 1)
    else:
        overall_rate = 0.0

    total_today_uploads = sum(acc["today"] for acc in accounts_list)
    total_yesterday_uploads = sum(acc["yesterday"] for acc in accounts_list)

    summary = {
        "download_success": total_dl_ok,
        "download_fail": total_dl_fail,
        "download_skipped": total_dl_skipped,
        "upload_success": total_up_ok,
        "upload_fail": total_up_fail,
        "uploads_today": total_today_uploads,
        "uploads_yesterday": total_yesterday_uploads,
        "overall_success_rate": overall_rate,
        "overall_success_rate_str": f"{overall_rate:.1f}%" if total_uploads > 0 else "-",
        "total_accounts": len(accounts_list),
        "active_accounts": sum(
            1 for a in accounts_list
            if (a["up_ok"] > 0 or a["up_fail"] > 0 or a["dl_ok"] > 0 or a["dl_fail"] > 0 or a["dl_skipped"] > 0 or a["today"] > 0 or a["yesterday"] > 0)
        ),
    }

    return {
        "summary": summary,
        "accounts": accounts_list,
        "recent_errors_map": recent_errors_map,
    }


def export_statistics_to_csv(aggregated_data: Dict[str, Any], file_path: str) -> bool:
    """Xuất toàn bộ bảng thống kê theo từng tài khoản ra file CSV định dạng UTF-8-sig."""
    if not file_path or not aggregated_data:
        return False
    try:
        accounts = aggregated_data.get("accounts", [])
        fieldnames = [
            "Tên Hồ Sơ",
            "Dự Án",
            "Tải Thành Công",
            "Tải Lỗi",
            "Tải Bỏ Qua",
            "Đăng Thành Công",
            "Đăng Lỗi",
            "Đã Đăng Hôm Nay",
            "Đã Đăng Hôm Qua",
            "Tỷ Lệ Thành Công",
            "Hoạt Động Cuối",
        ]
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(fieldnames)
            for acc in accounts:
                writer.writerow([
                    acc.get("name", ""),
                    acc.get("project", ""),
                    acc.get("dl_ok", 0),
                    acc.get("dl_fail", 0),
                    acc.get("dl_skipped", 0),
                    acc.get("up_ok", 0),
                    acc.get("up_fail", 0),
                    acc.get("today", 0),
                    acc.get("yesterday", 0),
                    acc.get("rate_str", "-"),
                    acc.get("last_active", ""),
                ])
        return True
    except Exception:
        return False


# ==============================================================================
# 3. PRESENTATION LAYER (STATISTICS WORKSPACE VIEW)
# ==============================================================================

class StatisticsWorkspaceView(ctk.CTkFrame):
    """
    Giao diện Tab Thống Kê Toàn Diện:
    - 4 Thẻ KPI Tổng quan: Video Tải Về, Video Đăng TikTok, Hôm Nay / Hôm Qua, Tỷ Lệ Thành Công.
    - Thanh công cụ bộ lọc: Lọc theo Dự án, Mốc thời gian, Tìm kiếm tài khoản.
    - Bảng thống kê chi tiết theo tài khoản (Hỗ trợ click header để sort).
    - Bảng kiểm tra lỗi nhanh (Quick Error Inspector): Click chọn tài khoản hiển thị ngay chi tiết các video bị lỗi.
    """

    def __init__(self, parent: Any, state: Dict[str, Any], handlers: Dict[str, Any]) -> None:
        super().__init__(parent, fg_color="transparent")
        self.state = state or {}
        self.handlers = handlers or {}

        self._last_mtime: float = 0.0
        self._cached_aggregated: Dict[str, Any] = {}
        self._sort_column: str = "name"
        self._sort_reverse: bool = False
        self._selected_account_name: str = ""

        # Filter StringVars
        self.project_var = ctk.StringVar(value=ALL_OPTION)
        self.timeframe_var = ctk.StringVar(value="all")
        self.search_var = ctk.StringVar(value="")

        # KPI StringVars
        self.kpi_download_var = ctk.StringVar(value="0 OK (0 Lỗi)")
        self.kpi_upload_var = ctk.StringVar(value="0 OK (0 Lỗi)")
        self.kpi_today_yesterday_var = ctk.StringVar(value="0 / 0")
        self.kpi_rate_var = ctk.StringVar(value="0.0%")
        self.kpi_accounts_var = ctk.StringVar(value="0/0 hồ sơ hoạt động")

        self.error_inspector_title_var = ctk.StringVar(value="⚠️ Chi Tiết Lỗi Gần Nhất (Chọn tài khoản để xem)")

        self._build_ui()
        self.reload_data(force=True)

    def _build_ui(self) -> None:
        self.pack(fill="both", expand=True)

        # ----------------------------------------------------------------------
        # 1. TOP KPI SUMMARY CARDS
        # ----------------------------------------------------------------------
        kpi_container = ctk.CTkFrame(self, fg_color="transparent")
        kpi_container.pack(fill="x", padx=6, pady=(4, 6))

        for col_idx in range(4):
            kpi_container.grid_columnconfigure(col_idx, weight=1, uniform="kpi")

        # Card 1: Video Tải Về
        self._create_kpi_card(
            parent=kpi_container,
            col=0,
            icon="📥",
            title="Video Đã Tải Về",
            val_var=self.kpi_download_var,
            accent_color=UIThemeTokens.STATUS_RUNNING,
            sub_text="YouTube Monitor & Batch",
        )

        # Card 2: Video Đã Đăng TikTok
        self._create_kpi_card(
            parent=kpi_container,
            col=1,
            icon="🚀",
            title="Video Đã Đăng TikTok",
            val_var=self.kpi_upload_var,
            accent_color=UIThemeTokens.STATUS_LIVE,
            sub_text="Auto Upload",
        )

        # Card 3: Hôm Nay / Hôm Qua
        self._create_kpi_card(
            parent=kpi_container,
            col=2,
            icon="📅",
            title="Hôm Nay / Hôm Qua",
            val_var=self.kpi_today_yesterday_var,
            accent_color=UIThemeTokens.ACCENT_PRIMARY,
            sub_text="Số lượt đăng",
        )

        # Card 4: Tỷ Lệ Thành Công
        self._create_kpi_card(
            parent=kpi_container,
            col=3,
            icon="📈",
            title="Tỷ Lệ Thành Công",
            val_var=self.kpi_rate_var,
            accent_color=UIThemeTokens.STATUS_WARN,
            sub_var=self.kpi_accounts_var,
        )

        # ----------------------------------------------------------------------
        # 2. FILTER & ACTION TOOLBAR
        # ----------------------------------------------------------------------
        toolbar_card = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        toolbar_card.pack(fill="x", padx=6, pady=(0, 6))

        tb_inner = ctk.CTkFrame(toolbar_card, fg_color="transparent")
        tb_inner.pack(fill="x", padx=10, pady=8)

        # Dropdown Dự án
        ctk.CTkLabel(
            tb_inner,
            text="📁 Dự án:",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left", padx=(0, 4))

        self.project_dropdown = ctk.CTkComboBox(
            tb_inner,
            variable=self.project_var,
            values=[ALL_OPTION],
            width=140,
            height=30,
            font=UIThemeTokens.FONT_BODY,
            command=lambda _v: self.refresh_view(),
        )
        self.project_dropdown.pack(side="left", padx=(0, 10))

        # Filter Chips Mốc thời gian
        ctk.CTkLabel(
            tb_inner,
            text="⏱️ Thời gian:",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left", padx=(0, 4))

        self.chip_buttons: Dict[str, ctk.CTkButton] = {}
        for tf_key, tf_label in TIMEFRAME_OPTIONS:
            btn = ctk.CTkButton(
                tb_inner,
                text=tf_label,
                font=UIThemeTokens.FONT_BADGE,
                height=26,
                width=62,
                corner_radius=6,
                fg_color=UIThemeTokens.ACCENT_PRIMARY if tf_key == "all" else UIThemeTokens.BG_ROOT,
                hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
                text_color=UIThemeTokens.TEXT_PRIMARY,
                border_width=0 if tf_key == "all" else 1,
                border_color=UIThemeTokens.BORDER_LIGHT,
                command=lambda k=tf_key: self._on_timeframe_selected(k),
            )
            btn.pack(side="left", padx=2)
            self.chip_buttons[tf_key] = btn

        # Ô tìm kiếm
        self.search_entry = ctk.CTkEntry(
            tb_inner,
            textvariable=self.search_var,
            placeholder_text="🔍 Tìm hồ sơ, dự án...",
            height=30,
            font=UIThemeTokens.FONT_BODY,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(10, 8))
        self.search_var.trace_add("write", lambda *_a: self.refresh_view())

        # Nút Làm mới
        ctk.CTkButton(
            tb_inner,
            text="🔄 Làm mới",
            font=UIThemeTokens.FONT_BUTTON,
            width=80,
            height=30,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=lambda: self.reload_data(force=True),
        ).pack(side="left", padx=(0, 4))

        # Nút Xuất CSV
        ctk.CTkButton(
            tb_inner,
            text="📤 Xuất CSV",
            font=UIThemeTokens.FONT_BUTTON,
            width=80,
            height=30,
            fg_color=UIThemeTokens.BG_CARD,
            hover_color=UIThemeTokens.BG_HOVER,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self._on_export_csv,
        ).pack(side="left", padx=(0, 4))

        # Nút Đặt lại thống kê
        ctk.CTkButton(
            tb_inner,
            text="🗑️ Đặt lại",
            font=UIThemeTokens.FONT_BUTTON,
            width=70,
            height=30,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            command=self._on_reset_stats,
        ).pack(side="left")

        # ----------------------------------------------------------------------
        # 3. MAIN SPLIT VIEW (BẢNG HỒ SƠ & BẢNG LỖI NHANH)
        # ----------------------------------------------------------------------
        content_paned = ctk.CTkFrame(self, fg_color="transparent")
        content_paned.pack(fill="both", expand=True, padx=6, pady=(0, 4))
        content_paned.grid_rowconfigure(0, weight=3)
        content_paned.grid_rowconfigure(1, weight=2)
        content_paned.grid_columnconfigure(0, weight=1)

        # 3.1. Main Account Breakdown Table Card
        accounts_card = ctk.CTkFrame(
            content_paned,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        accounts_card.grid(row=0, column=0, sticky="nsew", pady=(0, 4))
        accounts_card.grid_rowconfigure(1, weight=1)
        accounts_card.grid_columnconfigure(0, weight=1)

        # Header Bảng Hồ Sơ
        acc_header = ctk.CTkFrame(accounts_card, fg_color="transparent")
        acc_header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 4))
        ctk.CTkLabel(
            acc_header,
            text="📋 BẢNG THỐNG KÊ CHI TIẾT THEO HỒ SƠ",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            acc_header,
            text="💡 Click vào tiêu đề cột để sắp xếp • Click chọn dòng để xem chi tiết lỗi bên dưới",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="e",
        ).pack(side="right")

        # Treeview Accounts
        self.tree_accounts = ttk.Treeview(
            accounts_card,
            style="Modern.Treeview",
            columns=[col[0] for col in ACCOUNT_COLUMNS],
            show="headings",
            selectmode="browse",
        )
        for col_id, col_text, col_width, col_align in ACCOUNT_COLUMNS:
            self.tree_accounts.heading(
                col_id,
                text=col_text,
                command=lambda c=col_id: self._sort_accounts_by_column(c),
            )
            self.tree_accounts.column(
                col_id,
                width=col_width,
                minwidth=60,
                anchor=col_align,
                stretch=col_id in ("name", "project"),
            )

        self.tree_accounts.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(0, 6))

        vsb_acc = ttk.Scrollbar(accounts_card, orient="vertical", command=self.tree_accounts.yview)
        vsb_acc.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(0, 6))
        self.tree_accounts.configure(yscrollcommand=vsb_acc.set)

        self.tree_accounts.bind("<<TreeviewSelect>>", self._on_account_selected)

        # Tags màu sắc
        self.tree_accounts.tag_configure("good", foreground=UIThemeTokens.STATUS_LIVE)
        self.tree_accounts.tag_configure("warn", foreground=UIThemeTokens.STATUS_WARN)
        self.tree_accounts.tag_configure("error", foreground=UIThemeTokens.STATUS_ERROR)
        self.tree_accounts.tag_configure("idle", foreground=UIThemeTokens.TEXT_MUTED)

        # 3.2. Quick Error Inspector Panel Card
        error_card = ctk.CTkFrame(
            content_paned,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        error_card.grid(row=1, column=0, sticky="nsew")
        error_card.grid_rowconfigure(1, weight=1)
        error_card.grid_columnconfigure(0, weight=1)

        err_header = ctk.CTkFrame(error_card, fg_color="transparent")
        err_header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(6, 4))
        ctk.CTkLabel(
            err_header,
            textvariable=self.error_inspector_title_var,
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.STATUS_ERROR,
            anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            err_header,
            text="🔗 Double-click để mở link video hoặc kiểm tra file",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="e",
        ).pack(side="right")

        # Treeview Errors
        self.tree_errors = ttk.Treeview(
            error_card,
            style="Modern.Treeview",
            columns=[col[0] for col in ERROR_COLUMNS],
            show="headings",
            selectmode="browse",
        )
        for col_id, col_text, col_width, col_align in ERROR_COLUMNS:
            self.tree_errors.heading(col_id, text=col_text)
            self.tree_errors.column(
                col_id,
                width=col_width,
                minwidth=60,
                anchor=col_align,
                stretch=col_id in ("video_name", "detail", "file_path"),
            )

        self.tree_errors.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(0, 6))

        vsb_err = ttk.Scrollbar(error_card, orient="vertical", command=self.tree_errors.yview)
        vsb_err.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(0, 6))
        self.tree_errors.configure(yscrollcommand=vsb_err.set)

        self.tree_errors.bind("<Double-1>", self._on_error_row_double_click)
        self.tree_errors.tag_configure("fail", foreground=UIThemeTokens.STATUS_ERROR)

    def _create_kpi_card(
        self,
        parent: Any,
        col: int,
        icon: str,
        title: str,
        val_var: ctk.StringVar,
        accent_color: str,
        sub_text: str = "",
        sub_var: Optional[ctk.StringVar] = None,
    ) -> ctk.CTkFrame:
        card = ctk.CTkFrame(
            parent,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        card.grid(row=0, column=col, sticky="nsew", padx=3)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        # Header dòng 1: Icon + Title
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top,
            text=f"{icon} {title}",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        ).pack(side="left")

        ctk.CTkLabel(
            top,
            text="●",
            font=("Segoe UI", 12),
            text_color=accent_color,
            width=12,
        ).pack(side="right")

        # Dòng 2: Giá trị số chính
        ctk.CTkLabel(
            inner,
            textvariable=val_var,
            font=UIThemeTokens.FONT_STAT_NUM,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        # Dòng 3: Phụ đề nhỏ
        if sub_var:
            ctk.CTkLabel(
                inner,
                textvariable=sub_var,
                font=UIThemeTokens.FONT_BADGE,
                text_color=UIThemeTokens.TEXT_MUTED,
                anchor="w",
            ).pack(anchor="w")
        elif sub_text:
            ctk.CTkLabel(
                inner,
                text=sub_text,
                font=UIThemeTokens.FONT_BADGE,
                text_color=UIThemeTokens.TEXT_MUTED,
                anchor="w",
            ).pack(anchor="w")

        return card

    # --------------------------------------------------------------------------
    # DATA LOADING & REACTIVITY
    # --------------------------------------------------------------------------

    def reload_data(self, force: bool = False) -> None:
        """Tải lại dữ liệu thô từ backend và tính toán lại thống kê."""
        try:
            mtime_func = self.handlers.get("get_mtime", lambda: 0.0)
            cur_mtime = float(mtime_func() or 0.0)
        except Exception:
            cur_mtime = 0.0

        if not force and cur_mtime == self._last_mtime and self._cached_aggregated:
            return

        self._last_mtime = cur_mtime

        # 1. Lấy logs từ backend
        try:
            logs_func = self.handlers.get("get_activity_logs", lambda **_k: [])
            activity_rows = logs_func(limit=100000) or []
        except Exception:
            activity_rows = []

        # 2. Lấy snapshot profiles & projects
        try:
            profiles_func = self.handlers.get("get_profiles_data", lambda: ({}, {}))
            profiles_data, project_mapping = profiles_func()
        except Exception:
            profiles_data, project_mapping = {}, {}

        # 3. Cập nhật danh sách dự án trong dropdown
        available_projects = [ALL_OPTION]
        if isinstance(project_mapping, dict):
            available_projects.extend(sorted(k for k in project_mapping.keys() if k))
        self.project_dropdown.configure(values=available_projects)

        # 4. Tính toán thống kê
        self._raw_activity_rows = activity_rows
        self._profiles_data = profiles_data
        self._project_mapping = project_mapping

        self.refresh_view()

    def refresh_view(self) -> None:
        """Cập nhật lại toàn bộ giao diện từ bộ nhớ đệm theo bộ lọc hiện tại."""
        raw_rows = getattr(self, "_raw_activity_rows", [])
        profiles_data = getattr(self, "_profiles_data", {})
        project_mapping = getattr(self, "_project_mapping", {})

        proj_filter = self.project_var.get()
        tf_filter = self.timeframe_var.get()
        search_q = self.search_var.get()

        aggregated = aggregate_statistics(
            activity_rows=raw_rows,
            profiles_data=profiles_data,
            project_mapping=project_mapping,
            project_filter=proj_filter,
            timeframe=tf_filter,
            search_query=search_q,
        )
        self._cached_aggregated = aggregated

        # Cập nhật KPI Cards
        summary = aggregated.get("summary", {})
        dl_ok = summary.get("download_success", 0)
        dl_fail = summary.get("download_fail", 0)
        self.kpi_download_var.set(f"{dl_ok} OK ({dl_fail} Lỗi)")

        up_ok = summary.get("upload_success", 0)
        up_fail = summary.get("upload_fail", 0)
        self.kpi_upload_var.set(f"{up_ok} OK ({up_fail} Lỗi)")

        today = summary.get("uploads_today", 0)
        yesterday = summary.get("uploads_yesterday", 0)
        self.kpi_today_yesterday_var.set(f"{today} / {yesterday}")

        self.kpi_rate_var.set(summary.get("overall_success_rate_str", "-"))
        tot_acc = summary.get("total_accounts", 0)
        act_acc = summary.get("active_accounts", 0)
        self.kpi_accounts_var.set(f"{act_acc}/{tot_acc} hồ sơ hoạt động")

        # Cập nhật Bảng Hồ Sơ
        self._render_accounts_table()

        # Cập nhật Bảng Lỗi Nhanh
        self._render_errors_table()

    def _render_accounts_table(self) -> None:
        accounts = list(self._cached_aggregated.get("accounts", []))

        # Áp dụng sắp xếp cột nếu có
        if self._sort_column:
            def sort_key(acc: Dict[str, Any]) -> Any:
                val = acc.get(self._sort_column, "")
                if isinstance(val, (int, float)):
                    return val
                return str(val).lower()

            accounts.sort(key=sort_key, reverse=self._sort_reverse)

        self.tree_accounts.delete(*self.tree_accounts.get_children())

        selected_iid: Optional[str] = None
        for idx, acc in enumerate(accounts):
            name = acc.get("name", "")
            iid = f"acc_{idx}_{name}"
            values = (
                name,
                acc.get("project", "Mặc định"),
                acc.get("dl_ok", 0),
                acc.get("dl_fail", 0),
                acc.get("up_ok", 0),
                acc.get("up_fail", 0),
                acc.get("today", 0),
                acc.get("yesterday", 0),
                acc.get("rate_str", "-"),
                acc.get("last_active", "Chưa có"),
            )
            tag = acc.get("status_tag", "idle")
            self.tree_accounts.insert("", "end", iid=iid, values=values, tags=(tag,))

            if name == self._selected_account_name:
                selected_iid = iid

        if selected_iid:
            self.tree_accounts.selection_set(selected_iid)
            self.tree_accounts.see(selected_iid)

    def _render_errors_table(self) -> None:
        self.tree_errors.delete(*self.tree_errors.get_children())

        if not self._selected_account_name:
            self.error_inspector_title_var.set("⚠️ Chi Tiết Lỗi Gần Nhất (Chọn một tài khoản ở bảng trên để xem)")
            return

        self.error_inspector_title_var.set(f"⚠️ Chi Tiết Lỗi Gần Nhất Của Tài Khoản: [{self._selected_account_name}]")

        raw_rows = getattr(self, "_raw_activity_rows", [])
        errors = get_account_recent_errors(raw_rows, self._selected_account_name, limit=50)

        if not errors:
            self.tree_errors.insert("", "end", values=("", "", "✅ Không ghi nhận lỗi nào cho tài khoản này.", "", "", ""))
            return

        for idx, err in enumerate(errors):
            values = (
                err.get("time", ""),
                err.get("type", ""),
                err.get("video_name", ""),
                err.get("detail", ""),
                err.get("video_url", ""),
                err.get("file_path", ""),
            )
            self.tree_errors.insert("", "end", iid=str(idx), values=values, tags=("fail",))

    # --------------------------------------------------------------------------
    # EVENT HANDLERS
    # --------------------------------------------------------------------------

    def _on_timeframe_selected(self, tf_key: str) -> None:
        self.timeframe_var.set(tf_key)
        for k, btn in self.chip_buttons.items():
            is_active = (k == tf_key)
            btn.configure(
                fg_color=UIThemeTokens.ACCENT_PRIMARY if is_active else UIThemeTokens.BG_ROOT,
                border_width=0 if is_active else 1,
            )
        self.refresh_view()

    def _on_account_selected(self, _event: Any = None) -> None:
        selected = self.tree_accounts.selection()
        if not selected:
            return
        item_values = self.tree_accounts.item(selected[0], "values")
        if item_values:
            self._selected_account_name = str(item_values[0])
            self._render_errors_table()

    def _sort_accounts_by_column(self, col_id: str) -> None:
        if self._sort_column == col_id:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col_id
            self._sort_reverse = False
        self._render_accounts_table()

    def _on_error_row_double_click(self, _event: Any = None) -> None:
        selected = self.tree_errors.selection()
        if not selected:
            return
        values = self.tree_errors.item(selected[0], "values")
        if not values or len(values) < 6:
            return

        video_url = str(values[4] or "").strip()
        file_path = str(values[5] or "").strip()

        if video_url.startswith(("http://", "https://")):
            try:
                webbrowser.open(video_url)
                return
            except Exception:
                pass

        if file_path and os.path.exists(file_path):
            try:
                if os.name == "nt":
                    os.system(f'explorer /select,"{os.path.abspath(file_path)}"')
                return
            except Exception:
                pass

    def _on_export_csv(self) -> None:
        if not self._cached_aggregated:
            messagebox.showinfo("Xuất CSV", "Không có dữ liệu thống kê để xuất.")
            return

        from tkinter import filedialog
        default_name = f"ThongKe_DONGLAO_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        out_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV Files (*.csv)", "*.csv"), ("All Files (*.*)", "*.*")],
            title="Lưu Báo Cáo Thống Kê CSV",
        )
        if not out_path:
            return

        ok = export_statistics_to_csv(self._cached_aggregated, out_path)
        if ok:
            messagebox.showinfo("Xuất CSV", f"Đã xuất báo cáo thống kê thành công:\n{out_path}")
        else:
            messagebox.showerror("Xuất CSV", "Lỗi khi ghi file báo cáo CSV.")

    def _on_reset_stats(self) -> None:
        if not messagebox.askyesno(
            "Đặt Lại Thống Kê",
            "Bạn có chắc chắn muốn xóa toàn bộ lịch sử hoạt động và đặt lại thống kê về 0?\n"
            "Hành động này sẽ làm sạch file activity_log.csv.",
        ):
            return

        clear_func = self.handlers.get("clear_stats", self.handlers.get("clear"))
        if callable(clear_func):
            try:
                ok, msg = clear_func()
                if ok:
                    messagebox.showinfo("Đặt Lại Thống Kê", "Đã đặt lại dữ liệu thống kê thành công.")
                    self.reload_data(force=True)
                else:
                    messagebox.showerror("Lỗi", str(msg))
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể đặt lại thống kê: {e}")
        else:
            messagebox.showinfo("Thông báo", "Handler clear_stats chưa sẵn sàng.")


def build_statistics_workspace(
    parent: Any,
    state: Dict[str, Any],
    handlers: Dict[str, Any],
) -> StatisticsWorkspaceView:
    """Khởi tạo và trả về StatisticsWorkspaceView hoàn chỉnh cho app_ui."""
    view = StatisticsWorkspaceView(parent, state, handlers)
    return view
