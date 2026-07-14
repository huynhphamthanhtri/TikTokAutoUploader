import webbrowser
from tkinter import messagebox, ttk

import customtkinter as ctk


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
        header = ctk.CTkFrame(self, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
        header.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(header, textvariable=self.summary_var, font=("Segoe UI Semibold", 13), text_color="#0f172a").pack(side="left", padx=10, pady=8)

        filters = ctk.CTkFrame(self, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
        filters.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(filters, text="Loại:", text_color="#334155").pack(side="left", padx=(8, 4), pady=8)
        ctk.CTkComboBox(filters, variable=self.type_var, values=list(TYPE_LABELS.keys()), width=150, height=30, command=lambda _v: self.reload(force=True)).pack(side="left", padx=4, pady=8)
        ctk.CTkLabel(filters, text="Trạng thái:", text_color="#334155").pack(side="left", padx=(10, 4), pady=8)
        ctk.CTkComboBox(filters, variable=self.status_var, values=list(STATUS_LABELS.keys()), width=120, height=30, command=lambda _v: self.reload(force=True)).pack(side="left", padx=4, pady=8)
        search = ctk.CTkEntry(filters, textvariable=self.search_var, placeholder_text="Tìm tên video, link, profile, lỗi...", height=30)
        search.pack(side="left", fill="x", expand=True, padx=(10, 6), pady=8)
        search.bind("<Return>", lambda _e: self.reload(force=True))
        ctk.CTkButton(filters, text="Làm mới", width=90, height=30, command=lambda: self.reload(force=True)).pack(side="left", padx=3, pady=8)
        ctk.CTkButton(filters, text="Xóa lịch sử", width=105, height=30, fg_color="#ef4444", hover_color="#dc2626", command=self._clear).pack(side="left", padx=(3, 8), pady=8)

        table_frame = ctk.CTkFrame(self, fg_color="transparent")
        table_frame.pack(fill="both", expand=True)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame,
            style="Modern.Treeview",
            columns=("time", "type", "status", "profile", "video_name", "video_url", "detail", "file_path"),
            show="headings",
            selectmode="browse",
        )
        headers = (
            ("time", "Thời gian", 140),
            ("type", "Loại", 120),
            ("status", "Trạng thái", 80),
            ("profile", "Profile", 130),
            ("video_name", "Tên video", 260),
            ("video_url", "Link", 180),
            ("detail", "Chi tiết", 220),
            ("file_path", "File", 260),
        )
        for col, text, width in headers:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=60, stretch=col in ("video_name", "detail", "file_path"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.bind("<Double-1>", self._open_selected_link)
        self.tree.tag_configure("success", foreground="#166534")
        self.tree.tag_configure("fail", foreground="#b91c1c")
        self.tree.tag_configure("skipped", foreground="#b45309")

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
                f"Download OK: {stats.get('download_success', 0)} | Download Fail: {stats.get('download_fail', 0)} | "
                f"Upload OK: {stats.get('upload_success', 0)} | Upload Fail: {stats.get('upload_fail', 0)} | "
                f"Skipped: {stats.get('download_skipped', 0) + stats.get('batch_skipped', 0)}"
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
