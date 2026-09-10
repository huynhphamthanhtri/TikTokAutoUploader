"""Centralized Vietnamese API pool and predictive polling controls."""
import queue
import threading
from datetime import datetime

import customtkinter as ctk

from ui_components import UIThemeTokens, fit_and_center_dialog
from ui_dialogs import SafeCTkToplevel
from .polling_settings import KEY_STATUS_LABELS, estimate_requests, normalize_settings


def format_number(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".").replace(",", "_").replace(".", ",").replace("_", ".")


def mask_key(key):
    return "••••••••" + (str(key)[-4:] if len(str(key)) > 8 else "")


class BulkApiKeyModal(SafeCTkToplevel):
    """Hộp thoại nhập hàng loạt API Key vào pool."""

    def __init__(self, parent, on_success=None):
        super().__init__(parent)
        self.parent_view = parent
        self.title("📋 Nhập Hàng Loạt API Key")
        fit_and_center_dialog(self, 580, 520, parent=parent, min_w=480, min_h=400)
        self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        self.grab_set()

        self.on_success = on_success
        self._busy = False

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=18, pady=16)

        # Header
        ctk.CTkLabel(
            container,
            text="📋 Nhập Hàng Loạt YouTube API Key",
            font=("Arial", 16, "bold"),
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            container,
            text="Dán danh sách API key vào khung bên dưới. Mỗi dòng một key, hoặc phân tách bằng dấu phẩy, chấm phẩy, khoảng trắng.\nTự động loại bỏ chú thích (#, //), khoảng trắng thừa và lọc trùng lặp.",
            font=("Arial", 12),
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=520,
        ).pack(fill="x", pady=(0, 10))

        # Text input area
        self.text_input = ctk.CTkTextbox(
            container,
            height=160,
            font=("Consolas", 12),
            fg_color="#ffffff",
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.text_input.pack(fill="both", expand=True, pady=(0, 8))
        self.text_input.bind("<KeyRelease>", lambda e: self._update_count())

        # Count label
        self.count_lbl = ctk.CTkLabel(
            container,
            text="Đã phát hiện: 0 API Key",
            font=("Arial", 12),
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        )
        self.count_lbl.pack(fill="x", pady=(0, 6))

        # Checkbox: test before add
        self.test_var = ctk.BooleanVar(value=False)
        self.test_chk = ctk.CTkCheckBox(
            container,
            text="Kiểm tra tính hợp lệ qua Google API trước khi thêm (khuyến nghị nếu ít key)",
            variable=self.test_var,
            font=("Arial", 12),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.test_chk.pack(anchor="w", pady=(0, 8))

        # Status & Progress
        self.status_lbl = ctk.CTkLabel(
            container,
            text="",
            font=("Arial", 12),
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
            justify="left",
            wraplength=520,
        )
        self.status_lbl.pack(fill="x", pady=(0, 6))

        self.progress_bar = ctk.CTkProgressBar(container, mode="determinate")
        self.progress_bar.set(0)

        # Action buttons
        self.btn_row = ctk.CTkFrame(container, fg_color="transparent")
        self.btn_row.pack(fill="x", pady=(4, 0))

        self.btn_cancel = ctk.CTkButton(
            self.btn_row,
            text="Đóng",
            width=90,
            fg_color="#e2e8f0",
            hover_color="#cbd5e1",
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self.destroy,
        )
        self.btn_cancel.pack(side="right", padx=(8, 0))

        self.btn_import = ctk.CTkButton(
            self.btn_row,
            text="Thêm vào Pool",
            width=140,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            text_color="#ffffff",
            command=self._start_import,
        )
        self.btn_import.pack(side="right")

    def destroy(self):
        if hasattr(self, "parent_view") and hasattr(self.parent_view, "_busy"):
            self.parent_view._busy = False
        super().destroy()

    def _update_count(self):
        from .core import parse_api_keys_text
        text = self.text_input.get("1.0", "end")
        keys = parse_api_keys_text(text)
        self.count_lbl.configure(text=f"Đã phát hiện: {len(keys)} API Key")
        return keys

    def _start_import(self):
        if self._busy:
            return
        keys = self._update_count()
        if not keys:
            self.status_lbl.configure(
                text="⚠️ Vui lòng nhập hoặc dán ít nhất 1 API Key vào khung trên.",
                text_color=UIThemeTokens.STATUS_ERROR if hasattr(UIThemeTokens, "STATUS_ERROR") else "#dc2626"
            )
            return

        self._busy = True
        if hasattr(self.parent_view, "_busy"):
            self.parent_view._busy = True
        self.btn_import.configure(state="disabled")
        self.btn_cancel.configure(state="disabled")
        self.test_chk.configure(state="disabled")

        if not self.progress_bar.winfo_ismapped():
            self.progress_bar.pack(fill="x", pady=(0, 8), before=self.btn_row)
        self.progress_bar.set(0)
        self.status_lbl.configure(
            text="Đang xử lý nạp API Key vào pool...",
            text_color=UIThemeTokens.TEXT_PRIMARY
        )

        test_before_add = self.test_var.get()
        total_keys = len(keys)

        def progress_cb(cur, tot, k, ok, msg):
            def update_ui():
                if not self.winfo_exists():
                    return
                frac = cur / max(tot, 1)
                self.progress_bar.set(frac)
                masked = "..." + k[-6:] if len(k) > 6 else k
                status_txt = f"[{cur}/{tot}] {masked}: {msg}"
                self.status_lbl.configure(text=status_txt)
            try:
                self.after(0, update_ui)
            except Exception:
                pass

        def worker():
            from .core import add_api_keys_bulk_to_pool
            try:
                res = add_api_keys_bulk_to_pool(
                    keys,
                    test_before_add=test_before_add,
                    progress_cb=progress_cb if test_before_add else None
                )
            except Exception as exc:
                res = {"error": str(exc)}

            def on_finish():
                if not self.winfo_exists():
                    return
                self._busy = False
                if hasattr(self.parent_view, "_busy"):
                    self.parent_view._busy = False
                self.btn_cancel.configure(state="normal", text="Đóng")
                self.btn_import.configure(state="normal", text="Thêm tiếp")
                self.test_chk.configure(state="normal")
                self.progress_bar.set(1.0)

                if "error" in res:
                    self.status_lbl.configure(
                        text=f"❌ Lỗi khi nạp key: {res['error']}",
                        text_color="#dc2626"
                    )
                    return

                added = len(res.get("added", []))
                duplicate = len(res.get("duplicate", []))
                invalid = len(res.get("invalid", []))

                lines = [f"✅ Đã thêm thành công {added} API Key vào pool."]
                if duplicate:
                    lines.append(f"• Bỏ qua {duplicate} key đã có sẵn trong pool.")
                if invalid:
                    lines.append(f"• Có {invalid} key bị lỗi hoặc không hoạt động.")

                summary_text = "\n".join(lines)
                self.status_lbl.configure(
                    text=summary_text,
                    text_color="#16a34a" if added > 0 else UIThemeTokens.TEXT_PRIMARY
                )
                if added > 0:
                    self.text_input.delete("1.0", "end")
                    self._update_count()

                if self.on_success:
                    try:
                        self.on_success(f"Đã thêm {added} API Key mới vào pool.")
                    except Exception:
                        pass

            try:
                self.after(0, on_finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()


class ApiPollingView(ctk.CTkScrollableFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        from . import core
        self._results = queue.Queue()
        self._busy = False
        self._key_snapshot = None
        self._timer = None
        ctk.CTkLabel(self, text="🔑 Nhóm API Key", font=("Arial", 18, "bold"), text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=8)
        entry_row = ctk.CTkFrame(self)
        entry_row.pack(fill="x")
        self.key_entry = ctk.CTkEntry(entry_row, placeholder_text="Nhập API Key", show="•")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ctk.CTkButton(entry_row, text="Thêm API Key", width=120, command=self._add_key).pack(side="right", padx=(4, 8))
        ctk.CTkButton(
            entry_row,
            text="📋 Nhập hàng loạt",
            width=140,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._open_bulk_dialog
        ).pack(side="right", padx=4)

        pool_toolbar = ctk.CTkFrame(self, fg_color="transparent")
        pool_toolbar.pack(fill="x", pady=(8, 2))
        self.pool_count_lbl = ctk.CTkLabel(
            pool_toolbar,
            text="Danh sách API Key trong Pool:",
            font=("Arial", 13, "bold"),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.pool_count_lbl.pack(side="left", padx=4)

        self.btn_test_all = ctk.CTkButton(
            pool_toolbar,
            text="🔄 Kiểm tra toàn bộ Pool",
            width=180,
            fg_color="#0284c7",
            hover_color="#0369a1",
            text_color="#ffffff",
            command=self._test_all_keys,
        )
        self.btn_test_all.pack(side="right", padx=4)

        self.keys_frame = ctk.CTkFrame(self)
        self.keys_frame.pack(fill="x", pady=(4, 8))
        self.feedback = ctk.StringVar(value="")
        ctk.CTkLabel(self, textvariable=self.feedback, wraplength=760, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(self, text="⚡ Cài đặt quét video", font=("Arial", 18, "bold"), text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=8)
        ctk.CTkLabel(self, text="Hệ thống sẽ tăng tần suất kiểm tra video trong khung thời gian quanh giờ đăng dự kiến.", wraplength=760, justify="left", text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(self, text="Nếu phát hiện video mới, hệ thống sẽ ngừng gửi yêu cầu API cho kênh đó trong phần thời gian còn lại của khung quét.", wraplength=760, justify="left", text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        cfg = core.get_config()["predictive_polling"]
        self.fields = {}
        for key, label, unit in (
            ("poll_interval_seconds", "Khoảng cách giữa mỗi lần quét", "giây"),
            ("window_before_minutes", "Bắt đầu trước giờ dự kiến", "phút"),
            ("window_after_minutes", "Kết thúc sau giờ dự kiến", "phút"),
        ):
            row = ctk.CTkFrame(self)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=label, width=310, anchor="w", text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=8)
            var = ctk.StringVar(value=str(cfg[key]))
            self.fields[key] = var
            ctk.CTkEntry(row, textvariable=var, width=120).pack(side="left", padx=8)
            ctk.CTkLabel(row, text=unit, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left")
            var.trace_add("write", self._update_estimate)
        self.stop_var = ctk.BooleanVar(value=cfg["stop_after_detection"])
        ctk.CTkCheckBox(self, text="Ngừng quét khi đã phát hiện video mới", variable=self.stop_var, text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=8)
        self.estimate = ctk.StringVar()
        ctk.CTkLabel(self, text="📊 Ước tính số lượt gọi API", font=("Arial", 16, "bold"), text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w")
        ctk.CTkLabel(self, textvariable=self.estimate, justify="left", text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(self, text="Ước tính tối đa cho một kênh trong một khung, trước khi ngừng quét sớm. Khung thủ công giữ giờ đã nhập.", wraplength=760, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
        ctk.CTkButton(self, text="Lưu cài đặt", command=self._save).pack(anchor="w", pady=8)
        self.runtime = ctk.StringVar(value="Đang chờ")
        ctk.CTkLabel(self, textvariable=self.runtime, justify="left", wraplength=760, text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=8)
        self._update_estimate()
        self._tick()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _values(self):
        return normalize_settings({**{k: v.get().replace(",", ".") for k, v in self.fields.items()}, "stop_after_detection": self.stop_var.get()}, strict=True)

    def _update_estimate(self, *_):
        if not hasattr(self, "estimate"):
            return
        try:
            cfg = self._values()
            count = estimate_requests(cfg)
            text = f"Tối đa mỗi khung: {format_number(count)} lượt\nThời lượng khung: {format_number(cfg['window_before_minutes'] + cfg['window_after_minutes'])} phút\nKhoảng cách mỗi lần: {format_number(cfg['poll_interval_seconds'])} giây"
            if count > 1200:
                text += "\n⚠️ Cấu hình này có thể sử dụng nhiều hạn mức API."
            self.estimate.set(text)
        except ValueError as exc:
            self.estimate.set(str(exc))

    def _save(self):
        from .core import set_predictive_polling
        try:
            set_predictive_polling(self._values())
            self.feedback.set("Đã lưu cài đặt quét video.")
        except ValueError as exc:
            self.feedback.set(str(exc))
        except OSError:
            self.feedback.set("Không thể lưu cài đặt. Vui lòng kiểm tra quyền ghi tệp.")

    def _run_key_action(self, action, success):
        if self._busy:
            return
        self._busy = True
        self.feedback.set("Đang kiểm tra…")
        def worker():
            try:
                result = action()
                ok = result[0] if isinstance(result, tuple) else result
                self._results.put(success if ok else "Thao tác chưa thành công. Vui lòng kiểm tra API Key, hạn mức và kết nối mạng.")
            except Exception:
                self._results.put("Không thể thực hiện thao tác. Vui lòng kiểm tra kết nối và quyền ghi tệp.")
        threading.Thread(target=worker, daemon=True).start()

    def _add_key(self):
        from .core import add_api_key_to_pool
        key = self.key_entry.get().strip()
        if not key:
            self.feedback.set("Vui lòng nhập API Key.")
            return
        if self._busy:
            return
        self.key_entry.delete(0, "end")
        self._run_key_action(lambda: add_api_key_to_pool(key), "Đã thêm API Key.")

    def _open_bulk_dialog(self):
        def on_done(msg):
            self.feedback.set(msg)
            self._key_snapshot = None
            self._refresh_keys()
        BulkApiKeyModal(self, on_success=on_done)


    def _refresh_keys(self):
        from .core import get_api_keys_pool_status, remove_api_key_from_pool
        items = get_api_keys_pool_status()
        if hasattr(self, "pool_count_lbl"):
            self.pool_count_lbl.configure(text=f"Danh sách API Key trong Pool ({len(items)} key):")
        view_sig = tuple(
            (
                item["key"],
                item["status"],
                item.get("quota_resets_at", 0) if item["status"] == "QUOTA_EXCEEDED" else 0,
                item.get("retry_after", 0) if item["status"] == "RATE_LIMITED" else 0,
            )
            for item in items
        )
        if view_sig == self._key_snapshot:
            return
        self._key_snapshot = view_sig
        for widget in self.keys_frame.winfo_children():
            widget.destroy()
        if not items:
            ctk.CTkLabel(self.keys_frame, text="Chưa có API Key.", text_color=UIThemeTokens.TEXT_MUTED).pack()
        for item in items:
            key = item["key"]
            row = ctk.CTkFrame(self.keys_frame)
            row.pack(fill="x", pady=3)
            label = str(key) + " — " + KEY_STATUS_LABELS.get(item["status"], "Chưa xác định")
            stamp = item.get("quota_resets_at") if item["status"] == "QUOTA_EXCEEDED" else item.get("retry_after") if item["status"] == "RATE_LIMITED" else 0
            if stamp:
                label += " — Thử lại lúc: " + datetime.fromtimestamp(stamp).strftime("%d/%m %H:%M:%S")
            ctk.CTkLabel(row, text=label, font=("Consolas", 12), text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=8)
            ctk.CTkButton(row, text="Xóa", width=60, fg_color="#ef4444", hover_color="#dc2626", command=lambda k=key: self._run_key_action(lambda: remove_api_key_from_pool(k), "Đã xóa API Key.")).pack(side="right", padx=4)
        try:
            self.keys_frame.update_idletasks()
        except Exception:
            pass

    def _test_all_keys(self):
        if self._busy:
            return
        from .core import get_api_keys_pool_status, test_all_api_keys_in_pool
        items = get_api_keys_pool_status()
        if not items:
            self.feedback.set("Pool hiện chưa có API Key nào để kiểm tra.")
            return

        self._busy = True
        self.btn_test_all.configure(state="disabled")
        self.feedback.set("Đang bắt đầu kiểm tra toàn bộ API Key trong pool…")

        def progress_cb(cur, tot, k, status, msg):
            def update():
                self.feedback.set(f"Đang kiểm tra [{cur}/{tot}] {k}: {msg}")
            try:
                self.after(0, update)
            except Exception:
                pass

        def worker():
            try:
                summary = test_all_api_keys_in_pool(progress_cb=progress_cb)
                tot = summary["total"]
                act = summary["active"]
                q = summary["quota_exceeded"]
                r = summary["rate_limited"]
                inv = summary["invalid"] + summary["error"]
                parts = [f"Kiểm tra hoàn tất {tot} key:", f"✅ {act} hoạt động"]
                if q:
                    parts.append(f"⚠️ {q} hết quota")
                if r:
                    parts.append(f"⏳ {r} rate-limited")
                if inv:
                    parts.append(f"❌ {inv} lỗi")
                self._results.put(" | ".join(parts))
            except Exception as e:
                self._results.put(f"Lỗi khi kiểm tra pool: {e}")
            finally:
                def finish():
                    self._busy = False
                    self.btn_test_all.configure(state="normal")
                    self._key_snapshot = None
                    self._refresh_keys()
                try:
                    self.after(0, finish)
                except Exception:
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _tick(self):
        from .core import get_predictive_runtime_status
        try:
            while True:
                self.feedback.set(self._results.get_nowait())
                self._busy = False
        except queue.Empty:
            pass
        if not self._busy:
            self._refresh_keys()
        lines = []
        for cid, status in get_predictive_runtime_status().items():
            line = f"{cid}: {status['label']}"
            if status.get("detected_at"):
                line += f" — Phát hiện lúc: {status['detected_at']}\nĐã ngừng quét trong khung hiện tại. Tiết kiệm: {format_number(status['skipped_seconds'])} giây."
            lines.append(line)
        new_runtime = "\n".join(lines) or "Đang chờ"
        if self.runtime.get() != new_runtime:
            self.runtime.set(new_runtime)
        self._timer = self.after(1000, self._tick)

    def _on_destroy(self, event):
        if event.widget is self and self._timer:
            self.after_cancel(self._timer)
            self._timer = None
