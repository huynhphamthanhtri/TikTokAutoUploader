"""
ui_dialogs.py - Bộ Hộp Thoại (Modal Dialogs) Chuẩn Hóa cho VIBE_AUTO_UPLOAD-LP.

Bao gồm:
1. BatchSetProxyModal: Gán proxy hàng loạt từ văn bản nhiều dòng, parse HTTP/SOCKS5,
   preview masked password (host:port:user:***), báo dòng lỗi cụ thể, atomic save/rollback.
2. MonetizationDetailModal: Xem chi tiết snapshot tài chính & KYC của một tài khoản.
3. CreateEditProfileModal: Form chia 3 tabs (Cơ bản, Proxy [password mask], Cookie & Session).
"""

from __future__ import annotations

import customtkinter as ctk
from typing import Any, Callable, Dict, List, Optional, Tuple

from core_helpers import parse_proxy_string
from ui_components import UIThemeTokens, redact_proxy_string


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
        self.geometry("580x520")
        self.minsize(520, 440)
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
        self.geometry("640x740")
        self.minsize(580, 580)
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
        self.geometry("540x480")
        self.minsize(480, 420)
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
