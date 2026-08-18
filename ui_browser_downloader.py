"""
ui_browser_downloader.py - Modern CustomTkinter Dialog for Browser Engine Download & Updates.
"""

import threading
import time
from pathlib import Path
from typing import Optional, Callable

import customtkinter as ctk

from ui_components import UIThemeTokens, fit_and_center_dialog
from browser_engine_manager import (
    get_browser_root_dir,
    download_file_with_progress,
    extract_engine_zip_atomic,
    compute_sha256,
)


class BrowserEngineDownloadDialog(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        download_url: str,
        target_engine_name: str = "donglao-browser-144",
        expected_sha256: Optional[str] = None,
        on_complete: Optional[Callable[[bool, str], None]] = None,
    ):
        super().__init__(parent)
        self.parent_win = parent
        self.download_url = download_url
        self.target_engine_name = target_engine_name
        self.expected_sha256 = expected_sha256
        self.on_complete = on_complete

        self.cancel_event = threading.Event()
        self.download_thread: Optional[threading.Thread] = None

        self.title("Tải Động Cơ Trình Duyệt Dong Lao TikTok Browser 144")
        fit_and_center_dialog(self, 520, 260, parent=parent, min_w=460, min_h=240)
        self.configure(fg_color=UIThemeTokens.BG_ROOT)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self.after(200, self._start_download)

    def _build_ui(self):
        self.card = ctk.CTkFrame(
            self,
            fg_color=UIThemeTokens.BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        self.card.pack(fill="both", expand=True, padx=14, pady=14)

        top_row = ctk.CTkFrame(self.card, fg_color="transparent")
        top_row.pack(fill="x", padx=16, pady=(16, 4))

        logo_path = Path(__file__).resolve().parent / "assets" / "donglao_browser_logo.png"
        if logo_path.exists():
            try:
                from PIL import Image
                pil_img = Image.open(logo_path)
                self._logo_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(44, 44))
                ctk.CTkLabel(top_row, image=self._logo_img, text="").pack(side="left", padx=(0, 10))
            except Exception:
                pass

        title_container = ctk.CTkFrame(top_row, fg_color="transparent")
        title_container.pack(side="left", fill="x", expand=True)

        self.lbl_title = ctk.CTkLabel(
            title_container,
            text="🚀 Đang Tải Browser Engine (DONGLAO Browser 144)",
            font=("Segoe UI", 13, "bold"),
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        )
        self.lbl_title.pack(anchor="w")

        self.lbl_status = ctk.CTkLabel(
            title_container,
            text="Đang kết nối tới máy chủ...",
            font=("Segoe UI", 11),
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        )
        self.lbl_status.pack(anchor="w", pady=(2, 0))

        self.progress_bar = ctk.CTkProgressBar(
            self.card,
            height=12,
            corner_radius=6,
            progress_color=UIThemeTokens.ACCENT_PRIMARY,
        )
        self.progress_bar.pack(fill="x", padx=16, pady=(4, 6))
        self.progress_bar.set(0.0)

        self.lbl_details = ctk.CTkLabel(
            self.card,
            text="0 MB / 0 MB (0%)",
            font=("Segoe UI", 10),
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        self.lbl_details.pack(anchor="e", padx=16, pady=(0, 12))

        self.btn_row = ctk.CTkFrame(self.card, fg_color="transparent")
        self.btn_row.pack(side="bottom", fill="x", padx=16, pady=(0, 14))

        self.btn_cancel = ctk.CTkButton(
            self.btn_row,
            text="Hủy Bỏ",
            fg_color="#ef4444",
            hover_color="#dc2626",
            height=30,
            width=90,
            command=self._on_close,
        )
        self.btn_cancel.pack(side="right")

    def _start_download(self):
        self.download_thread = threading.Thread(target=self._worker, daemon=True)
        self.download_thread.start()

    def _worker(self):
        b_root = get_browser_root_dir()
        temp_zip = b_root / f".engine_{self.target_engine_name}.zip"
        target_dir = b_root / self.target_engine_name

        def _progress(downloaded, total, speed_mbps):
            if self.cancel_event.is_set():
                return
            pct = (downloaded / total) if total > 0 else 0.0
            dl_mb = downloaded / (1024 * 1024)
            tot_mb = total / (1024 * 1024)
            self.after(
                0,
                lambda: self._update_ui_progress(pct, dl_mb, tot_mb, speed_mbps),
            )

        try:
            self.after(0, lambda: self.lbl_status.configure(text="Đang tải dữ liệu động cơ..."))
            download_file_with_progress(
                url=self.download_url,
                dest_path=temp_zip,
                progress_callback=_progress,
                cancel_event=self.cancel_event,
            )

            if self.cancel_event.is_set():
                return

            self.after(
                0,
                lambda: self.lbl_status.configure(
                    text="Đang xác thực và giải nén trình duyệt..."
                ),
            )
            extract_engine_zip_atomic(
                zip_path=temp_zip,
                target_engine_dir=target_dir,
                expected_sha256=self.expected_sha256,
            )

            try:
                temp_zip.unlink(missing_ok=True)
            except OSError:
                pass

            self.after(0, self._on_success)
        except Exception as exc:
            if not self.cancel_event.is_set():
                self.after(0, lambda e=str(exc): self._on_error(e))

    def _update_ui_progress(self, pct: float, dl_mb: float, tot_mb: float, speed_mbps: float):
        try:
            self.progress_bar.set(pct)
            self.lbl_details.configure(
                text=f"{dl_mb:.1f} MB / {tot_mb:.1f} MB ({int(pct * 100)}%) • {speed_mbps:.1f} MB/s"
            )
        except Exception:
            pass

    def _on_success(self):
        self.progress_bar.set(1.0)
        self.lbl_status.configure(
            text="✅ Cài đặt Browser Engine thành công!",
            text_color=UIThemeTokens.STATUS_LIVE,
        )
        self.btn_cancel.configure(text="Đóng", fg_color=UIThemeTokens.ACCENT_PRIMARY, hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER)
        if self.on_complete:
            self.on_complete(True, "Success")
        self.after(1200, self.destroy)

    def _on_error(self, err_msg: str):
        self.lbl_status.configure(
            text=f"❌ Lỗi: {err_msg[:45]}...",
            text_color=UIThemeTokens.STATUS_ERROR,
        )
        self.btn_cancel.configure(text="Đóng")
        if self.on_complete:
            self.on_complete(False, err_msg)

    def _on_close(self):
        self.cancel_event.set()
        self.destroy()
