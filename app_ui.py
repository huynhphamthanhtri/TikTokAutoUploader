"""
app_ui.py - Shell và Bố cục Giao diện Người Dùng cho VIBE_AUTO_UPLOAD-LP.

Kiến trúc Sidebar Layout hiện đại:
- Sidebar (220px) bên trái: Logo, Profiles, YouTube Monitor, Batch, Lịch sử, Thu nhập, Projects list.
- Header Bar phía trên: Tên dự án đang chọn, Tìm kiếm nhanh, Zoom control, Compatibility frame.
- Workspace Container: Frame stacking chuyển đổi tức thì không rebuild YouTube views.
- Profiles Workspace: 4 Summary Cards, Toolbar hành động, Bảng Treeview hiện đại.
- Collapsible Log Drawer: Chứa 3 tabs log (Quan trọng, Lỗi, Chi tiết), thu gọn 34px.
- Bảo tồn 100% handler và widget dictionary contract cho main.py.
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk, Menu
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable, Dict

from ui_components import (
    CollapsibleLogDrawer,
    ProjectList,
    SidebarButton,
    SummaryCard,
    UIThemeTokens,
)
from ui_guide import build_guide_workspace
from youtube_monitor.activity_view import ActivityLogView
from youtube_monitor.batch_view import BatchDownloadView
from youtube_monitor.ui import YouTubeMonitorView


def configure_ttk_styles() -> None:
    style = ttk.Style()
    try:
        style.theme_use('clam')
    except Exception:
        pass

    style.configure(
        'Modern.Treeview',
        background='#ffffff',
        fieldbackground='#ffffff',
        foreground='#0f172a',
        rowheight=34,
        borderwidth=0,
        relief='flat',
        font=('Segoe UI', 10),
    )
    style.map(
        'Modern.Treeview',
        background=[('selected', '#2563eb')],
        foreground=[('selected', '#ffffff')],
    )
    style.configure(
        'Modern.Treeview.Heading',
        background='#f1f5f9',
        foreground='#0f172a',
        relief='flat',
        borderwidth=0,
        font=('Segoe UI Semibold', 10),
    )
    style.map('Modern.Treeview.Heading', background=[('active', '#e2e8f0')])
    style.configure(
        'Vertical.TScrollbar',
        gripcount=0,
        background='#cbd5e1',
        darkcolor='#cbd5e1',
        lightcolor='#cbd5e1',
        troughcolor='#f8fafc',
        bordercolor='#f8fafc',
        arrowcolor='#334155',
    )


def build_card(parent: Any, title: str, subtitle: str | None = None) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, corner_radius=10, fg_color='#ffffff', border_width=1, border_color='#e2e8f0')
    header = ctk.CTkFrame(card, fg_color='transparent')
    header.pack(fill='x', padx=12, pady=(8, 6))
    ctk.CTkLabel(header, text=title, font=('Segoe UI Semibold', 14), text_color='#0f172a').pack(anchor='w')
    if subtitle:
        ctk.CTkLabel(header, text=subtitle, font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w', pady=(2, 0))
    return card


def build_stat_card(parent: Any, title: str, value_var: Any, accent: str) -> ctk.CTkFrame:
    card = ctk.CTkFrame(parent, corner_radius=10, fg_color='#ffffff', border_width=1, border_color='#e2e8f0')
    ctk.CTkLabel(card, text=title, font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w', padx=12, pady=(10, 2))
    ctk.CTkLabel(card, textvariable=value_var, font=('Segoe UI Semibold', 22), text_color=accent).pack(anchor='w', padx=12, pady=(0, 10))
    return card


def classify_log_message(message: str) -> tuple[str, str | None]:
    msg_lower = str(message).lower()
    base_tag = 'INFO'
    important_tag = None

    if any(x in msg_lower for x in ('error', 'failed', 'lỗi', 'thất bại', 'exception', 'resource low', 'mismatch')):
        base_tag = 'ERROR'
    elif any(x in msg_lower for x in ('warning', 'cảnh báo', 'timeout', 'đợi')):
        base_tag = 'WARN'

    if any(x in msg_lower for x in (
        'đang khởi động', 'đang dừng', 'đang đăng', 'đã gửi lệnh đăng', 'đã đăng',
        'đang theo dõi', 'phát hiện video mới', 'proxy ok', 'đã đăng nhập',
        'đã nạp', 'đã mở', 'sẵn sàng'
    )):
        important_tag = 'INFO'

    if any(x in msg_lower for x in (
        'không đăng', 'mất kết nối', 'ngắt phiên', 'driver lỗi', 'proxy sai',
        'lỗi nghiêm trọng', 'exception init', 'lỗi khởi tạo', 'mismatch'
    )):
        important_tag = 'CRITICAL'
    elif base_tag in ('ERROR', 'WARN') and important_tag is None:
        important_tag = base_tag

    return base_tag, important_tag


def _open_overflow_menu(btn: Any, actions: list[tuple[str, Callable[[], None]]]) -> None:
    menu = None
    try:
        menu = Menu(btn.master, tearoff=0)
        for label, command in actions:
            menu.add_command(label=label, command=command)
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        menu.tk_popup(x, y)
    finally:
        if menu is not None:
            try:
                menu.grab_release()
            except Exception:
                pass


def build_dashboard(root: Any, state: Dict[str, Any], handlers: Dict[str, Any]) -> Dict[str, Any]:
    """Khởi tạo toàn bộ giao diện Sidebar Layout và trả về dictionary widgets theo contract."""
    widgets: Dict[str, Any] = {}

    # Root Container: horizontal layout (Sidebar | Main Area)
    app_shell = ctk.CTkFrame(root, fg_color="transparent", corner_radius=0)
    app_shell.pack(fill="both", expand=True)
    widgets["main_container"] = app_shell

    # ==========================================================================
    # 1. SIDEBAR (Width: 220px, Dark Slate Theme)
    # ==========================================================================
    sidebar = ctk.CTkFrame(
        app_shell,
        width=220,
        corner_radius=0,
        fg_color=UIThemeTokens.BG_SIDEBAR,
    )
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)
    widgets["sidebar"] = sidebar

    # Logo & Brand Header
    logo_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
    logo_frame.pack(fill="x", padx=14, pady=(16, 12))
    ctk.CTkLabel(
        logo_frame,
        text="DONGLAO-TIKTOK",
        font=("Segoe UI Semibold", 13),
        text_color="#38bdf8",
        anchor="w",
    ).pack(anchor="w")
    ctk.CTkLabel(
        logo_frame,
        text="Automation & Studio Suite",
        font=("Segoe UI", 10),
        text_color="#64748b",
        anchor="w",
    ).pack(anchor="w", pady=(1, 0))

    # Navigation Menu Section
    nav_container = ctk.CTkFrame(sidebar, fg_color="transparent")
    nav_container.pack(fill="x", padx=10, pady=4)

    sidebar_buttons: Dict[str, SidebarButton] = {}

    btn_nav_profiles = SidebarButton(
        nav_container,
        text="Hồ Sơ (Profiles)",
        icon_text="👤",
    )
    btn_nav_profiles.pack(fill="x", pady=2)
    sidebar_buttons["profiles"] = btn_nav_profiles

    btn_nav_youtube = SidebarButton(
        nav_container,
        text="YouTube Studio",
        icon_text="🎬",
    )
    btn_nav_youtube.pack(fill="x", pady=2)
    sidebar_buttons["youtube"] = btn_nav_youtube
    sidebar_buttons["batch"] = btn_nav_youtube
    sidebar_buttons["history"] = btn_nav_youtube

    btn_nav_monetization = SidebarButton(
        nav_container,
        text="Thu Nhập / KYC",
        icon_text="💰",
    )
    btn_nav_monetization.pack(fill="x", pady=2)
    sidebar_buttons["monetization"] = btn_nav_monetization

    btn_nav_guide = SidebarButton(
        nav_container,
        text="Hướng Dẫn",
        icon_text="📖",
    )
    btn_nav_guide.pack(fill="x", pady=2)
    sidebar_buttons["guide"] = btn_nav_guide

    # Projects List Section on Sidebar
    proj_header_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
    proj_header_frame.pack(fill="x", padx=12, pady=(16, 4))
    ctk.CTkLabel(
        proj_header_frame,
        text="📁 DỰ ÁN",
        font=("Segoe UI Semibold", 10),
        text_color="#94a3b8",
        anchor="w",
    ).pack(side="left")

    ctk.CTkButton(
        proj_header_frame,
        text="+",
        width=22,
        height=22,
        font=("Segoe UI Semibold", 11),
        fg_color="#334155",
        hover_color="#475569",
        command=handlers["create_project"],
    ).pack(side="right", padx=1)
    ctk.CTkButton(
        proj_header_frame,
        text="-",
        width=22,
        height=22,
        font=("Segoe UI Semibold", 11),
        fg_color="#ef4444",
        hover_color="#dc2626",
        command=handlers["delete_project"],
    ).pack(side="right", padx=1)

    def _on_sidebar_project_selected(proj_name: str) -> None:
        state["selected_project_var"].set(proj_name)

    project_list_view = ProjectList(
        sidebar,
        on_select_project=_on_sidebar_project_selected,
        height=140,
    )
    project_list_view.pack(fill="x", padx=6, pady=2)
    widgets["project_list_view"] = project_list_view

    # Bottom Settings in Sidebar
    settings_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
    settings_frame.pack(side="bottom", fill="x", padx=10, pady=12)

    overflow_actions = [
        ("Cập nhật phần mềm", handlers["check_update"]),
        ("License Bản Quyền", handlers["change_license_key"]),
        ("Reset Browser Cache", handlers["clean_browser"]),
        ("Thống Kê Tổng Quan", handlers["show_statistics_board"]),
    ]
    btn_settings = SidebarButton(
        settings_frame,
        text="Cài Đặt & Khác ⋯",
        icon_text="⚙️",
        command=lambda: _open_overflow_menu(btn_settings, overflow_actions),
    )
    btn_settings.pack(fill="x")

    # ==========================================================================
    # 2. MAIN CONTENT AREA (Header + Workspaces Stack + Log Drawer)
    # ==========================================================================
    main_area = ctk.CTkFrame(app_shell, fg_color=UIThemeTokens.BG_ROOT, corner_radius=0)
    main_area.pack(side="left", fill="both", expand=True)

    # Top Header Bar
    header_bar = ctk.CTkFrame(main_area, height=48, corner_radius=0, fg_color=UIThemeTokens.BG_HEADER, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    header_bar.pack(fill="x", side="top")

    # Current Project Label
    header_proj_lbl = ctk.CTkLabel(
        header_bar,
        textvariable=state.get("header_project_label", state["selected_project_var"]),
        font=("Segoe UI Semibold", 13),
        text_color=UIThemeTokens.TEXT_PRIMARY,
    )
    header_proj_lbl.pack(side="left", padx=16)

    # Search Bar in Header
    search_frame = ctk.CTkFrame(header_bar, corner_radius=8, fg_color="#f8fafc", border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    search_frame.pack(side="left", fill="x", expand=True, padx=16, pady=7)
    ctk.CTkEntry(
        search_frame,
        textvariable=state["filter_var"],
        height=28,
        font=UIThemeTokens.FONT_BODY,
        placeholder_text="🔍 Tìm kiếm hồ sơ, TikTok ID, proxy, trạng thái, khu vực...",
        border_width=0,
        fg_color="transparent",
    ).pack(fill="x", padx=6)

    # Zoom / Theme / Hidden Compatibility Dropdown Frame
    compat_frame = ctk.CTkFrame(header_bar, fg_color="transparent")
    compat_frame.pack(side="right", padx=12)

    ctk.CTkLabel(compat_frame, text="Zoom:", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left", padx=(0, 4))
    zoom_box = ctk.CTkComboBox(compat_frame, values=["90%", "100%", "110%"], variable=state["scale_var"], width=82, height=28, font=UIThemeTokens.FONT_BODY)
    zoom_box.pack(side="left")

    # Compatibility project dropdown (hidden frame for main.py contract preservation)
    hidden_compat_frame = ctk.CTkFrame(header_bar, fg_color="transparent")
    project_dropdown = ctk.CTkComboBox(hidden_compat_frame, variable=state["selected_project_var"], width=10)
    widgets["project_dropdown"] = project_dropdown

    # Workspace View Container (Frame Stacking)
    workspace_container = ctk.CTkFrame(main_area, fg_color="transparent", corner_radius=0)
    workspace_container.pack(fill="both", expand=True, padx=10, pady=(8, 0))
    workspace_container.grid_rowconfigure(0, weight=1)
    workspace_container.grid_columnconfigure(0, weight=1)
    widgets["workspace_container"] = workspace_container

    # --------------------------------------------------------------------------
    # WORKSPACE 1: PROFILES WORKSPACE
    # --------------------------------------------------------------------------
    profiles_workspace = ctk.CTkFrame(workspace_container, fg_color="transparent")
    profiles_workspace.grid(row=0, column=0, sticky="nsew")

    # Summary Cards Row (4 Cards)
    summary_row = ctk.CTkFrame(profiles_workspace, fg_color="transparent")
    summary_row.pack(fill="x", pady=(0, 8))
    summary_row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")

    # Summary state variables
    summary_total_var = state.get("header_total_label", ctk.StringVar(value="0"))
    summary_running_var = state.get("header_running_label", ctk.StringVar(value="0"))
    summary_cookie_var = state.get("summary_cookie_var", ctk.StringVar(value="0"))
    summary_error_var = state.get("summary_error_var", ctk.StringVar(value="0"))

    card_total = SummaryCard(summary_row, title="Tổng Hồ Sơ", value_var=summary_total_var, accent_color=UIThemeTokens.ACCENT_PRIMARY)
    card_total.grid(row=0, column=0, padx=(0, 4), sticky="nsew")

    card_running = SummaryCard(summary_row, title="Đang Chạy", value_var=summary_running_var, accent_color=UIThemeTokens.STATUS_RUNNING)
    card_running.grid(row=0, column=1, padx=4, sticky="nsew")

    card_cookie = SummaryCard(summary_row, title="Cookie Live", value_var=summary_cookie_var, accent_color=UIThemeTokens.STATUS_LIVE)
    card_cookie.grid(row=0, column=2, padx=4, sticky="nsew")

    card_error = SummaryCard(summary_row, title="Lỗi / Die", value_var=summary_error_var, accent_color=UIThemeTokens.STATUS_ERROR)
    card_error.grid(row=0, column=3, padx=(4, 0), sticky="nsew")

    widgets["summary_cards"] = {
        "total": card_total,
        "running": card_running,
        "cookie": card_cookie,
        "error": card_error,
    }

    # Action Toolbar (Top Toolbar for Profiles)
    topbar = ctk.CTkFrame(profiles_workspace, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    topbar.pack(fill="x", pady=(0, 8))
    widgets["topbar"] = topbar

    topbar_inner = ctk.CTkFrame(topbar, fg_color="transparent")
    topbar_inner.pack(fill="x", padx=8, pady=8)

    manage_left = ctk.CTkFrame(topbar_inner, fg_color="transparent")
    manage_left.pack(side="left", fill="x", expand=True)
    widgets["manage_frame"] = manage_left

    neutral = ("#64748b", "#475569")
    danger = ("#ef4444", "#dc2626")
    success = ("#16a34a", "#15803d")

    ctk.CTkButton(
        manage_left,
        text="Thêm",
        width=72,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color=neutral[0],
        hover_color=neutral[1],
        command=handlers['add_profile'],
    ).pack(side="left", padx=3)

    ctk.CTkButton(
        manage_left,
        text="Login/Mở trình duyệt",
        width=165,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color=neutral[0],
        hover_color=neutral[1],
        command=handlers['open_browser'],
    ).pack(side="left", padx=3)

    btn_check_cookie = ctk.CTkButton(
        manage_left,
        text="Check Cookie",
        width=104,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color=("#7c3aed", "#6d28d9"),
        command=handlers['check_cookie_live'],
    )
    btn_check_cookie.pack(side="left", padx=3)
    widgets["btn_check_cookie"] = btn_check_cookie

    overflow_actions = [
        ("Sửa", handlers['edit_profile']),
        ("Chi tiết", handlers['view_profile_details']),
        ("Đổi tên", handlers['rename_profile']),
        ("Xóa", handlers['delete_profile']),
        ("Gán DA", handlers['assign_to_project']),
        ("Import", handlers['batch_add_profiles']),
        ("Export", handlers['export_profiles']),
        ("Thống kê", handlers['show_statistics_board']),
        ("Reset Browser", handlers['clean_browser']),
        ("License", handlers['change_license_key']),
        ("Cập nhật", handlers['check_update']),
    ]
    more_btn = ctk.CTkButton(
        manage_left,
        text="⋯",
        width=40,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color="#475569",
        hover_color="#334155",
        command=lambda: _open_overflow_menu(more_btn, overflow_actions),
    )
    more_btn.pack(side="left", padx=3)

    control_frame = ctk.CTkFrame(topbar_inner, fg_color="transparent")
    control_frame.pack(side="right")
    widgets["control_frame"] = control_frame

    btn_start_selected = ctk.CTkButton(
        control_frame,
        text="Start chọn",
        width=94,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color=success[0],
        hover_color=success[1],
        command=handlers['start_selected_batch'],
    )
    btn_start_selected.pack(side="left", padx=3)
    widgets["btn_start_selected"] = btn_start_selected

    btn_stop_selected = ctk.CTkButton(
        control_frame,
        text="Stop chọn",
        width=94,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color=danger[0],
        hover_color=danger[1],
        command=handlers['stop_selected_batch'],
    )
    btn_stop_selected.pack(side="left", padx=3)
    widgets["btn_stop_selected"] = btn_stop_selected

    btn_start_all = ctk.CTkButton(
        control_frame,
        text="Start tất cả",
        width=102,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color=success[0],
        hover_color=success[1],
        command=handlers['start_all_in_project'],
    )
    btn_start_all.pack(side="left", padx=3)
    widgets["btn_start_all"] = btn_start_all

    btn_stop_all = ctk.CTkButton(
        control_frame,
        text="Stop tất cả",
        width=102,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color=danger[0],
        hover_color=danger[1],
        command=handlers['stop_all_in_project'],
    )
    btn_stop_all.pack(side="left", padx=3)
    widgets["btn_stop_all"] = btn_stop_all

    # Filter Chips Row for Profiles
    filter_chips_frame = ctk.CTkFrame(profiles_workspace, fg_color="transparent")
    filter_chips_frame.pack(fill="x", pady=(0, 6))

    active_chip_var = state.get("active_filter_chip", ctk.StringVar(value="ALL"))
    state["active_filter_chip"] = active_chip_var

    chip_buttons: Dict[str, ctk.CTkButton] = {}
    chips = [
        ("ALL", "Tất Cả"),
        ("COOKIE_LIVE", "🟢 Cookie Sống"),
        ("COOKIE_DIE", "🔴 Cookie Die"),
        ("NO_COOKIE", "⚪ Chưa Có Cookie"),
        ("KYC_OK", "🟢 Đã KYC"),
        ("TAX_OK", "🟢 Đã Khai Thuế"),
        ("TKTBM", "🔴 TKTBM (Bảo Mật)"),
        ("RUNNING", "⚡ Đang Chạy"),
    ]

    def _select_filter_chip(chip_key: str) -> None:
        active_chip_var.set(chip_key)
        for key, btn in chip_buttons.items():
            if key == chip_key:
                btn.configure(fg_color="#2563eb", text_color="#ffffff")
            else:
                btn.configure(fg_color="#e2e8f0", text_color="#334155")
        if "apply_filter_chip" in handlers:
            handlers["apply_filter_chip"](chip_key)

    for key, label in chips:
        btn = ctk.CTkButton(
            filter_chips_frame,
            text=label,
            height=26,
            font=("Segoe UI Semibold", 9),
            corner_radius=13,
            fg_color="#2563eb" if key == "ALL" else "#e2e8f0",
            text_color="#ffffff" if key == "ALL" else "#334155",
            hover_color="#3b82f6" if key == "ALL" else "#cbd5e1",
            command=lambda k=key: _select_filter_chip(k),
        )
        btn.pack(side="left", padx=2)
        chip_buttons[key] = btn
    widgets["filter_chip_buttons"] = chip_buttons

    # Profile Treeview Table Card (layout weight=78, weight=22)
    table_card = build_card(profiles_workspace, "Danh sách hồ sơ")
    table_card.pack(fill="both", expand=True, pady=(0, 6))

    table_frame = ctk.CTkFrame(table_card, fg_color="transparent")
    table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    table_frame.grid_rowconfigure(0, weight=1)
    table_frame.grid_columnconfigure(0, weight=1)

    tree = ttk.Treeview(
        table_frame,
        style="Modern.Treeview",
        columns=('name', 'tiktok', 'cookie_st', 'activity', 'monetization', 'proxy_region', 'upload', 'folder', 'last_error'),
        show="headings",
        selectmode="extended",
    )
    tree.heading('name', text='Tên Hồ Sơ', command=lambda: handlers['sort_tree'](tree, 'name', False))
    tree.heading('tiktok', text='TikTok ID', command=lambda: handlers['sort_tree'](tree, 'tiktok', False))
    tree.heading('cookie_st', text='Cookie', command=lambda: handlers['sort_tree'](tree, 'cookie_st', False))
    tree.heading('activity', text='Trạng Thái', command=lambda: handlers['sort_tree'](tree, 'activity', False))
    tree.heading('monetization', text='Kiếm Tiền / KYC', command=lambda: handlers['sort_tree'](tree, 'monetization', False))
    tree.heading('proxy_region', text='Proxy / Vùng', command=lambda: handlers['sort_tree'](tree, 'proxy_region', False))
    tree.heading('upload', text='Tiến Độ Đăng', command=lambda: handlers['sort_tree'](tree, 'upload', False))
    tree.heading('folder', text='Thư Mục Video', command=lambda: handlers['sort_tree'](tree, 'folder', False))
    tree.heading('last_error', text='Chi Tiết Lỗi', command=lambda: handlers['sort_tree'](tree, 'last_error', False))

    tree.column('name', width=140, minwidth=100, stretch=False)
    tree.column('tiktok', width=125, minwidth=90, anchor='center', stretch=False)
    tree.column('cookie_st', width=105, minwidth=85, anchor='center', stretch=False)
    tree.column('activity', width=110, minwidth=95, anchor='center', stretch=False)
    tree.column('monetization', width=130, minwidth=100, anchor='center', stretch=False)
    tree.column('proxy_region', width=140, minwidth=100, anchor='center', stretch=False)
    tree.column('upload', width=115, minwidth=95, anchor='center', stretch=False)
    tree.column('folder', width=130, minwidth=80, stretch=False)
    tree.column('last_error', width=170, minwidth=100, stretch=True)

    tree.grid(row=0, column=0, sticky="nsew")
    vsb = ttk.Scrollbar(table_frame, style='Vertical.TScrollbar', orient='vertical', command=tree.yview)
    vsb.grid(row=0, column=1, sticky='ns')
    hsb = ttk.Scrollbar(table_frame, orient='horizontal', command=tree.xview)
    hsb.grid(row=1, column=0, sticky='ew')
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    widgets["tree"] = tree

    # Pagination Bar (Dưới đáy bảng hồ sơ)
    pagination_bar = ctk.CTkFrame(table_card, fg_color="transparent", height=32)
    pagination_bar.pack(fill="x", padx=10, pady=(0, 6), side="bottom")

    # Left: Page size selector
    p_left = ctk.CTkFrame(pagination_bar, fg_color="transparent")
    p_left.pack(side="left")
    ctk.CTkLabel(p_left, text="Hiển thị:", font=UIThemeTokens.FONT_SUBTITLE, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left", padx=(0, 6))
    page_size_menu = ctk.CTkOptionMenu(
        p_left,
        values=["10 / trang", "25 / trang", "50 / trang", "100 / trang", "200 / trang", "Tất cả"],
        width=115,
        height=26,
        font=UIThemeTokens.FONT_BADGE,
        command=lambda val: handlers.get("change_page_size", lambda v: None)(val),
    )
    page_size_menu.pack(side="left")
    page_size_menu.set("10 / trang")
    widgets["pagination_page_size_menu"] = page_size_menu

    # Right: Pagination navigation buttons & label
    p_right = ctk.CTkFrame(pagination_bar, fg_color="transparent")
    p_right.pack(side="right")

    btn_first = ctk.CTkButton(
        p_right, text="⏮", width=30, height=26, font=UIThemeTokens.FONT_BADGE,
        fg_color="#f1f5f9", text_color=UIThemeTokens.TEXT_PRIMARY, hover_color="#e2e8f0",
        command=lambda: handlers.get("go_first_page", lambda: None)(),
    )
    btn_first.pack(side="left", padx=2)
    widgets["pagination_btn_first"] = btn_first

    btn_prev = ctk.CTkButton(
        p_right, text="◀", width=30, height=26, font=UIThemeTokens.FONT_BADGE,
        fg_color="#f1f5f9", text_color=UIThemeTokens.TEXT_PRIMARY, hover_color="#e2e8f0",
        command=lambda: handlers.get("go_prev_page", lambda: None)(),
    )
    btn_prev.pack(side="left", padx=2)
    widgets["pagination_btn_prev"] = btn_prev

    page_info_label = ctk.CTkLabel(
        p_right, text="Trang 1 / 1 (Tổng 0 hồ sơ)", font=UIThemeTokens.FONT_BODY,
        text_color=UIThemeTokens.TEXT_PRIMARY,
    )
    page_info_label.pack(side="left", padx=10)
    widgets["pagination_page_info_label"] = page_info_label

    btn_next = ctk.CTkButton(
        p_right, text="▶", width=30, height=26, font=UIThemeTokens.FONT_BADGE,
        fg_color="#f1f5f9", text_color=UIThemeTokens.TEXT_PRIMARY, hover_color="#e2e8f0",
        command=lambda: handlers.get("go_next_page", lambda: None)(),
    )
    btn_next.pack(side="left", padx=2)
    widgets["pagination_btn_next"] = btn_next

    btn_last = ctk.CTkButton(
        p_right, text="⏭", width=30, height=26, font=UIThemeTokens.FONT_BADGE,
        fg_color="#f1f5f9", text_color=UIThemeTokens.TEXT_PRIMARY, hover_color="#e2e8f0",
        command=lambda: handlers.get("go_last_page", lambda: None)(),
    )
    btn_last.pack(side="left", padx=2)
    widgets["pagination_btn_last"] = btn_last

    # --------------------------------------------------------------------------
    # WORKSPACE 2: YOUTUBE STUDIO (UNIFIED WORKSPACE WITH 3 SUB-TABS)
    # --------------------------------------------------------------------------
    youtube_workspace = ctk.CTkFrame(workspace_container, fg_color="transparent")
    youtube_workspace.grid(row=0, column=0, sticky="nsew")
    youtube_workspace.grid_remove()
    youtube_workspace.grid_rowconfigure(1, weight=1)
    youtube_workspace.grid_columnconfigure(0, weight=1)

    # Sub-tab Navigation Bar
    yt_subtab_bar = ctk.CTkFrame(youtube_workspace, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT, height=40)
    yt_subtab_bar.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))

    yt_subtab_buttons: Dict[str, ctk.CTkButton] = {}
    yt_subtabs = [
        ("monitor", "📡 Giám Sát Tự Động"),
        ("batch", "📦 Tải Hàng Loạt"),
        ("history", "📜 Lịch Sử Video"),
    ]

    # Sub-tab Content Container
    yt_content_container = ctk.CTkFrame(youtube_workspace, fg_color="transparent")
    yt_content_container.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
    yt_content_container.grid_rowconfigure(0, weight=1)
    yt_content_container.grid_columnconfigure(0, weight=1)

    # Sub-tab 1: Monitor View
    subtab_monitor_frame = ctk.CTkFrame(yt_content_container, fg_color="transparent")
    subtab_monitor_frame.grid(row=0, column=0, sticky="nsew")
    youtube_monitor_view = YouTubeMonitorView(subtab_monitor_frame, handlers.get('youtube_monitor', {}))
    youtube_monitor_view.pack(fill="both", expand=True)
    widgets["youtube_monitor_view"] = youtube_monitor_view

    # Sub-tab 2: Batch View
    subtab_batch_frame = ctk.CTkFrame(yt_content_container, fg_color="transparent")
    subtab_batch_frame.grid(row=0, column=0, sticky="nsew")
    subtab_batch_frame.grid_remove()
    batch_download_view = BatchDownloadView(subtab_batch_frame, handlers.get('youtube_monitor', {}))
    batch_download_view.pack(fill="both", expand=True)
    widgets["batch_download_view"] = batch_download_view

    # Sub-tab 3: History View
    subtab_history_frame = ctk.CTkFrame(yt_content_container, fg_color="transparent")
    subtab_history_frame.grid(row=0, column=0, sticky="nsew")
    subtab_history_frame.grid_remove()
    activity_view = ActivityLogView(subtab_history_frame, handlers.get('activity', {}))
    activity_view.pack(fill="both", expand=True)
    widgets["activity_view"] = activity_view

    yt_subtab_frames = {
        "monitor": subtab_monitor_frame,
        "batch": subtab_batch_frame,
        "history": subtab_history_frame,
    }

    def _switch_youtube_subtab(subtab_key: str) -> None:
        if subtab_key not in yt_subtab_frames:
            subtab_key = "monitor"
        for key, frame in yt_subtab_frames.items():
            btn = yt_subtab_buttons.get(key)
            if key == subtab_key:
                frame.grid()
                if btn:
                    btn.configure(fg_color=UIThemeTokens.ACCENT_PRIMARY, text_color="#ffffff", hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER)
            else:
                frame.grid_remove()
                if btn:
                    btn.configure(fg_color=UIThemeTokens.BG_HOVER, text_color=UIThemeTokens.TEXT_PRIMARY, hover_color=UIThemeTokens.BORDER_LIGHT)

    for key, label in yt_subtabs:
        btn = ctk.CTkButton(
            yt_subtab_bar,
            text=label,
            height=28,
            font=UIThemeTokens.FONT_BUTTON,
            corner_radius=6,
            fg_color=UIThemeTokens.ACCENT_PRIMARY if key == "monitor" else UIThemeTokens.BG_HOVER,
            text_color="#ffffff" if key == "monitor" else UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER if key == "monitor" else UIThemeTokens.BORDER_LIGHT,
            command=lambda k=key: _switch_youtube_subtab(k),
        )
        btn.pack(side="left", padx=4, pady=6)
        yt_subtab_buttons[key] = btn

    widgets["switch_youtube_subtab"] = _switch_youtube_subtab

    # --------------------------------------------------------------------------
    # WORKSPACE 5: MONETIZATION WORKSPACE
    # --------------------------------------------------------------------------
    monetization_workspace = ctk.CTkFrame(workspace_container, fg_color="transparent")
    monetization_workspace.grid(row=0, column=0, sticky="nsew")
    monetization_workspace.grid_remove()

    # Monetization Summary Row (5 KPI Cards)
    mono_summary_row = ctk.CTkFrame(monetization_workspace, fg_color="transparent")
    mono_summary_row.pack(fill="x", pady=(0, 8))
    mono_summary_row.grid_columnconfigure((0, 1, 2, 3, 4), weight=1, uniform="mono_stat")

    mono_total_balance_var = state.get("mono_total_balance_var", ctk.StringVar(value="$0.00"))
    mono_crp_count_var = state.get("mono_crp_count_var", ctk.StringVar(value="0 Acc"))
    mono_kyc_count_var = state.get("mono_kyc_count_var", ctk.StringVar(value="0 Acc"))
    mono_tax_count_var = state.get("mono_tax_count_var", ctk.StringVar(value="0 Acc"))
    mono_tktbm_count_var = state.get("mono_tktbm_count_var", ctk.StringVar(value="0 Acc"))
    mono_ready_count_var = state.get("mono_ready_count_var", ctk.StringVar(value="0"))
    mono_action_needed_var = state.get("mono_action_needed_var", ctk.StringVar(value="0"))

    card_m_balance = SummaryCard(mono_summary_row, title="Tổng Số Dư Khả Dụng", value_var=mono_total_balance_var, accent_color="#10b981")
    card_m_balance.grid(row=0, column=0, padx=(0, 3), sticky="nsew")

    card_m_crp = SummaryCard(mono_summary_row, title="Quỹ Kiếm Tiền (CRP)", value_var=mono_crp_count_var, accent_color="#3b82f6")
    card_m_crp.grid(row=0, column=1, padx=3, sticky="nsew")

    card_m_kyc = SummaryCard(mono_summary_row, title="Đã KYC Danh Tính", value_var=mono_kyc_count_var, accent_color="#8b5cf6")
    card_m_kyc.grid(row=0, column=2, padx=3, sticky="nsew")

    card_m_tax = SummaryCard(mono_summary_row, title="Đã Khai Báo Thuế", value_var=mono_tax_count_var, accent_color="#06b6d4")
    card_m_tax.grid(row=0, column=3, padx=3, sticky="nsew")

    card_m_tktbm = SummaryCard(mono_summary_row, title="TKTBM / Cảnh Báo", value_var=mono_tktbm_count_var, accent_color="#ef4444")
    card_m_tktbm.grid(row=0, column=4, padx=(3, 0), sticky="nsew")

    widgets["mono_summary_cards"] = {
        "balance": card_m_balance,
        "crp": card_m_crp,
        "kyc": card_m_kyc,
        "tax": card_m_tax,
        "tktbm": card_m_tktbm,
    }

    # Monetization Toolbar
    mono_toolbar = ctk.CTkFrame(monetization_workspace, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
    mono_toolbar.pack(fill="x", pady=(0, 8))

    mono_tb_inner = ctk.CTkFrame(mono_toolbar, fg_color="transparent")
    mono_tb_inner.pack(fill="x", padx=8, pady=8)

    ctk.CTkButton(
        mono_tb_inner,
        text="🔄 Cập Nhật Tất Cả",
        width=130,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color=UIThemeTokens.ACCENT_PRIMARY,
        hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
        command=handlers.get('refresh_all_monetization', lambda: None),
    ).pack(side="left", padx=3)

    ctk.CTkButton(
        mono_tb_inner,
        text="🔄 Cập Nhật Đã Chọn",
        width=130,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color="#475569",
        hover_color="#334155",
        command=handlers.get('refresh_selected_monetization', lambda: None),
    ).pack(side="left", padx=3)

    ctk.CTkButton(
        mono_tb_inner,
        text="🔍 Xem Chi Tiết",
        width=110,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color="#0284c7",
        hover_color="#0369a1",
        command=handlers.get('view_monetization_details', lambda: None),
    ).pack(side="left", padx=3)

    ctk.CTkButton(
        mono_tb_inner,
        text="🚀 Gửi Duyệt CRP",
        width=125,
        height=32,
        font=UIThemeTokens.FONT_BUTTON,
        fg_color="#10b981",
        hover_color="#059669",
        command=handlers.get('apply_crp_selected', lambda: None),
    ).pack(side="left", padx=3)

    mono_status_var = state.get("mono_status_var", ctk.StringVar(value=""))
    ctk.CTkLabel(
        mono_tb_inner,
        textvariable=mono_status_var,
        font=UIThemeTokens.FONT_BODY,
        text_color=UIThemeTokens.TEXT_MUTED,
        anchor="e",
    ).pack(side="right", padx=10)

    # Monetization Filter Chips Row
    mono_filter_chips_frame = ctk.CTkFrame(monetization_workspace, fg_color="transparent")
    mono_filter_chips_frame.pack(fill="x", pady=(0, 6))

    mono_active_chip_var = state.get("mono_active_filter_chip", ctk.StringVar(value="ALL"))
    state["mono_active_filter_chip"] = mono_active_chip_var

    mono_chip_buttons: Dict[str, ctk.CTkButton] = {}
    mono_chips = [
        ("ALL", "Tất Cả"),
        ("PAYOUT_READY", "🟢 Sẵn Sàng Rút"),
        ("CRP_ACTIVE", "🏆 Đang Kiếm Tiền"),
        ("TAX_OK", "🟢 Đã Khai Thuế"),
        ("KYC_OK", "🟢 Đã KYC"),
        ("TKTBM", "🔴 TKTBM (Bảo Mật)"),
    ]

    def _select_mono_filter_chip(chip_key: str) -> None:
        mono_active_chip_var.set(chip_key)
        for key, btn in mono_chip_buttons.items():
            if key == chip_key:
                btn.configure(fg_color="#2563eb", text_color="#ffffff")
            else:
                btn.configure(fg_color="#e2e8f0", text_color="#334155")
        if "apply_mono_filter_chip" in handlers:
            handlers["apply_mono_filter_chip"](chip_key)

    for key, label in mono_chips:
        btn = ctk.CTkButton(
            mono_filter_chips_frame,
            text=label,
            height=26,
            font=("Segoe UI Semibold", 9),
            corner_radius=13,
            fg_color="#2563eb" if key == "ALL" else "#e2e8f0",
            text_color="#ffffff" if key == "ALL" else "#334155",
            hover_color="#3b82f6" if key == "ALL" else "#cbd5e1",
            command=lambda k=key: _select_mono_filter_chip(k),
        )
        btn.pack(side="left", padx=2)
        mono_chip_buttons[key] = btn
    widgets["mono_filter_chip_buttons"] = mono_chip_buttons

    # Monetization Treeview Table Card
    mono_card = build_card(monetization_workspace, "Trung Tâm Thu Nhập & Payout (Monetization)")
    mono_card.pack(fill="both", expand=True, pady=(0, 6))

    mono_body = ctk.CTkFrame(mono_card, fg_color="transparent")
    mono_body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    mono_body.grid_rowconfigure(0, weight=1)
    mono_body.grid_columnconfigure(0, weight=1)

    mono_tree = ttk.Treeview(
        mono_body,
        style="Modern.Treeview",
        columns=('name', 'tiktok', 'region', 'crp_status', 'balance', 'payout_status', 'tax_status', 'kyc_status', 'payment_method', 'freshness'),
        show="headings",
        selectmode="extended",
    )
    mono_tree.heading('name', text='Tên Profile')
    mono_tree.heading('tiktok', text='TikTok ID')
    mono_tree.heading('region', text='Khu Vực')
    mono_tree.heading('crp_status', text='Quỹ Kiếm Tiền (CRP)')
    mono_tree.heading('balance', text='Số Dư ($)')
    mono_tree.heading('payout_status', text='Trạng Thái Payout')
    mono_tree.heading('tax_status', text='Khai Báo Thuế')
    mono_tree.heading('kyc_status', text='Xác Minh KYC')
    mono_tree.heading('payment_method', text='Phương Thức PTTT')
    mono_tree.heading('freshness', text='Cập Nhật Lúc')

    mono_tree.column('name', width=115, minwidth=85, anchor="w")
    mono_tree.column('tiktok', width=115, minwidth=85, anchor="w")
    mono_tree.column('region', width=60, minwidth=45, anchor="center")
    mono_tree.column('crp_status', width=160, minwidth=110, anchor="center")
    mono_tree.column('balance', width=80, minwidth=65, anchor="e")
    mono_tree.column('payout_status', width=115, minwidth=85, anchor="center")
    mono_tree.column('tax_status', width=115, minwidth=85, anchor="center")
    mono_tree.column('kyc_status', width=115, minwidth=85, anchor="center")
    mono_tree.column('payment_method', width=155, minwidth=100, anchor="w")
    mono_tree.column('freshness', width=125, minwidth=85, anchor="center")

    mono_tree.grid(row=0, column=0, sticky="nsew")
    mono_vsb = ttk.Scrollbar(mono_body, style='Vertical.TScrollbar', orient='vertical', command=mono_tree.yview)
    mono_vsb.grid(row=0, column=1, sticky='ns')
    mono_hsb = ttk.Scrollbar(mono_body, orient='horizontal', command=mono_tree.xview)
    mono_hsb.grid(row=1, column=0, sticky='ew')
    mono_tree.configure(yscrollcommand=mono_vsb.set, xscrollcommand=mono_hsb.set)

    widgets["monetization_tree"] = mono_tree

    # --------------------------------------------------------------------------
    # WORKSPACE 6: HƯỚNG DẪN SỬ DỤNG WORKSPACE
    # --------------------------------------------------------------------------
    guide_workspace = ctk.CTkFrame(workspace_container, fg_color="transparent")
    guide_workspace.grid(row=0, column=0, sticky="nsew")
    guide_workspace.grid_remove()

    guide_view = build_guide_workspace(guide_workspace)
    guide_view.pack(fill="both", expand=True, padx=6, pady=6)
    widgets["guide_workspace"] = guide_workspace
    widgets["guide_view"] = guide_view

    # ==========================================================================
    # WORKSPACE ROUTER LOGIC
    # ==========================================================================
    workspaces_map = {
        "profiles": (profiles_workspace, btn_nav_profiles),
        "youtube": (youtube_workspace, btn_nav_youtube),
        "youtube_studio": (youtube_workspace, btn_nav_youtube),
        "batch": (youtube_workspace, btn_nav_youtube),
        "history": (youtube_workspace, btn_nav_youtube),
        "monetization": (monetization_workspace, btn_nav_monetization),
        "guide": (guide_workspace, btn_nav_guide),
    }

    def switch_workspace(target_name: str) -> None:
        # Route subtabs if needed
        if target_name in ("youtube", "monitor"):
            _switch_youtube_subtab("monitor")
            target_key = "youtube"
        elif target_name == "batch":
            _switch_youtube_subtab("batch")
            target_key = "youtube"
        elif target_name == "history":
            _switch_youtube_subtab("history")
            target_key = "youtube"
        elif target_name == "youtube_studio":
            target_key = "youtube"
        else:
            target_key = target_name

        primary_workspaces = {
            "profiles": (profiles_workspace, btn_nav_profiles),
            "youtube": (youtube_workspace, btn_nav_youtube),
            "monetization": (monetization_workspace, btn_nav_monetization),
            "guide": (guide_workspace, btn_nav_guide),
        }

        for name, (ws_frame, nav_btn) in primary_workspaces.items():
            if name == target_key:
                ws_frame.grid()
                nav_btn.set_active(True)
            else:
                ws_frame.grid_remove()
                nav_btn.set_active(False)

    btn_nav_profiles.configure(command=lambda: switch_workspace("profiles"))
    btn_nav_youtube.configure(command=lambda: switch_workspace("youtube_studio"))
    btn_nav_monetization.configure(command=lambda: switch_workspace("monetization"))
    btn_nav_guide.configure(command=lambda: switch_workspace("guide"))

    switch_workspace("profiles")  # Default to profiles
    widgets["switch_workspace"] = switch_workspace

    # ==========================================================================
    # 3. COLLAPSIBLE LOG DRAWER (Under Main Content)
    # ==========================================================================
    log_drawer = CollapsibleLogDrawer(main_area, collapsed_height=34, expanded_height=180)
    log_drawer.pack(fill="x", side="bottom", padx=10, pady=(4, 6))
    widgets["log_drawer"] = log_drawer

    # Tabview inside log drawer content
    log_tabview = ctk.CTkTabview(log_drawer.content_frame, height=140)
    log_tabview.pack(fill="both", expand=True)
    widgets["main_tabview"] = log_tabview  # Contract compatibility

    tab_important = log_tabview.add("Theo dõi")
    tab_failed = log_tabview.add("Lỗi")
    tab_log = log_tabview.add("Nhật ký chi tiết")

    # Important Log Text
    important_log_text = ScrolledText(tab_important, height=8, state='disabled', font=('Consolas', 10), relief='flat', bd=0)
    important_log_text.pack(fill='both', expand=True, padx=4, pady=4)
    important_log_text.tag_configure('DEBUG', foreground='#1d4ed8')
    important_log_text.tag_configure('INFO', foreground='black')
    important_log_text.tag_configure('WARN', foreground='#b45309')
    important_log_text.tag_configure('ERROR', foreground='#b91c1c')
    important_log_text.tag_configure('CRITICAL', foreground='#ffffff', background='#dc2626')
    widgets['important_log_text'] = important_log_text

    # Failed Log Text + Toolbar
    failed_toolbar = ctk.CTkFrame(tab_failed, fg_color='transparent')
    failed_toolbar.pack(fill='x', pady=(2, 4))
    ctk.CTkButton(
        failed_toolbar,
        text="Xóa log lỗi",
        width=110,
        height=24,
        font=UIThemeTokens.FONT_BADGE,
        command=handlers['clear_failed_uploads_panel'],
        fg_color="#64748b",
        hover_color="#475569",
    ).pack(side='right', padx=2)
    ctk.CTkButton(
        failed_toolbar,
        text="Xóa video",
        width=80,
        height=24,
        font=UIThemeTokens.FONT_BADGE,
        command=handlers['cleanup_failed_videos'],
        fg_color="#dc2626",
        hover_color="#b91c1c",
    ).pack(side='right', padx=2)

    failed_text = ScrolledText(tab_failed, height=8, state='disabled', font=('Consolas', 10), relief='flat', bd=0)
    failed_text.pack(fill='both', expand=True, padx=4, pady=2)
    failed_text.tag_configure('FAILED', foreground='#b91c1c')
    widgets['failed_uploads_text'] = failed_text

    # Detail Log Text
    status_text = ScrolledText(tab_log, height=8, state='disabled', font=('Consolas', 10), relief='flat', bd=0)
    status_text.pack(fill='both', expand=True, padx=4, pady=4)
    status_text.tag_configure('DEBUG', foreground='#1d4ed8')
    status_text.tag_configure('INFO', foreground='black')
    status_text.tag_configure('WARN', foreground='orange')
    status_text.tag_configure('ERROR', foreground='red')
    widgets['status_text'] = status_text

    # Super Context Menu for Profiles Tree
    ctx_menu = Menu(root, tearoff=0)
    ctx_menu.add_command(label="🌐 Login / Mở trình duyệt", command=handlers['open_browser'])
    ctx_menu.add_command(label="Kiểm tra Cookie (Đã chọn)", command=handlers['check_cookie_live'])
    ctx_menu.add_command(label="Kiểm tra thông tin TikTok", command=handlers['inspect_tiktok_account'])
    ctx_menu.add_command(label="💰 Kiểm tra Thu nhập / KYC / CRP", command=handlers.get('check_monetization_selected', handlers.get('refresh_selected_monetization', lambda: None)))
    ctx_menu.add_separator()
    ctx_menu.add_command(label="Sửa", command=handlers['edit_profile'])
    ctx_menu.add_command(label="Xem chi tiết", command=handlers['view_profile_details'])
    ctx_menu.add_command(label="📂 Mở thư mục Profile (User Data)", command=handlers.get('open_profile_folder', lambda: None))
    ctx_menu.add_command(label="Copy Folder Video", command=handlers['copy_folder_path'])
    ctx_menu.add_command(label="Copy Link Kênh", command=handlers['copy_channel_link'])
    ctx_menu.add_separator()
    ctx_menu.add_command(label="📋 Sao chép TikTok UID", command=handlers.get('copy_tiktok_uid', lambda: None))
    ctx_menu.add_command(label="📋 Sao chép Chuỗi Proxy", command=handlers.get('copy_proxy_string', lambda: None))
    ctx_menu.add_command(label="Lấy Cookie TikTok", command=handlers['get_tiktok_cookies'])
    ctx_menu.add_separator()
    ctx_menu.add_command(label="Khởi động (Đã chọn)", command=handlers['start_selected_batch'])
    ctx_menu.add_command(label="Dừng (Đã chọn)", command=handlers['stop_selected_batch'])
    ctx_menu.add_separator()
    ctx_menu.add_command(label="Reset Browser", command=handlers['clean_browser'])
    ctx_menu.add_command(label="Export tài khoản", command=handlers['export_profiles'])
    ctx_menu.add_command(label="Xoá", command=handlers['delete_profile'])
    widgets['ctx_menu'] = ctx_menu

    # Super Context Menu for Monetization Tree
    mono_ctx_menu = Menu(root, tearoff=0)
    mono_ctx_menu.add_command(label="🔍 Xem Chi Tiết Toàn Diện (Monetization & KYC)", command=handlers.get('view_monetization_details', lambda: None))
    mono_ctx_menu.add_command(label="🔄 Kiểm Tra Lại Tài Khoản Này", command=handlers.get('refresh_selected_monetization', lambda: None))
    mono_ctx_menu.add_command(label="🚀 Gửi Duyệt Quỹ Kiếm Tiền (CRP)", command=handlers.get('apply_crp_selected', lambda: None))
    mono_ctx_menu.add_separator()
    mono_ctx_menu.add_command(label="📋 Sao chép TikTok UID", command=handlers.get('copy_tiktok_uid', lambda: None))
    mono_ctx_menu.add_command(label="📋 Sao chép Phương Thức PTTT", command=handlers.get('copy_payout_method', lambda: None))
    mono_ctx_menu.add_command(label="📂 Mở Thư Mục Profile", command=handlers.get('open_profile_folder', lambda: None))
    widgets['mono_ctx_menu'] = mono_ctx_menu

    # Status Bar
    status_bar = ctk.CTkFrame(main_area, height=24, fg_color="#e2e8f0", corner_radius=0)
    status_bar.pack(fill='x', side='bottom')
    status_count_label = ctk.CTkLabel(status_bar, text="Ready", font=UIThemeTokens.FONT_BADGE, text_color="#334155")
    status_count_label.pack(side='left', padx=10)
    clock_label = ctk.CTkLabel(status_bar, text="", font=UIThemeTokens.FONT_BADGE, text_color="#334155")
    clock_label.pack(side='right', padx=10)
    widgets['status_count_label'] = status_count_label
    widgets['clock_label'] = clock_label

    return widgets
