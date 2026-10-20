# -*- coding: utf-8 -*-
"""
check_features.ui.video_rank_dialog - Hộp thoại kiểm tra điều kiện thưởng Creator Rewards Program (VPCS 2).
Hỗ trợ F4: Quét 10 trang, giải mã lỗi %s, tính ngày hết gậy, và mở hộp thoại kháng thủ công <= 400 ký tự.
"""

from __future__ import annotations

from datetime import datetime
import threading
import time
from typing import Any, Dict, List, Optional
import webbrowser

import customtkinter as ctk
from tkinter import ttk, messagebox

from ui_components import UIThemeTokens
from ..models import VPCSVideo
from ..clients.vpcs_rewards_client import TikTokRewardsClient
from .common_appeal_dialog import ManualAppealDialog


class VideoRankDialog(ctk.CTkToplevel):
    """
    Hộp thoại Kiểm Tra Điều Kiện Thưởng VPCS 2 của Profile.
    - Quét tuần tự tối đa 10 trang với delay 150ms chống rate-limit.
    - Bộ lọc thời gian: 30 ngày (33 ngày theo quy định), 90 ngày, 1 năm.
    - Bóc tách chi tiết vi phạm, ngày hết gậy và kháng nghị trực tiếp.
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
        self.client = TikTokRewardsClient(self.profile_config)

        self._videos: List[VPCSVideo] = []
        self._is_scanning = False
        self._cancel_flag = False

        self.title(f"Kiểm Tra Điều Kiện Thưởng VPCS: {profile_name}")
        self.geometry("1100x640")
        self.minsize(920, 520)

        try:
            self.transient(parent)
        except Exception:
            pass

        self._build_ui()
        self.after(100, self.start_scan)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Header Control Bar
        hdr_frame = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        hdr_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))

        ctk.CTkLabel(
            hdr_frame,
            text=f"🏆 Quỹ Sáng Tạo (VPCS 2): {self.profile_name}",
            font=("Segoe UI Bold", 15),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(side="left", padx=14, pady=10)

        # Bộ lọc thời gian
        ctk.CTkLabel(hdr_frame, text="Khoảng thời gian:", font=UIThemeTokens.FONT_SUBTITLE, text_color="#64748b").pack(side="left", padx=(20, 6))
        self.time_filter_var = ctk.StringVar(value="30 ngày")
        self.time_menu = ctk.CTkOptionMenu(
            hdr_frame,
            values=["30 ngày", "90 ngày", "1 năm"],
            variable=self.time_filter_var,
            width=110,
            height=30,
            font=UIThemeTokens.FONT_BADGE,
            command=lambda _val: self.start_scan(),
        )
        self.time_menu.pack(side="left", padx=(0, 14))

        self.btn_scan = ctk.CTkButton(
            hdr_frame,
            text="🔄 Quét Lại",
            width=90,
            height=30,
            font=UIThemeTokens.FONT_BADGE,
            fg_color="#334155",
            hover_color="#1e293b",
            command=self.start_scan,
        )
        self.btn_scan.pack(side="right", padx=14, pady=10)

        # Table Container
        table_container = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        table_container.grid(row=1, column=0, sticky="nsew", padx=16, pady=0)
        table_container.grid_columnconfigure(0, weight=1)
        table_container.grid_rowconfigure(0, weight=1)

        cols = ("idx", "vid", "desc", "duration", "posted", "expiry", "status", "violation")
        self.tree = ttk.Treeview(
            table_container,
            style="Modern.Treeview",
            columns=cols,
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("idx", text="#")
        self.tree.heading("vid", text="Video ID")
        self.tree.heading("desc", text="Tiêu Đề / Mô Tả Video")
        self.tree.heading("duration", text="Thời Lượng")
        self.tree.heading("posted", text="Ngày Đăng")
        self.tree.heading("expiry", text="Hạn Hết Gậy")
        self.tree.heading("status", text="Trạng Thái Kháng")
        self.tree.heading("violation", text="Chi Tiết Vi Phạm / Đủ Điều Kiện")

        self.tree.column("idx", width=35, anchor="center")
        self.tree.column("vid", width=140, anchor="center")
        self.tree.column("desc", width=250, anchor="w")
        self.tree.column("duration", width=75, anchor="center")
        self.tree.column("posted", width=110, anchor="center")
        self.tree.column("expiry", width=125, anchor="center")
        self.tree.column("status", width=120, anchor="center")
        self.tree.column("violation", width=230, anchor="w")

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        vsb = ttk.Scrollbar(table_container, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.tag_configure("ok", background="#ffffff", foreground="#0f172a")
        self.tree.tag_configure("violation", background="#ffe4e6", foreground="#be123c")
        self.tree.tag_configure("appealing", background="#fef3c7", foreground="#92400e")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_tree_select())
        self.tree.bind("<Double-1>", lambda e: self._open_video_url())

        # Bottom Action Bar
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.grid(row=2, column=0, sticky="ew", padx=16, pady=12)
        action_bar.grid_columnconfigure(0, weight=1)

        self.lbl_summary = ctk.CTkLabel(
            action_bar,
            text="Đang chuẩn bị quét...",
            font=("Segoe UI Semibold", 11),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.lbl_summary.grid(row=0, column=0, sticky="w")

        btn_box = ctk.CTkFrame(action_bar, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        self.btn_appeal = ctk.CTkButton(
            btn_box,
            text="⚖️ Kháng Nghị Vi Phạm",
            font=("Segoe UI Semibold", 11),
            width=150,
            height=30,
            fg_color="#ea580c",
            hover_color="#c2410c",
            command=self._on_appeal_clicked,
            state="disabled",
        )
        self.btn_appeal.pack(side="left", padx=(0, 6))

        self.btn_open_url = ctk.CTkButton(
            btn_box,
            text="🌐 Mở Video",
            font=("Segoe UI Semibold", 11),
            width=100,
            height=30,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._open_video_url,
        )
        self.btn_open_url.pack(side="left", padx=(0, 6))

        self.btn_copy_vid = ctk.CTkButton(
            btn_box,
            text="📋 Copy Video ID",
            font=("Segoe UI Semibold", 11),
            width=110,
            height=30,
            fg_color="#64748b",
            hover_color="#475569",
            command=self._copy_video_id,
        )
        self.btn_copy_vid.pack(side="left")

    def start_scan(self) -> None:
        """Bắt đầu quét danh sách video VPCS theo khoảng thời gian đã chọn."""
        if self._is_scanning:
            return
        self._is_scanning = True
        self.btn_scan.configure(state="disabled", text="⏳ Đang quét...")
        self.time_menu.configure(state="disabled")
        self.btn_appeal.configure(state="disabled")

        for row in self.tree.get_children():
            self.tree.delete(row)

        now = int(time.time())
        t_filter = self.time_filter_var.get()
        if "30" in t_filter:
            start_epoch = now - (33 * 86400)  # 33 ngày theo đặc tả
        elif "90" in t_filter:
            start_epoch = now - (90 * 86400)
        else:
            start_epoch = now - (365 * 86400)

        self.lbl_summary.configure(text="Đang kết nối API Creator Rewards Program...")

        def _prog(cur_page, max_p):
            def _u():
                if self.winfo_exists():
                    self.lbl_summary.configure(text=f"⏳ Đang quét trang {cur_page} / {max_p}...")
            try:
                self.after(0, _u)
            except Exception:
                pass

        def _worker():
            ok, videos, msg = self.client.fetch_vpcs_videos(
                start_epoch=start_epoch,
                end_epoch=now,
                max_pages=10,
                delay_ms=150,
                progress_cb=_prog,
            )

            def _done():
                if not self.winfo_exists():
                    return
                self._is_scanning = False
                self.btn_scan.configure(state="normal", text="🔄 Quét Lại")
                self.time_menu.configure(state="normal")
                self._videos = videos

                if ok:
                    self._render_table(videos)
                else:
                    self.lbl_summary.configure(text=f"❌ {msg}", text_color="#ef4444")
                    messagebox.showerror("Lỗi", msg)

            try:
                self.after(0, _done)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _render_table(self, videos: List[VPCSVideo]) -> None:
        for row in self.tree.get_children():
            self.tree.delete(row)

        ineligible_c = 0
        appealing_c = 0

        for idx, v in enumerate(videos, start=1):
            posted_str = datetime.fromtimestamp(v.video_posted_date).strftime("%d/%m/%Y") if v.video_posted_date else "—"
            dur_str = f"{v.duration_sec // 60}:{v.duration_sec % 60:02d}" if v.duration_sec >= 60 else f"{v.duration_sec}s"

            if v.eligibility:
                v_text = "🟢 Đủ điều kiện thưởng"
                tag = "ok"
                exp_str = "—"
                st_str = "—"
            else:
                ineligible_c += 1
                v_text = f"🔴 {v.violation_title}: {v.violation_description}" if v.violation_description else f"🔴 {v.violation_title or 'Bị từ chối thưởng'}"
                tag = "violation"

                if v.days_remaining > 0:
                    exp_dt = datetime.fromtimestamp(v.expiry_date).strftime("%d/%m") if v.expiry_date else ""
                    exp_str = f"Còn {v.days_remaining} ngày ({exp_dt})"
                else:
                    exp_str = "Đã hết gậy"

                st_map = {0: "Chưa kháng", 1: "Chưa kháng", 2: "Đang xét duyệt", 3: "Thất bại", 4: "Kháng xịt"}
                st_str = st_map.get(v.appeal_status, "Chưa kháng")
                if v.appeal_status == 2:
                    tag = "appealing"
                    appealing_c += 1

            self.tree.insert(
                "",
                "end",
                iid=v.vid,
                values=(
                    idx,
                    v.vid,
                    v.description[:45],
                    dur_str,
                    posted_str,
                    exp_str,
                    st_str,
                    v_text,
                ),
                tags=(tag,),
            )

        total = len(videos)
        if ineligible_c > 0:
            self.lbl_summary.configure(
                text=f"⚠️ Tổng {total} video  |  {ineligible_c} video bị gậy/không đủ điều kiện  |  {appealing_c} đang xét duyệt",
                text_color="#dc2626",
            )
        else:
            self.lbl_summary.configure(
                text=f"✅ Toàn bộ {total} video đều đủ điều kiện nhận thưởng Quỹ Sáng Tạo!",
                text_color="#16a34a",
            )

    def _get_selected_video(self) -> Optional[VPCSVideo]:
        sel = self.tree.selection()
        if not sel:
            return None
        vid = sel[0]
        for v in self._videos:
            if v.vid == vid:
                return v
        return None

    def _on_tree_select(self) -> None:
        v = self._get_selected_video()
        if v and v.can_appeal:
            self.btn_appeal.configure(state="normal")
        else:
            self.btn_appeal.configure(state="disabled")

    def _on_appeal_clicked(self) -> None:
        v = self._get_selected_video()
        if not v:
            messagebox.showinfo("Thông báo", "Vui lòng chọn một video trong bảng.")
            return

        if not v.can_appeal:
            messagebox.showinfo("Thông báo", "Video này hiện không ở trạng thái được phép gửi kháng nghị.")
            return

        reason = v.violation_description or v.violation_title or "Không đủ điều kiện nhận thưởng"

        def _submit_cb(text: str):
            ok, msg = self.client.submit_vpcs_appeal(v.vid, text)
            if ok:
                v.appeal_status = 2  # Đang xét duyệt
                if self.tree.exists(v.vid):
                    vals = list(self.tree.item(v.vid, "values"))
                    if len(vals) >= 7:
                        vals[6] = "⏳ Đang xét duyệt"
                        self.tree.item(v.vid, values=vals, tags=("appealing",))
                self.btn_appeal.configure(state="disabled")
            return ok, msg

        ManualAppealDialog(
            parent=self,
            target_title=f"Video VPCS {v.vid}",
            item_id=v.vid,
            violation_reason=reason,
            on_submit_callback=_submit_cb,
        )

    def _open_video_url(self) -> None:
        v = self._get_selected_video()
        if not v or not v.vid:
            return
        webbrowser.open(f"https://www.tiktok.com/video/{v.vid}")

    def _copy_video_id(self) -> None:
        v = self._get_selected_video()
        if not v or not v.vid:
            return
        self.clipboard_clear()
        self.clipboard_append(v.vid)
        messagebox.showinfo("Đã Sao Chép", f"Đã sao chép Video ID: {v.vid}")
