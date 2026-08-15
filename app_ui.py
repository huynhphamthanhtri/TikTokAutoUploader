import customtkinter as ctk
from tkinter import ttk, Menu
from tkinter.scrolledtext import ScrolledText
from youtube_monitor.activity_view import ActivityLogView
from youtube_monitor.batch_view import BatchDownloadView
from youtube_monitor.ui import YouTubeMonitorView


def configure_ttk_styles():
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
        rowheight=32,
        borderwidth=0,
        relief='flat',
        font=('Segoe UI', 10)
    )
    style.map(
        'Modern.Treeview',
        background=[('selected', '#2563eb')],
        foreground=[('selected', '#ffffff')]
    )
    style.configure(
        'Modern.Treeview.Heading',
        background='#e2e8f0',
        foreground='#0f172a',
        relief='flat',
        borderwidth=0,
        font=('Segoe UI Semibold', 10)
    )
    style.map('Modern.Treeview.Heading', background=[('active', '#cbd5e1')])
    style.configure(
        'Vertical.TScrollbar',
        gripcount=0,
        background='#cbd5e1',
        darkcolor='#cbd5e1',
        lightcolor='#cbd5e1',
        troughcolor='#f8fafc',
        bordercolor='#f8fafc',
        arrowcolor='#334155'
    )


