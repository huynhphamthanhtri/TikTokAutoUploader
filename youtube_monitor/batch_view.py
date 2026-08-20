import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, List, Optional, Tuple

import customtkinter as ctk
from ui_components import (
    UIThemeTokens,
    ProfilePickerField,
    load_live_profile_names,
    normalize_profile_names,
    normalized_fs_path,
)


class BatchDownloadView(ctk.CTkFrame):
    """
    BatchDownloadView - Giao diện Tải Hàng Loạt Video từ danh sách kênh YouTube.
    Áp dụng Design System UIThemeTokens, ProfilePickerField, Queue-based UI event draining và State Machine quản lý folder.
    """

    def __init__(self, parent, handlers):
        super().__init__(parent, fg_color="transparent")
        self.handlers = handlers or {}
        self.profile_names: List[str] = []
        self.running: bool = False
        self.run_generation: int = 0
        self.stop_event = threading.Event()
        self.active_stop_event: Optional[threading.Event] = None
        self.profile_var = ctk.StringVar(value="")
        self.folder_var = ctk.StringVar(value="")
        self.max_minutes_var = ctk.StringVar(value="0")
        self.status_var = ctk.StringVar(value="Sẵn sàng")

        # Thread-safe UI event queue (Worker chỉ put vào queue, không gọi Tk method)
        self._ui_queue: queue.Queue = queue.Queue()
        self._drain_timer_id: Optional[str] = None

        # Batch State Machine
        self.profile_default_folder: str = ""
        self.folder_source: str = "none"  # "none" | "profile" | "manual"
        self.folder_owner_profile: str = ""
        self._updating_folder: bool = True
        self._destroying: bool = False

        self._build()
        self._folder_trace_id = self.folder_var.trace_add("write", self._on_folder_var_changed)
        self._updating_folder = False

        self.refresh_profiles()
        self._load_max_minutes()
        self._start_ui_drain_loop()

        self.bind("<Destroy>", self._on_destroy_event, add="+")

    def _on_destroy_event(self, event=None):
        if event and getattr(event, "widget", None) is not self:
            return
        self._cleanup_lifecycle()

    def _cleanup_lifecycle(self):
        self._destroying = True
        if getattr(self, "_drain_timer_id", None):
            try:
                self.after_cancel(self._drain_timer_id)
            except Exception:
                pass
            self._drain_timer_id = None
        if getattr(self, "active_stop_event", None):
            try:
                self.active_stop_event.set()
            except Exception:
                pass
        try:
            self.stop_event.set()
        except Exception:
            pass
        if hasattr(self, "_folder_trace_id") and self._folder_trace_id:
            try:
                self.folder_var.trace_remove("write", self._folder_trace_id)
            except Exception:
                pass
            self._folder_trace_id = None
        if hasattr(self, "batch_profile_picker_field"):
            try:
                self.batch_profile_picker_field.destroy()
            except Exception:
                pass

    def destroy(self):
        self._cleanup_lifecycle()
        super().destroy()

    def _schedule_drain(self, delay_ms: int = 100):
        if self._destroying:
            return
        if self._drain_timer_id is not None:
            return
        try:
            self._drain_timer_id = self.after(delay_ms, self._on_drain_timer_tick)
        except (tk.TclError, RuntimeError, Exception):
            self._drain_timer_id = None

    def _on_drain_timer_tick(self):
        self._drain_timer_id = None
        if self._destroying:
            return
        self._drain_ui_queue()
        if not self._destroying:
            if not self._ui_queue.empty():
                self._schedule_drain(5)
            else:
                self._schedule_drain(100)

    def _start_ui_drain_loop(self):
        self._schedule_drain(100)

    def _drain_ui_queue(self):
        if self._destroying:
            return
        processed = 0
        while not self._ui_queue.empty() and processed < 50:
            try:
                item = self._ui_queue.get_nowait()
            except (queue.Empty, Exception):
                break

            processed += 1
            if not isinstance(item, (tuple, list)) or len(item) < 1:
                continue

            try:
                msg_type = item[0]
                if msg_type == "log" and len(item) == 4:
                    _, gen, kind, message = item
                    if gen == self.run_generation:
                        self._append_log(str(message), str(kind))
                elif msg_type == "idle" and len(item) >= 2:
                    gen = item[1]
                    token = item[2] if len(item) >= 3 else None
                    if gen == self.run_generation:
                        self._mark_idle(gen, token)
            except Exception:
                continue

    def _on_folder_var_changed(self, *args):
        if self._destroying or self._updating_folder:
            return
        val = self.folder_var.get().strip()
        current_prof = self.profile_var.get().strip()
        if not val:
            self.folder_source = "none"
            self.folder_owner_profile = ""
        elif self.profile_default_folder and normalized_fs_path(val) == normalized_fs_path(self.profile_default_folder):
            self.folder_source = "profile"
            self.folder_owner_profile = current_prof
        else:
            self.folder_source = "manual"
            self.folder_owner_profile = current_prof

    def _snapshot_target_state(self) -> Dict[str, Any]:
        return {
            "profile": self.profile_var.get(),
            "folder": self.folder_var.get(),
            "default_folder": self.profile_default_folder,
            "source": self.folder_source,
            "owner": self.folder_owner_profile,
        }

    def _restore_target_state(self, snap: Dict[str, Any]) -> List[str]:
        old_guard = self._updating_folder
        self._updating_folder = True
        errors = []
        try:
            try:
                self.folder_var.set(snap.get("folder", ""))
            except Exception as e:
                errors.append(f"folder_var: {e}")
            try:
                self.profile_var.set(snap.get("profile", ""))
            except Exception as e:
                errors.append(f"profile_var: {e}")
            # Khôi phục metadata cuối cùng để bảo đảm tính thẩm quyền
            self.profile_default_folder = snap.get("default_folder", "")
            self.folder_source = snap.get("source", "none")
            self.folder_owner_profile = snap.get("owner", "")
        finally:
            self._updating_folder = old_guard
        return errors

    def _commit_profile_target(self, profile: str, folder: str) -> Tuple[bool, str]:
        old = self._snapshot_target_state()
        self._updating_folder = True
        try:
            self.profile_default_folder = folder
            self.folder_var.set(folder)
            self.folder_source = "profile"
            self.folder_owner_profile = profile
            self.profile_var.set(profile)
            return True, ""
        except Exception as exc:
            rollback_errors = self._restore_target_state(old)
            msg = f"Lỗi cập nhật trạng thái: {exc}"
            if rollback_errors:
                msg += f"; không thể khôi phục hoàn toàn: {'; '.join(rollback_errors)}"
            return False, msg
        finally:
            self._updating_folder = False

    def _build(self):
        # 1. Header Status Card
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
            text="📦 TẢI HÀNG LOẠT (BATCH YOUTUBE DOWNLOAD)",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(side="left", padx=12, pady=8)

        ctk.CTkLabel(
            header,
            textvariable=self.status_var,
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.ACCENT_PRIMARY,
        ).pack(side="right", padx=12)

        # 2. Main Body Container
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)
        body.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(
            body,
            text="Danh sách link kênh YouTube (Mỗi dòng 1 URL kênh hoặc @handle):",
            anchor="w",
            font=UIThemeTokens.FONT_BUTTON,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 4))

        self.links_text = ScrolledText(
            body,
            height=6,
            font=("Consolas", 10),
            relief="flat",
            bd=0,
            bg="#ffffff",
            fg="#0f172a",
        )
        self.links_text.grid(row=1, column=0, sticky="nsew", pady=(0, 6))

        # Configuration Card
        folder_card = ctk.CTkFrame(
            body,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        folder_card.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        folder_card.grid_columnconfigure(1, weight=1)

        # Profile Row
        ctk.CTkLabel(
            folder_card,
            text="Gán Profile đích:",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).grid(row=0, column=0, padx=(10, 4), pady=8, sticky="w")

        self.batch_profile_picker_field = ProfilePickerField(
            folder_card,
            variable=self.profile_var,
            command=self._open_batch_profile_picker,
            placeholder_text="Chưa chọn profile",
            button_text="🔍 Chọn",
            height=30,
        )
        self.batch_profile_picker_field.grid(row=0, column=1, padx=(4, 4), pady=8, sticky="w")
        self.batch_profile_picker_btn = self.batch_profile_picker_field.btn_picker

        ctk.CTkButton(
            folder_card,
            text="Dùng folder profile",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._use_profile_folder,
        ).grid(row=0, column=2, padx=4, pady=8)

        ctk.CTkButton(
            folder_card,
            text="Chọn thư mục khác",
            font=UIThemeTokens.FONT_BUTTON,
            height=30,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._choose_folder,
        ).grid(row=0, column=3, padx=(4, 10), pady=8)

        # Folder Path Row
        ctk.CTkLabel(
            folder_card,
            text="Thư mục lưu:",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).grid(row=1, column=0, padx=(10, 4), pady=(0, 8), sticky="w")

        ctk.CTkEntry(
            folder_card,
            textvariable=self.folder_var,
            height=30,
            font=UIThemeTokens.FONT_BODY,
        ).grid(row=1, column=1, columnspan=3, padx=(4, 10), pady=(0, 8), sticky="ew")

        # Duration Limit Row
        ctk.CTkLabel(
            folder_card,
            text="Giới hạn phút:",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).grid(row=2, column=0, padx=(10, 4), pady=(0, 8), sticky="w")

        self.max_minutes_entry = ctk.CTkEntry(
            folder_card,
            textvariable=self.max_minutes_var,
            placeholder_text="0 = không giới hạn",
            width=120,
            height=30,
            font=UIThemeTokens.FONT_BODY,
        )
        self.max_minutes_entry.grid(row=2, column=1, padx=4, pady=(0, 8), sticky="w")

        ctk.CTkButton(
            folder_card,
            text="Lưu giới hạn",
            font=UIThemeTokens.FONT_BUTTON,
            width=100,
            height=30,
            command=self._save_max_minutes,
        ).grid(row=2, column=2, padx=4, pady=(0, 8))

        ctk.CTkLabel(
            folder_card,
            text="(Batch sẽ quét tìm video mới nhất trong giới hạn thời lượng)",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).grid(row=2, column=3, padx=(4, 10), pady=(0, 8), sticky="w")

        # 3. Action Control Bar
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", pady=(0, 6))

        self.btn_start = ctk.CTkButton(
            actions,
            text="▶ Bắt Đầu Tải Hàng Loạt",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._start_batch,
        )
        self.btn_start.pack(side="left", padx=(0, 8))

        self.btn_stop = ctk.CTkButton(
            actions,
            text="⏹ Dừng Batch",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.STATUS_ERROR,
            hover_color="#b91c1c",
            command=self._stop_batch,
        )
        self.btn_stop.pack(side="left")

        ctk.CTkButton(
            actions,
            text="Xóa log",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._clear_log,
        ).pack(side="right")

        # Log Text Box
        self.log_text = ScrolledText(
            body,
            height=8,
            state="disabled",
            font=("Consolas", 9),
            relief="flat",
            bd=0,
            bg="#ffffff",
            fg="#0f172a",
        )
        self.log_text.grid(row=4, column=0, sticky="nsew")
        self.log_text.tag_configure("ERROR", foreground="#b91c1c")
        self.log_text.tag_configure("WARN", foreground="#b45309")
        self.log_text.tag_configure("SUCCESS", foreground="#16a34a")
        self.log_text.tag_configure("INFO", foreground="#0f172a")

    def refresh_profiles(self):
        ok, live_profiles, _err = load_live_profile_names(self.handlers)
        if not ok:
            return

        profiles = list(live_profiles)
        self.profile_names = profiles
        if hasattr(self, "batch_profile_picker_field"):
            self.batch_profile_picker_field.set_profiles(self.profile_names)

    def refresh_data(self):
        self.refresh_profiles()

    def _load_max_minutes(self):
        try:
            self.max_minutes_var.set(str(self.handlers.get("get_max_video_minutes", lambda: 0)()))
        except Exception:
            self.max_minutes_var.set("0")

    def _open_batch_profile_picker(self):
        """Mở Searchable Profile Picker để chọn nhanh Profile cho tải hàng loạt."""
        from ui_dialogs import SearchableProfilePickerModal

        def _on_confirm(selected_profile: str) -> Tuple[bool, str]:
            ok, live_profiles, msg = load_live_profile_names(self.handlers)
            if not ok:
                return False, msg
            if not live_profiles:
                return False, "Hệ thống hiện không có profile nào khả dụng."
            if selected_profile not in live_profiles:
                return False, "Profile không còn tồn tại trong hệ thống; vui lòng chọn lại."

            ok_f, folder = self._run_handler("get_profile_folder", selected_profile)
            if not ok_f or not folder:
                err_msg = str(folder or "Không lấy được thư mục lưu của profile")
                return False, f"Không thể chọn profile: {err_msg}"

            return self._commit_profile_target(selected_profile, str(folder))

        SearchableProfilePickerModal(
            parent=self,
            profiles=self.profile_names,
            current_profile=self.profile_var.get(),
            title_text="Chọn Profile Tải Hàng Loạt",
            header_text="📦 CHỌN PROFILE TẢI HÀNG LOẠT",
            subject_text="🎯 Chọn profile TikTok sẽ nhận các video tải hàng loạt",
            confirm_text="Chọn Profile",
            return_focus_to=getattr(self, "batch_profile_picker_btn", None),
            on_confirm=_on_confirm,
        )

    def _use_profile_folder(self):
        profile = self.profile_var.get().strip()
        if not profile:
            self._append_log("Chưa chọn profile đích", "WARN")
            return
        ok, folder = self._run_handler("get_profile_folder", profile)
        if ok and folder:
            commit_ok, commit_msg = self._commit_profile_target(profile, str(folder))
            if not commit_ok:
                self._append_log(f"Lỗi khi đặt thư mục: {commit_msg}", "ERROR")
                return
            self._append_log(f"Đã đặt thư mục lưu theo profile '{profile}'", "SUCCESS")
        else:
            self._append_log(folder or "Không lấy được folder profile", "ERROR")

    def _choose_folder(self):
        path = filedialog.askdirectory(title="Chọn thư mục lưu video")
        if path:
            current_prof = self.profile_var.get().strip()
            self.folder_owner_profile = current_prof
            self.folder_source = "manual"
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
        if self._destroying:
            return
        try:
            if not hasattr(self, "log_text") or not self.log_text.winfo_exists():
                return
            line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line, kind)
            self.log_text.see("end")
            lines = int(float(self.log_text.index("end-1c").split(".")[0]))
            if lines > 400:
                self.log_text.delete("1.0", "80.0")
            self.log_text.configure(state="disabled")
        except (tk.TclError, RuntimeError, Exception):
            pass

    def _start_batch(self):
        if self.running:
            self._append_log("Batch đang chạy", "WARN")
            return

        links = [line.strip() for line in self.links_text.get("1.0", "end").splitlines() if line.strip()]
        if not links:
            self._append_log("Danh sách kênh trống", "ERROR")
            return

        profile = self.profile_var.get().strip()
        if not profile:
            self._append_log("Vui lòng chọn một profile TikTok trước khi bắt đầu", "ERROR")
            return

        # 1. Live profile verification at preflight time
        ok_live, live_profiles, msg_live = load_live_profile_names(self.handlers)
        if not ok_live:
            self._append_log(f"Không thể xác thực danh sách profile (kết nối lỗi): {msg_live}", "ERROR")
            return
        if profile not in live_profiles:
            if hasattr(self, "batch_profile_picker_field"):
                self.batch_profile_picker_field.set_profiles(live_profiles)
            self._append_log("Profile đã chọn không còn tồn tại trong hệ thống; vui lòng chọn lại", "ERROR")
            return

        folder = self.folder_var.get().strip()
        if not folder:
            self._append_log("Chưa chọn thư mục đích", "ERROR")
            return

        if self.folder_source not in ("profile", "manual"):
            self._append_log("Nguồn thư mục không hợp lệ", "ERROR")
            return

        # 2. Folder provenance & live folder check
        if self.folder_source == "profile":
            if self.folder_owner_profile != profile:
                self._append_log("Thư mục profile không khớp với profile đang chọn", "ERROR")
                return
            # Live verify default folder
            ok_f, live_default_folder = self._run_handler("get_profile_folder", profile)
            if not ok_f or not live_default_folder:
                self._append_log("Không thể kiểm tra thư mục profile live từ hệ thống", "ERROR")
                return
            if normalized_fs_path(folder) != normalized_fs_path(live_default_folder):
                self.profile_default_folder = str(live_default_folder)
                self._append_log("Đường dẫn thư mục profile đã thay đổi trên hệ thống; vui lòng bấm 'Dùng folder profile' để cập nhật", "ERROR")
                return
        elif self.folder_source == "manual":
            if self.folder_owner_profile != profile:
                self._append_log("Thư mục thủ công chưa được gán hoặc được cấu hình cho profile khác; vui lòng chọn lại", "ERROR")
                return

        # 3. Snapshot bất biến trước khi dispatch
        links_snapshot = list(links)
        profile_snapshot = str(profile)
        folder_snapshot = str(folder)
        source_snapshot = str(self.folder_source)

        stop_event = threading.Event()
        self.active_stop_event = stop_event
        start_barrier = threading.Event()

        self.running = True
        self.status_var.set("Đang chạy")
        self.run_generation += 1
        gen = self.run_generation

        # 4. Safe thread creation with start barrier handshake and orphan worker guard
        thread_started = False
        try:
            worker_thread = threading.Thread(
                target=self._run_batch,
                args=(gen, stop_event, start_barrier, links_snapshot, folder_snapshot, profile_snapshot),
                daemon=True,
            )
            worker_thread.start()
            thread_started = True
            # Enqueue start log only after thread.start() succeeds
            self._ui_queue.put(("log", gen, "INFO", f"Bắt đầu tải {len(links_snapshot)} kênh (Source: {source_snapshot})"))
            # Release barrier so worker thread begins producing logs in correct sequence
            start_barrier.set()
        except Exception as exc:
            stop_event.set()
            start_barrier.set()
            if not thread_started:
                # Thread never started: clean rollback to idle state
                self.running = False
                self.active_stop_event = None
                if not self._destroying and hasattr(self, "status_var"):
                    try:
                        self.status_var.set("Sẵn sàng")
                    except Exception:
                        pass
            # Generation remains monotonic (not decremented)
            self._append_log(f"Không thể khởi chạy tiến trình tải: {exc}", "ERROR")

    def _run_batch(self, gen, stop_event, start_barrier, links, folder, profile):
        # Cancellation-aware barrier wait: allows worker to exit cleanly if cancelled before barrier is released
        while not start_barrier.wait(timeout=0.1):
            if stop_event.is_set():
                self._ui_queue.put(("idle", gen, stop_event))
                return

        if stop_event.is_set():
            self._ui_queue.put(("idle", gen, stop_event))
            return

        def callback(kind, message):
            kind_tag = "ERROR" if kind == "error" else "WARN" if kind == "warn" else "SUCCESS" if kind == "success" else "INFO"
            self._ui_queue.put(("log", gen, kind_tag, message))
        try:
            res = self._run_handler("batch_download_latest", links, folder, profile, callback, stop_event)
            if isinstance(res, (tuple, list)) and len(res) == 2 and isinstance(res[0], bool) and isinstance(res[1], str):
                ok, msg = res[0], res[1]
                self._ui_queue.put(("log", gen, "SUCCESS" if ok else "WARN", msg))
            else:
                self._ui_queue.put(("log", gen, "ERROR", f"Handler trả kết quả không hợp lệ (cần tuple (bool, str)): {res}"))
        except Exception as exc:
            self._ui_queue.put(("log", gen, "ERROR", f"Lỗi tiến trình tải: {exc}"))
        finally:
            self._ui_queue.put(("idle", gen, stop_event))

    def _mark_idle(self, gen: Optional[int] = None, token: Optional[threading.Event] = None):
        if gen is not None and gen != self.run_generation:
            return
        if token is not None and self.active_stop_event is not None and token is not self.active_stop_event:
            return
        self.running = False
        self.active_stop_event = None
        if not self._destroying and hasattr(self, "status_var"):
            try:
                self.status_var.set("Sẵn sàng")
            except Exception:
                pass

    def _stop_batch(self):
        if not self.running:
            self._append_log("Không có batch đang chạy", "WARN")
            return
        if self.active_stop_event:
            self.active_stop_event.set()
        self.stop_event.set()
        self.status_var.set("Đang dừng")
        self._append_log("Đã yêu cầu dừng sau video hiện tại", "WARN")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
