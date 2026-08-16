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
        text="🚀 VIBE AUTO UPLOAD",
        font=("Segoe UI Semibold", 13),
        text_color="#38bdf8",
        anchor="w",
    ).pack(anchor="w")
    ctk.CTkLabel(
        logo_frame,
        text="TikTok Studio Suite",
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
        text="YouTube Monitor",
        icon_text="📺",
    )
    btn_nav_youtube.pack(fill="x", pady=2)
    sidebar_buttons["youtube"] = btn_nav_youtube

    btn_nav_batch = SidebarButton(
        nav_container,
        text="Batch YouTube",
        icon_text="📥",
    )
    btn_nav_batch.pack(fill="x", pady=2)
    sidebar_buttons["batch"] = btn_nav_batch

    btn_nav_history = SidebarButton(
        nav_container,
        text="Lịch Sử Đăng",
        icon_text="📜",
    )
    btn_nav_history.pack(fill="x", pady=2)
    sidebar_buttons["history"] = btn_nav_history

    btn_nav_monetization = SidebarButton(
        nav_container,
        text="Thu Nhập / KYC",
        icon_text="💰",
    )
    btn_nav_monetization.pack(fill="x", pady=2)
    sidebar_buttons["monetization"] = btn_nav_monetization

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
        text="Mở Chrome",
        width=96,
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
        columns=('name', 'activity', 'browser', 'status', 'tiktok', 'proxy', 'region', 'upload', 'last_error', 'folder', 'chrome', 'headless', 'limit'),
        show="headings",
        selectmode="extended",
    )
    tree.heading('name', text='Tên', command=lambda: handlers['sort_tree'](tree, 'name', False))
    tree.heading('activity', text='Hoạt động', command=lambda: handlers['sort_tree'](tree, 'activity', False))
    tree.heading('browser', text='Browser', command=lambda: handlers['sort_tree'](tree, 'browser', False))
    tree.heading('status', text='Sức khỏe', command=lambda: handlers['sort_tree'](tree, 'status', False))
    tree.heading('tiktok', text='TikTok ID', command=lambda: handlers['sort_tree'](tree, 'tiktok', False))
    tree.heading('proxy', text='Proxy', command=lambda: handlers['sort_tree'](tree, 'proxy', False))
    tree.heading('region', text='Khu vực', command=lambda: handlers['sort_tree'](tree, 'region', False))
    tree.heading('upload', text='Đăng video', command=lambda: handlers['sort_tree'](tree, 'upload', False))
    tree.heading('last_error', text='Lỗi gần nhất', command=lambda: handlers['sort_tree'](tree, 'last_error', False))
    tree.heading('folder', text='Folder', command=lambda: handlers['sort_tree'](tree, 'folder', False))
    tree.heading('chrome', text='User Data', command=lambda: handlers['sort_tree'](tree, 'chrome', False))
    tree.heading('headless', text='Headless', command=lambda: handlers['sort_tree'](tree, 'headless', False))
    tree.heading('limit', text='Limit', command=lambda: handlers['sort_tree'](tree, 'limit', False))

    tree.column('name', width=140, minwidth=100, stretch=False)
    tree.column('activity', width=120, minwidth=105, anchor='center', stretch=False)
    tree.column('browser', width=130, minwidth=115, anchor='center', stretch=False)
    tree.column('status', width=120, minwidth=100, anchor='center', stretch=False)
    tree.column('tiktok', width=110, minwidth=90, anchor='center', stretch=False)
    tree.column('proxy', width=100, minwidth=85, anchor='center', stretch=False)
    tree.column('region', width=80, minwidth=60, anchor='center', stretch=False)
    tree.column('upload', width=115, minwidth=100, anchor='center', stretch=False)
    tree.column('last_error', width=160, minwidth=120, stretch=False)
    tree.column('folder', width=0, minwidth=0, stretch=False)
    tree.column('chrome', width=0, minwidth=0, stretch=False)
    tree.column('headless', width=0, minwidth=0, stretch=False)
    tree.column('limit', width=0, minwidth=0, stretch=False)

    tree.grid(row=0, column=0, sticky="nsew")
    vsb = ttk.Scrollbar(table_frame, style='Vertical.TScrollbar', orient='vertical', command=tree.yview)
    vsb.grid(row=0, column=1, sticky='ns')
    hsb = ttk.Scrollbar(table_frame, orient='horizontal', command=tree.xview)
    hsb.grid(row=1, column=0, sticky='ew')
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    widgets["tree"] = tree

    # --------------------------------------------------------------------------
    # WORKSPACE 2: YOUTUBE MONITOR WORKSPACE
    # --------------------------------------------------------------------------
    youtube_workspace = ctk.CTkFrame(workspace_container, fg_color="transparent")
    youtube_workspace.grid(row=0, column=0, sticky="nsew")
    youtube_workspace.grid_remove()

    youtube_body = ctk.CTkFrame(youtube_workspace, fg_color="transparent")
    youtube_body.pack(fill="both", expand=True, padx=6, pady=6)
    youtube_monitor_view = YouTubeMonitorView(youtube_body, handlers.get('youtube_monitor', {}))
    youtube_monitor_view.pack(fill="both", expand=True)
    widgets["youtube_monitor_view"] = youtube_monitor_view

    # --------------------------------------------------------------------------
    # WORKSPACE 3: BATCH YOUTUBE WORKSPACE
    # --------------------------------------------------------------------------
    batch_workspace = ctk.CTkFrame(workspace_container, fg_color="transparent")
    batch_workspace.grid(row=0, column=0, sticky="nsew")
    batch_workspace.grid_remove()

    batch_body = ctk.CTkFrame(batch_workspace, fg_color="transparent")
    batch_body.pack(fill="both", expand=True, padx=6, pady=6)
    batch_download_view = BatchDownloadView(batch_body, handlers.get('youtube_monitor', {}))
    batch_download_view.pack(fill="both", expand=True)
    widgets["batch_download_view"] = batch_download_view

    # --------------------------------------------------------------------------
    # WORKSPACE 4: VIDEO HISTORY WORKSPACE
    # --------------------------------------------------------------------------
    history_workspace = ctk.CTkFrame(workspace_container, fg_color="transparent")
    history_workspace.grid(row=0, column=0, sticky="nsew")
    history_workspace.grid_remove()

    history_body = ctk.CTkFrame(history_workspace, fg_color="transparent")
    history_body.pack(fill="both", expand=True, padx=6, pady=6)
    activity_view = ActivityLogView(history_body, handlers.get('activity', {}))
    activity_view.pack(fill="both", expand=True)
    widgets["activity_view"] = activity_view

    # --------------------------------------------------------------------------
    # WORKSPACE 5: MONETIZATION WORKSPACE
    # --------------------------------------------------------------------------
    monetization_workspace = ctk.CTkFrame(workspace_container, fg_color="transparent")
    monetization_workspace.grid(row=0, column=0, sticky="nsew")
    monetization_workspace.grid_remove()

    # Monetization Summary Row (3 Cards)
    mono_summary_row = ctk.CTkFrame(monetization_workspace, fg_color="transparent")
    mono_summary_row.pack(fill="x", pady=(0, 8))
    mono_summary_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="mono_stat")

    mono_total_balance_var = state.get("mono_total_balance_var", ctk.StringVar(value="$0.00"))
    mono_ready_count_var = state.get("mono_ready_count_var", ctk.StringVar(value="0"))
    mono_action_needed_var = state.get("mono_action_needed_var", ctk.StringVar(value="0"))

    card_m_balance = SummaryCard(mono_summary_row, title="Tổng Số Dư Dàn ($)", value_var=mono_total_balance_var, accent_color=UIThemeTokens.STATUS_LIVE)
    card_m_balance.grid(row=0, column=0, padx=(0, 4), sticky="nsew")

    card_m_ready = SummaryCard(mono_summary_row, title="Sẵn Sàng Rút (Payout Ready)", value_var=mono_ready_count_var, accent_color=UIThemeTokens.ACCENT_PRIMARY)
    card_m_ready.grid(row=0, column=1, padx=4, sticky="nsew")

    card_m_action = SummaryCard(mono_summary_row, title="Cần Xử Lý (Chưa KYC / PTTT)", value_var=mono_action_needed_var, accent_color=UIThemeTokens.STATUS_WARN)
    card_m_action.grid(row=0, column=2, padx=(4, 0), sticky="nsew")

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
        columns=('name', 'tiktok', 'region', 'balance', 'payout_status', 'kyc_status', 'payment_method', 'freshness'),
        show="headings",
        selectmode="extended",
    )
    mono_tree.heading('name', text='Tên Profile')
    mono_tree.heading('tiktok', text='TikTok ID')
    mono_tree.heading('region', text='Khu Vực')
    mono_tree.heading('balance', text='Số Dư ($)')
    mono_tree.heading('payout_status', text='Trạng Thái Payout')
    mono_tree.heading('kyc_status', text='Xác Minh KYC')
    mono_tree.heading('payment_method', text='Phương Thức PTTT')
    mono_tree.heading('freshness', text='Lần Cập Nhật')

    mono_tree.column('name', width=140, minwidth=100, stretch=False)
    mono_tree.column('tiktok', width=120, minwidth=90, anchor='center', stretch=False)
    mono_tree.column('region', width=80, minwidth=60, anchor='center', stretch=False)
    mono_tree.column('balance', width=110, minwidth=90, anchor='center', stretch=False)
    mono_tree.column('payout_status', width=130, minwidth=110, anchor='center', stretch=False)
    mono_tree.column('kyc_status', width=120, minwidth=100, anchor='center', stretch=False)
    mono_tree.column('payment_method', width=170, minwidth=120, stretch=False)
    mono_tree.column('freshness', width=140, minwidth=110, anchor='center', stretch=False)

    mono_tree.grid(row=0, column=0, sticky="nsew")
    mono_vsb = ttk.Scrollbar(mono_body, style='Vertical.TScrollbar', orient='vertical', command=mono_tree.yview)
    mono_vsb.grid(row=0, column=1, sticky='ns')
    mono_hsb = ttk.Scrollbar(mono_body, orient='horizontal', command=mono_tree.xview)
    mono_hsb.grid(row=1, column=0, sticky='ew')
    mono_tree.configure(yscrollcommand=mono_vsb.set, xscrollcommand=mono_hsb.set)

    widgets["monetization_tree"] = mono_tree

    # ==========================================================================
    # WORKSPACE ROUTER LOGIC
    # ==========================================================================
    workspaces_map = {
        "profiles": (profiles_workspace, btn_nav_profiles),
        "youtube": (youtube_workspace, btn_nav_youtube),
        "batch": (batch_workspace, btn_nav_batch),
        "history": (history_workspace, btn_nav_history),
        "monetization": (monetization_workspace, btn_nav_monetization),
    }

    def switch_workspace(target_name: str) -> None:
        for name, (ws_frame, nav_btn) in workspaces_map.items():
            if name == target_name:
                ws_frame.grid()
                nav_btn.set_active(True)
            else:
                ws_frame.grid_remove()
                nav_btn.set_active(False)

    btn_nav_profiles.configure(command=lambda: switch_workspace("profiles"))
    btn_nav_youtube.configure(command=lambda: switch_workspace("youtube"))
    btn_nav_batch.configure(command=lambda: switch_workspace("batch"))
    btn_nav_history.configure(command=lambda: switch_workspace("history"))
    btn_nav_monetization.configure(command=lambda: switch_workspace("monetization"))

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

    # Context Menu
    ctx_menu = Menu(root, tearoff=0)
    ctx_menu.add_command(label="Khởi động (Đã chọn)", command=handlers['start_selected_batch'])
    ctx_menu.add_command(label="Dừng (Đã chọn)", command=handlers['stop_selected_batch'])
    ctx_menu.add_separator()
    ctx_menu.add_command(label="Copy Folder Video", command=handlers['copy_folder_path'])
    ctx_menu.add_command(label="Copy Link Kênh", command=handlers['copy_channel_link'])
    ctx_menu.add_command(label="Mở trình duyệt", command=handlers['open_browser'])
    ctx_menu.add_command(label="Kiểm tra Cookie (Đã chọn)", command=handlers['check_cookie_live'])
    ctx_menu.add_command(label="Kiểm tra thông tin TikTok", command=handlers['inspect_tiktok_account'])
    ctx_menu.add_command(label="Lấy Cookie TikTok", command=handlers['get_tiktok_cookies'])
    ctx_menu.add_command(label="Reset Browser", command=handlers['clean_browser'])
    ctx_menu.add_command(label="Xem chi tiết", command=handlers['view_profile_details'])
    ctx_menu.add_command(label="Sửa", command=handlers['edit_profile'])
    ctx_menu.add_command(label="Export tài khoản", command=handlers['export_profiles'])
    ctx_menu.add_command(label="Xoá", command=handlers['delete_profile'])
    widgets['ctx_menu'] = ctx_menu

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