def build_card(parent, title, subtitle=None):
    card = ctk.CTkFrame(parent, corner_radius=12, fg_color='#ffffff', border_width=1, border_color='#e5e7eb')
    header = ctk.CTkFrame(card, fg_color='transparent')
    header.pack(fill='x', padx=12, pady=(8, 6))
    ctk.CTkLabel(header, text=title, font=('Segoe UI Semibold', 15), text_color='#0f172a').pack(anchor='w')
    if subtitle:
        ctk.CTkLabel(header, text=subtitle, font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w', pady=(2, 0))
    return card


def build_stat_card(parent, title, value_var, accent):
    card = ctk.CTkFrame(parent, corner_radius=16, fg_color='#ffffff', border_width=1, border_color='#dbeafe')
    ctk.CTkLabel(card, text=title, font=('Segoe UI', 11), text_color='#64748b').pack(anchor='w', padx=14, pady=(12, 2))
    ctk.CTkLabel(card, textvariable=value_var, font=('Segoe UI Semibold', 24), text_color=accent).pack(anchor='w', padx=14, pady=(0, 12))
    return card


def classify_log_message(message: str):
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


def _open_overflow_menu(btn, actions):
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


def build_dashboard(root, state, handlers):
    widgets = {}

    main_container = ctk.CTkFrame(root, fg_color='transparent', corner_radius=0)
    main_container.pack(fill='both', expand=True, padx=10, pady=10)
    widgets['main_container'] = main_container

    main_tabview = ctk.CTkTabview(main_container)
    main_tabview.pack(fill='both', expand=True)
    widgets['main_tabview'] = main_tabview

    profile_tab = main_tabview.add("Quản lý Profile")
    youtube_workspace_tab = main_tabview.add("Theo dõi YouTube")
    batch_youtube_tab = main_tabview.add("Batch YouTube")
    video_history_tab = main_tabview.add("Lịch sử Video")

    topbar = ctk.CTkFrame(profile_tab, corner_radius=12, fg_color='#ffffff', border_width=1, border_color='#e5e7eb')
    topbar.pack(fill='x', pady=(0, 8))
    widgets['topbar'] = topbar
    topbar_body = ctk.CTkFrame(topbar, fg_color='transparent')
    topbar_body.pack(fill='x', padx=10, pady=10)
    topbar_row1 = ctk.CTkFrame(topbar_body, fg_color='transparent')
    topbar_row1.pack(fill='x', pady=(0, 8))
    topbar_row2 = ctk.CTkFrame(topbar_body, fg_color='transparent')
    topbar_row2.pack(fill='x')

    f_proj = ctk.CTkFrame(topbar_row1, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
    f_proj.pack(side='left', padx=(0, 10), pady=2)
    ctk.CTkLabel(f_proj, text="Dự án:", text_color='#334155').pack(side='left', padx=(8, 4))
    project_dropdown = ctk.CTkComboBox(f_proj, variable=state['selected_project_var'], width=190, height=30)
    project_dropdown.pack(side='left', padx=5)
    ctk.CTkButton(f_proj, text="+", width=30, height=30, command=handlers['create_project'], fg_color="#64748b", hover_color="#475569").pack(side='left', padx=2, pady=5)
    ctk.CTkButton(f_proj, text="-", width=30, height=30, command=handlers['delete_project'], fg_color="#ef4444", hover_color="#dc2626").pack(side='left', padx=(2, 6), pady=5)
    widgets['project_dropdown'] = project_dropdown

    f_search = ctk.CTkFrame(topbar_row1, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
    f_search.pack(side='left', fill='x', expand=True, padx=(0, 10), pady=2)
    ctk.CTkEntry(f_search, textvariable=state['filter_var'], height=30, placeholder_text="Tìm hồ sơ, TikTok ID, proxy, trạng thái, khu vực...").pack(fill='x', padx=8, pady=5)

    f_view = ctk.CTkFrame(topbar_row1, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
    f_view.pack(side='right', padx=5)
    ctk.CTkLabel(f_view, text="Zoom:", text_color='#334155').pack(side='left', padx=(8, 4))
    ctk.CTkComboBox(f_view, values=["90%", "100%", "110%"], variable=state['scale_var'], width=86, height=30).pack(side='left', padx=(0, 8), pady=5)

    manage_frame = topbar_row2
    widgets['manage_frame'] = manage_frame
    manage_left = ctk.CTkFrame(manage_frame, fg_color='transparent')
    manage_left.pack(side='left', fill='x', expand=True)

    neutral = ("#64748b", "#475569")
    danger = ("#ef4444", "#dc2626")
    success = ("#16a34a", "#15803d")

    ctk.CTkButton(manage_left, text="Thêm", width=72, height=32, command=handlers['add_profile'], fg_color=neutral[0], hover_color=neutral[1]).pack(side='left', padx=3, pady=2)
    ctk.CTkButton(manage_left, text="Mở Chrome", width=96, height=32, command=handlers['open_browser'], fg_color=neutral[0], hover_color=neutral[1]).pack(side='left', padx=3, pady=2)
    widgets['btn_check_cookie'] = ctk.CTkButton(manage_left, text="Check Cookie", width=104, height=32, command=handlers['check_cookie_live'], fg_color=("#7c3aed", "#6d28d9"))
    widgets['btn_check_cookie'].pack(side='left', padx=3, pady=2)

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
    more_btn = ctk.CTkButton(manage_left, text="⋯", width=40, height=32, command=lambda: _open_overflow_menu(more_btn, overflow_actions), fg_color="#475569", hover_color="#334155")
    more_btn.pack(side='left', padx=3, pady=2)

    control_frame = ctk.CTkFrame(manage_frame, fg_color='transparent')
    control_frame.pack(side='right')
    widgets['control_frame'] = control_frame
    control_left = ctk.CTkFrame(control_frame, fg_color='transparent')
    control_left.pack(side='left')
    control_right = ctk.CTkFrame(control_frame, fg_color='transparent')
    control_right.pack(side='left', padx=(8, 0))
    widgets['btn_start_selected'] = ctk.CTkButton(control_left, text="Start chọn", width=94, height=32, fg_color=success[0], hover_color=success[1], command=handlers['start_selected_batch'])
    widgets['btn_start_selected'].pack(side='left', padx=5)
    widgets['btn_stop_selected'] = ctk.CTkButton(control_left, text="Stop chọn", width=94, height=32, fg_color=danger[0], hover_color=danger[1], command=handlers['stop_selected_batch'])
    widgets['btn_stop_selected'].pack(side='left', padx=5)
    widgets['btn_start_all'] = ctk.CTkButton(control_right, text="Start tất cả", width=102, height=32, fg_color=success[0], hover_color=success[1], command=handlers['start_all_in_project'])
    widgets['btn_start_all'].pack(side='left', padx=5)
    widgets['btn_stop_all'] = ctk.CTkButton(control_right, text="Stop tất cả", width=102, height=32, fg_color=danger[0], hover_color=danger[1], command=handlers['stop_all_in_project'])
    widgets['btn_stop_all'].pack(side='left', padx=5)

    content_row = ctk.CTkFrame(profile_tab, fg_color='transparent')
    content_row.pack(fill='both', expand=True)
    content_row.grid_columnconfigure(0, weight=1)
    content_row.grid_rowconfigure(0, weight=78)
    content_row.grid_rowconfigure(1, weight=22)

    table_card = build_card(content_row, "Danh sách hồ sơ")
    table_card.grid(row=0, column=0, sticky='nsew', pady=(0, 6))
    table_frame = ctk.CTkFrame(table_card, fg_color='transparent')
    table_frame.pack(fill='both', expand=True, padx=12, pady=(0, 12))
    table_frame.grid_rowconfigure(0, weight=1)
    table_frame.grid_columnconfigure(0, weight=1)
    tree = ttk.Treeview(
        table_frame,
        style='Modern.Treeview',
        columns=('name', 'activity', 'browser', 'status', 'tiktok', 'proxy', 'region', 'upload', 'last_error', 'folder', 'chrome', 'headless', 'limit'),
        show='headings',
        selectmode='extended'
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
    tree.grid(row=0, column=0, sticky='nsew')
    vsb = ttk.Scrollbar(table_frame, style='Vertical.TScrollbar', orient='vertical', command=tree.yview)
    vsb.grid(row=0, column=1, sticky='ns')
    hsb = ttk.Scrollbar(table_frame, orient='horizontal', command=tree.xview)
    hsb.grid(row=1, column=0, sticky='ew')
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    widgets['tree'] = tree

    tab_view = ctk.CTkTabview(content_row)
    tab_view.grid(row=1, column=0, sticky='nsew', pady=(6, 0))

    tab_important = tab_view.add("Theo dõi")
    tab_important_body = ctk.CTkFrame(tab_important, fg_color='transparent')
    tab_important_body.pack(fill='both', expand=True, padx=8, pady=(4, 8))
    important_log_text = ScrolledText(tab_important_body, height=12, state='disabled', font=('Consolas', 10), relief='flat', bd=0)
    important_log_text.pack(fill='both', expand=True)
    important_log_text.tag_configure('DEBUG', foreground='#1d4ed8')
    important_log_text.tag_configure('INFO', foreground='black')
    important_log_text.tag_configure('WARN', foreground='#b45309')
    important_log_text.tag_configure('ERROR', foreground='#b91c1c')
    important_log_text.tag_configure('CRITICAL', foreground='#ffffff', background='#dc2626')
    widgets['important_log_text'] = important_log_text

    tab_failed = tab_view.add("Lỗi")
    tab_failed_body = ctk.CTkFrame(tab_failed, fg_color='transparent')
    tab_failed_body.pack(fill='both', expand=True, padx=8, pady=(4, 8))
    failed_toolbar = ctk.CTkFrame(tab_failed_body, fg_color='transparent')
    failed_toolbar.pack(fill='x', pady=(0, 6))
    ctk.CTkButton(
        failed_toolbar,
        text="Xóa log lỗi",
        width=130,
        height=28,
        command=handlers['clear_failed_uploads_panel'],
        fg_color="#64748b",
        hover_color="#475569"
    ).pack(side='right')
    ctk.CTkButton(
        failed_toolbar,
        text="Xóa video",
        width=90,
        height=28,
        command=handlers['cleanup_failed_videos'],
        fg_color="#dc2626",
        hover_color="#b91c1c"
    ).pack(side='right', padx=(0, 6))
    failed_text = ScrolledText(tab_failed_body, height=12, state='disabled', font=('Consolas', 10), relief='flat', bd=0)
    failed_text.pack(fill='both', expand=True)
    failed_text.tag_configure('FAILED', foreground='#b91c1c')
    widgets['failed_uploads_text'] = failed_text

    tab_log = tab_view.add("Nhật ký chi tiết")
    tab_log_body = ctk.CTkFrame(tab_log, fg_color='transparent')
    tab_log_body.pack(fill='both', expand=True, padx=8, pady=(4, 8))
    status_text = ScrolledText(tab_log_body, height=12, state='disabled', font=('Consolas', 10), relief='flat', bd=0)
    status_text.pack(fill='both', expand=True)
    status_text.tag_configure('DEBUG', foreground='#1d4ed8')
    status_text.tag_configure('INFO', foreground='black')
    status_text.tag_configure('WARN', foreground='orange')
    status_text.tag_configure('ERROR', foreground='red')
    widgets['status_text'] = status_text

    youtube_tab_body = ctk.CTkFrame(youtube_workspace_tab, fg_color='transparent')
    youtube_tab_body.pack(fill='both', expand=True, padx=8, pady=(4, 8))
    youtube_monitor_view = YouTubeMonitorView(youtube_tab_body, handlers.get('youtube_monitor', {}))
    youtube_monitor_view.pack(fill='both', expand=True)
    widgets['youtube_monitor_view'] = youtube_monitor_view

    batch_tab_body = ctk.CTkFrame(batch_youtube_tab, fg_color='transparent')
    batch_tab_body.pack(fill='both', expand=True, padx=8, pady=(4, 8))
    batch_download_view = BatchDownloadView(batch_tab_body, handlers.get('youtube_monitor', {}))
    batch_download_view.pack(fill='both', expand=True)
    widgets['batch_download_view'] = batch_download_view

    activity_tab_body = ctk.CTkFrame(video_history_tab, fg_color='transparent')
    activity_tab_body.pack(fill='both', expand=True, padx=8, pady=(4, 8))
    activity_view = ActivityLogView(activity_tab_body, handlers.get('activity', {}))
    activity_view.pack(fill='both', expand=True)
    widgets['activity_view'] = activity_view

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

    status_bar = ctk.CTkFrame(root, height=28, fg_color="#e5e7eb")
    status_bar.pack(fill='x', side='bottom')
    status_count_label = ctk.CTkLabel(status_bar, text="Ready", text_color="#334155")
    status_count_label.pack(side='left', padx=10)
    clock_label = ctk.CTkLabel(status_bar, text="", text_color="#334155")
    clock_label.pack(side='right', padx=10)
    widgets['status_count_label'] = status_count_label
    widgets['clock_label'] = clock_label

    return widgets
