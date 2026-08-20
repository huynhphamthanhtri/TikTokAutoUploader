import webbrowser
from tkinter import messagebox, ttk

import customtkinter as ctk
from ui_components import UIThemeTokens

TYPE_LABELS = {
    "Tất cả": "",
    "YouTube Download": "youtube_download",
    "TikTok Upload": "tiktok_upload",
    "Batch Find": "batch_find",
}
STATUS_LABELS = {
    "Tất cả": "",
    "success": "success",
    "fail": "fail",
    "skipped": "skipped",
}


class ActivityLogView(ctk.CTkFrame):
    """
    ActivityLogView - Giao diện Lịch Sử Hoạt Động Video (YouTube Download & TikTok Upload).
    Áp dụng Design System UIThemeTokens và bố cục Card chuyên nghiệp.
    """

    def __init__(self, parent, handlers):
        super().__init__(parent, fg_color="transparent")
        self.handlers = handlers or {}
        self._last_mtime = None
        self.type_var = ctk.StringVar(value="Tất cả")
        self.status_var = ctk.StringVar(value="Tất cả")
        self.search_var = ctk.StringVar(value="")
        self.summary_var = ctk.StringVar(value="Lịch sử Video")

        self._build()
        self.reload(force=True)

    def _build(self):
        # 1. Header KPI Summary Card
        header = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        header.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(
            header,
            text="📜 LỊCH SỬ HOẠT ĐỘNG VIDEO",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(side="left", padx=12, pady=8)

        ctk.CTkLabel(
            header,
            textvariable=self.summary_var,
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.ACCENT_PRIMARY,
        ).pack(side="right", padx=12)

        # 2. Filter Toolbar Card
        filters = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        filters.pack(fill="x", pady=(0, 6))

        filter_inner = ctk.CTkFrame(filters, fg_color="transparent")
        filter_inner.pack(fill="x", padx=10, pady=8)

        ctk.CTkLabel(
            filter_inner,
            text="Loại:",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left", padx=(0, 4))

        ctk.CTkComboBox(
            filter_inner,
            variable=self.type_var,
            values=list(TYPE_LABELS.keys()),
            width=150,
            height=30,
            font=UIThemeTokens.FONT_BODY,
            command=lambda _v: self.reload(force=True),
        ).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            filter_inner,
            text="Trạng thái:",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="left", padx=(0, 4))

        ctk.CTkComboBox(
            filter_inner,
            variable=self.status_var,
            values=list(STATUS_LABELS.keys()),
            width=110,
            height=30,
            font=UIThemeTokens.FONT_BODY,
            command=lambda _v: self.reload(force=True),
        ).pack(side="left", padx=(0, 10))

        search = ctk.CTkEntry(
            filter_inner,
            textvariable=self.search_var,
            placeholder_text="Tìm tên video, link, profile, mã lỗi...",
            height=30,
            font=UIThemeTokens.FONT_BODY,
        )
        search.pack(side="left", fill="x", expand=True, padx=(0, 8))
        search.bind("<Return>", lambda _e: self.reload(force=True))

        ctk.CTkButton(
            filter_inner,
            text="Làm mới",
            font=UIThemeTokens.FONT_BUTTON,
            width=80,
            height=30,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=lambda: self.reload(force=True),
        ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(
            filter_inner,
            text="Xóa lịch sử",
            font=UIThemeTokens.FONT_BUTTON,
            width=90,
            height=30,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            command=self._clear,
        ).pack(side="left")

        # 3. Main Data Table Card
        table_card = ctk.CTkFrame(
            self,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        table_card.pack(fill="both", expand=True)
        table_card.grid_rowconfigure(0, weight=1)
        table_card.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            table_card,
            style="Modern.Treeview",
            columns=("time", "type", "status", "profile", "video_name", "video_url", "detail", "file_path"),
            show="headings",
            selectmode="browse",
        )
        headers = (
            ("time", "Thời Gian", 130),
            ("type", "Loại Tác Vụ", 120),
            ("status", "Trạng Thái", 85),
            ("profile", "Profile", 120),
            ("video_name", "Tên Video", 260),
            ("video_url", "Link Video", 180),
            ("detail", "Chi Tiết / Mã Lỗi", 220),
            ("file_path", "Đường Dẫn File", 260),
        )
        for col, text, width in headers:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=60, stretch=col in ("video_name", "detail", "file_path"))

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)

        vsb = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        hsb = ttk.Scrollbar(table_card, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky="ew", padx=(6, 0), pady=(0, 6))
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.bind("<Double-1>", self._open_selected_link)
        self.tree.tag_configure("success", foreground="#16a34a")
        self.tree.tag_configure("fail", foreground="#dc2626")
        self.tree.tag_configure("skipped", foreground="#d97706")

    def refresh_data(self):
        try:
            mtime = self.handlers.get("get_mtime", lambda: 0)()
        except Exception:
            mtime = 0
        if mtime != self._last_mtime:
            self.reload(force=True)

    def reload(self, force=False):
        try:
            self._last_mtime = self.handlers.get("get_mtime", lambda: 0)()
            stats = self.handlers.get("get_stats", lambda: {})()
            self.summary_var.set(
                f"Tải OK: {stats.get('download_success', 0)} | Tải Lỗi: {stats.get('download_fail', 0)} | "
                f"Đăng OK: {stats.get('upload_success', 0)} | Đăng Lỗi: {stats.get('upload_fail', 0)} | "
                f"Bỏ qua: {stats.get('download_skipped', 0) + stats.get('batch_skipped', 0)}"
            )
            logs = self.handlers.get("get_logs", lambda **_kw: [])(
                limit=500,
                event_type=TYPE_LABELS.get(self.type_var.get(), ""),
                status=STATUS_LABELS.get(self.status_var.get(), ""),
                keyword=self.search_var.get().strip(),
            )
        except Exception:
            logs = []
        self.tree.delete(*self.tree.get_children())
        for idx, row in enumerate(logs):
            status = row.get("status", "")
            values = tuple(row.get(col, "") for col in ("time", "type", "status", "profile", "video_name", "video_url", "detail", "file_path"))
            self.tree.insert("", "end", iid=str(idx), values=values, tags=(status,))

    def _clear(self):
        if not messagebox.askyesno("Xóa lịch sử", "Xóa toàn bộ lịch sử video?"):
            return
        ok, msg = self.handlers.get("clear", lambda: (False, "Handler clear chưa có"))()
        if not ok:
            messagebox.showerror("Lịch sử Video", msg)
            return
        self._last_mtime = None
        self.reload(force=True)

    def _open_selected_link(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        if len(values) < 6:
            return
        url = str(values[5] or "").strip()
        if url.startswith(("http://", "https://")):
            webbrowser.open(url)
