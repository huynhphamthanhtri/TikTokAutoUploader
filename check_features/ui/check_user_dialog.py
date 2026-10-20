# -*- coding: utf-8 -*-
"""
check_features.ui.check_user_dialog - Hộp thoại kiểm tra thông tin tài khoản (F1).
Thiết kế chuẩn 5 Cards + Mini Grid và logic đổi Nickname có quản lý Cooldown 7 ngày.
"""

from __future__ import annotations

from datetime import datetime
import threading
import time
from typing import Any, Dict, Optional

import customtkinter as ctk
from tkinter import messagebox

from ui_components import UIThemeTokens
from ..models import UserInfo
from ..clients.account_client import TikTokAccountClient


class CheckUserDialog(ctk.CTkToplevel):
    """
    Hộp thoại Kiểm Tra Thông Tin Tài Khoản chuyên biệt cho một profile (F1).
    Hiển thị 5 Cards thông tin và xử lý đổi Nickname an toàn.
    """

    def __init__(
        self,
        parent,
        profile_name: str,
        profile_config: Dict[str, Any],
        *args,
        **kwargs,
    ):
        super().__init__(parent, *args, **kwargs)
        self.profile_name = profile_name
        self.profile_config = profile_config or {}
        self.client = TikTokAccountClient(self.profile_config)

        self._user_info = UserInfo()
        self._is_loading = False
        self._is_updating_name = False

        self.title(f"Thông Tin Tài Khoản: {profile_name}")
        self.geometry("620x680")
        self.minsize(560, 600)

        try:
            self.transient(parent)
        except Exception:
            pass

        self._build_ui()
        self.after(100, self.refresh_data)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header bar
        hdr_frame = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        hdr_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))

        ctk.CTkLabel(
            hdr_frame,
            text=f"👤 Hồ Sơ: {self.profile_name}",
            font=("Segoe UI Bold", 15),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(side="left", padx=14, pady=10)

        self.btn_refresh = ctk.CTkButton(
            hdr_frame,
            text="🔄 Tải Lại",
            width=90,
            height=30,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="#334155",
            hover_color="#1e293b",
            command=self.refresh_data,
        )
        self.btn_refresh.pack(side="right", padx=14, pady=10)

        # Content scrollable frame
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        self.scroll.grid_columnconfigure(0, weight=1)

        # --- CARD 1: Tên hiển thị (Nickname) & Đổi tên ---
        self.card_nick = ctk.CTkFrame(self.scroll, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        self.card_nick.pack(fill="x", pady=6)

        ctk.CTkLabel(self.card_nick, text="Tên hiển thị (Nickname):", font=("Segoe UI Semibold", 11), text_color="#64748b").pack(anchor="w", padx=14, pady=(10, 2))
        
        nick_row = ctk.CTkFrame(self.card_nick, fg_color="transparent")
        nick_row.pack(fill="x", padx=14, pady=(0, 4))
        
        self.entry_nickname = ctk.CTkEntry(nick_row, font=("Segoe UI Bold", 13), height=32)
        self.entry_nickname.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.entry_nickname.bind("<KeyRelease>", lambda e: self._check_nickname_button_state())

        self.btn_change_nick = ctk.CTkButton(
            nick_row,
            text="✏️ Đổi Tên",
            width=90,
            height=32,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._on_change_nickname_clicked,
            state="disabled",
        )
        self.btn_change_nick.pack(side="right")

        self.lbl_nick_cooldown = ctk.CTkLabel(
            self.card_nick,
            text="Đang kiểm tra thời hạn đổi tên...",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color="#64748b",
            anchor="w",
        )
        self.lbl_nick_cooldown.pack(fill="x", padx=14, pady=(0, 10))

        # --- CARD 2: Tên người dùng (@handle) ---
        self.card_user = ctk.CTkFrame(self.scroll, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        self.card_user.pack(fill="x", pady=6)

        ctk.CTkLabel(self.card_user, text="Tên người dùng (@handle):", font=("Segoe UI Semibold", 11), text_color="#64748b").pack(anchor="w", padx=14, pady=(10, 2))
        
        user_row = ctk.CTkFrame(self.card_user, fg_color="transparent")
        user_row.pack(fill="x", padx=14, pady=(0, 4))

        self.lbl_handle = ctk.CTkLabel(user_row, text="@--", font=("Segoe UI Bold", 13), text_color="#0f172a", anchor="w")
        self.lbl_handle.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            user_row,
            text="📋 Copy",
            width=65,
            height=28,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: self._copy_to_clipboard(self.lbl_handle.cget("text").lstrip("@")),
        ).pack(side="right")

        self.lbl_handle_info = ctk.CTkLabel(
            self.card_user,
            text="💡 @handle chỉ đổi được trực tiếp trên TikTok (chu kỳ 30 ngày).",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color="#64748b",
            anchor="w",
        )
        self.lbl_handle_info.pack(fill="x", padx=14, pady=(0, 10))

        # --- CARD 3: Bio ---
        self.card_bio = ctk.CTkFrame(self.scroll, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        self.card_bio.pack(fill="x", pady=6)

        ctk.CTkLabel(self.card_bio, text="Tiểu sử (Bio):", font=("Segoe UI Semibold", 11), text_color="#64748b").pack(anchor="w", padx=14, pady=(10, 2))
        
        bio_row = ctk.CTkFrame(self.card_bio, fg_color="transparent")
        bio_row.pack(fill="x", padx=14, pady=(0, 10))

        self.lbl_bio = ctk.CTkLabel(bio_row, text="Chưa có tiểu sử.", font=UIThemeTokens.FONT_BODY, text_color="#334155", wraplength=440, justify="left", anchor="w")
        self.lbl_bio.pack(side="left", fill="x", expand=True)

        ctk.CTkButton(
            bio_row,
            text="📋 Copy",
            width=65,
            height=28,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: self._copy_to_clipboard(self._user_info.bio),
        ).pack(side="right", padx=(6, 0))

        # --- CARD 4: Email ---
        self.card_email = ctk.CTkFrame(self.scroll, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        self.card_email.pack(fill="x", pady=6)

        ctk.CTkLabel(self.card_email, text="Email liên kết:", font=("Segoe UI Semibold", 11), text_color="#64748b").pack(anchor="w", padx=14, pady=(10, 2))
        
        email_row = ctk.CTkFrame(self.card_email, fg_color="transparent")
        email_row.pack(fill="x", padx=14, pady=(0, 10))

        self.lbl_email = ctk.CTkLabel(email_row, text="-- chưa gắn email", font=UIThemeTokens.FONT_BODY, text_color="#0f172a", anchor="w")
        self.lbl_email.pack(side="left")

        self.lbl_email_badge = ctk.CTkLabel(email_row, text="", font=UIThemeTokens.FONT_BADGE)
        self.lbl_email_badge.pack(side="left", padx=8)

        ctk.CTkButton(
            email_row,
            text="📋 Copy",
            width=65,
            height=28,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: self._copy_to_clipboard(self._user_info.email or ""),
        ).pack(side="right")

        # --- MINI GRID (3 Ô: Ngày tạo, SĐT, Quốc gia) ---
        grid_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        grid_frame.pack(fill="x", pady=6)
        grid_frame.grid_columnconfigure((0, 1, 2), weight=1)

        # Ô 1: Ngày tạo
        cell1 = ctk.CTkFrame(grid_frame, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        cell1.grid(row=0, column=0, sticky="ew", padx=(0, 4), pady=0)
        ctk.CTkLabel(cell1, text="📅 Ngày Tạo", font=UIThemeTokens.FONT_SUBTITLE, text_color="#64748b").pack(padx=8, pady=(8, 2))
        self.lbl_created = ctk.CTkLabel(cell1, text="--", font=("Segoe UI Bold", 11), text_color="#0f172a")
        self.lbl_created.pack(padx=8, pady=(0, 8))

        # Ô 2: SĐT
        cell2 = ctk.CTkFrame(grid_frame, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        cell2.grid(row=0, column=1, sticky="ew", padx=2, pady=0)
        ctk.CTkLabel(cell2, text="📱 Số Điện Thoại", font=UIThemeTokens.FONT_SUBTITLE, text_color="#64748b").pack(padx=8, pady=(8, 2))
        self.lbl_phone = ctk.CTkLabel(cell2, text="--", font=("Segoe UI Bold", 11), text_color="#0f172a")
        self.lbl_phone.pack(padx=8, pady=(0, 8))

        # Ô 3: Quốc gia
        cell3 = ctk.CTkFrame(grid_frame, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        cell3.grid(row=0, column=2, sticky="ew", padx=(4, 0), pady=0)
        ctk.CTkLabel(cell3, text="🌍 Quốc Gia / Vùng", font=UIThemeTokens.FONT_SUBTITLE, text_color="#64748b").pack(padx=8, pady=(8, 2))
        self.lbl_country = ctk.CTkLabel(cell3, text="--", font=("Segoe UI Bold", 11), text_color="#0f172a")
        self.lbl_country.pack(padx=8, pady=(0, 8))

        # --- CARD 5: UID ---
        self.card_uid = ctk.CTkFrame(self.scroll, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        self.card_uid.pack(fill="x", pady=6)

        ctk.CTkLabel(self.card_uid, text="TikTok User ID (UID):", font=("Segoe UI Semibold", 11), text_color="#64748b").pack(anchor="w", padx=14, pady=(10, 2))
        
        uid_row = ctk.CTkFrame(self.card_uid, fg_color="transparent")
        uid_row.pack(fill="x", padx=14, pady=(0, 10))

        self.lbl_uid = ctk.CTkLabel(uid_row, text="--", font=("Consolas", 12, "bold"), text_color="#0f172a", anchor="w")
        self.lbl_uid.pack(side="left")

        ctk.CTkButton(
            uid_row,
            text="📋 Copy UID",
            width=85,
            height=28,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: self._copy_to_clipboard(self.lbl_uid.cget("text")),
        ).pack(side="right")

        # Footer Status bar
        self.lbl_status = ctk.CTkLabel(self, text="Sẵn sàng.", font=UIThemeTokens.FONT_SUBTITLE, text_color="#64748b")
        self.lbl_status.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))

    def refresh_data(self) -> None:
        """Tải dữ liệu tài khoản từ background thread."""
        if self._is_loading:
            return
        self._is_loading = True
        self.btn_refresh.configure(state="disabled")
        self.lbl_status.configure(text="⏳ Đang kết nối lấy thông tin tài khoản TikTok...")

        def _worker():
            ok, user_info, msg = self.client.check_user()

            def _update_ui():
                if not self.winfo_exists():
                    return
                self._is_loading = False
                self.btn_refresh.configure(state="normal")
                self._user_info = user_info

                if ok:
                    self.lbl_status.configure(text=f"✅ {msg}", text_color="#16a34a")
                    self._render_user_info(user_info)
                else:
                    self.lbl_status.configure(text=f"⚠️ {msg}", text_color="#ef4444")

            try:
                self.after(0, _update_ui)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _render_user_info(self, u: UserInfo) -> None:
        """Render các Card thông tin lên giao diện."""
        # Card 1: Nickname
        self.entry_nickname.delete(0, "end")
        self.entry_nickname.insert(0, u.nickname or u.username)

        cooldown = u.nickname_cooldown_days
        if cooldown > 0:
            self.lbl_nick_cooldown.configure(
                text=f"⚠️ Vừa đổi tên gần đây — còn khoảng {cooldown} ngày nữa mới được đổi lại.",
                text_color="#ef4444",
            )
        else:
            self.lbl_nick_cooldown.configure(
                text="✅ Đã đủ điều kiện đổi tên hiển thị (Chu kỳ 7 ngày).",
                text_color="#16a34a",
            )
        self._check_nickname_button_state()

        # Card 2: @handle
        self.lbl_handle.configure(text=f"@{u.username}" if u.username else "@--")

        # Card 3: Bio
        self.lbl_bio.configure(text=u.bio if u.bio else "Chưa có tiểu sử.")

        # Card 4: Email
        if u.email:
            self.lbl_email.configure(text=u.email)
            if u.is_email_verified:
                self.lbl_email_badge.configure(text="[Đã xác minh]", text_color="#16a34a")
            else:
                self.lbl_email_badge.configure(text="[Chưa xác minh]", text_color="#f59e0b")
        else:
            self.lbl_email.configure(text="— chưa gắn email")
            self.lbl_email_badge.configure(text="")

        # Mini Grid
        if u.user_create_time:
            dt = datetime.fromtimestamp(u.user_create_time)
            self.lbl_created.configure(text=dt.strftime("%d/%m/%Y"))
        else:
            self.lbl_created.configure(text="Không rõ")

        self.lbl_phone.configure(text="Đã gắn" if (u.phone_collected or u.mobile) else "Chưa gắn")
        self.lbl_country.configure(text=u.store_country or "US")

        # Card 5: UID
        self.lbl_uid.configure(text=u.uid or "--")

    def _check_nickname_button_state(self) -> None:
        """Kích hoạt nút Đổi tên khi đủ điều kiện."""
        current_input = self.entry_nickname.get().strip()
        original = self._user_info.nickname or self._user_info.username
        cooldown = self._user_info.nickname_cooldown_days

        if (
            not self._is_updating_name
            and cooldown == 0
            and current_input
            and current_input != original
        ):
            self.btn_change_nick.configure(state="normal")
        else:
            self.btn_change_nick.configure(state="disabled")

    def _on_change_nickname_clicked(self) -> None:
        """Xử lý sự kiện bấm Đổi Tên."""
        new_name = self.entry_nickname.get().strip()
        if not new_name:
            return

        if not messagebox.askyesno(
            "Xác nhận đổi tên",
            f"Bạn có chắc chắn muốn đổi Tên hiển thị (Nickname) thành:\n\n\"{new_name}\"\n\n"
            f"Lưu ý: TikTok chỉ cho phép đổi 1 lần mỗi 7 ngày!",
        ):
            return

        self._is_updating_name = True
        self.btn_change_nick.configure(state="disabled")
        self.lbl_status.configure(text="⏳ Đang gửi yêu cầu đổi tên lên TikTok...", text_color="#2563eb")

        def _worker():
            ok, msg = self.client.set_nickname(new_name)

            def _update_ui():
                if not self.winfo_exists():
                    return
                self._is_updating_name = False
                if ok:
                    messagebox.showinfo("Thành công", msg)
                    self.refresh_data()
                else:
                    messagebox.showerror("Thất bại", msg)
                    self._check_nickname_button_state()

            try:
                self.after(0, _update_ui)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _copy_to_clipboard(self, text: str) -> None:
        """Sao chép chuỗi vào Clipboard kèm phản hồi thông báo."""
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.lbl_status.configure(text=f"📋 Đã sao chép: {text[:40]}", text_color="#0284c7")
        except Exception:
            pass
