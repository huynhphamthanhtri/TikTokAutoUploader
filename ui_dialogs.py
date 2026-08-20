"""
ui_dialogs.py - Bộ Hộp Thoại (Modal Dialogs) Chuẩn Hóa cho VIBE_AUTO_UPLOAD-LP.

Bao gồm:
1. BatchSetProxyModal: Gán proxy hàng loạt từ văn bản nhiều dòng, parse HTTP/SOCKS5,
   preview masked password (host:port:user:***), báo dòng lỗi cụ thể, atomic save/rollback.
2. MonetizationDetailModal: Xem chi tiết snapshot tài chính & KYC của một tài khoản.
3. CreateEditProfileModal: Form chia 3 tabs (Cơ bản, Proxy [password mask], Cookie & Session).
"""

from __future__ import annotations

import tkinter as tk
import customtkinter as ctk
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core_helpers import parse_proxy_string
from ui_components import UIThemeTokens, redact_proxy_string, fit_and_center_dialog


# ==============================================================================
# 1. BATCH SET PROXY MODAL
# ==============================================================================

class BatchSetProxyModal(ctk.CTkToplevel):
    """Hộp thoại gán proxy hàng loạt cho các profile được chọn."""

    def __init__(
        self,
        parent: Any,
        selected_profiles: List[str],
        on_save: Callable[[Dict[str, Dict[str, Any]]], bool],
        proxy_type_default: str = "http",
    ):
        super().__init__(parent)
        self.title("Gán Proxy Hàng Loạt")
        fit_and_center_dialog(self, 580, 520, parent=parent, min_w=480, min_h=380)
        self.transient(parent)
        self.grab_set()

        self.selected_profiles = selected_profiles
        self.on_save = on_save
        self._parsed_mapping: Dict[str, Dict[str, Any]] = {}

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=14)

        # Header info
        ctk.CTkLabel(
            container,
            text=f"🌐 Gán Proxy Cho {len(selected_profiles)} Profile Đã Chọn",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        ).pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            container,
            text="Nhập danh sách proxy (mỗi dòng một proxy dạng IP:Port hoặc IP:Port:User:Pass):",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        # Proxy Type selector
        type_row = ctk.CTkFrame(container, fg_color="transparent")
        type_row.pack(fill="x", pady=(0, 6))

        ctk.CTkLabel(type_row, text="Loại Proxy:", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="left", padx=(0, 8))
        self.proxy_type_var = ctk.StringVar(value=proxy_type_default.lower())
        self.type_menu = ctk.CTkOptionMenu(
            type_row,
            values=["http", "socks5"],
            variable=self.proxy_type_var,
            width=100,
            height=28,
            font=UIThemeTokens.FONT_BODY,
        )
        self.type_menu.pack(side="left")

        # Text input area
        self.text_input = ctk.CTkTextbox(container, height=140, font=("Consolas", 10))
        self.text_input.pack(fill="both", expand=True, pady=(4, 8))
        self.text_input.bind("<KeyRelease>", lambda e: self._update_preview())

        # Error / Status label
        self.status_label = ctk.CTkLabel(
            container,
            text="",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.STATUS_ERROR,
            anchor="w",
        )
        self.status_label.pack(fill="x", pady=(0, 4))

        # Preview list area
        ctk.CTkLabel(
            container,
            text="Bản Xem Trước Phân Bổ (Mật khẩu được che giấu):",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        ).pack(fill="x", pady=(4, 2))

        self.preview_box = ctk.CTkTextbox(container, height=110, font=("Consolas", 9), state="disabled", fg_color="#f8fafc")
        self.preview_box.pack(fill="both", expand=True, pady=(0, 10))

        # Action Buttons Row
        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill="x", side="bottom")

        ctk.CTkButton(
            btn_row,
            text="Hủy Bỏ",
            width=90,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color="#64748b",
            hover_color="#475569",
            command=self.destroy,
        ).pack(side="right", padx=(6, 0))

        self.btn_save = ctk.CTkButton(
            btn_row,
            text="Áp Dụng & Lưu",
            width=120,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._apply_save,
        )
        self.btn_save.pack(side="right")

    def _parse_lines(self) -> Tuple[List[str], List[int]]:
        raw_text = self.text_input.get("1.0", "end").strip()
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        valid_proxies = []
        invalid_line_indices = []

        for idx, line in enumerate(lines, start=1):
            parsed = parse_proxy_string(line)
            if parsed and parsed.get("ip") and parsed.get("port"):
                valid_proxies.append(line)
            else:
                invalid_line_indices.append(idx)

        return valid_proxies, invalid_line_indices

    def _update_preview(self) -> None:
        valid_proxies, invalid_lines = self._parse_lines()
        self._parsed_mapping.clear()

        if invalid_lines:
            self.status_label.configure(
                text=f"⚠️ Dòng không hợp lệ: {', '.join(str(i) for i in invalid_lines[:5])}",
                text_color=UIThemeTokens.STATUS_ERROR,
            )
        elif not valid_proxies:
            self.status_label.configure(
                text="Chưa có proxy hợp lệ nào được nhập.",
                text_color=UIThemeTokens.TEXT_MUTED,
            )
        else:
            self.status_label.configure(
                text=f"✔️ Đã nhận diện {len(valid_proxies)} proxy hợp lệ.",
                text_color=UIThemeTokens.STATUS_LIVE,
            )

        preview_lines = []
        p_type = self.proxy_type_var.get().lower()

        for idx, prof_name in enumerate(self.selected_profiles):
            if valid_proxies:
                chosen_proxy = valid_proxies[idx % len(valid_proxies)]
                masked = redact_proxy_string(chosen_proxy)
                preview_lines.append(f"[{prof_name}] -> {p_type}://{masked}")
                self._parsed_mapping[prof_name] = {
                    "use_proxy": True,
                    "proxy_type": p_type,
                    "proxy_string": chosen_proxy,
                }
            else:
                preview_lines.append(f"[{prof_name}] -> (Chưa có proxy)")

        self.preview_box.configure(state="normal")
        self.preview_box.delete("1.0", "end")
        self.preview_box.insert("1.0", "\n".join(preview_lines))
        self.preview_box.configure(state="disabled")

    def _apply_save(self) -> None:
        self._update_preview()
        if not self._parsed_mapping:
            self.status_label.configure(text="Vui lòng nhập ít nhất 1 proxy hợp lệ trước khi lưu.")
            return

        success = self.on_save(self._parsed_mapping)
        if success:
            self.destroy()
        else:
            self.status_label.configure(text="❌ Lỗi khi lưu cấu hình proxy. Đã tự động rollback.")


