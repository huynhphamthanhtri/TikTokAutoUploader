# -*- coding: utf-8 -*-
"""
check_features.ui.common_appeal_dialog - Hộp thoại nhập nội dung kháng nghị thủ công.
Dùng chung cho cả F2 (Kháng Strike bài đăng) và F4 (Kháng vi phạm VPCS).
Khống chế nghiêm ngặt tối đa 400 ký tự theo quy định TikTok.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional, Tuple
import customtkinter as ctk
from tkinter import messagebox

from ui_components import UIThemeTokens


class ManualAppealDialog(ctk.CTkToplevel):
    """
    Sub-dialog cho phép người dùng tự nhập nội dung kháng nghị.
    - Khống chế tối đa 400 ký tự.
    - Bộ đếm trực tiếp thời gian thực, hỗ trợ tiếng Việt mượt mà.
    - Khóa gửi khi đang submit hoặc khi nội dung rỗng / quá 400 ký tự.
    - Lưu giữ bản nháp nếu gửi thất bại.
    """

    MAX_CHARS = 400

    def __init__(
        self,
        parent,
        target_title: str,
        item_id: str,
        violation_reason: str,
        on_submit_callback: Callable[[str], Tuple[bool, str]],
        initial_text: str = "",
        *args,
        **kwargs,
    ):
        super().__init__(parent, *args, **kwargs)
        self.target_title = target_title
        self.item_id = item_id
        self.violation_reason = violation_reason
        self.on_submit_callback = on_submit_callback
        self._is_submitting = False

        self.title(f"Kháng Nghị: {self.target_title}")
        self.geometry("540x480")
        self.minsize(480, 420)
        self.resizable(False, False)

        try:
            self.transient(parent)
            self.grab_set()
        except Exception:
            pass

        self._build_ui(initial_text)

    def _build_ui(self, initial_text: str) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # Header Info Card
        hdr_frame = ctk.CTkFrame(self, fg_color="#f8fafc", corner_radius=8, border_width=1, border_color="#e2e8f0")
        hdr_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))

        ctk.CTkLabel(
            hdr_frame,
            text=f"📌 Đối tượng: {self.target_title} (ID: {self.item_id})",
            font=("Segoe UI Semibold", 12),
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 2))

        ctk.CTkLabel(
            hdr_frame,
            text=f"⚠️ Lý do vi phạm: {self.violation_reason or 'Không xác định'}",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color="#dc2626",
            wraplength=490,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(0, 10))

        # Instructions
        inst_frame = ctk.CTkFrame(self, fg_color="transparent")
        inst_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))

        ctk.CTkLabel(
            inst_frame,
            text="Nhập lý do kháng nghị (Tối đa 400 ký tự theo quy định TikTok):",
            font=("Segoe UI Semibold", 11),
            text_color="#334155",
            anchor="w",
        ).pack(side="left")

        self.lbl_counter = ctk.CTkLabel(
            inst_frame,
            text=f"0 / {self.MAX_CHARS}",
            font=("Segoe UI Bold", 11),
            text_color="#64748b",
            anchor="e",
        )
        self.lbl_counter.pack(side="right")

        # Text Box Container
        txt_frame = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#cbd5e1")
        txt_frame.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 10))
        txt_frame.grid_columnconfigure(0, weight=1)
        txt_frame.grid_rowconfigure(0, weight=1)

        self.textbox = ctk.CTkTextbox(
            txt_frame,
            font=("Segoe UI", 12),
            fg_color="#ffffff",
            text_color="#0f172a",
            wrap="word",
        )
        self.textbox.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        if initial_text:
            self.textbox.insert("1.0", initial_text[:self.MAX_CHARS])

        # Bindings for Realtime Counter & Limit
        self.textbox.bind("<KeyRelease>", self._on_text_changed)
        self.textbox.bind("<<Paste>>", lambda e: self.after(10, self._on_text_changed))

        # Footer Actions
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 16))

        self.lbl_status = ctk.CTkLabel(
            footer,
            text="",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color="#64748b",
            anchor="w",
        )
        self.lbl_status.pack(side="left", padx=4)

        self.btn_cancel = ctk.CTkButton(
            footer,
            text="Hủy",
            width=80,
            height=32,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="#94a3b8",
            hover_color="#64748b",
            command=self.destroy,
        )
        self.btn_cancel.pack(side="right", padx=(8, 0))

        self.btn_submit = ctk.CTkButton(
            footer,
            text="🚀 Gửi Kháng Nghị",
            width=140,
            height=32,
            font=("Segoe UI Semibold", 12),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._on_submit_clicked,
            state="disabled",
        )
        self.btn_submit.pack(side="right")

        self._on_text_changed()

    def _on_text_changed(self, event=None) -> None:
        """Đếm số ký tự và ngăn vượt quá 400 ký tự."""
        raw_text = self.textbox.get("1.0", "end-1c")
        length = len(raw_text)

        if length > self.MAX_CHARS:
            # Cắt ngắn lại nếu dán quá dài
            truncated = raw_text[:self.MAX_CHARS]
            self.textbox.delete("1.0", "end")
            self.textbox.insert("1.0", truncated)
            length = self.MAX_CHARS
            raw_text = truncated

        # Cập nhật màu sắc bộ đếm
        if length >= self.MAX_CHARS:
            self.lbl_counter.configure(text=f"{length} / {self.MAX_CHARS}", text_color="#ef4444")
        elif length >= 350:
            self.lbl_counter.configure(text=f"{length} / {self.MAX_CHARS}", text_color="#f59e0b")
        else:
            self.lbl_counter.configure(text=f"{length} / {self.MAX_CHARS}", text_color="#64748b")

        # Nút gửi chỉ sáng khi có nội dung và không vượt quá giới hạn
        if not self._is_submitting:
            if 0 < length <= self.MAX_CHARS and raw_text.strip():
                self.btn_submit.configure(state="normal")
            else:
                self.btn_submit.configure(state="disabled")

    def _on_submit_clicked(self) -> None:
        """Thực thi gửi kháng nghị qua background thread."""
        text = self.textbox.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("Cảnh báo", "Vui lòng nhập nội dung giải trình kháng nghị.")
            return

        if len(text) > self.MAX_CHARS:
            messagebox.showwarning("Cảnh báo", f"Nội dung kháng nghị không được vượt quá {self.MAX_CHARS} ký tự.")
            return

        self._is_submitting = True
        self.btn_submit.configure(state="disabled")
        self.btn_cancel.configure(state="disabled")
        self.textbox.configure(state="disabled")
        self.lbl_status.configure(text="⏳ Đang gửi đơn kháng lên TikTok...", text_color="#2563eb")

        def _worker():
            try:
                ok, msg = self.on_submit_callback(text)
            except Exception as e:
                ok, msg = False, f"Lỗi không xác định: {e}"

            def _update_ui():
                if not self.winfo_exists():
                    return
                self._is_submitting = False
                self.textbox.configure(state="normal")
                self.btn_cancel.configure(state="normal")
                if ok:
                    self.lbl_status.configure(text="✅ Đã gửi kháng nghị thành công!", text_color="#16a34a")
                    messagebox.showinfo("Thành công", msg or "Đã gửi đơn kháng nghị thành công lên TikTok!")
                    self.destroy()
                else:
                    self.lbl_status.configure(text=f"❌ {msg}", text_color="#ef4444")
                    self.btn_submit.configure(state="normal")
                    messagebox.showerror("Thất bại", f"Không thể gửi kháng nghị:\n{msg}")

            try:
                self.after(0, _update_ui)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()
