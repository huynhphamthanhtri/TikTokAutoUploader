# -*- coding: utf-8 -*-
"""
check_features.ui.summary_dialog - Hộp thoại hiển thị kết quả kiểm tra thông tin hàng loạt.
Liệt kê chi tiết các profile thất bại (Chưa login, Hết hạn cookie 20001, Lỗi Proxy, Lỗi mạng)
kèm gợi ý khắc phục và hỗ trợ chọn nhanh trên bảng chính.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional
import customtkinter as ctk

from ui_components import UIThemeTokens


def sanitize_secret_text(text: str) -> str:
    """Loại bỏ thông tin nhạy cảm (proxy credentials, sessionid) trước khi hiển thị hoặc sao chép."""
    if not text:
        return ""
    # Ẩn credentials user:pass trong proxy URL (vd: http://user:pass@host:port -> http://***:***@host:port)
    cleaned = re.sub(r'://([^:]+):([^@]+)@', r'://***:***@', str(text))
    # Ẩn sessionid (vd: sessionid=abcdef123 -> sessionid=***)
    cleaned = re.sub(r'(sessionid=)[^;\s&]+', r'\1***', cleaned, flags=re.IGNORECASE)
    return cleaned


class CheckInfoSummaryDialog(ctk.CTkToplevel):
    """
    Hộp thoại báo cáo chi tiết đợt kiểm tra thông tin.
    Cung cấp:
    - Thẻ thống kê tổng quan (Thành công, Chưa login, Cần login lại, Lỗi Proxy, Lỗi khác).
    - Danh sách cuộn từng profile cần xử lý kèm nguyên nhân cụ thể và hướng dẫn.
    - Nút Copy danh sách lỗi vào Clipboard (đã sanitize bảo mật).
    - Nút Tự động chọn các profile lỗi trên bảng quản lý chính.
    """

    def __init__(
        self,
        parent,
        summary: Dict[str, Any],
        on_select_profiles: Optional[Callable[[List[str]], None]] = None,
        *args,
        **kwargs,
    ):
        super().__init__(parent, *args, **kwargs)
        self.summary = summary or {}
        self.on_select_profiles = on_select_profiles

        self.title("Báo Cáo Kiểm Tra Thông Tin Profile")
        self.geometry("780x560")
        self.minsize(680, 460)

        try:
            self.transient(parent)
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # 1. Header & Statistics Bar
        header_frame = ctk.CTkFrame(
            self,
            fg_color="#ffffff",
            corner_radius=8,
            border_width=1,
            border_color="#e2e8f0",
        )
        header_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))

        tot = self.summary.get("total", 0)
        suc = self.summary.get("success", 0) + self.summary.get("partial", 0)
        not_logged = self.summary.get("not_logged_in", 0)
        expired = self.summary.get("expired", 0)
        proxy_err = self.summary.get("proxy_error", 0)
        other_err = self.summary.get("error", 0)
        failed_items: List[Dict[str, Any]] = self.summary.get("failed_items", [])

        # Title Label
        ctk.CTkLabel(
            header_frame,
            text=f"📊 Tổng kết kiểm tra {tot} profile",
            font=("Segoe UI Bold", 15),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(side="top", anchor="w", padx=16, pady=(12, 6))

        # Stat Badges Container
        badge_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        badge_box.pack(side="top", fill="x", padx=16, pady=(0, 12))

        def _make_badge(parent_widget, text: str, fg_col: str, txt_col: str):
            f = ctk.CTkFrame(parent_widget, fg_color=fg_col, corner_radius=6)
            f.pack(side="left", padx=(0, 8))
            ctk.CTkLabel(
                f,
                text=text,
                font=("Segoe UI Semibold", 11),
                text_color=txt_col,
            ).pack(padx=10, pady=4)

        _make_badge(badge_box, f"🟢 Thành công: {suc}", "#dcfce7", "#166534")
        if not_logged > 0:
            _make_badge(badge_box, f"⚪ Chưa login: {not_logged}", "#f1f5f9", "#475569")
        if expired > 0:
            _make_badge(badge_box, f"🟠 Cần login lại: {expired}", "#ffedd5", "#c2410c")
        if proxy_err > 0:
            _make_badge(badge_box, f"🔴 Lỗi Proxy: {proxy_err}", "#fee2e2", "#991b1b")
        if other_err > 0:
            _make_badge(badge_box, f"⚠️ Lỗi khác: {other_err}", "#fef3c7", "#92400e")

        # 2. Body: Danh sách kết quả chi tiết
        body_frame = ctk.CTkFrame(
            self,
            fg_color="#ffffff",
            corner_radius=8,
            border_width=1,
            border_color="#e2e8f0",
        )
        body_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=0)
        body_frame.grid_columnconfigure(0, weight=1)
        body_frame.grid_rowconfigure(1, weight=1)

        if not failed_items:
            # Trường hợp 100% thành công
            success_box = ctk.CTkFrame(body_frame, fg_color="transparent")
            success_box.grid(row=0, column=0, rowspan=2, sticky="nsew", pady=40)
            success_box.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                success_box,
                text="🎉",
                font=("Segoe UI", 48),
            ).pack(pady=(20, 10))

            ctk.CTkLabel(
                success_box,
                text="Tất cả các profile đã kiểm tra đều có phiên TikTok hoạt động tốt!",
                font=("Segoe UI Bold", 14),
                text_color="#166534",
            ).pack(pady=(0, 6))

            ctk.CTkLabel(
                success_box,
                text="Không có profile nào cần xử lý lại cookie hay cấu hình mạng.",
                font=("Segoe UI", 12),
                text_color="#64748b",
            ).pack()
        else:
            # Có profile lỗi hoặc cần xử lý
            hdr_list = ctk.CTkFrame(body_frame, fg_color="transparent")
            hdr_list.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
            ctk.CTkLabel(
                hdr_list,
                text=f"⚠️ Danh sách profile cần xử lý ({len(failed_items)} profile):",
                font=("Segoe UI Bold", 12),
                text_color="#b91c1c",
            ).pack(side="left")

            scroll_frame = ctk.CTkScrollableFrame(
                body_frame,
                fg_color="transparent",
            )
            scroll_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
            scroll_frame.grid_columnconfigure(0, weight=1)

            for idx, item in enumerate(failed_items):
                p_card = ctk.CTkFrame(
                    scroll_frame,
                    fg_color="#f8fafc",
                    corner_radius=6,
                    border_width=1,
                    border_color="#e2e8f0",
                )
                p_card.pack(fill="x", pady=(0, 8), padx=4)
                p_card.grid_columnconfigure(1, weight=1)

                # Cột trái: Tên profile & Badge
                top_row = ctk.CTkFrame(p_card, fg_color="transparent")
                top_row.pack(fill="x", padx=12, pady=(8, 4))

                p_name = item.get("profile", "Unknown")
                ctk.CTkLabel(
                    top_row,
                    text=f"{idx + 1}. {p_name}",
                    font=("Segoe UI Bold", 12),
                    text_color=UIThemeTokens.TEXT_PRIMARY,
                ).pack(side="left")

                badge_txt = item.get("status_badge", "Lỗi")
                status = item.get("status", "")
                badge_bg = "#fef3c7"
                badge_fg = "#92400e"
                if status == "NOT_LOGGED_IN":
                    badge_bg = "#f1f5f9"
                    badge_fg = "#475569"
                elif status in ("SESSION_INVALID", "COOKIE_EXPIRED"):
                    badge_bg = "#ffedd5"
                    badge_fg = "#c2410c"
                elif status == "PROXY_ERROR":
                    badge_bg = "#fee2e2"
                    badge_fg = "#991b1b"

                badge_f = ctk.CTkFrame(top_row, fg_color=badge_bg, corner_radius=4)
                badge_f.pack(side="right")
                ctk.CTkLabel(
                    badge_f,
                    text=badge_txt,
                    font=("Segoe UI Semibold", 10),
                    text_color=badge_fg,
                ).pack(padx=8, pady=2)

                # Dòng nguyên nhân lỗi (đã sanitize)
                err_msg = sanitize_secret_text(item.get("error", "Lỗi không xác định"))
                err_row = ctk.CTkFrame(p_card, fg_color="transparent")
                err_row.pack(fill="x", padx=12, pady=(0, 2))
                ctk.CTkLabel(
                    err_row,
                    text=f"• Nguyên nhân: {err_msg}",
                    font=("Segoe UI", 11),
                    text_color="#dc2626" if "PROXY" in status else "#475569",
                    wraplength=640,
                    justify="left",
                ).pack(side="left", anchor="w")

                # Dòng gợi ý xử lý
                suggest = sanitize_secret_text(item.get("suggested_action", ""))
                if suggest:
                    sug_row = ctk.CTkFrame(p_card, fg_color="transparent")
                    sug_row.pack(fill="x", padx=12, pady=(0, 8))
                    ctk.CTkLabel(
                        sug_row,
                        text=f"👉 Khắc phục: {suggest}",
                        font=("Segoe UI Italic", 11),
                        text_color="#2563eb",
                        wraplength=640,
                        justify="left",
                    ).pack(side="left", anchor="w")

        # 3. Bottom Action Bar
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=12)
        action_bar.grid_columnconfigure(0, weight=1)

        left_btn_box = ctk.CTkFrame(action_bar, fg_color="transparent")
        left_btn_box.grid(row=0, column=0, sticky="w")

        if failed_items:
            self.btn_copy = ctk.CTkButton(
                left_btn_box,
                text="📋 Copy Danh Sách Lỗi",
                font=("Segoe UI Semibold", 11),
                width=150,
                height=32,
                fg_color="#64748b",
                hover_color="#475569",
                command=self._copy_error_report,
            )
            self.btn_copy.pack(side="left", padx=(0, 8))

            if self.on_select_profiles:
                self.btn_select_table = ctk.CTkButton(
                    left_btn_box,
                    text=f"🎯 Chọn {len(failed_items)} Profile Trên Bảng",
                    font=("Segoe UI Semibold", 11),
                    width=180,
                    height=32,
                    fg_color="#ea580c",
                    hover_color="#c2410c",
                    command=self._trigger_select_on_table,
                )
                self.btn_select_table.pack(side="left")

        btn_close = ctk.CTkButton(
            action_bar,
            text="Đóng",
            font=("Segoe UI Semibold", 11),
            width=90,
            height=32,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self.destroy,
        )
        btn_close.grid(row=0, column=1, sticky="e")

    def _trigger_select_on_table(self) -> None:
        """Kích hoạt callback chọn các profile lỗi trên bảng chính."""
        failed_items = self.summary.get("failed_items", [])
        names = [item["profile"] for item in failed_items if "profile" in item]
        if self.on_select_profiles and names:
            self.on_select_profiles(names)
            if hasattr(self, "btn_select_table"):
                self.btn_select_table.configure(text="✅ Đã Chọn Trên Bảng!")
                self.after(2000, lambda: self.btn_select_table.configure(text=f"🎯 Chọn {len(failed_items)} Profile Trên Bảng"))

    def _copy_error_report(self) -> None:
        """Sao chép báo cáo chi tiết vào clipboard (đã làm sạch thông tin nhạy cảm)."""
        tot = self.summary.get("total", 0)
        suc = self.summary.get("success", 0) + self.summary.get("partial", 0)
        failed_items = self.summary.get("failed_items", [])

        lines = [
            f"=== BÁO CÁO KIỂM TRA THÔNG TIN TIKTOK ===",
            f"Tổng số profile: {tot}",
            f"Thành công: {suc}",
            f"Cần xử lý: {len(failed_items)}",
            "",
            "CHI TIẾT PROFILE CẦN XỬ LÝ:",
        ]

        for idx, item in enumerate(failed_items, 1):
            p_name = item.get("profile", "Unknown")
            status_badge = item.get("status_badge", "")
            err = sanitize_secret_text(item.get("error", ""))
            suggest = sanitize_secret_text(item.get("suggested_action", ""))
            lines.append(f"{idx}. [{p_name}] {status_badge}")
            lines.append(f"   - Lỗi: {err}")
            if suggest:
                lines.append(f"   - Khắc phục: {suggest}")
            lines.append("")

        report_text = "\n".join(lines)
        try:
            self.clipboard_clear()
            self.clipboard_append(report_text)
            if hasattr(self, "btn_copy"):
                self.btn_copy.configure(text="✅ Đã Copy Báo Cáo!")
                self.after(2000, lambda: self.btn_copy.configure(text="📋 Copy Danh Sách Lỗi"))
        except Exception:
            pass
