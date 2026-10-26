# -*- coding: utf-8 -*-
"""
check_features.ui.check_post_dialog - Hộp thoại Quản trị & Kiểm tra Bài đăng (Check Post - F2).
Tuân thủ nghiêm ngặt đặc tả dac_ta_chuc_nang_check_post.md:
- Bảng hiển thị 13 cột chuẩn Vue 3 / TikTok Manager.
- Tích hợp PostModerationEvaluator (Decision Table 7 bậc) và Creator Studio Item List API.
- Cơ chế Run Guard đa tầng chống race condition khi đổi profile hoặc tải lại.
- Hộp thoại xác nhận an toàn khi xóa vĩnh viễn video (Profile, Caption, Video ID).
- Tích hợp ManualAppealDialog (giới hạn 400 ký tự Unicode) và tải video No-Logo.
"""

from __future__ import annotations

from datetime import datetime
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional
import webbrowser

import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog

from ui_components import UIThemeTokens
from ..models import AwemePost, PenaltyDetails
from ..clients.studio_post_client import TikTokStudioPostClient
from ..services.video_downloader import download_video_no_watermark
from .common_appeal_dialog import ManualAppealDialog

logger = logging.getLogger("CheckPostDialog")


class CheckPostDialog(ctk.CTkToplevel):
    """
    Hộp thoại Kiểm Tra Sức Khỏe Bài Đăng & Quản Trị Video (F2 - Check Post).
    Bảng dữ liệu 13 cột chuẩn:
    1. # | 2. Ảnh bìa | 3. Mô tả | 4. Nguồn | 5. Nhãn | 6. Xem | 7. Chỉ số phụ
    8. GEO | 9. Shadowban | 10. Án phạt | 11. Trạng thái | 12. aweme_id | 13. Thao tác
    """

    def __init__(
        self,
        parent,
        profile_name: str,
        profile_config: Dict[str, Any],
        autoload: bool = True,
        *args,
        **kwargs,
    ):
        super().__init__(parent, *args, **kwargs)
        self.profile_name = profile_name
        self.profile_config = profile_config or {}
        self.client = TikTokStudioPostClient(self.profile_config)

        self._posts: List[AwemePost] = []
        self._posts_by_id: Dict[str, AwemePost] = {}
        self._current_cursor = 0
        self._has_more = False
        self._is_loading = False
        self._current_run_id = ""

        uname = self.profile_config.get("tiktok_id") or self.profile_config.get("tiktok_account") or self.profile_name
        self.title(f"Kiểm Tra Bài Đăng & Sức Khỏe Nội Dung: {self.profile_name} (@{str(uname).lstrip('@')})")
        self.geometry("1220x720")
        self.minsize(1020, 580)

        try:
            self.transient(parent)
        except Exception:
            pass

        self._build_ui()
        if autoload:
            self.after(100, self.load_first_page)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ----------------------------------------------------------------------
        # 1. Header Information & Control Bar
        # ----------------------------------------------------------------------
        hdr_frame = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        hdr_frame.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        hdr_frame.grid_columnconfigure(0, weight=1)

        hdr_inner = ctk.CTkFrame(hdr_frame, fg_color="transparent")
        hdr_inner.pack(fill="x", padx=16, pady=12)
        hdr_inner.grid_columnconfigure(0, weight=1)

        meta_box = ctk.CTkFrame(hdr_inner, fg_color="transparent")
        meta_box.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            meta_box,
            text=f"🎬 Quản Trị Bài Đăng: {self.profile_name}",
            font=("Segoe UI Bold", 15),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        ).pack(anchor="w")

        self.lbl_meta_sub = ctk.CTkLabel(
            meta_box,
            text="Đang chuẩn bị kết nối dữ liệu Creator Studio...",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_meta_sub.pack(anchor="w", pady=(2, 0))

        # Nút điều khiển phân trang
        btn_box = ctk.CTkFrame(hdr_inner, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        self.btn_refresh = ctk.CTkButton(
            btn_box,
            text="🔄 Tải Lại",
            font=("Segoe UI Semibold", 11),
            width=95,
            height=30,
            fg_color="#334155",
            hover_color="#1e293b",
            command=self.load_first_page,
        )
        self.btn_refresh.pack(side="left", padx=(0, 6))

        self.btn_load_more = ctk.CTkButton(
            btn_box,
            text="⬇ Tải Thêm",
            font=("Segoe UI Semibold", 11),
            width=100,
            height=30,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self.load_more_posts,
            state="disabled",
        )
        self.btn_load_more.pack(side="left")

        # ----------------------------------------------------------------------
        # Banner trạng thái / cảnh báo tại chỗ (In-place Status Banner)
        # ----------------------------------------------------------------------
        self.banner_frame = ctk.CTkFrame(self, fg_color="#fffbeb", corner_radius=8, border_width=1, border_color="#f59e0b")
        banner_inner = ctk.CTkFrame(self.banner_frame, fg_color="transparent")
        banner_inner.pack(fill="x", padx=12, pady=8)
        banner_inner.grid_columnconfigure(0, weight=1)

        self.lbl_banner_msg = ctk.CTkLabel(
            banner_inner,
            text="",
            font=("Segoe UI Semibold", 12),
            text_color="#92400e",
            anchor="w",
            justify="left",
        )
        self.lbl_banner_msg.grid(row=0, column=0, sticky="w", padx=(4, 8))

        banner_btn_box = ctk.CTkFrame(banner_inner, fg_color="transparent")
        banner_btn_box.grid(row=0, column=1, sticky="e")

        self.btn_banner_browser = ctk.CTkButton(
            banner_btn_box,
            text="🌐 Mở Trình Duyệt",
            font=("Segoe UI Semibold", 11),
            width=125,
            height=28,
            fg_color="#d97706",
            hover_color="#b45309",
            command=self._open_profile_browser,
        )
        self.btn_banner_browser.pack(side="left", padx=(0, 6))

        self.btn_banner_retry = ctk.CTkButton(
            banner_btn_box,
            text="🔄 Thử Lại",
            font=("Segoe UI Semibold", 11),
            width=85,
            height=28,
            fg_color="#475569",
            hover_color="#334155",
            command=self.load_first_page,
        )
        self.btn_banner_retry.pack(side="left")

        # ----------------------------------------------------------------------
        # 2. Table Container - Treeview 13 Cột Chuẩn
        # ----------------------------------------------------------------------
        self.grid_rowconfigure(2, weight=1)
        table_container = ctk.CTkFrame(self, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#e2e8f0")
        table_container.grid(row=2, column=0, sticky="nsew", padx=16, pady=0)
        table_container.grid_columnconfigure(0, weight=1)
        table_container.grid_rowconfigure(0, weight=1)

        cols = (
            "c_idx", "c_cover", "c_desc", "c_src", "c_flags",
            "c_play", "c_submetrics", "c_geo", "c_shadow", "c_penalty",
            "c_status", "c_id", "c_action"
        )
        self.tree = ttk.Treeview(
            table_container,
            style="Modern.Treeview",
            columns=cols,
            show="headings",
            selectmode="browse",
        )

        # Định nghĩa Headings
        self.tree.heading("c_idx", text="#")
        self.tree.heading("c_cover", text="Ảnh bìa")
        self.tree.heading("c_desc", text="Mô tả / Ngày đăng")
        self.tree.heading("c_src", text="Nguồn")
        self.tree.heading("c_flags", text="Nhãn")
        self.tree.heading("c_play", text="Xem")
        self.tree.heading("c_submetrics", text="Chỉ số phụ (❤️/💬/🔗/🔖)")
        self.tree.heading("c_geo", text="GEO")
        self.tree.heading("c_shadow", text="Shadowban")
        self.tree.heading("c_penalty", text="Án phạt / Strike")
        self.tree.heading("c_status", text="Trạng thái")
        self.tree.heading("c_id", text="aweme_id")
        self.tree.heading("c_action", text="Thao tác")

        # Cấu hình Columns width & alignment
        self.tree.column("c_idx", width=35, anchor="center")
        self.tree.column("c_cover", width=105, anchor="center")
        self.tree.column("c_desc", width=250, anchor="w")
        self.tree.column("c_src", width=85, anchor="center")
        self.tree.column("c_flags", width=85, anchor="center")
        self.tree.column("c_play", width=80, anchor="center")
        self.tree.column("c_submetrics", width=165, anchor="center")
        self.tree.column("c_geo", width=65, anchor="center")
        self.tree.column("c_shadow", width=120, anchor="center")
        self.tree.column("c_penalty", width=140, anchor="center")
        self.tree.column("c_status", width=85, anchor="center")
        self.tree.column("c_id", width=145, anchor="center")
        self.tree.column("c_action", width=115, anchor="center")

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        vsb = ttk.Scrollbar(table_container, orient="vertical", command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        self.tree.configure(yscrollcommand=vsb.set)

        # Tags màu sắc trạng thái
        self.tree.tag_configure("ok", background="#ffffff", foreground="#0f172a")
        self.tree.tag_configure("shadow_bad", background="#fee2e2", foreground="#991b1b")
        self.tree.tag_configure("shadow_suspect", background="#fef3c7", foreground="#92400e")
        self.tree.tag_configure("strike", background="#ffe4e6", foreground="#be123c")
        self.tree.tag_configure("dup", background="#ffedd5", foreground="#c2410c")
        self.tree.tag_configure("unknown", background="#f8fafc", foreground="#475569")

        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_tree_select())
        self.tree.bind("<Double-1>", lambda e: self._open_video_url())

        # ----------------------------------------------------------------------
        # 3. Bottom Action Bar
        # ----------------------------------------------------------------------
        action_bar = ctk.CTkFrame(self, fg_color="transparent")
        action_bar.grid(row=3, column=0, sticky="ew", padx=16, pady=12)
        action_bar.grid_columnconfigure(0, weight=1)

        info_box = ctk.CTkFrame(action_bar, fg_color="transparent")
        info_box.grid(row=0, column=0, sticky="w")

        self.lbl_status_summary = ctk.CTkLabel(
            info_box,
            text="Đang kết nối lấy bài viết...",
            font=("Segoe UI Bold", 11),
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        )
        self.lbl_status_summary.pack(anchor="w")

        self.lbl_selected_detail = ctk.CTkLabel(
            info_box,
            text="💡 Chọn một video để xem chi tiết đối soát và bằng chứng.",
            font=("Segoe UI", 11),
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        )
        self.lbl_selected_detail.pack(anchor="w", pady=(2, 0))

        btn_action_box = ctk.CTkFrame(action_bar, fg_color="transparent")
        btn_action_box.grid(row=0, column=1, sticky="e")

        self.btn_appeal = ctk.CTkButton(
            btn_action_box,
            text="⚖️ Kháng Strike",
            font=("Segoe UI Semibold", 11),
            width=120,
            height=30,
            fg_color="#ea580c",
            hover_color="#c2410c",
            command=self._on_appeal_clicked,
            state="disabled",
        )
        self.btn_appeal.pack(side="left", padx=(0, 6))

        self.btn_download = ctk.CTkButton(
            btn_action_box,
            text="📥 Tải No-Logo",
            font=("Segoe UI Semibold", 11),
            width=110,
            height=30,
            fg_color="#0284c7",
            hover_color="#0369a1",
            command=self._on_download_clicked,
            state="disabled",
        )
        self.btn_download.pack(side="left", padx=(0, 6))

        self.btn_open_dup = ctk.CTkButton(
            btn_action_box,
            text="🔗 Bài Liên Quan",
            font=("Segoe UI Semibold", 11),
            width=125,
            height=30,
            fg_color="#dc2626",
            hover_color="#b91c1c",
            command=self._on_open_related_dup,
            state="disabled",
        )
        self.btn_open_dup.pack(side="left", padx=(0, 6))

        self.btn_open_current = ctk.CTkButton(
            btn_action_box,
            text="🌐 Mở Video",
            font=("Segoe UI Semibold", 11),
            width=95,
            height=30,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=self._open_video_url,
            state="disabled",
        )
        self.btn_open_current.pack(side="left", padx=(0, 6))

        self.btn_hide = ctk.CTkButton(
            btn_action_box,
            text="🔒 Ẩn Private",
            font=("Segoe UI Semibold", 11),
            width=100,
            height=30,
            fg_color="#475569",
            hover_color="#334155",
            command=self._on_hide_clicked,
            state="disabled",
        )
        self.btn_hide.pack(side="left", padx=(0, 6))

        self.btn_delete = ctk.CTkButton(
            btn_action_box,
            text="🗑️ Xóa",
            font=("Segoe UI Semibold", 11),
            width=80,
            height=30,
            fg_color="#b91c1c",
            hover_color="#991b1b",
            command=self._on_delete_clicked,
            state="disabled",
        )
        self.btn_delete.pack(side="left", padx=(0, 6))

        self.btn_copy_id = ctk.CTkButton(
            btn_action_box,
            text="📋 Copy ID",
            font=("Segoe UI Semibold", 11),
            width=85,
            height=30,
            fg_color="#64748b",
            hover_color="#475569",
            command=self._copy_selected_id,
            state="disabled",
        )
        self.btn_copy_id.pack(side="left")

    # --------------------------------------------------------------------------
    # Luồng Tải Dữ Liệu & Phân Trang (Run Guard Protected)
    # --------------------------------------------------------------------------
    def load_first_page(self, force: bool = True) -> None:
        """Tải trang bài đăng đầu tiên (cursor = 0)."""
        if force:
            self._is_loading = False
        self._current_cursor = 0
        self._posts = []
        self._posts_by_id = {}
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._fetch_page_async(cursor=0, append=False)

    def load_more_posts(self) -> None:
        """Tải trang tiếp theo sử dụng cursor."""
        if not self._has_more:
            messagebox.showinfo("Check Post", "Đã tải hết toàn bộ bài viết của kênh.")
            return
        self._fetch_page_async(cursor=self._current_cursor, append=True)

    def _fetch_page_async(self, cursor: int, append: bool) -> None:
        if self._is_loading:
            return
        self._is_loading = True

        import uuid
        run_id = f"run_{self.profile_name}_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        self._current_run_id = run_id

        self.btn_refresh.configure(state="disabled", text="⏳ Đang tải...")
        self.btn_load_more.configure(state="disabled")
        self.lbl_meta_sub.configure(text=f"Đang đồng bộ dữ liệu bài đăng...")

        def _worker():
            try:
                # 1. Thu thập snapshot an toàn qua WebSigner & Phân trang
                snapshot = self.client.fetch_live_snapshot(
                    run_id=run_id,
                    max_items=50 if append else 35,
                    page_size=20,
                    max_pages=3 if append else 2,
                    initial_cursor=cursor,
                )

                # 2. Đối soát trùng lặp 3 trạng thái qua DuplicateResolver (TTM SQLite + Feed)
                from ..services.duplicate_resolver import DuplicateResolver
                resolver = DuplicateResolver()
                resolver.resolve_batch(snapshot.posts, query_ttm=True)

                # 3. Batching tra cứu án phạt cho các video có cờ penalty_flag
                strike_candidates = [
                    (p.aweme_id, p.create_time)
                    for p in snapshot.posts
                    if p.penalty_flag and p.aweme_id
                ]
                penalty_map: Dict[str, PenaltyDetails] = {}
                if strike_candidates:
                    penalty_map = self.client.fetch_penalties_batch(strike_candidates, max_workers=4)
                    for p in snapshot.posts:
                        if p.aweme_id in penalty_map:
                            p.penalty_details = penalty_map[p.aweme_id]

                # 4. Run Guard Check trước khi commit vào UI
                def _ui_commit():
                    if self._current_run_id != run_id:
                        logger.warning(f"Run Guard: Bỏ qua kết quả cũ của {self.profile_name} (run_id: {run_id} != active: {self._current_run_id}).")
                        return
                    if not self.winfo_exists():
                        return

                    self._is_loading = False
                    self.btn_refresh.configure(state="normal", text="🔄 Tải Lại")

                    if not snapshot.posts and snapshot.error:
                        self.lbl_meta_sub.configure(text=f"Lỗi: {snapshot.error}")
                        self.lbl_status_summary.configure(
                            text=f"⚠️ {snapshot.error} • Nhấn 'Mở Trình Duyệt' để xử lý hoặc 'Thử Lại'."
                        )
                        self._show_banner(f"⚠️ {snapshot.error}", is_error=True)
                        return

                    self._hide_banner()

                    # Sử dụng trực tiếp next_cursor và has_more do Snapshot/API trả về (Codex Finding 1)
                    self._current_cursor = snapshot.next_cursor
                    self._has_more = snapshot.has_more
                    self.btn_load_more.configure(state="normal" if snapshot.has_more else "disabled")

                    start_idx = len(self._posts) if append else 0
                    for idx, post in enumerate(snapshot.posts, start=start_idx + 1):
                        self._posts.append(post)
                        self._posts_by_id[post.aweme_id] = post
                        self._insert_row(idx, post)

                    tot = len(self._posts)
                    dup_cnt = sum(1 for p in self._posts if (p.dedup_result and p.dedup_result.state == "DUPLICATE") or (not p.dedup_result and p.is_dup))
                    clear_cnt = sum(1 for p in self._posts if p.dedup_result and p.dedup_result.state == "CLEAR")
                    unknown_cnt = max(0, tot - dup_cnt - clear_cnt)
                    shadow_cnt = sum(1 for p in self._posts if p.shadow in ("yes", "suspect"))

                    src_text = "LIVE Creator Studio" if snapshot.source == "live_studio" else "Bản sao lưu Offline"
                    comp_text = "Đầy đủ" if snapshot.completeness == "COMPLETE" else "Một phần"
                    self.lbl_meta_sub.configure(
                        text=f"Đã tải {tot} video • Nguồn: {src_text} ({comp_text}) • Đã đối soát TTM & Feed"
                    )
                    self.lbl_status_summary.configure(
                        text=f"Tổng số: {tot} bài | 🔴 Trùng: {dup_cnt} | ✅ Không trùng: {clear_cnt} | ⚠️ Chưa xác định: {unknown_cnt} | ⛔ Bị bóp: {shadow_cnt}"
                    )

                self.after(0, _ui_commit)

            except Exception as e:
                logger.error(f"Lỗi worker check post: {e}", exc_info=True)
                def _ui_err():
                    if self._current_run_id == run_id and self.winfo_exists():
                        self._is_loading = False
                        self.btn_refresh.configure(state="normal", text="🔄 Tải Lại")
                        self.lbl_meta_sub.configure(text=f"Lỗi ngoại lệ: {e}")
                self.after(0, _ui_err)

        threading.Thread(target=_worker, daemon=True).start()

    def _insert_row(self, idx: int, post: AwemePost) -> None:
        """Render 1 dòng dữ liệu 13 cột chuẩn vào Treeview."""
        # 1. Thời lượng mm:ss
        dur_sec = post.duration
        dur_str = f"{dur_sec // 60:02d}:{dur_sec % 60:02d}" if dur_sec > 0 else "00:00"
        cover_display = f"▶ {dur_str}"

        # 2. Ngày đăng format VN
        posted_vn = ""
        if post.create_time > 0:
            try:
                dt = datetime.fromtimestamp(post.create_time)
                posted_vn = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                posted_vn = str(post.create_time)
        desc_display = f"{post.desc[:45]}..." if len(post.desc) > 45 else post.desc
        desc_full = f"{desc_display}\n📅 {posted_vn}" if posted_vn else desc_display

        # 3. Nguồn
        src_display = post.source_label or ("Web" if post.device == "web" else "Điện thoại")

        # 4. Nhãn
        flags_display = ", ".join(post.flags) if post.flags else "—"

        # 5. Lượt xem format US
        play_display = f"{post.play:,}" if post.play else "0"

        # 6. Chỉ số phụ
        submetrics = f"❤️ {post.like:,} | 💬 {post.comment:,} | 🔗 {post.share:,} | 🔖 {post.save:,}"

        # 7. GEO
        geo_display = post.region.upper() if post.region else "—"

        # 8. Shadowban badge
        shadow_map = {
            "yes": "⛔ Có (Bóp reach)",
            "suspect": "⏳ Nghi vấn / Duyệt",
            "no": "✅ Không",
            "unknown": "❓ Chưa rõ",
        }
        if post.data_source == "channel_feed" and post.shadow == "unknown":
            shadow_display = "❓ Hết phiên Studio"
        else:
            shadow_display = shadow_map.get(post.shadow, "❓ Chưa rõ")

        # 9. Án phạt / Strike / Trùng lặp (3 trạng thái phân minh)
        penalty_display = "—"
        if post.dedup_result and post.dedup_result.state == "DUPLICATE":
            gid_short = post.group_id[:8] if post.group_id else "dup"
            penalty_display = f"🔴 Trùng ({gid_short}..)"
        elif post.dedup_result and post.dedup_result.state == "CLEAR":
            penalty_display = "✅ Không trùng"
        elif post.dedup_result and post.dedup_result.state == "UNKNOWN":
            penalty_display = "⚠️ Chưa xác định"
        elif post.is_dup:
            penalty_display = f"🔴 Trùng ({post.group_id[:6]}..)"
        elif post.penalty_details and post.penalty_details.has_penalty:
            status_map = {1: "Chưa kháng", 2: "Đang xét", 4: "Đã kháng"}
            st_text = status_map.get(post.penalty_details.appeal_status, "Có án phạt")
            penalty_display = f"⚠️ {st_text}"
        elif post.penalty_flag:
            penalty_display = "⚠️ Dính án phạt"

        # 10. Trạng thái
        status_display = "Công khai"
        if post.is_delete:
            status_display = "🗑️ Đã xóa"
        elif post.is_private:
            status_display = "🔒 Riêng tư"

        # Tag phân loại màu dòng
        tag = "ok"
        if (post.dedup_result and post.dedup_result.state == "DUPLICATE") or post.is_dup:
            tag = "dup"
        elif post.penalty_details and post.penalty_details.has_penalty:
            tag = "strike"
        elif post.shadow == "yes":
            tag = "shadow_bad"
        elif post.shadow == "suspect":
            tag = "shadow_suspect"
        elif post.dedup_result and post.dedup_result.state == "UNKNOWN":
            tag = "unknown"

        self.tree.insert(
            "",
            "end",
            iid=post.aweme_id,
            values=(
                idx,
                cover_display,
                desc_full,
                src_display,
                flags_display,
                play_display,
                submetrics,
                geo_display,
                shadow_display,
                penalty_display,
                status_display,
                post.aweme_id,
                "🔒 Ẩn  |  🗑️ Xóa",
            ),
            tags=(tag,),
        )

    # --------------------------------------------------------------------------
    # Thao Tác Bảng & Hành Động An Toàn (Actions)
    # --------------------------------------------------------------------------
    def _get_selected_post(self) -> Optional[AwemePost]:
        sel = self.tree.selection()
        if not sel:
            return None
        aweme_id = sel[0]
        return self._posts_by_id.get(aweme_id)

    def _show_banner(self, message: str, is_error: bool = True) -> None:
        """Hiển thị banner cảnh báo / thông báo tại chỗ, thay thế hoàn toàn cho popup modal."""
        if not self.winfo_exists():
            return
        bg_col = "#fee2e2" if is_error else "#fffbeb"
        border_col = "#ef4444" if is_error else "#f59e0b"
        text_col = "#991b1b" if is_error else "#92400e"

        self.banner_frame.configure(fg_color=bg_col, border_color=border_col)
        self.lbl_banner_msg.configure(text=message, text_color=text_col)
        self.banner_frame.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

    def _hide_banner(self) -> None:
        """Ẩn banner trạng thái."""
        if not self.winfo_exists():
            return
        self.banner_frame.grid_remove()

    def _open_profile_browser(self) -> None:
        """Mở trình duyệt Chromium thực tế của profile để người dùng xử lý."""
        try:
            from browser_patchright_glue import open_session
            cfg = dict(self.profile_config)
            cfg["headless"] = False
            self.lbl_meta_sub.configure(text="🌐 Đang khởi động trình duyệt profile...")
            def _launch():
                try:
                    open_session(cfg, profile_name=self.profile_name, timeout=60)
                except Exception as ex:
                    logger.warning(f"Lỗi khi mở browser thủ công: {ex}")
            threading.Thread(target=_launch, daemon=True).start()
        except Exception as e:
            logger.error(f"Lỗi khi kích hoạt browser: {e}")

    def _on_tree_select(self) -> None:
        """Kích hoạt trạng thái các nút chức năng theo video đang chọn và hiển thị chi tiết đối soát."""
        post = self._get_selected_post()
        if not post:
            self._set_action_buttons_state("disabled")
            if hasattr(self, "lbl_selected_detail"):
                self.lbl_selected_detail.configure(
                    text="💡 Chọn một video để xem chi tiết đối soát và bằng chứng."
                )
            return

        self._set_action_buttons_state("normal")
        has_dup_video = bool(post.is_dup and post.group_id and post.group_id != post.aweme_id)
        self.btn_open_dup.configure(state="normal" if has_dup_video else "disabled")
        can_appeal = bool(post.penalty_details and post.penalty_details.has_penalty and post.penalty_details.appeal_status in (0, 1))
        self.btn_appeal.configure(state="normal" if (can_appeal or post.penalty_flag) else "disabled")

        # Codex Finding 2: Cập nhật chi tiết đối soát trên lbl_selected_detail riêng biệt,
        # bảo toàn 100% dòng thống kê bảo toàn trên lbl_status_summary
        if hasattr(self, "lbl_selected_detail"):
            if post.dedup_result:
                st = post.dedup_result.state
                reason = post.dedup_result.reason
                src = post.dedup_result.source
                if st == "DUPLICATE":
                    self.lbl_selected_detail.configure(
                        text=f"🔴 TRÙNG LẶP [ID: {post.aweme_id}] • Video gốc: {post.group_id} • Nguồn: {src} • Lý do: {reason}"
                    )
                elif st == "CLEAR":
                    self.lbl_selected_detail.configure(
                        text=f"✅ KHÔNG TRÙNG [ID: {post.aweme_id}] • Khớp video gốc trong nguồn đối soát ({src}) • Lý do: {reason}"
                    )
                else:
                    self.lbl_selected_detail.configure(
                        text=f"⚠️ CHƯA XÁC ĐỊNH [ID: {post.aweme_id}] • Lý do: {reason} • Nguồn: {src}"
                    )
            elif post.is_dup:
                self.lbl_selected_detail.configure(
                    text=f"🔴 TRÙNG LẶP [ID: {post.aweme_id}] • Video gốc: {post.group_id}"
                )
            else:
                self.lbl_selected_detail.configure(
                    text=f"ℹ️ Bài đăng [ID: {post.aweme_id}] • Trạng thái: {post.duplicate_status}"
                )

    def _set_action_buttons_state(self, state: str) -> None:
        self.btn_download.configure(state=state)
        self.btn_open_current.configure(state=state)
        self.btn_hide.configure(state=state)
        self.btn_delete.configure(state=state)
        self.btn_copy_id.configure(state=state)

    def _open_video_url(self) -> None:
        post = self._get_selected_post()
        if post and post.share_url:
            webbrowser.open(post.share_url)

    def _on_open_related_dup(self) -> None:
        post = self._get_selected_post()
        if post and post.group_id:
            url = f"https://www.tiktok.com/@tiktok/video/{post.group_id}"
            webbrowser.open(url)

    def _copy_selected_id(self) -> None:
        post = self._get_selected_post()
        if post and post.aweme_id:
            self.clipboard_clear()
            self.clipboard_append(post.aweme_id)
            self.btn_copy_id.configure(text="✅ Đã Copy!")
            self.after(1500, lambda: self.btn_copy_id.configure(text="📋 Copy ID"))

    def _on_download_clicked(self) -> None:
        """Tải video không watermark về máy."""
        post = self._get_selected_post()
        if not post or not post.download_url:
            messagebox.showinfo("Tải Video", "Không tìm thấy đường dẫn tải video không watermark.")
            return

        target_url = str(post.download_url)
        target_vid = str(post.aweme_id)

        out_dir = filedialog.askdirectory(title="Chọn Thư Mục Lưu Video")
        if not out_dir:
            return

        def _dl():
            def _btn_busy():
                if self.winfo_exists():
                    self.btn_download.configure(state="disabled", text="⏳ Đang tải...")
            self.after(0, _btn_busy)

            ok, msg, fpath = download_video_no_watermark(target_url, out_dir, target_vid)
            def _ui_done():
                if not self.winfo_exists():
                    return
                self.btn_download.configure(state="normal", text="📥 Tải No-Logo")
                if ok:
                    messagebox.showinfo("Tải Video", f"Tải video thành công!\nĐã lưu tại: {fpath}")
                else:
                    messagebox.showerror("Tải Video", f"Lỗi tải video: {msg}")
            self.after(0, _ui_done)

        threading.Thread(target=_dl, daemon=True).start()

    def _on_hide_clicked(self) -> None:
        """Chuyển video sang chế độ Riêng tư (Private)."""
        post = self._get_selected_post()
        if not post or not post.aweme_id:
            return

        target_vid = str(post.aweme_id)
        if not messagebox.askyesno("Xác Nhận Ẩn Video", f"Bạn có chắc muốn chuyển video [{target_vid}] sang chế độ Riêng tư (Chỉ mình tôi)?"):
            return

        self.btn_hide.configure(state="disabled", text="⏳ Đang ẩn...")

        def _do_hide():
            ok, msg = self.client.hide_studio_item(target_vid)
            def _ui():
                if not self.winfo_exists():
                    return
                self.btn_hide.configure(state="normal", text="🔒 Ẩn Private")
                if ok:
                    if target_vid in self._posts_by_id:
                        self._posts_by_id[target_vid].is_private = True
                    if self.tree.exists(target_vid):
                        vals = list(self.tree.item(target_vid, "values"))
                        vals[10] = "🔒 Riêng tư"
                        self.tree.item(target_vid, values=vals)
                    messagebox.showinfo("Ẩn Video", msg)
                else:
                    messagebox.showerror("Ẩn Video", msg)
            self.after(0, _ui)

        threading.Thread(target=_do_hide, daemon=True).start()

    def _on_delete_clicked(self) -> None:
        """Hộp thoại xác nhận an toàn trước khi xóa vĩnh viễn video khỏi kênh (Target Freeze)."""
        post = self._get_selected_post()
        if not post or not post.aweme_id:
            return

        # Đóng băng định danh video mục tiêu (Target Freeze) trước khi mở confirm dialog
        target_vid = str(post.aweme_id)
        target_desc = str(post.desc or "")
        target_profile = str(self.profile_name)

        confirm_msg = (
            f"CẢNH BÁO: Thao tác này KHÔNG THỂ HOÀN TÁC trên TikTok!\n\n"
            f"• Profile: {target_profile}\n"
            f"• Video ID: {target_vid}\n"
            f"• Tiêu đề: {target_desc[:60]}...\n\n"
            f"Bạn có chắc chắn muốn xóa vĩnh viễn video này không?"
        )
        if not messagebox.askyesno("⚠️ XÁC NHẬN XÓA VIDEO", confirm_msg, icon="warning"):
            return

        self.btn_delete.configure(state="disabled", text="⏳ Đang xóa...")

        def _do_del():
            ok, msg = self.client.delete_studio_item(target_vid)
            def _ui():
                if not self.winfo_exists():
                    return
                self.btn_delete.configure(state="normal", text="🗑️ Xóa")
                if ok:
                    if target_vid in self._posts_by_id:
                        self._posts_by_id[target_vid].is_delete = True
                    if self.tree.exists(target_vid):
                        vals = list(self.tree.item(target_vid, "values"))
                        vals[10] = "🗑️ Đã xóa"
                        self.tree.item(target_vid, values=vals)
                    messagebox.showinfo("Xóa Video", msg)
                else:
                    messagebox.showerror("Xóa Video", msg)
            self.after(0, _ui)

        threading.Thread(target=_do_del, daemon=True).start()

    def _on_appeal_clicked(self) -> None:
        """Mở hộp thoại ManualAppealDialog để người dùng tự nhập nội dung kháng Strike."""
        post = self._get_selected_post()
        if not post:
            return

        penalty_id = post.penalty_details.penalty_id if post.penalty_details else post.aweme_id
        issuer = post.penalty_details.issuer if post.penalty_details else "TRUST_AND_SAFETY"
        reasons = ", ".join(post.penalty_details.reason_codes) if (post.penalty_details and post.penalty_details.reason_codes) else "Vi phạm tiêu chuẩn cộng đồng"

        def _submit_callback(appeal_text: str) -> Tuple[bool, str]:
            return self.client.submit_studio_appeal(
                vid=post.aweme_id,
                penalty_id=penalty_id,
                reason_text=appeal_text,
                issuer=issuer,
            )

        ManualAppealDialog(
            self,
            target_title=f"Video {post.aweme_id}",
            item_id=post.aweme_id,
            violation_reason=f"Lý do án phạt: {reasons}",
            on_submit_callback=_submit_callback,
        )
