# -*- coding: utf-8 -*-
"""
check_features.ui.check_dup_view - Giao diện kiểm tra video trùng lặp theo kênh công khai (F3).
Hoạt động hoàn toàn độc lập, không yêu cầu session, cookie hay proxy của profile.
Hỗ trợ cả dạng CTkFrame (CheckDupView) và CTkToplevel (CheckDupDialog).
"""

from __future__ import annotations

import csv
from datetime import datetime
import logging
import threading
from typing import Any, Dict, List, Optional
import webbrowser

import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog

from ui_components import UIThemeTokens
from ..models import AwemePost, ChannelMeta
from ..clients.public_channel_client import TikTokPublicChannelClient

logger = logging.getLogger("CheckDupView")


def format_views(count: int) -> str:
    """Format lượt xem thân thiện theo BR-F3-04."""
    if count >= 1_000_000:
        val = count / 1_000_000
        return f"{val:.1f}M".replace(".0M", "M")
    if count >= 1_000:
        val = count / 1_000
        return f"{val:.1f}K".replace(".0K", "K")
    return str(count)


class CheckDupView(ctk.CTkFrame):
    """
    View kiểm tra video trùng lặp (repost) theo kênh công khai (F3).
    """

    def __init__(self, parent, initial_channel: str = "", *args, **kwargs):
        super().__init__(parent, fg_color="transparent", *args, **kwargs)
        self.client = TikTokPublicChannelClient()

        self._channel_meta = ChannelMeta()
        self._posts: List[AwemePost] = []
        self._next_cursor: int = 0
        self._has_more: bool = False
        self._is_loading: bool = False
        self._initial_channel = initial_channel

        self._build_ui()
        if self._initial_channel:
            self.ent_search.insert(0, self._initial_channel)
            self.after(200, self.start_load_channel)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ----------------------------------------------------------------------
        # 1. SEARCH BAR
        # ----------------------------------------------------------------------
        search_card = ctk.CTkFrame(self, fg_color=UIThemeTokens.BG_CARD, corner_radius=8)
        search_card.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        search_card.grid_columnconfigure(1, weight=1)

        lbl_input = ctk.CTkLabel(
            search_card,
            text="Kênh TikTok:",
            font=UIThemeTokens.FONT_SUBHEADER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        lbl_input.grid(row=0, column=0, padx=(12, 8), pady=12, sticky="w")

        self.ent_search = ctk.CTkEntry(
            search_card,
            placeholder_text="Nhập @username, link kênh (VD: @khaby.lame hoặc https://www.tiktok.com/@tiktok)...",
            height=34,
            font=UIThemeTokens.FONT_BODY,
        )
        self.ent_search.grid(row=0, column=1, padx=6, pady=12, sticky="ew")
        self.ent_search.bind("<Return>", lambda e: self.start_load_channel())

        self.btn_load = ctk.CTkButton(
            search_card,
            text="🔍 Tải Kênh",
            command=self.start_load_channel,
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_HOVER,
            width=100,
            height=34,
            font=UIThemeTokens.FONT_SUBHEADER,
        )
        self.btn_load.grid(row=0, column=2, padx=(6, 12), pady=12)

        # ----------------------------------------------------------------------
        # 2. CHANNEL METADATA & SUMMARY BANNER
        # ----------------------------------------------------------------------
        self.meta_card = ctk.CTkFrame(self, fg_color=UIThemeTokens.BG_CARD, corner_radius=8)
        self.meta_card.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))
        self.meta_card.grid_columnconfigure(0, weight=1)
        self.meta_card.grid_columnconfigure(1, weight=0)

        # Cột trái: Tên kênh + Stats
        left_meta = ctk.CTkFrame(self.meta_card, fg_color="transparent")
        left_meta.grid(row=0, column=0, sticky="w", padx=12, pady=8)

        self.lbl_channel_name = ctk.CTkLabel(
            left_meta,
            text="Chưa tải kênh nào",
            font=("Segoe UI Bold", 15),
            text_color=UIThemeTokens.TEXT_PRIMARY,
        )
        self.lbl_channel_name.pack(anchor="w")

        self.lbl_channel_stats = ctk.CTkLabel(
            left_meta,
            text="Nhập link kênh hoặc @username rồi bấm 'Tải Kênh' để bắt đầu quét",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_channel_stats.pack(anchor="w")

        # Cột phải: KPI đếm trùng lặp
        self.right_kpi = ctk.CTkFrame(self.meta_card, fg_color="transparent")
        self.right_kpi.grid(row=0, column=1, sticky="e", padx=12, pady=8)

        self.lbl_kpi_dup = ctk.CTkLabel(
            self.right_kpi,
            text="0 trùng / 0 đã tải",
            font=("Segoe UI Bold", 14),
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_kpi_dup.pack(anchor="e")

        self.lbl_kpi_status = ctk.CTkLabel(
            self.right_kpi,
            text="Sẵn sàng",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_kpi_status.pack(anchor="e")

        # ----------------------------------------------------------------------
        # 3. BẢNG DANH SÁCH BÀI ĐĂNG (TREEVIEW)
        # ----------------------------------------------------------------------
        table_frame = ctk.CTkFrame(self, fg_color=UIThemeTokens.BG_CARD, corner_radius=8)
        table_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 6))
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        columns = ("idx", "desc", "play", "created", "device", "geo", "shadow", "dup_badge", "group_id")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree.heading("idx", text="#")
        self.tree.heading("desc", text="Video (Nội dung / Caption)")
        self.tree.heading("play", text="Xem")
        self.tree.heading("created", text="Ngày Đăng")
        self.tree.heading("device", text="Nguồn")
        self.tree.heading("geo", text="GEO")
        self.tree.heading("shadow", text="Shadowban")
        self.tree.heading("dup_badge", text="Trạng Thái")
        self.tree.heading("group_id", text="ID Gốc (Group ID)")

        self.tree.column("idx", width=40, anchor="center")
        self.tree.column("desc", width=300, anchor="w")
        self.tree.column("play", width=70, anchor="center")
        self.tree.column("created", width=120, anchor="center")
        self.tree.column("device", width=70, anchor="center")
        self.tree.column("geo", width=60, anchor="center")
        self.tree.column("shadow", width=90, anchor="center")
        self.tree.column("dup_badge", width=110, anchor="center")
        self.tree.column("group_id", width=130, anchor="center")

        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        sb.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)

        # Style tags
        self.tree.tag_configure("dup_row", background="#2a1215", foreground="#f87171")
        self.tree.tag_configure("orig_row", background="#132318", foreground="#4ade80")
        self.tree.tag_configure("normal_row", foreground="#e2e8f0")

        # Binds
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)

        # Context Menu
        self._context_menu = None

        # ----------------------------------------------------------------------
        # 4. FOOTER & ACTIONS
        # ----------------------------------------------------------------------
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))
        footer.grid_columnconfigure(0, weight=1)

        self.lbl_status = ctk.CTkLabel(
            footer,
            text="Nhập kênh để phân tích bài trùng lặp công khai.",
            font=UIThemeTokens.FONT_BADGE,
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_status.grid(row=0, column=0, sticky="w", padx=4)

        btn_box = ctk.CTkFrame(footer, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        self.btn_more = ctk.CTkButton(
            btn_box,
            text="⬇ Tải Thêm",
            command=self.load_more_posts,
            fg_color=UIThemeTokens.BG_CARD,
            hover_color=UIThemeTokens.BG_TOOLTIP,
            width=100,
            height=32,
            font=UIThemeTokens.FONT_SUBHEADER,
            state="disabled",
        )
        self.btn_more.pack(side="left", padx=4)

        self.btn_export = ctk.CTkButton(
            btn_box,
            text="📄 Xuất CSV",
            command=self.export_csv,
            fg_color=UIThemeTokens.BG_CARD,
            hover_color=UIThemeTokens.BG_TOOLTIP,
            width=100,
            height=32,
            font=UIThemeTokens.FONT_SUBHEADER,
        )
        self.btn_export.pack(side="left", padx=4)

    # --------------------------------------------------------------------------
    # BUSINESS LOGIC & FETCHING
    # --------------------------------------------------------------------------
    def start_load_channel(self) -> None:
        """Kích hoạt tải kênh lần đầu (BR-F3-01)."""
        raw_input = self.ent_search.get().strip()
        if not raw_input:
            messagebox.showwarning("Thiếu thông tin", "Vui lòng nhập @username hoặc link kênh TikTok.")
            return

        if self._is_loading:
            return

        self._is_loading = True
        self.btn_load.configure(state="disabled")
        self.btn_more.configure(state="disabled")
        self.lbl_status.configure(text="Đang lấy thông tin kênh công khai...", text_color=UIThemeTokens.ACCENT_PRIMARY)
        self.lbl_kpi_status.configure(text="Đang phân tích...")

        # Xóa bảng cũ
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._posts.clear()
        self._next_cursor = 0
        self._has_more = False

        threading.Thread(target=self._worker_load_channel, args=(raw_input,), daemon=True).start()

    def _worker_load_channel(self, channel_input: str) -> None:
        # Bước 1: Resolve metadata
        ok, meta, msg = self.client.resolve_channel_metadata(channel_input)
        if not ok:
            self.after(0, self._handle_load_error, msg)
            return

        self._channel_meta = meta

        # Bước 2: Fetch trang đầu
        sec_uid = meta.sec_uid
        p_ok, posts, next_cursor, has_more, p_msg = self.client.fetch_channel_videos(
            sec_uid=sec_uid, count=35, max_cursor=0
        )

        self.after(0, self._handle_first_page_result, p_ok, posts, next_cursor, has_more, p_msg)

    def _handle_load_error(self, err_msg: str) -> None:
        self._is_loading = False
        self.btn_load.configure(state="normal")
        self.lbl_status.configure(text=f"Lỗi: {err_msg}", text_color="#ef4444")
        self.lbl_kpi_status.configure(text="Lỗi")
        messagebox.showerror("Không thể tải kênh", err_msg)

    def _handle_first_page_result(
        self,
        success: bool,
        posts: List[AwemePost],
        next_cursor: int,
        has_more: bool,
        msg: str,
    ) -> None:
        self._is_loading = False
        self.btn_load.configure(state="normal")

        # Cập nhật thông tin kênh
        meta = self._channel_meta
        self.lbl_channel_name.configure(text=f"{meta.nickname} (@{meta.unique_id})")
        followers_str = format_views(meta.followers)
        self.lbl_channel_stats.configure(
            text=f"Followers: {followers_str}  •  Tổng video khai báo: {meta.video_count}"
        )

        if not success:
            self.lbl_status.configure(text=f"Lỗi: {msg}", text_color="#ef4444")
            self.lbl_kpi_status.configure(text="Lỗi tải bài")
            return

        self._posts.extend(posts)
        self._next_cursor = next_cursor
        self._has_more = has_more

        self._render_posts(posts)
        self._update_kpi_and_controls()
        self.lbl_status.configure(text=msg, text_color=UIThemeTokens.TEXT_MUTED)

    def load_more_posts(self) -> None:
        """Tải trang tiếp theo dùng secUid (BR-F3-01)."""
        if self._is_loading or not self._has_more:
            return

        sec_uid = self._channel_meta.sec_uid
        if not sec_uid:
            return

        self._is_loading = True
        self.btn_more.configure(state="disabled")
        self.lbl_status.configure(text="Đang tải thêm video...", text_color=UIThemeTokens.ACCENT_PRIMARY)
        self.lbl_kpi_status.configure(text="Đang tải...")

        cursor = self._next_cursor
        threading.Thread(target=self._worker_load_more, args=(sec_uid, cursor), daemon=True).start()

    def _worker_load_more(self, sec_uid: str, cursor: int) -> None:
        p_ok, posts, next_cursor, has_more, p_msg = self.client.fetch_channel_videos(
            sec_uid=sec_uid, count=35, max_cursor=cursor
        )
        self.after(0, self._handle_more_page_result, p_ok, posts, next_cursor, has_more, p_msg)

    def _handle_more_page_result(
        self,
        success: bool,
        posts: List[AwemePost],
        next_cursor: int,
        has_more: bool,
        msg: str,
    ) -> None:
        self._is_loading = False

        if not success:
            self.lbl_status.configure(text=f"Lỗi tải thêm: {msg}", text_color="#ef4444")
            self.btn_more.configure(state="normal" if self._has_more else "disabled")
            return

        self._posts.extend(posts)
        self._next_cursor = next_cursor
        self._has_more = has_more

        self._render_posts(posts)
        self._update_kpi_and_controls()
        self.lbl_status.configure(text=f"Đã tải thêm {len(posts)} video mới.", text_color=UIThemeTokens.TEXT_MUTED)

    # --------------------------------------------------------------------------
    # UI RENDERING & HELPERS
    # --------------------------------------------------------------------------
    def _render_posts(self, new_posts: List[AwemePost]) -> None:
        start_idx = len(self._posts) - len(new_posts) + 1
        for i, post in enumerate(new_posts):
            idx = start_idx + i
            desc_short = post.desc.replace("\n", " ").strip()
            if len(desc_short) > 60:
                desc_short = desc_short[:57] + "..."
            if not desc_short:
                desc_short = f"Video ID #{post.aweme_id}"

            views_txt = format_views(post.play)

            # Date
            dt_txt = ""
            if post.create_time:
                dt_txt = datetime.fromtimestamp(post.create_time).strftime("%Y-%m-%d %H:%M")

            # Device
            device_txt = "🌐 Web" if post.device == "web" else "📱 Phone"

            # Geo
            geo_txt = post.region.upper() if post.region else "—"

            # Shadowban
            if post.shadow == "yes":
                shadow_txt = "🔴 Có"
            elif post.shadow == "suspect":
                shadow_txt = "🟡 Nghi ngờ"
            else:
                shadow_txt = "🟢 Không"

            # Dup status & badge (BR-F3-03)
            if post.is_dup:
                dup_txt = "🔴 TRÙNG"
                tag = "dup_row"
                orig_id = post.group_id
            else:
                dup_txt = "🟢 Gốc"
                tag = "orig_row"
                orig_id = "—"

            row_id = self.tree.insert(
                "",
                "end",
                iid=post.aweme_id,
                values=(
                    idx,
                    desc_short,
                    views_txt,
                    dt_txt,
                    device_txt,
                    geo_txt,
                    shadow_txt,
                    dup_txt,
                    orig_id,
                ),
                tags=(tag,),
            )

    def _update_kpi_and_controls(self) -> None:
        """Cập nhật đếm và trạng thái (BR-F3-02)."""
        total_loaded = len(self._posts)
        dup_count = sum(1 for p in self._posts if p.is_dup)

        color = "#ef4444" if dup_count > 0 else "#22c55e"
        self.lbl_kpi_dup.configure(
            text=f"{dup_count} trùng / {total_loaded} đã tải",
            text_color=color,
        )

        if self._has_more:
            self.lbl_kpi_status.configure(text="Còn video tiếp theo", text_color="#38bdf8")
            self.btn_more.configure(state="normal")
        else:
            self.lbl_kpi_status.configure(text="Đã tải toàn bộ kênh", text_color=UIThemeTokens.TEXT_MUTED)
            self.btn_more.configure(state="disabled")

    # --------------------------------------------------------------------------
    # USER ACTIONS: OPEN LINK, COPY, EXPORT
    # --------------------------------------------------------------------------
    def _on_tree_double_click(self, event) -> None:
        """BR-F3-06: Mở link video TikTok trên trình duyệt ngoài."""
        selected = self.tree.selection()
        if not selected:
            return
        aweme_id = selected[0]
        post = next((p for p in self._posts if p.aweme_id == aweme_id), None)
        if not post:
            return

        username = self._channel_meta.unique_id
        if username:
            url = f"https://www.tiktok.com/@{username}/video/{post.aweme_id}"
        else:
            url = f"https://www.tiktok.com/video/{post.aweme_id}"

        try:
            webbrowser.open(url)
        except Exception as e:
            logger.warning(f"Không thể mở browser: {e}")

    def _on_tree_right_click(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        post = next((p for p in self._posts if p.aweme_id == item), None)
        if not post:
            return

        menu = ctk.CTkMenu(self) if hasattr(ctk, "CTkMenu") else None
        # Sử dụng tkinter.Menu tiêu chuẩn để tránh phụ thuộc
        from tkinter import Menu
        tk_menu = Menu(self, tearoff=0)

        username = self._channel_meta.unique_id or "user"
        video_url = f"https://www.tiktok.com/@{username}/video/{post.aweme_id}"

        tk_menu.add_command(
            label="🌐 Mở video trên TikTok",
            command=lambda: webbrowser.open(video_url),
        )
        tk_menu.add_command(
            label="📋 Copy link video",
            command=lambda: self._copy_to_clipboard(video_url),
        )
        tk_menu.add_command(
            label="📋 Copy ID video này",
            command=lambda: self._copy_to_clipboard(post.aweme_id),
        )

        if post.is_dup and post.group_id:
            orig_url = f"https://www.tiktok.com/video/{post.group_id}"
            tk_menu.add_separator()
            tk_menu.add_command(
                label=f"🔴 Mở video GỐC (#{post.group_id})",
                command=lambda: webbrowser.open(orig_url),
            )
            tk_menu.add_command(
                label="📋 Copy link video GỐC",
                command=lambda: self._copy_to_clipboard(orig_url),
            )
            tk_menu.add_command(
                label="📋 Copy ID video GỐC",
                command=lambda: self._copy_to_clipboard(post.group_id),
            )

        try:
            tk_menu.tk_popup(event.x_root, event.y_root)
        finally:
            tk_menu.grab_release()

    def _copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.lbl_status.configure(text=f"Đã copy: {text}", text_color="#38bdf8")

    def export_csv(self) -> None:
        """Xuất danh sách video đã phân tích ra file CSV."""
        if not self._posts:
            messagebox.showinfo("Chưa có dữ liệu", "Chưa có danh sách video để xuất.")
            return

        default_name = f"TikTok_CheckDup_{self._channel_meta.unique_id or 'channel'}_{int(datetime.now().timestamp())}.csv"
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "STT",
                    "Aweme ID",
                    "Tiêu đề",
                    "Lượt xem",
                    "Lượt thích",
                    "Bình luận",
                    "Ngày đăng",
                    "Nguồn",
                    "GEO",
                    "Shadowban",
                    "Trạng thái",
                    "Group ID (Video Gốc)",
                    "Link Video",
                    "Link Video Gốc",
                ])

                username = self._channel_meta.unique_id
                for idx, post in enumerate(self._posts, 1):
                    dt_str = datetime.fromtimestamp(post.create_time).strftime("%Y-%m-%d %H:%M:%S") if post.create_time else ""
                    video_link = f"https://www.tiktok.com/@{username}/video/{post.aweme_id}" if username else f"https://www.tiktok.com/video/{post.aweme_id}"
                    orig_link = f"https://www.tiktok.com/video/{post.group_id}" if post.is_dup else ""
                    status_str = "TRÙNG LẶP" if post.is_dup else "GỐC"

                    writer.writerow([
                        idx,
                        post.aweme_id,
                        post.desc,
                        post.play,
                        post.like,
                        post.comment,
                        dt_str,
                        post.device,
                        post.region,
                        post.shadow,
                        status_str,
                        post.group_id if post.is_dup else "",
                        video_link,
                        orig_link,
                    ])

            messagebox.showinfo("Thành công", f"Đã xuất {len(self._posts)} dòng vào file:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Lỗi xuất file", f"Không thể lưu file CSV: {e}")


class CheckDupDialog(ctk.CTkToplevel):
    """
    Hộp thoại độc lập CheckDupDialog (F3).
    Có thể mở từ bất kỳ đâu mà không cần profile hay tài khoản đăng nhập.
    """

    def __init__(self, parent, initial_channel: str = "", *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.title("Check Video Trùng Theo Kênh TikTok (Công Khai)")
        self.geometry("980x680")
        self.minsize(800, 500)

        try:
            self.transient(parent)
        except Exception:
            pass

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.view = CheckDupView(self, initial_channel=initial_channel)
        self.view.grid(row=0, column=0, sticky="nsew")
