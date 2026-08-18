"""
ui_guide.py - Tab Hướng Dẫn Sử Dụng (User Guide) cho DONGLAO-TIKTOK.

Module thuần Presentation:
- Chỉ import ui_components (Design Tokens) — không đụng main.py / browser / repository.
- Cung cấp GUIDE_SECTIONS (nội dung hướng dẫn có cấu trúc chuẩn) và
  build_guide_workspace() (render thành các thẻ bài cuộn được hỗ trợ tìm kiếm nhanh).
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import customtkinter as ctk

from ui_components import UIThemeTokens

# Kiểu block trong mỗi mục hướng dẫn:
#   ("intro", text)  -> đoạn mô tả mở đầu
#   ("step", text)   -> bước thực hiện (tự đánh số Bước 1, 2, ...)
#   ("bullet", text) -> gạch đầu dòng
#   ("note", text)   -> lưu ý (màu xám, icon ℹ️)
#   ("warn", text)   -> cảnh báo (màu vàng, icon ⚠️)

GUIDE_SECTIONS: List[Dict[str, Any]] = [
    {
        "icon": "🚀",
        "title": "1. Cài Đặt & Lần Chạy Đầu",
        "subtitle": "Chuẩn bị máy và khởi động phần mềm lần đầu",
        "blocks": [
            ("intro", "DONGLAO-TIKTOK là bộ công cụ chuyên nghiệp tự động hóa đăng video TikTok từ nhiều hồ sơ độc lập, tích hợp theo dõi YouTube, quản lý thu nhập / KYC và nhân trình duyệt chống phát hiện."),
            ("step", "Tải bản phát hành mới nhất từ hệ thống và giải nén ra một thư mục riêng biệt."),
            ("step", "Chạy file TikTokAutoUploader.exe — luôn giữ nguyên cấu trúc thư mục _internal và Browser bên cạnh exe."),
            ("step", "Hệ thống tự động tải và kích hoạt nhân trình duyệt riêng biệt DONGLAO Browser 144 (DONGLAO Antidetect Engine), công cụ FFmpeg và ngrok."),
            ("bullet", "Yêu cầu hệ thống: Windows 10/11 64-bit; kết nối internet ổn định; cho phép exe và DONGLAO Browser qua tường lửa / anti-virus."),
            ("step", "Kích hoạt bản quyền: Nhập License Key được cấp ở lần chạy đầu tiên. Hệ thống tự động ghi nhớ key và kích hoạt ngầm (Silent Boot) ở các lần mở sau mà không làm phiền bạn."),
            ("warn", "Không xóa các thư mục Auto_Data, profiles/ hoặc Browser/ để tránh làm mất dữ liệu hồ sơ và phiên đăng nhập."),
        ],
    },
    {
        "icon": "👤",
        "title": "2. Thêm Hồ Sơ (Profiles)",
        "subtitle": "Tạo và cấu hình tài khoản TikTok độc lập",
        "blocks": [
            ("intro", "Mỗi hồ sơ (Profile) tương ứng với một tài khoản TikTok chạy trong môi trường trình duyệt và proxy riêng biệt."),
            ("step", "Bấm nút Thêm trên thanh công cụ quản lý profile."),
            ("step", "⚡ Dán Nhanh: Dán chuỗi phân tách bằng | hoặc Tab theo định dạng Name|Email|Pass|TikTokID|2FA|Cookie|Proxy rồi bấm Áp Dụng để tự động điền các trường."),
            ("bullet", "Thẻ Thông Tin Nhận Diện: Tên hồ sơ (*), Dự án / Nhóm, Email liên kết, TikTok ID (@username hoặc UID), Ghi chú."),
            ("bullet", "Thẻ Bảo Mật & Xác Thực: Mật khẩu TikTok, Khóa 2FA (Secret Key), Cookie TikTok (nếu có sẵn)."),
            ("bullet", "Thẻ Proxy & Mạng: Bật Kích hoạt Proxy, chọn loại HTTP hoặc SOCKS5, nhập chuỗi IP:Port hoặc IP:Port:User:Pass, bấm ⚡ Kiểm Tra Proxy để xem quốc gia và IP thực tế."),
            ("bullet", "Thẻ Thư Mục & Vận Hành: Bật Tự động sinh thư mục chuẩn Auto_Data/<Tên>/Video và /Profile (khuyên dùng); cấu hình Giới hạn video/ngày (0 = không giới hạn), Chạy ngầm (Headless), Chỉ mở khi có video mới."),
            ("step", "Nhập hàng loạt: Dùng nút ⋯ → Import Tài Khoản Hàng Loạt để dán danh sách nhiều tài khoản cùng lúc."),
            ("warn", "Mỗi tài khoản nên dùng 1 proxy riêng cùng quốc gia với tài khoản để đảm bảo độ tin cậy cao nhất."),
        ],
    },
    {
        "icon": "🍪",
        "title": "3. Cookie & Đăng Nhập",
        "subtitle": "Cơ chế Login Ưu Tiên Cookie và Tự Động Lưu Session Ngầm",
        "blocks": [
            ("intro", "Hệ thống hỗ trợ cơ chế quản lý phiên đăng nhập thông minh 1 chạm (Zero Manual Effort):"),
            ("step", "🌐 Login / Mở trình duyệt (Cookie-First): Bấm nút 'Login/Mở trình duyệt' trên thanh công cụ. Nếu hồ sơ đã có Cookie, trình duyệt tự động nạp phiên và vào thẳng TikTok mà không cần gõ lại mật khẩu."),
            ("step", "Đăng nhập lần đầu: Nếu hồ sơ chưa có Cookie, trình duyệt sẽ mở trang đăng nhập TikTok để bạn thao tác thủ công (OTP / Captcha)."),
            ("step", "Tự động Lưu Cookie & Thông Tin Tài Khoản Ngầm (Headless): Khi bạn tắt cửa sổ trình duyệt, hệ thống tự động chạy ngầm ở chế độ headless để trích xuất cookie mới nhất, lưu vào cấu hình và tự động quét lấy UID (tiktok_user_id), @username, và Quốc gia (Region) cập nhật ngay lên bảng giao diện."),
            ("step", "Check Cookie Trực Tiếp: Chọn hồ sơ → Bấm Check Cookie để kiểm tra nhanh tình trạng phiên làm việc (Live / Die)."),
            ("bullet", "Hệ thống huy hiệu trạng thái: 🟢 Cookie Sống / 🔴 Cookie Die / ⚪ Chưa Có Cookie / 🟢 Đã KYC / 🟢 Đã Khai Thuế / 🔴 TKTBM (Bảo Mật)."),
            ("warn", "Khi tài khoản bị Cookie Die hoặc TKTBM (Bảo Mật), hãy chọn hồ sơ và bấm 'Login/Mở trình duyệt' để xác minh lại phiên làm việc."),
        ],
    },
    {
        "icon": "🗂️",
        "title": "4. Quản Lý Hồ Sơ",
        "subtitle": "Bảng danh sách, phân nhóm dự án và thao tác dữ liệu",
        "blocks": [
            ("intro", "Bảng Quản Lý Hồ Sơ hiển thị trực quan: Tên, TikTok ID, UID, Khu Vực, Trạng Thái Cookie, Thu Nhập, Proxy/Vùng, Trạng Thái Upload và Thư Mục Video."),
            ("step", "Chọn một hồ sơ → Dùng nút ⋯ trên thanh công cụ hoặc Chuột Phải để thực hiện các thao tác: Sửa, Xem chi tiết, Đổi tên, Gán Dự án, Export Dữ liệu, Kiểm tra thông tin TikTok, Kiểm tra thu nhập."),
            ("bullet", "Double-click một dòng bất kỳ để mở cửa sổ Xem Chi Tiết Toàn Diện của hồ sơ."),
            ("bullet", "Ô tìm kiếm ở đầu bảng hỗ trợ lọc nhanh tức thì theo Tên / TikTok ID / Proxy / Note / Thư mục."),
            ("step", "Quản lý Dự Án: Dùng khung 📁 DỰ ÁN ở sidebar bên trái với nút [+] / [-] để tạo nhóm và lọc danh sách hồ sơ theo từng chiến dịch."),
            ("step", "Sao lưu dữ liệu: Dùng chức năng Xuất Dữ Liệu Hồ Sơ để sao lưu hoặc chuyển đổi sang máy khác an toàn."),
        ],
    },
    {
        "icon": "🎬",
        "title": "5. Đăng Video (Auto Upload)",
        "subtitle": "Quét thư mục video và tự động đăng tải lên TikTok",
        "blocks": [
            ("intro", "Mỗi hồ sơ tự động theo dõi thư mục video đã chỉ định, quét các video mới và thực hiện đăng tải tự động lên TikTok."),
            ("bullet", "Giới hạn video/ngày: Khống chế số lượng video đăng mỗi ngày để bảo vệ tài khoản an toàn."),
            ("bullet", "Chạy ngầm (Headless): Đăng video hoàn toàn ẩn dưới nền, không chiếm dụng màn hình làm việc."),
            ("bullet", "Chỉ mở khi có video mới: Tối ưu tài nguyên hệ thống, trình duyệt chỉ khởi chạy khi phát hiện video chờ đăng."),
            ("step", "Vận hành: Chọn hồ sơ → Bấm Start chọn để bắt đầu tự động hóa; Bấm Stop chọn để dừng. Có thể Start/Stop toàn bộ dự án."),
            ("step", "Theo dõi trạng thái: Quan sát thanh Nhật Ký Hoạt Động (3 tab: Quan trọng, Lỗi, Chi tiết) và các thẻ Summary Card trên cùng."),
            ("warn", "Không tắt ứng dụng đột ngột khi hồ sơ đang trong tiến trình tải video lên TikTok."),
        ],
    },
    {
        "icon": "📺",
        "title": "6. YouTube Monitor & Batch",
        "subtitle": "Tự động đồng bộ video từ kênh YouTube sang TikTok",
        "blocks": [
            ("intro", "Theo dõi các kênh YouTube liên kết; khi kênh phát hành video mới, hệ thống tự động nhận thông báo tức thì qua WebSub và chuẩn bị video đăng lên TikTok."),
            ("step", "Hệ thống sử dụng đường truyền an toàn ngrok để tiếp nhận webhook thông báo thời gian thực từ YouTube."),
            ("step", "Thêm kênh: Dán link kênh YouTube và liên kết với hồ sơ TikTok đích tương ứng."),
            ("bullet", "Khi có video mới, hệ thống tự động tải về, xử lý video qua FFmpeg và đưa vào hàng chờ đăng của hồ sơ."),
            ("bullet", "YouTube Batch: Hỗ trợ tải hàng loạt video từ danh sách URL YouTube để phục vụ xây dựng kho nội dung."),
        ],
    },
    {
        "icon": "💰",
        "title": "7. Thu Nhập / KYC",
        "subtitle": "Dashboard tài chính, quỹ sáng tạo CRP, KYC và thuế",
        "blocks": [
            ("intro", "Tab Thu Nhập / KYC cung cấp bức tranh toàn cảnh về tình trạng kiếm tiền của toàn bộ dàn tài khoản TikTok."),
            ("bullet", "5 Chỉ Số KPI Chính: Tổng Số Dư Khả Dụng, Quỹ Kiếm Tiền (CRP), Đã KYC Danh Tính, Đã Khai Báo Thuế, Cảnh Báo TKTBM."),
            ("bullet", "Bảng dữ liệu chi tiết: Tên Profile, TikTok ID, Khu Vực, Quỹ Kiếm Tiền (CRP), Số Dư ($), Trạng Thái Payout, Tờ Khai Thuế, Xác Minh KYC, Phương Thức Rút Tiền."),
            ("step", "Bấm 🔄 Cập Nhật Tất Cả hoặc 🔄 Cập Nhật Đã Chọn để đồng bộ số liệu mới nhất trực tiếp từ TikTok."),
            ("step", "Bấm 🚀 Gửi Duyệt CRP để nộp đơn tham gia Quỹ phần thưởng nhà sáng tạo (Creator Rewards Program) cho tài khoản đủ điều kiện."),
            ("warn", "Theo dõi sát các trạng thái KYC: CDD pending (chờ xác minh nâng cao), POA/ID resubmit (cần gửi lại giấy tờ) và hạn chót kháng nghị (Appeal deadline) để không bị gián đoạn kiếm tiền."),
        ],
    },
    {
        "icon": "🌐",
        "title": "8. Proxy & Bảo Trì",
        "subtitle": "Chuẩn hóa kết nối mạng và quản lý trình duyệt",
        "blocks": [
            ("step", "Định dạng Proxy hỗ trợ: IP:Port hoặc IP:Port:User:Pass (hỗ trợ cả giao thức HTTP và SOCKS5)."),
            ("step", "Luôn bấm ⚡ Kiểm Tra Proxy trước khi lưu để xác nhận địa chỉ IP và Quốc gia của proxy."),
            ("step", "Quản Lý Browser Engine: Hệ thống sử dụng nhân DONGLAO Browser 144 độc lập. Khi cần cập nhật hoặc cài đặt lại, hệ thống hỗ trợ tải tự động qua giao diện với xác thực mã băm SHA-256 an toàn."),
            ("step", "Bảo trì định kỳ: Menu Cài Đặt & Khác → Reset Browser Cache khi cần dọn sạch bộ nhớ đệm trình duyệt."),
        ],
    },
    {
        "icon": "🛠️",
        "title": "9. Khắc Phục Sự Cố",
        "subtitle": "Hướng dẫn xử lý nhanh các tình huống thường gặp",
        "blocks": [
            ("step", "Cookie Die / Hết hạn phiên: Chọn hồ sơ → Bấm 'Login/Mở trình duyệt' → Đăng nhập lại TikTok → Đóng trình duyệt để hệ thống tự động lưu session mới."),
            ("step", "DONGLAO Browser không mở được: Kiểm tra thư mục Browser/orbita-browser-144 bên cạnh app; đảm bảo tường lửa không chặn file thực thi."),
            ("step", "Proxy không kết nối được: Kiểm tra lại chuỗi định dạng (IP, cổng, tài khoản, mật khẩu) và kiểm tra proxy còn hoạt động hay không."),
            ("step", "ngrok không khởi động được: Kiểm tra kết nối mạng và quyền truy cập qua tường lửa cho tiến trình ngrok."),
            ("step", "Upload thất bại: Kiểm tra tab Lỗi trong Nhật Ký Hoạt Động; kiểm tra định dạng và dung lượng file video; đảm bảo tài khoản không bị dính gậy bản quyền."),
            ("note", "Mọi thắc mắc hoặc cần hỗ trợ kỹ thuật chuyên sâu, vui lòng liên hệ đội ngũ quản trị DONGLAO-TIKTOK."),
        ],
    },
]


# ==============================================================================
# RENDERER
# ==============================================================================

_ICON_BY_KIND = {
    "intro": "ℹ️",
    "step": "▸",
    "bullet": "•",
    "note": "ℹ️",
    "warn": "⚠️",
}

_COLOR_BY_KIND = {
    "intro": UIThemeTokens.TEXT_PRIMARY,
    "step": UIThemeTokens.TEXT_PRIMARY,
    "bullet": UIThemeTokens.TEXT_PRIMARY,
    "note": UIThemeTokens.TEXT_MUTED,
    "warn": UIThemeTokens.STATUS_WARN,
}


def _auto_wrap(label: ctk.CTkLabel, parent: Any, padding: int = 26) -> None:
    """Cập nhật wraplength theo chiều rộng thực tế của card để text xuống dòng đẹp."""

    def _on_configure(_event: Any) -> None:
        try:
            width = parent.winfo_width()
            if width > 120:
                label.configure(wraplength=max(120, width - padding))
        except Exception:
            pass

    try:
        parent.bind("<Configure>", _on_configure)
    except Exception:
        pass


def _render_block(card_body: Any, kind: str, text: str, step_index: int) -> None:
    icon = _ICON_BY_KIND.get(kind, "•")
    color = _COLOR_BY_KIND.get(kind, UIThemeTokens.TEXT_PRIMARY)

    prefix = icon
    if kind == "step":
        prefix = f"Bước {step_index}."
        color = UIThemeTokens.ACCENT_PRIMARY

    label = ctk.CTkLabel(
        card_body,
        text=f"{prefix} {text}",
        font=UIThemeTokens.FONT_BODY,
        text_color=color,
        justify="left",
        anchor="w",
        wraplength=600,
    )
    label.pack(fill="x", padx=(14, 14), pady=(2, 2))
    _auto_wrap(label, card_body, padding=28)


def _render_section(scroll: Any, section: Dict[str, Any]) -> ctk.CTkFrame:
    card = ctk.CTkFrame(
        scroll,
        corner_radius=10,
        fg_color=UIThemeTokens.BG_CARD,
        border_width=1,
        border_color=UIThemeTokens.BORDER_LIGHT,
    )
    card.pack(fill="x", padx=2, pady=6)

    header = ctk.CTkFrame(card, fg_color="transparent")
    header.pack(fill="x", padx=14, pady=(10, 4))

    ctk.CTkLabel(
        header,
        text=f"{section.get('icon', '📘')}  {section.get('title', '')}",
        font=("Segoe UI Semibold", 14),
        text_color=UIThemeTokens.TEXT_PRIMARY,
        anchor="w",
    ).pack(anchor="w")

    subtitle = section.get("subtitle")
    if subtitle:
        ctk.CTkLabel(
            header,
            text=subtitle,
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

    card_body = ctk.CTkFrame(card, fg_color="transparent")
    card_body.pack(fill="x", padx=6, pady=(2, 10))

    step_index = 0
    for kind, text in section.get("blocks", []):
        if kind == "step":
            step_index += 1
        _render_block(card_body, kind, text, step_index)

    return card


def build_guide_workspace(parent: Any) -> ctk.CTkScrollableFrame:
    """Xây dựng workspace Hướng Dẫn: thanh tìm kiếm + các thẻ bài nội dung."""
    scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent", corner_radius=0)
    scroll.pack(fill="both", expand=True, padx=6, pady=6)

    # Header Introduction Card
    intro_card = ctk.CTkFrame(
        scroll,
        corner_radius=12,
        fg_color=UIThemeTokens.BG_CARD,
        border_width=1,
        border_color=UIThemeTokens.BORDER_LIGHT,
    )
    intro_card.pack(fill="x", padx=2, pady=(0, 6))

    intro_header = ctk.CTkFrame(intro_card, fg_color="transparent")
    intro_header.pack(fill="x", padx=16, pady=(14, 6))

    top_banner = ctk.CTkFrame(intro_header, fg_color="transparent")
    top_banner.pack(fill="x", pady=(0, 6))

    # Embed brand logo if available
    from pathlib import Path
    logo_path = Path(__file__).resolve().parent / "assets" / "donglao_browser_logo.png"
    if logo_path.exists():
        try:
            from PIL import Image
            pil_img = Image.open(logo_path)
            logo_ctk = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(52, 52))
            lbl_logo = ctk.CTkLabel(top_banner, image=logo_ctk, text="")
            lbl_logo.pack(side="left", padx=(0, 12))
            scroll._guide_logo_ref = logo_ctk  # Prevent GC
        except Exception:
            pass

    text_container = ctk.CTkFrame(top_banner, fg_color="transparent")
    text_container.pack(side="left", fill="x", expand=True)

    ctk.CTkLabel(
        text_container,
        text="📖 Trung Tâm Hướng Dẫn & Tài Liệu Vận Hành DONGLAO-TIKTOK",
        font=("Segoe UI", 14, "bold"),
        text_color=UIThemeTokens.TEXT_PRIMARY,
        anchor="w",
    ).pack(anchor="w")

    ctk.CTkLabel(
        text_container,
        text="Hệ thống tự động đăng video TikTok, tích hợp nhân trình duyệt DONGLAO Browser 144 (DONGLAO Antidetect Engine), theo dõi YouTube và quản lý tài chính / KYC.",
        font=UIThemeTokens.FONT_SUBTITLE,
        text_color=UIThemeTokens.TEXT_MUTED,
        anchor="w",
    ).pack(anchor="w", pady=(2, 0))

    # Search Filter Entry
    search_var = ctk.StringVar(value="")
    search_entry = ctk.CTkEntry(
        intro_header,
        textvariable=search_var,
        placeholder_text="🔍 Gõ từ khóa để lọc nhanh hướng dẫn (VD: proxy, cookie, login, engine, kyc, upload)...",
        height=34,
        corner_radius=8,
        font=("Segoe UI", 12),
    )
    search_entry.pack(fill="x", pady=(2, 6))

    rendered_cards: List[Tuple[ctk.CTkFrame, str]] = []

    for section in GUIDE_SECTIONS:
        # Build searchable text from title, subtitle and blocks
        search_text = f"{section.get('title', '')} {section.get('subtitle', '')} "
        for _kind, text in section.get("blocks", []):
            search_text += f"{text} "
        search_text_lower = search_text.lower()

        card = _render_section(scroll, section)
        rendered_cards.append((card, search_text_lower))

    def _on_search_changed(*_args: Any) -> None:
        query = search_var.get().strip().lower()
        for card, search_text in rendered_cards:
            if not query or query in search_text:
                card.pack(fill="x", padx=2, pady=6)
            else:
                card.pack_forget()

    search_var.trace_add("write", _on_search_changed)

    return scroll