# ==============================================================================
# 2. MONETIZATION DETAIL MODAL
# ==============================================================================

class MonetizationDetailModal(ctk.CTkToplevel):
    """Xem thông tin chi tiết snapshot tài chính của 1 profile (học hỏi từ PayoutDialog)."""

    def __init__(self, parent: Any, profile_name: str, snapshot_data: Dict[str, Any]):
        super().__init__(parent)
        self.title(f"Chi Tiết Thu Nhập & Payout: {profile_name}")
        fit_and_center_dialog(self, 640, 680, parent=parent, min_w=520, min_h=450)
        self.transient(parent)
        self.grab_set()

        self.profile_name = profile_name
        self.data = snapshot_data or {}

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=14, pady=12)

        # Header Title
        head = ctk.CTkFrame(container, fg_color="transparent")
        head.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            head,
            text=f"💰 Payout & Thu Nhập: {profile_name}",
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        ).pack(side="left")

        reg = str(self.data.get("region", "US"))
        ctk.CTkLabel(
            head,
            text=f"Vùng: {reg}",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(side="right")

        # Scrollable content area
        scroll = ctk.CTkScrollableFrame(container, fg_color="transparent")
        scroll.pack(fill="both", expand=True, pady=(0, 8))

        # Banner alert if Cookie Die / Error
        if self.data.get("status") == "COOKIE_EXPIRED" or "cookie die" in str(self.data.get("payout_status", "")).lower():
            err_box = ctk.CTkFrame(scroll, corner_radius=8, fg_color="#fef2f2", border_width=1, border_color="#f87171")
            err_box.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(
                err_box,
                text="🔴 CẢNH BÁO: Cookie phiên làm việc đã hết hạn (Cookie Die)!\nKhông thể kết nối máy chủ TikTok. Vui lòng mở trình duyệt và đăng nhập lại TikTok Studio.",
                font=UIThemeTokens.FONT_BODY,
                text_color="#b91c1c",
                justify="left",
            ).pack(padx=12, pady=8, anchor="w")

        # 1. Hero Card: Balance & Next Payout Date
        hero = ctk.CTkFrame(scroll, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        hero.pack(fill="x", pady=(0, 8))

        hero_inner = ctk.CTkFrame(hero, fg_color="transparent")
        hero_inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(hero_inner, text="Số Dư Khả Dụng (Estimated Amount):", font=UIThemeTokens.FONT_SUBTITLE, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")

        bal_val = float(self.data.get("balance", 0.0) or 0.0)
        sym = self.data.get("currency_symbol", "$")
        ctk.CTkLabel(hero_inner, text=f"{sym}{bal_val:,.2f}", font=("Segoe UI Semibold", 24), text_color=UIThemeTokens.STATUS_LIVE).pack(anchor="w", pady=(2, 4))

        meta_row = ctk.CTkFrame(hero_inner, fg_color="transparent")
        meta_row.pack(fill="x")
        next_date = self.data.get("next_payout_date", "N/A")
        rew_est = self.data.get("rewards_estimated", "$0.00")
        ctk.CTkLabel(meta_row, text=f"📅 Ngày Payout kế tiếp: {next_date}", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left")
        ctk.CTkLabel(meta_row, text=f"🎁 Quỹ tác giả: {rew_est}", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_MUTED).pack(side="right")

        # 2. Quỹ Tác Giả & Tình Trạng Kiếm Tiền (CRP)
        sec_crp = ctk.CTkFrame(scroll, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        sec_crp.pack(fill="x", pady=(0, 8))

        crp_inner = ctk.CTkFrame(sec_crp, fg_color="transparent")
        crp_inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(crp_inner, text="🌟 Quỹ Tác Giả & Tình Trạng Kiếm Tiền (Creator Rewards - CRP):", font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))

        crp_st = self.data.get("crp_status", "NOT_STARTED")
        crp_display = self.data.get("crp_display", "Chưa kiểm tra")
        f_cnt = int(self.data.get("crp_followers", 0) or 0)
        f_th = int(self.data.get("crp_followers_threshold", 10000) or 10000)
        v_cnt = int(self.data.get("crp_views", 0) or 0)
        v_th = int(self.data.get("crp_views_threshold", 100000) or 100000)
        all_met = bool(self.data.get("crp_all_met", False))

        f_icon = "✅ Đạt" if f_cnt >= f_th > 0 else "❌ Chưa đủ"
        v_icon = "✅ Đạt" if v_cnt >= v_th > 0 else "❌ Chưa đủ"
        can_apply_str = "🟢 Đủ điều kiện (Có thể gửi đơn)" if all_met else "Chưa đủ điều kiện"

        st_label = crp_display
        if crp_st == "TKTBM":
            st_label = "🔴 TKTBM (Tắt Kiếm Tiền Bảo Mật)"

        crp_rows = [
            ("Tình Trạng Hiện Tại:", st_label),
            ("Số Follower Hợp Lệ:", f"{f_cnt:,} / {f_th:,} ({f_icon})"),
            ("Số View Hợp Lệ (30 ngày):", f"{v_cnt:,} / {v_th:,} ({v_icon})"),
            ("Có Thể Gửi Đơn Duyệt:", can_apply_str),
        ]

        if self.data.get("crp_punishment"):
            crp_rows.append(("Tiêu Đề Vi Phạm:", str(self.data.get("crp_punishment"))))

        if self.data.get("crp_reapply_date"):
            crp_rows.append(("Ngày Được Đăng Ký Lại:", str(self.data.get("crp_reapply_date"))))

        rpm_val = float(self.data.get("crp_rpm", 0.0) or 0.0)
        qviews_val = int(self.data.get("crp_qualified_views", 0) or 0)
        if rpm_val > 0 or qviews_val > 0:
            crp_rows.append(("Chỉ Số RPM:", f"${rpm_val:.2f}"))
            crp_rows.append(("Lượt Xem Tính Tiền:", f"{qviews_val:,}"))

        for label, val in crp_rows:
            r = ctk.CTkFrame(crp_inner, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkLabel(r, text=label, font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_MUTED, width=195, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=str(val), font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY, anchor="w").pack(side="left", fill="x", expand=True)

        # Punishment description alert box
        p_desc = self.data.get("crp_punishment_desc")
        if p_desc:
            p_box = ctk.CTkFrame(crp_inner, fg_color="#fff1f2", corner_radius=6, border_width=1, border_color="#fda4af")
            p_box.pack(fill="x", pady=(6, 2))
            ctk.CTkLabel(
                p_box,
                text=f"⚠️ Chi Tiết Vi Phạm:\n{p_desc}",
                font=UIThemeTokens.FONT_BODY,
                text_color="#9f1239",
                justify="left",
                wraplength=560,
            ).pack(padx=10, pady=6, anchor="w")

        # 3. Phương Thức Thanh Toán (PTTT) & Khai Báo Thuế (Tax)
        sec_pay_tax = ctk.CTkFrame(scroll, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        sec_pay_tax.pack(fill="x", pady=(0, 8))

        pt_inner = ctk.CTkFrame(sec_pay_tax, fg_color="transparent")
        pt_inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(pt_inner, text="💳 Thanh Toán & Khai Báo Thuế (Payout & Tax Compliance):", font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))

        tax_st = str(self.data.get("tax_status", "NOT_STARTED"))
        if tax_st in ("TAX_VERIFIED", "APPROVED"):
            tax_label = "🟢 ĐÃ KHAI THUẾ (Tax Verified)"
        elif tax_st in ("TAX_PENDING", "PENDING"):
            tax_label = "🟡 ĐANG XÉT DUYỆT THUẾ"
        elif tax_st == "Cookie Die":
            tax_label = "🔴 Cookie Die"
        else:
            tax_label = "⚪ CHƯA KHAI THUẾ"

        p_st = str(self.data.get("payout_status", "N/A"))
        if p_st == "PAYOUT_READY":
            p_label = "🟢 SẴN SÀNG (PAYOUT READY)"
        elif p_st == "PAYOUT_PENDING":
            p_label = "🟡 ĐANG XÁC MINH (PENDING)"
        elif p_st == "PAYOUT_NOT_LINKED":
            p_label = "⚪ CHƯA LIÊN KẾT (NOT LINKED)"
        elif p_st == "Cookie Die":
            p_label = "🔴 Cookie Die"
        else:
            p_label = p_st

        pay_rows = [
            ("Trạng Thái Payout:", p_label),
            ("Phương Thức Thanh Toán:", str(self.data.get("payment_method", "N/A"))),
            ("Khai Báo Thuế (Tax):", tax_label),
            ("Cập Nhật Lần Cuối:", str(self.data.get("checked_at", "N/A"))),
        ]

        for label, val in pay_rows:
            r = ctk.CTkFrame(pt_inner, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkLabel(r, text=label, font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_MUTED, width=195, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=str(val), font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY, anchor="w").pack(side="left", fill="x", expand=True)

        # 4. Xác Minh Danh Tính (KYC Identity Compliance)
        sec_kyc = ctk.CTkFrame(scroll, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
        sec_kyc.pack(fill="x", pady=(0, 8))

        kyc_inner = ctk.CTkFrame(sec_kyc, fg_color="transparent")
        kyc_inner.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(kyc_inner, text="🪪 Xác Minh Danh Tính (KYC Identity):", font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))

        k_st = str(self.data.get("kyc_status", "NOT_STARTED"))
        if k_st == "APPROVED":
            k_display_str = "🟢 ĐÃ KYC (APPROVED)"
        elif k_st == "PENDING":
            k_display_str = "🟡 ĐANG CHỜ DUYỆT (In Review)"
        elif k_st == "RESUBMIT":
            k_display_str = "🔴 CẦN NỘP LẠI (Lỗi Giấy Tờ / POA)"
        elif k_st == "WARNING":
            k_display_str = "🟠 CẦN KIỂM TRA (Warning)"
        elif k_st == "REJECTED":
            k_display_str = "🔴 BỊ TỪ CHỐI"
        elif k_st == "Cookie Die":
            k_display_str = "🔴 Cookie Die"
        else:
            k_display_str = "⚪ CHƯA KYC"

        kyc_rows = [
            ("Trạng Thái KYC:", k_display_str),
        ]

        if self.data.get("kyc_full_name"):
            kyc_rows.append(("Họ Tên Trên Giấy Tờ:", str(self.data.get("kyc_full_name"))))

        if self.data.get("kyc_id_type"):
            id_t = str(self.data.get("kyc_id_type"))
            id_c = str(self.data.get("kyc_id_country", ""))
            id_str = f"{id_t} (Quốc gia: {id_c})" if id_c else id_t
            kyc_rows.append(("Loại Giấy Tờ:", id_str))

        if self.data.get("kyc_birthday") and str(self.data.get("kyc_birthday")) != "0001-01-01":
            kyc_rows.append(("Ngày Sinh:", str(self.data.get("kyc_birthday"))))

        if self.data.get("unique_id"):
            kyc_rows.append(("TikTok Username (@):", f"@{self.data.get('unique_id')}"))

        if self.data.get("tiktok_user_id"):
            kyc_rows.append(("TikTok User ID (UID):", str(self.data.get("tiktok_user_id"))))

        for label, val in kyc_rows:
            r = ctk.CTkFrame(kyc_inner, fg_color="transparent")
            r.pack(fill="x", pady=1)
            ctk.CTkLabel(r, text=label, font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_MUTED, width=195, anchor="w").pack(side="left")
            ctk.CTkLabel(r, text=str(val), font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY, anchor="w").pack(side="left", fill="x", expand=True)

        # 4. Pending Earnings (Nếu có)
        pending_list = self.data.get("pending_earnings", [])
        if pending_list:
            sec_pending = ctk.CTkFrame(scroll, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
            sec_pending.pack(fill="x", pady=(0, 8))

            p_inner = ctk.CTkFrame(sec_pending, fg_color="transparent")
            p_inner.pack(fill="x", padx=14, pady=10)

            ctk.CTkLabel(p_inner, text="⏳ Các Khoản Chờ Về Ví (Pending Earnings):", font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))

            for p_item in pending_list:
                p_row = ctk.CTkFrame(p_inner, fg_color="#f8fafc", corner_radius=6)
                p_row.pack(fill="x", pady=2, padx=2)
                p_left = ctk.CTkFrame(p_row, fg_color="transparent")
                p_left.pack(side="left", padx=8, pady=4)
                ctk.CTkLabel(p_left, text=f"{p_item.get('title', 'Đợt Payout')} ({p_item.get('date', '')})", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w")
                if p_item.get("bill_id"):
                    ctk.CTkLabel(p_left, text=f"Bill ID: #{p_item.get('bill_id')}", font=UIThemeTokens.FONT_BADGE, text_color=UIThemeTokens.TEXT_MUTED).pack(anchor="w")
                
                ctk.CTkLabel(p_row, text=str(p_item.get("amount", "")), font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.STATUS_LIVE).pack(side="right", padx=10)

        # 4. Payout Breakdown (Nếu có)
        breakdown_list = self.data.get("payout_breakdown", [])
        if breakdown_list:
            sec_breakdown = ctk.CTkFrame(scroll, corner_radius=10, fg_color=UIThemeTokens.BG_CARD, border_width=1, border_color=UIThemeTokens.BORDER_LIGHT)
            sec_breakdown.pack(fill="x", pady=(0, 8))

            b_inner = ctk.CTkFrame(sec_breakdown, fg_color="transparent")
            b_inner.pack(fill="x", padx=14, pady=10)

            ctk.CTkLabel(b_inner, text="📊 Phân Bổ Nguồn Doanh Thu (Breakdown):", font=UIThemeTokens.FONT_BUTTON, text_color=UIThemeTokens.TEXT_PRIMARY).pack(anchor="w", pady=(0, 4))

            for b_item in breakdown_list:
                b_row = ctk.CTkFrame(b_inner, fg_color="transparent")
                b_row.pack(fill="x", pady=1)
                ctk.CTkLabel(b_row, text=f"• {b_item.get('title', '')}", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_MUTED).pack(side="left")
                ctk.CTkLabel(b_row, text=str(b_item.get("amount", "")), font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.TEXT_PRIMARY).pack(side="right")

        # Bottom Button Row
        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill="x", side="bottom")

        ctk.CTkButton(
            btn_row,
            text="Đóng",
            width=90,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self.destroy,
        ).pack(side="right")


# ==============================================================================
# 3. CREATE / EDIT PROFILE MODAL
# ==============================================================================

class CreateEditProfileModal(ctk.CTkToplevel):
    """Hộp thoại tạo mới hoặc chỉnh sửa hồ sơ với form chia 3 tabs khoa học."""

    def __init__(
        self,
        parent: Any,
        title: str,
        initial_config: Optional[Dict[str, Any]] = None,
        available_projects: Optional[List[str]] = None,
        on_save: Optional[Callable[[Dict[str, Any]], bool]] = None,
        on_test_proxy: Optional[Callable[[str, str], Tuple[bool, str]]] = None,
    ):
        super().__init__(parent)
        self.title(title)
        fit_and_center_dialog(self, 560, 480, parent=parent, min_w=460, min_h=380)
        self.transient(parent)
        self.grab_set()

        self.initial_config = initial_config or {}
        self.on_save = on_save
        self.on_test_proxy = on_test_proxy

        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=14, pady=12)

        # Tabview
        self.tabview = ctk.CTkTabview(container)
        self.tabview.pack(fill="both", expand=True, pady=(0, 10))

        tab_basic = self.tabview.add("1. Cơ Bản")
        tab_proxy = self.tabview.add("2. Proxy & Mạng")
        tab_cookie = self.tabview.add("3. Cookie & Session")

        # --- TAB 1: Cơ Bản ---
        f1 = ctk.CTkFrame(tab_basic, fg_color="transparent")
        f1.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(f1, text="Tên Profile (*):", font=UIThemeTokens.FONT_BODY).pack(anchor="w", pady=(0, 2))
        self.name_var = ctk.StringVar(value=str(self.initial_config.get("profile_name", "")))
        self.name_entry = ctk.CTkEntry(f1, textvariable=self.name_var, height=30)
        self.name_entry.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(f1, text="Dự Án / Nhóm:", font=UIThemeTokens.FONT_BODY).pack(anchor="w", pady=(0, 2))
        projects = available_projects or ["Mặc định"]
        cur_proj = str(self.initial_config.get("project_name", projects[0]))
        self.project_var = ctk.StringVar(value=cur_proj)
        self.project_menu = ctk.CTkOptionMenu(f1, values=projects, variable=self.project_var, height=30)
        self.project_menu.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(f1, text="TikTok Username (@...):", font=UIThemeTokens.FONT_BODY).pack(anchor="w", pady=(0, 2))
        self.tiktok_var = ctk.StringVar(value=str(self.initial_config.get("tiktok_account", "")))
        ctk.CTkEntry(f1, textvariable=self.tiktok_var, height=30).pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(f1, text="Ghi Chú:", font=UIThemeTokens.FONT_BODY).pack(anchor="w", pady=(0, 2))
        self.note_var = ctk.StringVar(value=str(self.initial_config.get("note", "")))
        ctk.CTkEntry(f1, textvariable=self.note_var, height=30).pack(fill="x")

        # --- TAB 2: Proxy & Mạng ---
        f2 = ctk.CTkFrame(tab_proxy, fg_color="transparent")
        f2.pack(fill="both", expand=True, padx=8, pady=8)

        self.use_proxy_var = ctk.BooleanVar(value=bool(self.initial_config.get("use_proxy", False)))
        ctk.CTkCheckBox(f2, text="Kích hoạt sử dụng Proxy cho Profile này", variable=self.use_proxy_var, font=UIThemeTokens.FONT_BODY).pack(anchor="w", pady=(0, 8))

        p_row = ctk.CTkFrame(f2, fg_color="transparent")
        p_row.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(p_row, text="Loại Proxy:", font=UIThemeTokens.FONT_BODY).pack(side="left", padx=(0, 6))
        self.proxy_type_var = ctk.StringVar(value=str(self.initial_config.get("proxy_type", "http")).lower())
        ctk.CTkOptionMenu(p_row, values=["http", "socks5"], variable=self.proxy_type_var, width=100, height=28).pack(side="left")

        ctk.CTkLabel(f2, text="Chuỗi Proxy (IP:Port hoặc IP:Port:User:Pass):", font=UIThemeTokens.FONT_BODY).pack(anchor="w", pady=(4, 2))
        self.proxy_str_var = ctk.StringVar(value=str(self.initial_config.get("proxy_string", "")))
        ctk.CTkEntry(f2, textvariable=self.proxy_str_var, height=30, show="*").pack(fill="x", pady=(0, 8))

        # Test proxy button & status
        test_row = ctk.CTkFrame(f2, fg_color="transparent")
        test_row.pack(fill="x", pady=(4, 0))
        self.test_proxy_btn = ctk.CTkButton(test_row, text="Kiểm Tra Proxy", width=110, height=28, command=self._test_proxy_clicked)
        self.test_proxy_btn.pack(side="left")
        self.proxy_test_status = ctk.CTkLabel(test_row, text="", font=UIThemeTokens.FONT_BODY)
        self.proxy_test_status.pack(side="left", padx=10)

        # --- TAB 3: Cookie & Session ---
        f3 = ctk.CTkFrame(tab_cookie, fg_color="transparent")
        f3.pack(fill="both", expand=True, padx=8, pady=8)

        ctk.CTkLabel(f3, text="TikTok Cookies String / JSON:", font=UIThemeTokens.FONT_BODY).pack(anchor="w", pady=(0, 2))
        self.cookie_text = ctk.CTkTextbox(f3, height=140, font=("Consolas", 9))
        self.cookie_text.pack(fill="both", expand=True, pady=(0, 6))
        if self.initial_config.get("cookie_str"):
            self.cookie_text.insert("1.0", str(self.initial_config.get("cookie_str")))

        # Error label at bottom
        self.error_label = ctk.CTkLabel(container, text="", font=UIThemeTokens.FONT_BODY, text_color=UIThemeTokens.STATUS_ERROR)
        self.error_label.pack(fill="x", pady=(0, 4))

        # Action Buttons
        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill="x", side="bottom")

        ctk.CTkButton(
            btn_row,
            text="Hủy",
            width=80,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color="#64748b",
            hover_color="#475569",
            command=self.destroy,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            btn_row,
            text="Lưu Hồ Sơ",
            width=110,
            height=32,
            font=UIThemeTokens.FONT_BUTTON,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._save_clicked,
        ).pack(side="right")

    def _test_proxy_clicked(self) -> None:
        p_str = self.proxy_str_var.get().strip()
        p_type = self.proxy_type_var.get().strip().lower()
        if not p_str:
            self.proxy_test_status.configure(text="Vui lòng nhập chuỗi proxy", text_color=UIThemeTokens.STATUS_ERROR)
            return

        if self.on_test_proxy:
            self.proxy_test_status.configure(text="Đang kiểm tra...", text_color=UIThemeTokens.TEXT_MUTED)
            ok, msg = self.on_test_proxy(p_str, p_type)
            if ok:
                self.proxy_test_status.configure(text=f"✔️ {msg}", text_color=UIThemeTokens.STATUS_LIVE)
            else:
                self.proxy_test_status.configure(text=f"❌ {msg}", text_color=UIThemeTokens.STATUS_ERROR)
        else:
            self.proxy_test_status.configure(text="Chức năng test proxy sẵn sàng", text_color=UIThemeTokens.STATUS_LIVE)

    def _save_clicked(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            self.error_label.configure(text="Tên profile không được để trống")
            return

        # Prepare new config
        new_config = dict(self.initial_config)
        new_config["profile_name"] = name
        new_config["project_name"] = self.project_var.get().strip()
        new_config["tiktok_account"] = self.tiktok_var.get().strip()
        new_config["note"] = self.note_var.get().strip()
        new_config["use_proxy"] = self.use_proxy_var.get()
        new_config["proxy_type"] = self.proxy_type_var.get().strip().lower()
        new_config["proxy_string"] = self.proxy_str_var.get().strip()
        new_config["cookie_str"] = self.cookie_text.get("1.0", "end").strip()

        if self.on_save:
            success = self.on_save(new_config)
            if success:
                self.destroy()
            else:
                self.error_label.configure(text="Không thể lưu profile. Vui lòng kiểm tra lại dữ liệu.")
        else:
            self.destroy()


# ==============================================================================
# 4. LICENSE MODAL
# ==============================================================================

class LicenseModal(ctk.CTkToplevel):
    """Hộp thoại kích hoạt & quản lý License bản quyền chuẩn Design System."""

    def __init__(
        self,
        parent: Any,
        check_func: Callable[[str], Tuple[bool, Dict[str, Any], str]],
        on_success: Callable[[str, Dict[str, Any]], None],
        initial_key: str = "",
        initial_status: str = "",
        initial_message: str = "",
        is_first_run: bool = True,
        on_close_app: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent)
        self.check_func = check_func
        self.on_success = on_success
        self.is_first_run = is_first_run
        self.on_close_app = on_close_app

        self.title("DONGLAO-TIKTOK — Bản Quyền & Kích Hoạt Phần Mềm")
        fit_and_center_dialog(self, 520, 310, parent=parent, min_w=460, min_h=280)
        self.configure(fg_color=UIThemeTokens.BG_ROOT)
        self.transient(parent)
        self.grab_set()
        self.focus_force()
        self.resizable(False, False)

        self._build_ui(initial_key, initial_status, initial_message)
        self.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.bind("<Return>", lambda _e: self._do_activate())
        self.bind("<Escape>", lambda _e: self._handle_close())

    def _build_ui(self, initial_key: str, initial_status: str, initial_message: str):
        self.card = ctk.CTkFrame(
            self,
            fg_color=UIThemeTokens.BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        self.card.pack(fill="both", expand=True, padx=14, pady=14)

        # Header Title
        lbl_title = ctk.CTkLabel(
            self.card,
            text="🔑 Kích Hoạt Bản Quyền Phần Mềm",
            font=("Segoe UI", 14, "bold"),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        lbl_title.pack(anchor="w", padx=18, pady=(16, 4))

        lbl_desc = ctk.CTkLabel(
            self.card,
            text="Nhập mã License Key được cấp để mở khóa toàn bộ tính năng tự động hóa.",
            font=("Segoe UI", 11),
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        lbl_desc.pack(anchor="w", padx=18, pady=(0, 12))

        # License Key Entry
        self.key_var = ctk.StringVar(value=initial_key)
        self.entry_key = ctk.CTkEntry(
            self.card,
            textvariable=self.key_var,
            placeholder_text="Dán mã License Key (VD: USER-XXXX-YYYY-ZZZZ)",
            height=36,
            corner_radius=8,
            font=("Segoe UI", 12),
        )
        self.entry_key.pack(fill="x", padx=18, pady=(0, 6))
        self.entry_key.focus_set()

        # Status Message Label
        self.msg_var = ctk.StringVar(value=initial_message)
        self.lbl_msg = ctk.CTkLabel(
            self.card,
            textvariable=self.msg_var,
            font=("Segoe UI", 11),
            text_color=UIThemeTokens.STATUS_ERROR if initial_message else UIThemeTokens.TEXT_MUTED,
            wraplength=460,
            justify="left",
        )
        self.lbl_msg.pack(anchor="w", padx=18, pady=(0, 8))

        # Bottom Button Row (Pin to bottom)
        btn_row = ctk.CTkFrame(self.card, fg_color="transparent")
        btn_row.pack(side="bottom", fill="x", padx=18, pady=(0, 16))

        close_text = "Thoát Ứng Dụng" if self.is_first_run else "Đóng"
        self.btn_close = ctk.CTkButton(
            btn_row,
            text=close_text,
            fg_color="#ef4444" if self.is_first_run else UIThemeTokens.BG_HOVER,
            text_color="#ffffff" if self.is_first_run else UIThemeTokens.TEXT_PRIMARY,
            hover_color="#dc2626" if self.is_first_run else UIThemeTokens.BORDER_LIGHT,
            height=32,
            width=110,
            command=self._handle_close,
        )
        self.btn_close.pack(side="left")

        self.btn_activate = ctk.CTkButton(
            btn_row,
            text="⚡ Kích Hoạt Ngay",
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            height=32,
            width=140,
            font=("Segoe UI", 12, "bold"),
            command=self._do_activate,
        )
        self.btn_activate.pack(side="right")

    def _do_activate(self):
        key = self.key_var.get().strip()
        if not key:
            self.lbl_msg.configure(text_color=UIThemeTokens.STATUS_ERROR)
            self.msg_var.set("⚠️ Vui lòng nhập License Key.")
            return

        self.lbl_msg.configure(text_color=UIThemeTokens.TEXT_MUTED)
        self.msg_var.set("⏳ Đang xác thực bản quyền với máy chủ...")
        self.btn_activate.configure(state="disabled")
        self.update()

        try:
            ok, info, msg = self.check_func(key)
            if ok:
                self.lbl_msg.configure(text_color=UIThemeTokens.STATUS_LIVE)
                self.msg_var.set("✅ Kích hoạt bản quyền thành công!")
                self.btn_activate.configure(state="normal")
                if self.on_success:
                    self.on_success(key, info)
                self.after(500, self.destroy)
            else:
                self.lbl_msg.configure(text_color=UIThemeTokens.STATUS_ERROR)
                self.msg_var.set(f"❌ {msg or 'License Key không hợp lệ.'}")
                self.btn_activate.configure(state="normal")
        except Exception as e:
            self.lbl_msg.configure(text_color=UIThemeTokens.STATUS_ERROR)
            self.msg_var.set(f"❌ Lỗi: {e}")
            self.btn_activate.configure(state="normal")

    def _handle_close(self):
        if self.is_first_run and self.on_close_app:
            self.on_close_app()
        else:
            self.destroy()


# ==============================================================================
# 5. SEARCHABLE PROFILE PICKER MODAL
# ==============================================================================

class SearchableProfilePickerModal(ctk.CTkToplevel):
    """
    SearchableProfilePickerModal - Hộp thoại tìm kiếm và gán Profile TikTok cho Kênh YouTube.
    Thiết kế thuần Presentation, lọc O(N) case-insensitive, hỗ trợ điều hướng bàn phím đầy đủ.
    """

    def __init__(
        self,
        parent: Any,
        profiles: Sequence[str],
        current_profile: str = "",
        channel_title: str = "",
        channel_id: str = "",
        on_confirm: Optional[Callable[[str], Tuple[bool, str]]] = None,
        title_text: Optional[str] = None,
        header_text: Optional[str] = None,
        subject_text: Optional[str] = None,
        confirm_text: Optional[str] = None,
        return_focus_to: Optional[Any] = None,
    ):
        toplevel_parent = parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent
        super().__init__(toplevel_parent)
        self.title(title_text or "Chọn Profile Đích Cho Kênh")
        fit_and_center_dialog(self, 480, 540, parent=toplevel_parent, min_w=400, min_h=440)
        self.transient(toplevel_parent)
        self.grab_set()

        # Immutable snapshot per modal lifecycle
        self._all_profiles: List[str] = list(dict.fromkeys(
            str(p).strip() for p in (profiles or []) if str(p).strip()
        ))
        self._filtered_profiles: List[str] = list(self._all_profiles)
        self._iid_to_profile: Dict[str, str] = {}
        self.current_profile: str = str(current_profile or "").strip()
        self._pending_selected_profile: Optional[str] = self.current_profile or None
        self._user_selected_profile: Optional[str] = None
        self.channel_title: str = str(channel_title or "").strip()
        self.channel_id: str = str(channel_id or "").strip()
        self.on_confirm = on_confirm
        self.header_text = header_text
        self.subject_text = subject_text
        self.confirm_text = confirm_text
        self.return_focus_to = return_focus_to
        self._is_submitting = False
        self._closing = False

        self.search_var = ctk.StringVar(value="")
        self.count_var = ctk.StringVar(value="")
        self.error_var = ctk.StringVar(value="")

        self._build_ui()
        self._populate_tree()

        # Keyboard shortcuts and window protocols
        self.protocol("WM_DELETE_WINDOW", self._handle_cancel)
        self.bind("<Escape>", lambda _e: self._handle_cancel())
        self.search_entry.focus_set()

    def _close_modal(self):
        """Hàm đóng modal thống nhất, giải phóng grab an toàn và schedule focus restoration."""
        if self._closing:
            return
        self._closing = True
        target = self.return_focus_to
        owner = self.master if (self.master and hasattr(self.master, "winfo_exists") and self.master.winfo_exists()) else None

        try:
            self.grab_release()
        except Exception:
            pass

        try:
            self.destroy()
        except Exception:
            pass

        if target and owner:
            def _restore_focus():
                try:
                    if hasattr(target, "winfo_exists") and target.winfo_exists():
                        target.focus_set()
                except (tk.TclError, RuntimeError, Exception):
                    pass
            try:
                if hasattr(owner, "winfo_exists") and owner.winfo_exists():
                    owner.after_idle(_restore_focus)
            except (tk.TclError, RuntimeError, Exception):
                pass

    def _build_ui(self):
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True, padx=16, pady=14)

        # 1. Header Card with Channel / Purpose Metadata
        header_card = ctk.CTkFrame(
            container,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        header_card.pack(fill="x", pady=(0, 10))

        h_inner = ctk.CTkFrame(header_card, fg_color="transparent")
        h_inner.pack(fill="x", padx=12, pady=10)

        header_title = self.header_text or "🔄 ĐỔI PROFILE ĐÍCH CHO KÊNH"
        ctk.CTkLabel(
            h_inner,
            text=header_title,
            font=UIThemeTokens.FONT_TITLE,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w", pady=(0, 4))

        if self.subject_text:
            ctk.CTkLabel(
                h_inner,
                text=self.subject_text,
                font=UIThemeTokens.FONT_BODY,
                text_color=UIThemeTokens.TEXT_PRIMARY,
            ).pack(anchor="w")
        else:
            display_name = self.channel_title if self.channel_title else (self.channel_id or "Chưa rõ")
            ctk.CTkLabel(
                h_inner,
                text=f"📺 Kênh: {display_name}",
                font=UIThemeTokens.FONT_BODY,
                text_color=UIThemeTokens.TEXT_PRIMARY,
            ).pack(anchor="w")

        curr_text = self.current_profile or "Chưa gán"
        if self.channel_id:
            meta_sub = f"ID: {self.channel_id}   |   Hiện tại: {curr_text}"
        else:
            meta_sub = f"Đang chọn: {curr_text}"
        ctk.CTkLabel(
            h_inner,
            text=meta_sub,
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        # 2. Search Input Box
        search_card = ctk.CTkFrame(container, fg_color="transparent")
        search_card.pack(fill="x", pady=(0, 6))

        self.search_entry = ctk.CTkEntry(
            search_card,
            textvariable=self.search_var,
            placeholder_text="🔍 Tìm theo tên profile TikTok...",
            height=32,
            font=UIThemeTokens.FONT_BODY,
        )
        self.search_entry.pack(fill="x")
        self.search_var.trace_add("write", lambda *_: self._on_search_change())
        self.search_entry.bind("<Down>", lambda _e: self._focus_tree())
        self.search_entry.bind("<Return>", lambda _e: self._on_search_return())

        # 3. Treeview Profile Table
        from tkinter import ttk
        table_card = ctk.CTkFrame(
            container,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        table_card.pack(fill="both", expand=True, pady=(0, 6))
        table_card.grid_rowconfigure(0, weight=1)
        table_card.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            table_card,
            style="Modern.Treeview",
            columns=("profile",),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("profile", text="Tên Profile TikTok", anchor="w")
        self.tree.column("profile", stretch=True, minwidth=180)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)

        vsb = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)
        self.tree.configure(yscrollcommand=vsb.set)

        self._select_bind_id = self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self._do_confirm())
        self.tree.bind("<Return>", lambda _e: self._do_confirm())

        # 4. Status, Count & Error Info
        info_row = ctk.CTkFrame(container, fg_color="transparent")
        info_row.pack(fill="x", pady=(0, 6))

        self.lbl_count = ctk.CTkLabel(
            info_row,
            textvariable=self.count_var,
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_count.pack(side="left")

        self.lbl_error = ctk.CTkLabel(
            info_row,
            textvariable=self.error_var,
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.STATUS_ERROR,
        )
        self.lbl_error.pack(side="right")

        # 5. Bottom Action Buttons
        btn_row = ctk.CTkFrame(container, fg_color="transparent")
        btn_row.pack(fill="x", pady=(2, 0))

        self.btn_cancel = ctk.CTkButton(
            btn_row,
            text="Hủy (Esc)",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=100,
            fg_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            hover_color=UIThemeTokens.BORDER_LIGHT,
            command=self._handle_cancel,
        )
        self.btn_cancel.pack(side="left")

        self.btn_confirm = ctk.CTkButton(
            btn_row,
            text=self.confirm_text or "✓ Xác Nhận Gán",
            font=UIThemeTokens.FONT_BUTTON,
            height=32,
            width=135,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            state="disabled",
            command=self._do_confirm,
        )
        self.btn_confirm.pack(side="right")

    def _populate_tree(self):
        if getattr(self, "_select_bind_id", None):
            try:
                self.tree.unbind("<<TreeviewSelect>>", self._select_bind_id)
            except Exception:
                pass
            self._select_bind_id = None

        try:
            self.tree.delete(*self.tree.get_children())
            self._iid_to_profile.clear()
            total = len(self._all_profiles)
            filtered_count = len(self._filtered_profiles)

            self.count_var.set(f"Hiển thị {filtered_count} / {total} profile")

            if not self._filtered_profiles:
                self.btn_confirm.configure(state="disabled")
                return

            target_profile = self._user_selected_profile if self._user_selected_profile is not None else self._pending_selected_profile
            target_iid = None
            for idx, p in enumerate(self._filtered_profiles):
                iid = f"prof_{idx}"
                self._iid_to_profile[iid] = p
                self.tree.insert("", "end", iid=iid, values=(p,))
                if p == target_profile and target_iid is None:
                    target_iid = iid

            # Preselect target profile if found, otherwise select first result as visual focus
            select_iid = target_iid if target_iid else (f"prof_0" if self._filtered_profiles else None)
            if select_iid and self.tree.exists(select_iid):
                self.tree.selection_set(select_iid)
                self.tree.see(select_iid)
                self.btn_confirm.configure(state="normal")
            else:
                self.btn_confirm.configure(state="disabled")
        finally:
            self._select_bind_id = self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _on_search_change(self):
        query = self.search_var.get().strip().casefold()
        if not query:
            self._filtered_profiles = list(self._all_profiles)
        else:
            self._filtered_profiles = [p for p in self._all_profiles if query in p.casefold()]
        self._populate_tree()

    def _focus_tree(self):
        children = self.tree.get_children()
        if children:
            if not self.tree.selection():
                self.tree.selection_set(children[0])
            self.tree.focus(self.tree.selection()[0])
            self.tree.focus_set()

    def _on_search_return(self):
        sel = self.tree.selection()
        if sel:
            self._do_confirm()
        elif self._filtered_profiles:
            first_iid = "prof_0"
            if self.tree.exists(first_iid):
                self.tree.selection_set(first_iid)
                self._do_confirm()

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if sel:
            selected_prof = self._get_selected_profile()
            if selected_prof:
                self._user_selected_profile = selected_prof
            self.btn_confirm.configure(state="normal")
        else:
            self.btn_confirm.configure(state="disabled")

    def _get_selected_profile(self) -> Optional[str]:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        return self._iid_to_profile.get(iid)

    def _do_confirm(self):
        if self._is_submitting or self._closing:
            return

        selected = self._get_selected_profile()
        if not selected:
            self.error_var.set("⚠️ Vui lòng chọn một profile từ danh sách.")
            return

        if not self.on_confirm:
            self._close_modal()
            return

        self._is_submitting = True
        try:
            self.btn_confirm.configure(state="disabled")
            self.error_var.set("")
        except Exception:
            pass

        try:
            ok, msg = self.on_confirm(selected)
            if ok:
                self._close_modal()
            else:
                if not self._closing and hasattr(self, "error_var") and hasattr(self, "btn_confirm"):
                    # Nếu profile bị xóa/stale, tự động làm mới danh sách loại bỏ profile này
                    err_text = str(msg or "Gán profile thất bại")
                    if "không còn tồn tại" in err_text.lower() or "đã bị xóa" in err_text.lower():
                        if selected in self._all_profiles:
                            self._all_profiles = [p for p in self._all_profiles if p != selected]
                            self._user_selected_profile = None
                            self._pending_selected_profile = None
                            self._on_search_change()
                            # Không auto-select profile khác sau khi bị báo stale
                            self.tree.selection_remove(*self.tree.selection())
                    self.error_var.set(f"❌ {err_text}")
                    self._is_submitting = False
                    has_selection = bool(self.tree.selection() and self._get_selected_profile())
                    self.btn_confirm.configure(state="normal" if has_selection else "disabled")
        except Exception as e:
            if not self._closing and hasattr(self, "error_var") and hasattr(self, "btn_confirm"):
                self.error_var.set(f"❌ Lỗi: {e}")
                self._is_submitting = False
                has_selection = bool(self.tree.selection() and self._get_selected_profile())
                self.btn_confirm.configure(state="normal" if has_selection else "disabled")

    def _handle_cancel(self):
        self._close_modal()
