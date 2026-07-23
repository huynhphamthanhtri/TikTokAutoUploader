import re
import threading
import time
import webbrowser
from datetime import datetime
from tkinter import filedialog, ttk
from tkinter.scrolledtext import ScrolledText

import customtkinter as ctk


class YouTubeMonitorView(ctk.CTkFrame):
    def __init__(self, parent, handlers):
        super().__init__(parent, fg_color="transparent")
        self.handlers = handlers or {}
        self.selected_channel_id = None
        self.profile_names = []
        self._channels_snapshot = None
        self._first_channel_render = True

        self.status_var = ctk.StringVar(value="Monitor: Chưa chạy")
        self.health_var = ctk.StringVar(value="Health: -")
        self.callback_var = ctk.StringVar(value="Callback: -")
        self.stats_var = ctk.StringVar(value="Channels: 0 | Queue: 0 | Workers: 0 | Hôm nay: 0")
        self.api_status_var = ctk.StringVar(value="API: -")
        self.cookie_var = ctk.StringVar(value="")
        self.max_minutes_var = ctk.StringVar(value="0")
        self.channel_filter_var = ctk.StringVar(value="Tất cả")

        self._build()
        self.refresh_profiles()
        self._load_max_minutes()

    def _build(self):
        status_frame = ctk.CTkFrame(self, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
        status_frame.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(status_frame, textvariable=self.status_var, text_color="#0f172a", font=("Segoe UI Semibold", 12)).pack(side="left", padx=10, pady=6)
        ctk.CTkLabel(status_frame, textvariable=self.stats_var, text_color="#334155").pack(side="left", padx=12)
        ctk.CTkLabel(status_frame, textvariable=self.health_var, text_color="#64748b", font=("Segoe UI", 10)).pack(side="left", padx=6)
        ctk.CTkLabel(status_frame, textvariable=self.api_status_var, text_color="#2563eb").pack(side="right", padx=10)

        callback_frame = ctk.CTkFrame(self, fg_color="transparent")
        callback_frame.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(callback_frame, textvariable=self.callback_var, text_color="#64748b", anchor="w").pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(callback_frame, text="Lọc profile:", text_color="#334155").pack(side="left", padx=(10, 4))
        self.channel_filter_combo = ctk.CTkComboBox(callback_frame, variable=self.channel_filter_var, values=["Tất cả"], width=155, height=28, command=self._on_channel_filter_change)
        self.channel_filter_combo.pack(side="left")

        table_frame = ctk.CTkFrame(self, fg_color="transparent")
        table_frame.pack(fill="both", expand=True, pady=(0, 6))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            table_frame,
            style="Modern.Treeview",
            columns=("channel", "profile", "active", "short", "seen", "folder"),
            show="tree headings",
            selectmode="browse",
            height=6,
        )
        self.tree.heading("#0", text="Nhóm profile")
        self.tree.column("#0", width=210, minwidth=140, stretch=False)
        for col, text, width in (
            ("channel", "Channel ID", 165),
            ("profile", "Profile đích", 120),
            ("active", "Active", 70),
            ("short", "Short", 70),
            ("seen", "Seen", 65),
            ("folder", "Folder", 280),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=60, stretch=(col == "folder"))
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._open_channel_link)

        controls = ctk.CTkFrame(self, corner_radius=10, fg_color="#f8fafc", border_width=1, border_color="#e5e7eb")
        controls.pack(fill="x", pady=(0, 6))

        row1 = ctk.CTkFrame(controls, fg_color="transparent")
        row1.pack(fill="x", padx=8, pady=(8, 4))
        self.api_key_entry = ctk.CTkEntry(row1, placeholder_text="YouTube API Key", show="*", height=30)
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(row1, text="Lưu/Test API", width=110, height=30, command=self._save_api_key).pack(side="left", padx=3)
        ctk.CTkButton(row1, text="Start", width=74, height=30, fg_color="#16a34a", hover_color="#15803d", command=self._start).pack(side="left", padx=3)
        ctk.CTkButton(row1, text="Stop", width=74, height=30, fg_color="#ef4444", hover_color="#dc2626", command=self._stop).pack(side="left", padx=3)

        row2 = ctk.CTkFrame(controls, fg_color="transparent")
        row2.pack(fill="x", padx=8, pady=4)
        self.channel_entry = ctk.CTkEntry(row2, placeholder_text="Channel URL / @handle / Channel ID", height=30)
        self.channel_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.profile_var = ctk.StringVar(value="")
        self.profile_combo = ctk.CTkComboBox(row2, variable=self.profile_var, values=[], width=145, height=30)
        self.profile_combo.pack(side="left", padx=3)
        ctk.CTkButton(row2, text="+ Thêm", width=86, height=30, command=self._add_channel).pack(side="left", padx=3)
        ctk.CTkButton(row2, text="Set Profile", width=96, height=30, command=self._set_profile).pack(side="left", padx=3)

        row3 = ctk.CTkFrame(controls, fg_color="transparent")
        row3.pack(fill="x", padx=8, pady=(4, 8))
        self.test_entry = ctk.CTkEntry(row3, placeholder_text="Test Video URL / ID", height=30)
        self.test_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.test_profile_var = ctk.StringVar(value="")
        self.test_profile_combo = ctk.CTkComboBox(row3, variable=self.test_profile_var, values=[], width=145, height=30)
        self.test_profile_combo.pack(side="left", padx=3)
        ctk.CTkButton(row3, text="Download Test", width=116, height=30, command=self._download_test).pack(side="left", padx=3)
        ctk.CTkButton(row3, text="Bật/Tắt", width=82, height=30, command=self._toggle_active).pack(side="left", padx=3)
        ctk.CTkButton(row3, text="Short", width=70, height=30, command=self._toggle_short).pack(side="left", padx=3)
        ctk.CTkButton(row3, text="Xóa", width=64, height=30, fg_color="#ef4444", hover_color="#dc2626", command=self._remove).pack(side="left", padx=3)

        row4 = ctk.CTkFrame(controls, fg_color="transparent")
        row4.pack(fill="x", padx=8, pady=(4, 8))
        self.cookie_entry = ctk.CTkEntry(row4, textvariable=self.cookie_var, placeholder_text="cookies.txt YouTube (tùy chọn)", height=30)
        self.cookie_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ctk.CTkButton(row4, text="Chọn Cookie", width=110, height=30, command=self._choose_cookie_file).pack(side="left", padx=3)
        ctk.CTkButton(row4, text="Lưu Cookie", width=100, height=30, command=self._save_cookie_file).pack(side="left", padx=3)
        ctk.CTkButton(row4, text="Xóa", width=64, height=30, fg_color="#ef4444", hover_color="#dc2626", command=self._clear_cookie_file).pack(side="left", padx=3)

        row5 = ctk.CTkFrame(controls, fg_color="transparent")
        row5.pack(fill="x", padx=8, pady=(0, 8))
        ctk.CTkLabel(row5, text="Giới hạn video (phút):", text_color="#334155").pack(side="left", padx=(0, 6))
        self.max_minutes_entry = ctk.CTkEntry(row5, textvariable=self.max_minutes_var, placeholder_text="0 = không giới hạn", width=120, height=30)
        self.max_minutes_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(row5, text="Lưu giới hạn", width=110, height=30, command=self._save_max_minutes).pack(side="left", padx=3)
        ctk.CTkLabel(row5, text="0 = không giới hạn", text_color="#64748b").pack(side="left", padx=8)

        row6 = ctk.CTkFrame(controls, fg_color="transparent")
        row6.pack(fill="x", padx=8, pady=(0, 8))
        self.quality_label = ctk.CTkLabel(row6, text="Chất lượng: 720p nhanh", text_color="#334155")
        self.quality_label.pack(side="left", padx=(0, 12))
        self.ffmpeg_status_label = ctk.CTkLabel(row6, text="FFmpeg: Đang kiểm tra...", text_color="#64748b")
        self.ffmpeg_status_label.pack(side="left", padx=(0, 12))
        self.dl_workers_label = ctk.CTkLabel(row6, text="Tải đồng thời: 4", text_color="#64748b")
        self.dl_workers_label.pack(side="left", padx=(0, 12))
        ctk.CTkButton(row6, text="Cài FFmpeg", width=100, height=28, command=self._install_ffmpeg).pack(side="right", padx=3)

        self.log_text = ScrolledText(self, height=6, state="disabled", font=("Consolas", 9), relief="flat", bd=0)
        self.log_text.pack(fill="x", pady=(0, 0))
        self.log_text.tag_configure("ERROR", foreground="#b91c1c")
        self.log_text.tag_configure("INFO", foreground="#0f172a")

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

    def refresh_profiles(self):
        try:
            profiles = self.handlers.get("get_profiles", lambda: [])()
        except Exception:
            profiles = []
        self.profile_names = list(profiles)
        self.profile_combo.configure(values=self.profile_names)
        self.test_profile_combo.configure(values=self.profile_names)
        filter_values = ["Tất cả"] + self.profile_names + (["Chưa gán"] if "Chưa gán" not in self.profile_names else [])
        current_filter = self.channel_filter_var.get()
        self.channel_filter_combo.configure(values=filter_values)
        if current_filter not in filter_values:
            self.channel_filter_var.set("Tất cả")
            self._channels_snapshot = None
        if self.profile_names and not self.profile_var.get():
            self.profile_var.set(self.profile_names[0])
        if self.profile_names and not self.test_profile_var.get():
            self.test_profile_var.set(self.profile_names[0])

    def refresh_data(self):
        self.refresh_profiles()
        status = self.handlers.get("get_status", lambda: {})()
        running = "Đang chạy" if status.get("running") else "Đã dừng"
        color = "#16a34a" if status.get("running") else "#ef4444"
        self.status_var.set(f"Monitor: {running}")
        healthy = status.get("healthy", False) if status.get("running") else False
        if status.get("running"):
            if healthy:
                ngrok_ok = status.get("callback_verified", False)
                subs_total = status.get("subscriptions_total", 0)
                subs_ok = status.get("subscriptions_ok", 0)
                degraded = subs_total - subs_ok
                if degraded > 0:
                    health_text = f"Degraded (WebSub: {subs_ok}/{subs_total})"
                elif not ngrok_ok:
                    health_text = "No ngrok"
                else:
                    health_text = "OK"
                hcolor = "#b45309" if degraded > 0 else "#16a34a" if ngrok_ok else "#ef4444"
            else:
                health_text = f"Lỗi: {status.get('health_msg', '?')}"
                hcolor = "#ef4444"
        else:
            health_text = "-"
            hcolor = "#64748b"
        self.health_var.set(f"Health: {health_text}")
        try:
            for child in self.health_var._label.winfo_children():
                pass
        except Exception:
            pass
        self.stats_var.set(f"Channels: {status.get('channels', 0)} | Queue: {status.get('queue', 0)} | Workers: {status.get('workers', 0)} | Hôm nay: {status.get('downloaded_today', 0)}")
        cookie_state = "OK" if status.get("cookies_set") else "Chưa có"
        self.api_status_var.set(("API: OK" if status.get("api_key_set") else "API: Chưa nhập") + f" | Cookie: {cookie_state}")
        port = status.get("callback_port", "")
        cb_url = status.get("callback_url", "") or ""
        last_post = status.get("last_callback_post", "")
        cb_parts = [f"Port: {port}" if port else ""]
        if cb_url:
            cb_parts.append(f"Ngrok: {'OK' if status.get('callback_verified') else '?'}")
        if last_post:
            cb_parts.append(f"POST cuối: {last_post[11:19] if len(last_post) > 19 else last_post}")
        self.callback_var.set(f"Callback: {' | '.join(cb_parts) if cb_parts else '-'}")
        cookie_path = self.handlers.get("get_cookies_file", lambda: "")()
        if cookie_path and self.cookie_var.get() != cookie_path:
            self.cookie_var.set(cookie_path)
        try:
            self.children[next(iter(self.children))].winfo_children()[0].configure(text_color=color)
        except Exception:
            pass

        channels = self._filtered_channels(self.handlers.get("get_channels", lambda: [])())
        snapshot = self._build_channels_snapshot(channels)
        if snapshot != self._channels_snapshot:
            self._channels_snapshot = snapshot
            self._render_channels(channels)

        logs = self.handlers.get("get_logs", lambda: [])()
        for line in logs:
            self.append_log(line, error=("lỗi" in line.lower() or "error" in line.lower() or "fail" in line.lower()))

        self._update_ffmpeg_status()

    def _update_ffmpeg_status(self):
        try:
            from . import ffmpeg_helper
            ok, msg, src = ffmpeg_helper.check_ffmpeg()
            if ok:
                label = f"FFmpeg: Sẵn sàng ({src})" if src else "FFmpeg: Sẵn sàng"
                self.ffmpeg_status_label.configure(text=label, text_color="#16a34a")
            else:
                self.ffmpeg_status_label.configure(text="FFmpeg: Chưa cài", text_color="#b91c1c")
        except Exception:
            self.ffmpeg_status_label.configure(text="FFmpeg: ?", text_color="#64748b")

    def append_log(self, line, error=False):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n", "ERROR" if error else "INFO")
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
        self.refresh_data()

    def _filtered_channels(self, channels):
        selected = self.channel_filter_var.get()
        if selected == "Tất cả":
            return list(channels or [])
        if selected == "Chưa gán":
            return [item for item in (channels or []) if not item.get("profile_name")]
        return [item for item in (channels or []) if item.get("profile_name", "") == selected]

    def _build_channels_snapshot(self, channels):
        rows = []
        for item in channels:
            rows.append((
                item.get("channel_id", ""),
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

    def _render_channels(self, channels):
        open_groups = {
            iid for iid in self.tree.get_children("")
            if iid.startswith("__group__") and self.tree.item(iid, "open")
        }
        selected = self.selected_channel_id
        self.tree.delete(*self.tree.get_children(""))
        grouped = {}
        for item in channels:
            profile = item.get("profile_name") or "Chưa gán"
            grouped.setdefault(profile, []).append(item)
        used_group_iids = set()
        for index, profile in enumerate(sorted(grouped.keys(), key=str.lower)):
            items = sorted(grouped[profile], key=lambda x: x.get("channel_id", ""))
            group_iid = self._group_iid(index, profile)
            base_group_iid = group_iid
            suffix = 2
            while group_iid in used_group_iids:
                group_iid = f"{base_group_iid}_{suffix}"
                suffix += 1
            used_group_iids.add(group_iid)
            group_text = f"[Profile] {profile} ({len(items)} kenh)"
            self.tree.insert("", "end", iid=group_iid, text=group_text, values=("", "", "", "", "", ""), open=(self._first_channel_render or group_iid in open_groups))
            for item in items:
                cid = item.get("channel_id", "")
                if not cid:
                    continue
                values = (
                    cid,
                    item.get("profile_name", ""),
                    "Yes" if item.get("active") else "No",
                    "Yes" if item.get("process_short") else "No",
                    item.get("seen_count", 0),
                    item.get("folder", ""),
                )
                self.tree.insert(group_iid, "end", iid=cid, text="", values=values)
        self._first_channel_render = False
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)

    def _open_channel_link(self, event=None):
        iid = self.tree.identify_row(event.y) if event else (self.tree.selection()[0] if self.tree.selection() else "")
        if not iid or iid.startswith("__group__"):
            return
        channel_id = self.tree.set(iid, "channel")
        if channel_id:
            webbrowser.open(f"https://www.youtube.com/channel/{channel_id}")

    def _save_api_key(self):
        key = self.api_key_entry.get().strip()
        threading.Thread(target=lambda: self._append_threadsafe(self._run_handler("save_api_key", key)[1]), daemon=True).start()

    def _start(self):
        threading.Thread(target=lambda: self._append_threadsafe(self._run_handler("start")[1]), daemon=True).start()

    def _stop(self):
        threading.Thread(target=lambda: self._append_threadsafe(self._run_handler("stop")[1]), daemon=True).start()

    def _add_channel(self):
        channel = self.channel_entry.get().strip()
        profile = self.profile_var.get().strip()
        threading.Thread(target=lambda: self._append_threadsafe(self._run_handler("add_channel", channel, profile)[1]), daemon=True).start()

    def _set_profile(self):
        if not self.selected_channel_id:
            self.append_log("Chưa chọn channel", error=True)
            return
        profile = self.profile_var.get().strip()
        self.append_log(self._run_handler("set_profile", self.selected_channel_id, profile)[1])

    def _download_test(self):
        video = self.test_entry.get().strip()
        profile = self.test_profile_var.get().strip()
        self.append_log(self._run_handler("download_test", video, profile)[1])

    def _choose_cookie_file(self):
        path = filedialog.askopenfilename(
            title="Chọn cookies.txt YouTube",
            filetypes=[("Cookies txt", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.cookie_var.set(path)

    def _save_cookie_file(self):
        path = self.cookie_var.get().strip()
        threading.Thread(target=lambda: self._append_threadsafe(self._run_handler("set_cookies_file", path)[1]), daemon=True).start()

    def _clear_cookie_file(self):
        self.cookie_var.set("")
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
                self.after(0, lambda: self.ffmpeg_status_label.configure(text="FFmpeg: Đang tải...", text_color="#b45309"))
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

    def _toggle_active(self):
        if self.selected_channel_id:
            self.append_log(self._run_handler("toggle_active", self.selected_channel_id)[1])

    def _toggle_short(self):
        if self.selected_channel_id:
            self.append_log(self._run_handler("toggle_short", self.selected_channel_id)[1])

    def _remove(self):
        if self.selected_channel_id:
            self.append_log(self._run_handler("remove_channel", self.selected_channel_id)[1])
