"""Centralized Vietnamese API pool and predictive polling controls."""
import queue
import threading
from datetime import datetime

import customtkinter as ctk

from .polling_settings import KEY_STATUS_LABELS, estimate_requests, normalize_settings


def format_number(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".").replace(",", "_").replace(".", ",").replace("_", ".")


def mask_key(key):
    return "••••••••" + (str(key)[-4:] if len(str(key)) > 8 else "")


class ApiPollingView(ctk.CTkScrollableFrame):
    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        from . import core
        self._results = queue.Queue()
        self._busy = False
        self._key_snapshot = None
        self._timer = None
        ctk.CTkLabel(self, text="🔑 Nhóm API Key", font=("Arial", 18, "bold")).pack(anchor="w", pady=8)
        entry_row = ctk.CTkFrame(self)
        entry_row.pack(fill="x")
        self.key_entry = ctk.CTkEntry(entry_row, placeholder_text="Nhập API Key", show="•")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ctk.CTkButton(entry_row, text="Thêm API Key", command=self._add_key).pack(side="right", padx=8)
        self.keys_frame = ctk.CTkFrame(self)
        self.keys_frame.pack(fill="x", pady=8)
        self.feedback = ctk.StringVar(value="")
        ctk.CTkLabel(self, textvariable=self.feedback, wraplength=760).pack(anchor="w")
        ctk.CTkLabel(self, text="⚡ Cài đặt quét video", font=("Arial", 18, "bold")).pack(anchor="w", pady=8)
        ctk.CTkLabel(self, text="Hệ thống sẽ tăng tần suất kiểm tra video trong khung thời gian quanh giờ đăng dự kiến.", wraplength=760, justify="left").pack(anchor="w")
        ctk.CTkLabel(self, text="Nếu phát hiện video mới, hệ thống sẽ ngừng gửi yêu cầu API cho kênh đó trong phần thời gian còn lại của khung quét.", wraplength=760, justify="left").pack(anchor="w")
        cfg = core.get_config()["predictive_polling"]
        self.fields = {}
        for key, label, unit in (
            ("poll_interval_seconds", "Khoảng cách giữa mỗi lần quét", "giây"),
            ("window_before_minutes", "Bắt đầu trước giờ dự kiến", "phút"),
            ("window_after_minutes", "Kết thúc sau giờ dự kiến", "phút"),
        ):
            row = ctk.CTkFrame(self)
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=label, width=310, anchor="w").pack(side="left", padx=8)
            var = ctk.StringVar(value=str(cfg[key]))
            self.fields[key] = var
            ctk.CTkEntry(row, textvariable=var, width=120).pack(side="left", padx=8)
            ctk.CTkLabel(row, text=unit).pack(side="left")
            var.trace_add("write", self._update_estimate)
        self.stop_var = ctk.BooleanVar(value=cfg["stop_after_detection"])
        ctk.CTkCheckBox(self, text="Ngừng quét khi đã phát hiện video mới", variable=self.stop_var).pack(anchor="w", pady=8)
        self.estimate = ctk.StringVar()
        ctk.CTkLabel(self, text="📊 Ước tính số lượt gọi API", font=("Arial", 16, "bold")).pack(anchor="w")
        ctk.CTkLabel(self, textvariable=self.estimate, justify="left").pack(anchor="w")
        ctk.CTkLabel(self, text="Ước tính tối đa cho một kênh trong một khung, trước khi ngừng quét sớm. Khung thủ công giữ giờ đã nhập.", wraplength=760).pack(anchor="w")
        ctk.CTkButton(self, text="Lưu cài đặt", command=self._save).pack(anchor="w", pady=8)
        self.runtime = ctk.StringVar(value="Đang chờ")
        ctk.CTkLabel(self, textvariable=self.runtime, justify="left", wraplength=760).pack(anchor="w", pady=8)
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

    def _refresh_keys(self):
        from .core import get_api_keys_pool_status, remove_api_key_from_pool, test_api_key_in_pool
        items = get_api_keys_pool_status()
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
            ctk.CTkLabel(self.keys_frame, text="Chưa có API Key.").pack()
        for item in items:
            key = item["key"]
            row = ctk.CTkFrame(self.keys_frame)
            row.pack(fill="x", pady=3)
            label = mask_key(key) + " — " + KEY_STATUS_LABELS.get(item["status"], "Chưa xác định")
            stamp = item.get("quota_resets_at") if item["status"] == "QUOTA_EXCEEDED" else item.get("retry_after") if item["status"] == "RATE_LIMITED" else 0
            if stamp:
                label += " — Thử lại lúc: " + datetime.fromtimestamp(stamp).strftime("%d/%m %H:%M:%S")
            ctk.CTkLabel(row, text=label).pack(side="left", padx=8)
            ctk.CTkButton(row, text="Xóa", width=60, command=lambda k=key: self._run_key_action(lambda: remove_api_key_from_pool(k), "Đã xóa API Key.")).pack(side="right", padx=4)
            ctk.CTkButton(row, text="Kiểm tra", width=80, command=lambda k=key: self._run_key_action(lambda: test_api_key_in_pool(k), "API Key hoạt động.")).pack(side="right", padx=4)

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
