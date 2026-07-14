import threading
from datetime import datetime
from tkinter import filedialog
from tkinter.scrolledtext import ScrolledText

import customtkinter as ctk


class BatchDownloadView(ctk.CTkFrame):
    def __init__(self, parent, handlers):
        super().__init__(parent, fg_color="transparent")
        self.handlers = handlers or {}
        self.profile_names = []
        self.running = False
        self.stop_event = threading.Event()
        self.profile_var = ctk.StringVar(value="")
        self.folder_var = ctk.StringVar(value="")
        self.max_minutes_var = ctk.StringVar(value="0")
        self.status_var = ctk.StringVar(value="Sẵn sàng")
        self._build()
        self.refresh_profiles()
        self._load_max_minutes()

    def _build(self):
        header = ctk.CTkFrame(self, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
        header.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(header, text="Batch YouTube", font=("Segoe UI Semibold", 14), text_color="#0f172a").pack(side="left", padx=10, pady=8)
        ctk.CTkLabel(header, textvariable=self.status_var, text_color="#334155").pack(side="right", padx=10)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        body.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(body, text="Danh sách link kênh YouTube (mỗi dòng 1 link):", anchor="w", text_color="#334155").grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.links_text = ScrolledText(body, height=8, font=("Consolas", 10), relief="flat", bd=0)
        self.links_text.grid(row=1, column=0, sticky="nsew", pady=(0, 8))

        folder_card = ctk.CTkFrame(body, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
        folder_card.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        folder_card.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(folder_card, text="Profile:", text_color="#334155").grid(row=0, column=0, padx=(8, 4), pady=8, sticky="w")
        self.profile_combo = ctk.CTkComboBox(folder_card, variable=self.profile_var, values=[], width=170, height=30, command=self._on_profile_change)
        self.profile_combo.grid(row=0, column=1, padx=4, pady=8, sticky="w")
        ctk.CTkButton(folder_card, text="Dùng folder profile", width=140, height=30, command=self._use_profile_folder).grid(row=0, column=2, padx=4, pady=8)
        ctk.CTkButton(folder_card, text="Chọn thư mục", width=110, height=30, command=self._choose_folder).grid(row=0, column=3, padx=(4, 8), pady=8)
        ctk.CTkLabel(folder_card, text="Thư mục:", text_color="#334155").grid(row=1, column=0, padx=(8, 4), pady=(0, 8), sticky="w")
        ctk.CTkEntry(folder_card, textvariable=self.folder_var, height=30).grid(row=1, column=1, columnspan=3, padx=(4, 8), pady=(0, 8), sticky="ew")
        ctk.CTkLabel(folder_card, text="Giới hạn phút:", text_color="#334155").grid(row=2, column=0, padx=(8, 4), pady=(0, 8), sticky="w")
        self.max_minutes_entry = ctk.CTkEntry(folder_card, textvariable=self.max_minutes_var, placeholder_text="0 = không giới hạn", width=120, height=30)
        self.max_minutes_entry.grid(row=2, column=1, padx=4, pady=(0, 8), sticky="w")
        ctk.CTkButton(folder_card, text="Lưu giới hạn", width=110, height=30, command=self._save_max_minutes).grid(row=2, column=2, padx=4, pady=(0, 8))
        ctk.CTkLabel(folder_card, text="Batch sẽ tìm video mới nhất trong giới hạn", text_color="#64748b").grid(row=2, column=3, padx=(4, 8), pady=(0, 8), sticky="w")

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self.btn_start = ctk.CTkButton(actions, text="Tải video", width=110, height=32, fg_color="#16a34a", hover_color="#15803d", command=self._start_batch)
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop = ctk.CTkButton(actions, text="Dừng", width=90, height=32, fg_color="#ef4444", hover_color="#dc2626", command=self._stop_batch)
        self.btn_stop.pack(side="left")
        ctk.CTkButton(actions, text="Xóa log", width=90, height=32, fg_color="#64748b", hover_color="#475569", command=self._clear_log).pack(side="right")

        self.log_text = ScrolledText(body, height=10, state="disabled", font=("Consolas", 9), relief="flat", bd=0)
        self.log_text.grid(row=4, column=0, sticky="nsew")
        self.log_text.tag_configure("ERROR", foreground="#b91c1c")
        self.log_text.tag_configure("WARN", foreground="#b45309")
        self.log_text.tag_configure("SUCCESS", foreground="#166534")
        self.log_text.tag_configure("INFO", foreground="#0f172a")

    def refresh_profiles(self):
        try:
            profiles = self.handlers.get("get_profiles", lambda: [])()
        except Exception:
            profiles = []
        profiles = list(profiles)
        if profiles == self.profile_names:
            return
        current = self.profile_var.get()
        self.profile_names = profiles
        self.profile_combo.configure(values=self.profile_names)
        if current in self.profile_names:
            self.profile_var.set(current)
        elif self.profile_names:
            self.profile_var.set(self.profile_names[0])
            self._use_profile_folder()

    def refresh_data(self):
        self.refresh_profiles()

    def _load_max_minutes(self):
        try:
            self.max_minutes_var.set(str(self.handlers.get("get_max_video_minutes", lambda: 0)()))
        except Exception:
            self.max_minutes_var.set("0")

    def _on_profile_change(self, _value=None):
        self._use_profile_folder()

    def _use_profile_folder(self):
        profile = self.profile_var.get().strip()
        if not profile:
            return
        ok, folder = self._run_handler("get_profile_folder", profile)
        if ok and folder:
            self.folder_var.set(folder)
        else:
            self._append_log(folder or "Không lấy được folder profile", "ERROR")

    def _choose_folder(self):
        path = filedialog.askdirectory(title="Chọn thư mục lưu video")
        if path:
            self.folder_var.set(path)

    def _save_max_minutes(self):
        value = self.max_minutes_var.get().strip()
        ok, msg = self._run_handler("set_max_video_minutes", value)
        if ok:
            self.max_minutes_var.set(str(value))
        self._append_log(msg, "SUCCESS" if ok else "ERROR")

    def _run_handler(self, name, *args):
        fn = self.handlers.get(name)
        if not fn:
            return False, f"Handler {name} chưa được cấu hình"
        try:
            return fn(*args)
        except Exception as e:
            return False, str(e)

    def _append_log(self, message, kind="INFO"):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line, kind)
        self.log_text.see("end")
        lines = int(float(self.log_text.index("end-1c").split(".")[0]))
        if lines > 400:
            self.log_text.delete("1.0", "80.0")
        self.log_text.configure(state="disabled")

    def _append_threadsafe(self, kind, message):
        try:
            self.after(0, lambda: self._append_log(message, "ERROR" if kind == "error" else "WARN" if kind == "warn" else "SUCCESS" if kind == "success" else "INFO"))
        except Exception:
            pass

    def _start_batch(self):
        if self.running:
            self._append_log("Batch đang chạy", "WARN")
            return
        links = [line.strip() for line in self.links_text.get("1.0", "end").splitlines() if line.strip()]
        folder = self.folder_var.get().strip()
        profile = self.profile_var.get().strip()
        if not links:
            self._append_log("Danh sách kênh trống", "ERROR")
            return
        if not folder:
            self._append_log("Chưa chọn thư mục đích", "ERROR")
            return
        self.stop_event.clear()
        self.running = True
        self.status_var.set("Đang chạy")
        self._append_log(f"Bắt đầu tải {len(links)} kênh", "INFO")
        threading.Thread(target=self._run_batch, args=(links, folder, profile), daemon=True).start()

    def _run_batch(self, links, folder, profile):
        def callback(kind, message):
            self._append_threadsafe(kind, message)
        try:
            ok, msg = self._run_handler("batch_download_latest", links, folder, profile, callback, self.stop_event)
            self._append_threadsafe("success" if ok else "warn", msg)
        finally:
            try:
                self.after(0, self._mark_idle)
            except Exception:
                pass

    def _mark_idle(self):
        self.running = False
        self.status_var.set("Sẵn sàng")

    def _stop_batch(self):
        if not self.running:
            self._append_log("Không có batch đang chạy", "WARN")
            return
        self.stop_event.set()
        self.status_var.set("Đang dừng")
        self._append_log("Đã yêu cầu dừng sau video hiện tại", "WARN")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
