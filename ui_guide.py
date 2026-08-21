"""
ui_guide.py - Tab Hướng Dẫn Sử Dụng (User Guide) cho DONGLAO-TIKTOK.

Module Presentation chuẩn User-Centric:
- Sử dụng ngôn ngữ hành động rõ ràng, giải thích theo lợi ích và triệu chứng người dùng.
- Loại bỏ 100% các thuật ngữ kỹ thuật nội bộ khỏi nội dung hiển thị cho người dùng.
- Cung cấp GUIDE_SECTIONS (schema block dict chuẩn hóa), các hàm logic thuần (Pure functions)
  và build_guide_workspace() hỗ trợ Filter Chips, Accordion, Sao chép mẫu và Điều hướng tab.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import customtkinter as ctk

from ui_components import UIThemeTokens

# ==============================================================================
# ALLOWLIST & CONSTANTS
# ==============================================================================

SAFE_NAVIGATION_TARGETS: Set[str] = {"profiles", "youtube", "monetization"}

FILTER_CATEGORIES: List[Tuple[str, str, str]] = [
    ("all", "Tất cả", "📋"),
    ("start", "Bắt đầu", "🚀"),
    ("accounts", "Tài khoản", "👤"),
    ("upload", "Đăng video", "🎬"),
    ("youtube", "YouTube", "📺"),
    ("kyc", "Thu nhập & KYC", "💰"),
    ("proxy", "Proxy", "🌐"),
    ("errors", "Xử lý lỗi", "🛠️"),
]

# ==============================================================================
# GUIDE DATA SECTIONS (SCHEMA BLOCK DICTIONARY)
# ==============================================================================

GUIDE_SECTIONS: List[Dict[str, Any]] = [
    {
        "id": "start",
        "tag": "start",
        "icon": "🚀",
        "title": "1. Bắt Đầu Sử Dụng",
        "subtitle": "Chuẩn bị và khởi động phần mềm lần đầu tiên",
        "blocks": [
            {
                "kind": "intro",
                "text": "DONGLAO-TIKTOK là công cụ tự động hóa toàn diện giúp bạn quản lý nhiều tài khoản TikTok, đăng video tự động, theo dõi kênh YouTube và theo dõi thu nhập tập trung.",
            },
            {
                "kind": "step",
                "text": "Tải phần mềm về máy và giải nén toàn bộ ra một thư mục riêng biệt.",
            },
            {
                "kind": "step",
                "text": "Chạy file TikTokAutoUploader.exe để mở ứng dụng. Luôn giữ nguyên các file và thư mục đi kèm cạnh file exe.",
            },
            {
                "kind": "step",
                "text": "Kích hoạt bản quyền: Nhập License Key được cấp ở lần mở đầu tiên. Phần mềm sẽ tự động ghi nhớ và kích hoạt sẵn sàng cho các lần sau.",
            },
            {
                "kind": "step",
                "text": "Nếu Windows hiển thị cửa sổ bảo mật hỏi quyền kết nối mạng, hãy chọn 'Cho phép' (Allow) để các tính năng tự động hoạt động bình thường.",
            },
            {
                "kind": "warn",
                "text": "Không xóa hoặc di chuyển các thư mục dữ liệu đi kèm phần mềm để tránh làm mất thông tin tài khoản và phiên đăng nhập.",
            },
        ],
    },
    {
        "id": "add_account",
        "tag": "accounts",
        "icon": "👤",
        "title": "2. Thêm Tài Khoản TikTok",
        "subtitle": "Tạo và thiết lập từng tài khoản độc lập",
        "blocks": [
            {
                "kind": "intro",
                "text": "Mỗi tài khoản được quản lý trong một hồ sơ riêng biệt với môi trường mạng và trình duyệt cách ly an toàn.",
            },
            {
                "kind": "step",
                "text": "Bấm nút 'Thêm' trên thanh công cụ quản lý tài khoản.",
            },
            {
                "kind": "step",
                "text": "Điền Tên tài khoản, TikTok ID (ví dụ @username), và chọn Thư mục lưu video tương ứng.",
            },
            {
                "kind": "step",
                "text": "⚡ Dán Nhanh: Bạn có thể dán danh sách tài khoản theo mẫu bên dưới để phần mềm tự động điền toàn bộ các trường:",
            },
            {
                "kind": "example",
                "text": "TenTaiKhoan|email@example.com|<MAT_KHAU>|@tiktok_id|<KHOA_2FA>|<COOKIE_NEU_CO>|<PROXY_NEU_CO>",
                "copy_text": "TenTaiKhoan|email@example.com|<MAT_KHAU>|@tiktok_id|<KHOA_2FA>|<COOKIE_NEU_CO>|<PROXY_NEU_CO>",
            },
            {
                "kind": "step",
                "text": "Nếu sử dụng Proxy riêng cho tài khoản, hãy nhập vào mục Proxy và bấm 'Kiểm Tra Proxy' để xem trạng thái kết nối và vùng IP trước khi lưu.",
            },
            {
                "kind": "bullet",
                "text": "Có thể chọn 'Tự động sinh thư mục chuẩn' để phần mềm tự tạo thư mục lưu trữ video gọn gàng cho từng tài khoản.",
            },
            {
                "kind": "warn",
                "text": "Mỗi tài khoản TikTok nên sử dụng một địa chỉ Proxy riêng cùng quốc gia với tài khoản để đạt độ ổn định và an toàn cao nhất.",
            },
        ],
    },
    {
        "id": "login_cookie",
        "tag": "accounts",
        "icon": "🍪",
        "title": "3. Đăng Nhập Và Cookie",
        "subtitle": "Cơ chế đăng nhập tự động và duy trì phiên làm việc",
        "blocks": [
            {
                "kind": "intro",
                "text": "Phần mềm hỗ trợ cơ chế đăng nhập ưu tiên cookie: vào thẳng tài khoản mà không cần gõ lại mật khẩu hay mã xác minh nhiều lần.",
            },
            {
                "kind": "step",
                "text": "Bấm nút 'Login/Mở trình duyệt' trên thanh công cụ. Nếu tài khoản đã có cookie hợp lệ, trình duyệt sẽ tự động mở thẳng trang TikTok cá nhân.",
            },
            {
                "kind": "step",
                "text": "Nếu tài khoản chưa có cookie hoặc cần đăng nhập lại, hãy đăng nhập thủ công trên cửa sổ trình duyệt (nhập mật khẩu, mã OTP hoặc hoàn thành mảnh ghép nếu có).",
            },
            {
                "kind": "step",
                "text": "Sau khi đăng nhập thành công vào trang chủ TikTok, hãy đóng cửa sổ trình duyệt. Phần mềm sẽ tự động nhận diện và lưu phiên làm việc mới.",
            },
            {
                "kind": "bullet",
                "text": "Huy hiệu trạng thái trực quan: 🟢 Cookie Sống (sẵn sàng đăng bài) | 🔴 Cookie Die (phiên hết hạn) | ⚪ Chưa Có Cookie.",
            },
            {
                "kind": "warn",
                "text": "Khi thấy trạng thái 'Cookie Die' hoặc thông báo yêu cầu đăng nhập lại, chỉ cần chọn tài khoản đó, bấm 'Login/Mở trình duyệt' và đăng nhập lại một lần.",
            },
        ],
    },
    {
        "id": "manage_accounts",
        "tag": "accounts",
        "icon": "🗂️",
        "title": "4. Quản Lý Danh Sách Tài Khoản",
        "subtitle": "Tìm kiếm, sắp xếp và phân nhóm các tài khoản",
        "blocks": [
            {
                "kind": "intro",
                "text": "Bảng điều khiển danh sách tài khoản hiển thị đầy đủ thông tin: Tên, TikTok ID, Trạng thái Cookie, Thu nhập, Proxy và Thư mục video.",
            },
            {
                "kind": "step",
                "text": "Tìm kiếm nhanh: Gõ tên tài khoản, TikTok ID hoặc ghi chú vào ô tìm kiếm ở đầu bảng để lọc danh sách tức thì.",
            },
            {
                "kind": "step",
                "text": "Thao tác chuột phải: Nhấp chuột phải vào một dòng tài khoản để mở menu tiện ích: Sửa thông tin, Mở trình duyệt, Xem chi tiết, Đổi tên hoặc Xóa.",
            },
            {
                "kind": "step",
                "text": "Phân nhóm Dự Án: Sử dụng danh sách Dự Án ở cột bên trái để gom nhóm các tài khoản theo chiến dịch hoặc nhóm quản lý.",
            },
            {
                "kind": "step",
                "text": "Chọn tài khoản dễ dàng: Khi gán tài khoản cho kênh YouTube hoặc tải video, sử dụng ô tìm kiếm tài khoản để gán chính xác trong 1 giây.",
            },
            {
                "kind": "shortcut",
                "text": "Đi đến Danh sách tài khoản",
                "target": "profiles",
            },
        ],
    },
    {
        "id": "auto_upload",
        "tag": "upload",
        "icon": "🎬",
        "title": "5. Đăng Video Tự Động",
        "subtitle": "Tự động quét thư mục và đăng tải video lên TikTok",
        "blocks": [
            {
                "kind": "intro",
                "text": "Phần mềm tự động theo dõi thư mục video đã chọn, nhận diện video mới và tiến hành đăng tải tự động lên TikTok.",
            },
            {
                "kind": "step",
                "text": "Chuẩn bị video trong thư mục video đã gán cho tài khoản.",
            },
            {
                "kind": "step",
                "text": "Chọn các tài khoản muốn vận hành rồi bấm nút 'Start chọn' (hoặc bấm 'Start dự án' để chạy toàn bộ).",
            },
            {
                "kind": "step",
                "text": "Phần mềm tự động kiểm tra phiên làm việc, chuẩn bị video và đăng tải ngầm mà không làm ảnh hưởng đến công việc của bạn trên máy tính.",
            },
            {
                "kind": "bullet",
                "text": "Theo dõi tiến độ qua các trạng thái: Đang chờ video mới, Đang xử lý, Đang đăng bài, Đã đăng thành công hoặc Báo lỗi chi tiết.",
            },
            {
                "kind": "bullet",
                "text": "Tự động tối ưu: Phần mềm tự động điều chỉnh độ dài một số video ngắn khi cần thiết để phù hợp với quy định đăng video của TikTok.",
            },
            {
                "kind": "warn",
                "text": "Không tắt phần mềm khi đang có tiến trình đăng video để tránh việc gián đoạn phiên tải lên.",
            },
        ],
    },
    {
        "id": "youtube_sync",
        "tag": "youtube",
        "icon": "📺",
        "title": "6. Tải Video Từ YouTube",
        "subtitle": "Tự động bắt video mới từ YouTube và tải hàng loạt theo danh sách",
        "blocks": [
            {
                "kind": "intro",
                "text": "Hệ thống YouTube Monitor tự động phát hiện khi các kênh YouTube bạn theo dõi phát hành video mới, tự tải về và chuyển vào thư mục chờ đăng của tài khoản TikTok.",
            },
            {
                "kind": "step",
                "text": "Thêm kênh: Dán đường link kênh YouTube cần theo dõi và chọn tài khoản TikTok nhận video tương ứng.",
            },
            {
                "kind": "step",
                "text": "Bấm 'Bắt Đầu' trong tab YouTube Monitor để kích hoạt chế độ tự động nhận thông báo video mới.",
            },
            {
                "kind": "step",
                "text": "Tải Hàng Loạt (Batch Download): Khi cần tải nhiều video cùng lúc, chuyển sang tab Tải Hàng Loạt, dán danh sách link YouTube và bấm Tải.",
            },
            {
                "kind": "step",
                "text": "Khi gặp thông báo YouTube yêu cầu xác minh bot: Bấm chọn đường dẫn file cookie YouTube trong cài đặt hoặc gán proxy cho tài khoản rồi tải lại.",
            },
            {
                "kind": "shortcut",
                "text": "Đi đến YouTube Monitor",
                "target": "youtube",
            },
        ],
    },
    {
        "id": "monetization_kyc",
        "tag": "kyc",
        "icon": "💰",
        "title": "7. Thu Nhập, KYC Và Thuế",
        "subtitle": "Theo dõi số dư, quỹ kiếm tiền CRP, xác minh danh tính và thuế",
        "blocks": [
            {
                "kind": "intro",
                "text": "Bảng tổng quan tài chính giúp bạn nắm bắt doanh thu và tình trạng tài khoản của toàn bộ hệ sinh thái chỉ trong một màn hình.",
            },
            {
                "kind": "bullet",
                "text": "Các chỉ số quan trọng: Tổng số dư khả dụng ($), Quỹ kiếm tiền (CRP), Tình trạng xác minh danh tính (KYC), Khai báo thuế và Rút tiền (Payout).",
            },
            {
                "kind": "step",
                "text": "Bấm 'Cập Nhật Tất Cả' hoặc 'Cập Nhật Đã Chọn' để đồng bộ số dư và trạng thái mới nhất trực tiếp từ TikTok.",
            },
            {
                "kind": "step",
                "text": "Theo dõi 4 nhóm trạng thái xác minh đơn giản: Đã xác minh (xanh lá) | Đang chờ duyệt (vàng) | Cần gửi lại giấy tờ | Cần kiểm tra / Kháng nghị.",
            },
            {
                "kind": "warn",
                "text": "Khi thấy trạng thái 'Cần gửi lại giấy tờ' hoặc 'Cần kiểm tra', hãy mở ứng dụng TikTok trên điện thoại hoặc bấm mở trình duyệt để bổ sung tài liệu theo đúng hạn chót.",
            },
            {
                "kind": "shortcut",
                "text": "Đi đến Thu nhập & KYC",
                "target": "monetization",
            },
        ],
    },
    {
        "id": "proxy_maintenance",
        "tag": "proxy",
        "icon": "🌐",
        "title": "8. Proxy Và Bảo Trì",
        "subtitle": "Thiết lập kết nối mạng an toàn và tối ưu phần mềm",
        "blocks": [
            {
                "kind": "intro",
                "text": "Sử dụng Proxy giúp bảo vệ từng tài khoản TikTok hoạt động trên địa chỉ mạng riêng biệt, tránh bị liên đới tài khoản.",
            },
            {
                "kind": "step",
                "text": "Mẫu nhập Proxy hỗ trợ định dạng phổ biến sau (không chứa thông tin thật):",
            },
            {
                "kind": "example",
                "text": "IP:Port\nIP:Port:<TEN_DANG_NHAP>:<MAT_KHAU>",
                "copy_text": "IP:Port:<TEN_DANG_NHAP>:<MAT_KHAU>",
            },
            {
                "kind": "step",
                "text": "Luôn bấm nút 'Kiểm Tra Proxy' trước khi lưu để xác nhận địa chỉ IP và Quốc gia của proxy có đang hoạt động tốt hay không.",
            },
            {
                "kind": "step",
                "text": "Khi proxy báo lỗi kết nối: Kiểm tra lại thông tin IP, cổng, tài khoản, mật khẩu hoặc liên hệ nhà cung cấp proxy để đổi địa chỉ mới.",
            },
            {
                "kind": "step",
                "text": "Bảo trì trình duyệt: Nếu trình duyệt gặp sự cố hiển thị, vào menu Cài Đặt → chọn 'Reset Browser' để dọn dẹp bộ nhớ đệm an toàn.",
            },
            {
                "kind": "step",
                "text": "Cập nhật công cụ tải: Khi việc tải video YouTube gặp trục trặc, bấm nút 'Cập nhật yt-dlp' trên giao diện YouTube để nhận bản nâng cấp mới nhất.",
            },
        ],
    },
    {
        "id": "troubleshooting",
        "tag": "errors",
        "icon": "🛠️",
        "title": "9. Xử Lý Lỗi Thường Gặp",
        "subtitle": "Hướng dẫn xử lý nhanh 7 tình huống thường gặp theo từng bước",
        "blocks": [
            {
                "kind": "intro",
                "text": "Dưới đây là các tình huống phổ biến và cách khắc phục nhanh chóng:",
            },
            {
                "kind": "bullet",
                "text": "1. Không mở được trình duyệt:\n- Dấu hiệu: Bấm 'Login/Mở trình duyệt' nhưng không thấy cửa sổ hiện ra.\n- Cách xử lý: Kiểm tra phần mềm diệt virus hoặc tường lửa Windows có đang chặn ứng dụng không; chọn Cài Đặt → Reset Browser rồi thử lại.\n- Khi nào cần liên hệ hỗ trợ: Khi đã Reset Browser nhưng vẫn không mở được cửa sổ.",
            },
            {
                "kind": "bullet",
                "text": "2. Cookie đã hết hạn (Cookie Die):\n- Dấu hiệu: Bảng tài khoản hiển thị chấm đỏ 'Cookie Die' hoặc upload báo lỗi đăng nhập.\n- Cách xử lý: Chọn tài khoản → Bấm 'Login/Mở trình duyệt' → Đăng nhập lại vào TikTok trên cửa sổ hiện ra → Đóng cửa sổ để lưu phiên mới.\n- Khi nào cần liên hệ hỗ trợ: Khi đăng nhập xong nhưng đóng cửa sổ vẫn không nhận diện được trạng thái.",
            },
            {
                "kind": "bullet",
                "text": "3. Proxy không kết nối được:\n- Dấu hiệu: Bấm 'Kiểm Tra Proxy' báo lỗi kết nối hoặc thời gian chờ quá lâu.\n- Cách xử lý: Kiểm tra lại chuỗi định dạng IP, cổng, tài khoản, mật khẩu; thử kiểm tra proxy trên website khác hoặc đổi proxy mới.\n- Khi nào cần liên hệ hỗ trợ: Khi proxy chắc chắn hoạt động ở ngoài nhưng phần mềm không nhận.",
            },
            {
                "kind": "bullet",
                "text": "4. YouTube yêu cầu xác minh bot:\n- Dấu hiệu: Thông báo 'Sign in to confirm you're not a bot' khi tải video YouTube.\n- Cách xử lý: Thêm file cookie YouTube đã đăng nhập trong cài đặt hoặc gán proxy cho tài khoản nhận video.\n- Khi nào cần liên hệ hỗ trợ: Khi đã nạp cookie YouTube mới mà vẫn báo lỗi tương tự.",
            },
            {
                "kind": "bullet",
                "text": "5. Không tải được video từ YouTube:\n- Dấu hiệu: Bấm tải nhưng báo lỗi hoặc tiến trình dừng lại.\n- Cách xử lý: Bấm nút 'Cập nhật yt-dlp' trên giao diện YouTube Monitor; kiểm tra đường link video có tồn tại công khai hay không.\n- Khi nào cần liên hệ hỗ trợ: Khi link video mở xem bình thường trên web nhưng phần mềm không tải được.",
            },
            {
                "kind": "bullet",
                "text": "6. Không đăng được video lên TikTok:\n- Dấu hiệu: Tiến trình đăng báo lỗi hoặc video không xuất hiện trên kênh.\n- Cách xử lý: Kiểm tra trạng thái Cookie có màu xanh 'Cookie Sống' không; kiểm tra định dạng file video (MP4) và dung lượng; đảm bảo tài khoản không bị dính án phạt đăng bài từ TikTok.\n- Khi nào cần liên hệ hỗ trợ: Khi cookie sống và video hợp lệ nhưng liên tục thất bại ở bước tải lên.",
            },
            {
                "kind": "bullet",
                "text": "7. Không nhận được thông báo video mới từ YouTube:\n- Dấu hiệu: Kênh YouTube đã đăng video mới nhưng phần mềm chưa tự động nhận.\n- Cách xử lý: Kiểm tra trạng thái YouTube Monitor xem đã bấm 'Bắt Đầu' chưa; đảm bảo máy tính có kết nối mạng ổn định.\n- Khi nào cần liên hệ hỗ trợ: Khi monitor đang chạy bình thường nhưng quá 30 phút vẫn chưa nhận video mới.",
            },
            {
                "kind": "note",
                "text": "Mọi thắc mắc cần hỗ trợ kỹ thuật chuyên sâu, vui lòng liên hệ đội ngũ quản trị DONGLAO-TIKTOK.",
            },
        ],
    },
]


# ==============================================================================
# PURE LOGIC FUNCTIONS (TESTABLE ON CI WITHOUT DISPLAY)
# ==============================================================================

def get_safe_navigation_target(target: str) -> Optional[str]:
    """Kiểm tra và trả về target điều hướng hợp lệ nằm trong allowlist an toàn."""
    target_clean = str(target or "").strip().lower()
    return target_clean if target_clean in SAFE_NAVIGATION_TARGETS else None


def filter_guide_sections(
    sections: List[Dict[str, Any]],
    query: str = "",
    category_tag: str = "all",
) -> List[Dict[str, Any]]:
    """
    Lọc danh sách chủ đề hướng dẫn theo từ khóa tìm kiếm và danh mục (Pure function).
    Không làm thay đổi danh sách sections gốc.
    """
    q = str(query or "").strip().lower()
    cat = str(category_tag or "all").strip().lower()

    results: List[Dict[str, Any]] = []

    for section in sections:
        sec_tag = str(section.get("tag", "")).lower()

        # 1. Kiểm tra Category filter
        if cat != "all" and sec_tag != cat:
            continue

        # 2. Kiểm tra Search query filter
        if not q:
            results.append(section)
            continue

        # Xây dựng chuỗi tìm kiếm từ title, subtitle và text của các block
        searchable_text_parts = [
            str(section.get("title", "")),
            str(section.get("subtitle", "")),
        ]
        for block in section.get("blocks", []):
            if isinstance(block, dict):
                searchable_text_parts.append(str(block.get("text", "")))
                searchable_text_parts.append(str(block.get("copy_text", "")))

        full_searchable = " ".join(searchable_text_parts).lower()
        if q in full_searchable:
            results.append(section)

    return results


def format_guide_summary(total: int, matching: int) -> str:
    """Định dạng nhãn tóm tắt số lượng hướng dẫn đang hiển thị."""
    return f"Đang xem {matching}/{total} hướng dẫn"


# ==============================================================================
# UI RENDERER & INTERACTIVE COMPONENTS
# ==============================================================================

def _copy_to_clipboard(widget: Any, text: str, feedback_label: Optional[ctk.CTkLabel] = None) -> bool:
    """Sao chép text vào clipboard an toàn và hiển thị thông báo phản hồi."""
    try:
        if hasattr(widget, "clipboard_clear"):
            widget.clipboard_clear()
        if hasattr(widget, "clipboard_append"):
            widget.clipboard_append(text)
        if hasattr(widget, "update"):
            widget.update()
        if feedback_label:
            if hasattr(feedback_label, "winfo_exists") and feedback_label.winfo_exists():
                feedback_label.configure(text="✅ Đã sao chép!", text_color=UIThemeTokens.STATUS_LIVE)
                if hasattr(widget, "after"):
                    widget.after(2000, lambda: _reset_feedback_label(feedback_label))
        return True
    except Exception:
        try:
            if feedback_label and hasattr(feedback_label, "winfo_exists") and feedback_label.winfo_exists():
                feedback_label.configure(text="❌ Lỗi sao chép", text_color=UIThemeTokens.STATUS_ERROR)
        except Exception:
            pass
        return False


def _reset_feedback_label(lbl: ctk.CTkLabel) -> None:
    try:
        if lbl and lbl.winfo_exists():
            lbl.configure(text="", text_color=UIThemeTokens.TEXT_MUTED)
    except Exception:
        pass


def _render_block_item(
    parent_frame: Any,
    block: Dict[str, Any],
    step_index: int,
    on_navigate: Optional[Callable[[str], None]] = None,
) -> None:
    """Render một block nội dung đơn lẻ bên trong thân card."""
    kind = block.get("kind", "bullet")
    text = block.get("text", "")

    if kind == "intro":
        lbl = ctk.CTkLabel(
            parent_frame,
            text=f"ℹ️ {text}",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            justify="left",
            anchor="w",
            wraplength=620,
        )
        lbl.pack(fill="x", padx=12, pady=(2, 4))

    elif kind == "step":
        lbl = ctk.CTkLabel(
            parent_frame,
            text=f"Bước {step_index}. {text}",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.ACCENT_PRIMARY,
            justify="left",
            anchor="w",
            wraplength=620,
        )
        lbl.pack(fill="x", padx=12, pady=(2, 3))

    elif kind == "bullet":
        lbl = ctk.CTkLabel(
            parent_frame,
            text=f"• {text}",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            justify="left",
            anchor="w",
            wraplength=620,
        )
        lbl.pack(fill="x", padx=12, pady=(2, 2))

    elif kind == "note":
        lbl = ctk.CTkLabel(
            parent_frame,
            text=f"💡 Lưu ý: {text}",
            font=UIThemeTokens.FONT_SUBTITLE,
            text_color=UIThemeTokens.TEXT_MUTED,
            justify="left",
            anchor="w",
            wraplength=620,
        )
        lbl.pack(fill="x", padx=12, pady=(3, 3))

    elif kind == "warn":
        lbl = ctk.CTkLabel(
            parent_frame,
            text=f"⚠️ Cảnh báo: {text}",
            font=UIThemeTokens.FONT_BODY,
            text_color=UIThemeTokens.STATUS_WARN,
            justify="left",
            anchor="w",
            wraplength=620,
        )
        lbl.pack(fill="x", padx=12, pady=(3, 3))

    elif kind == "example":
        # Khung code snippet có nút Sao chép mẫu
        box = ctk.CTkFrame(
            parent_frame,
            fg_color=UIThemeTokens.BG_ROOT,
            corner_radius=8,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )
        box.pack(fill="x", padx=12, pady=(4, 6))

        box_top = ctk.CTkFrame(box, fg_color="transparent")
        box_top.pack(fill="x", padx=8, pady=(6, 4))

        ctk.CTkLabel(
            box_top,
            text="📄 Mẫu định dạng tham khảo:",
            font=("Segoe UI Semibold", 10),
            text_color=UIThemeTokens.TEXT_MUTED,
            anchor="w",
        ).pack(side="left")

        feedback_lbl = ctk.CTkLabel(
            box_top,
            text="",
            font=("Segoe UI", 10),
            text_color=UIThemeTokens.TEXT_MUTED,
        )
        feedback_lbl.pack(side="right", padx=(0, 6))

        copy_text = block.get("copy_text") or text
        btn_copy = ctk.CTkButton(
            box_top,
            text="📋 Sao chép mẫu",
            width=110,
            height=24,
            font=("Segoe UI", 10, "bold"),
            fg_color=UIThemeTokens.ACCENT_PRIMARY,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            command=lambda: _copy_to_clipboard(parent_frame, copy_text, feedback_lbl),
        )
        btn_copy.pack(side="right")

        # Nội dung text mẫu
        lbl_code = ctk.CTkLabel(
            box,
            text=text,
            font=("Consolas", 11),
            text_color=UIThemeTokens.TEXT_PRIMARY,
            justify="left",
            anchor="w",
            wraplength=600,
        )
        lbl_code.pack(fill="x", padx=10, pady=(0, 8))

    elif kind == "shortcut":
        # Nút điều hướng interactive
        raw_target = block.get("target", "")
        safe_target = get_safe_navigation_target(raw_target)
        if safe_target and on_navigate and callable(on_navigate):
            btn_box = ctk.CTkFrame(parent_frame, fg_color="transparent")
            btn_box.pack(fill="x", padx=12, pady=(4, 4))

            btn_nav = ctk.CTkButton(
                btn_box,
                text=f"👉 {text}",
                font=("Segoe UI Semibold", 11),
                height=30,
                fg_color=UIThemeTokens.BG_CARD,
                hover_color=UIThemeTokens.BG_HOVER,
                border_width=1,
                border_color=UIThemeTokens.ACCENT_PRIMARY,
                text_color=UIThemeTokens.ACCENT_PRIMARY,
                command=lambda t=safe_target: on_navigate(t),
            )
            btn_nav.pack(anchor="w")


class GuideCardView:
    """Quản lý giao diện và trạng thái Thu gọn / Mở rộng (Accordion) của một thẻ hướng dẫn."""

    def __init__(
        self,
        scroll_parent: Any,
        section_data: Dict[str, Any],
        initially_expanded: bool = False,
        on_navigate: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.scroll_parent = scroll_parent
        self.section_data = section_data
        self.section_id = str(section_data.get("id", section_data.get("title", "")))
        self.is_expanded = bool(initially_expanded)
        self.user_manual_expanded = bool(initially_expanded)
        self.on_navigate = on_navigate

        # Card container
        self.card = ctk.CTkFrame(
            scroll_parent,
            corner_radius=10,
            fg_color=UIThemeTokens.BG_CARD,
            border_width=1,
            border_color=UIThemeTokens.BORDER_LIGHT,
        )

        # Header Frame
        self.header = ctk.CTkFrame(self.card, fg_color="transparent")
        self.header.pack(fill="x", padx=12, pady=(10, 8))

        # Title & Subtitle container
        title_box = ctk.CTkFrame(self.header, fg_color="transparent")
        title_box.pack(side="left", fill="x", expand=True)

        icon = section_data.get("icon", "📘")
        title_text = section_data.get("title", "")
        self.lbl_title = ctk.CTkLabel(
            title_box,
            text=f"{icon}  {title_text}",
            font=("Segoe UI Semibold", 13),
            text_color=UIThemeTokens.TEXT_PRIMARY,
            anchor="w",
        )
        self.lbl_title.pack(anchor="w")

        subtitle = section_data.get("subtitle")
        if subtitle:
            self.lbl_sub = ctk.CTkLabel(
                title_box,
                text=subtitle,
                font=UIThemeTokens.FONT_SUBTITLE,
                text_color=UIThemeTokens.TEXT_MUTED,
                anchor="w",
            )
            self.lbl_sub.pack(anchor="w", pady=(1, 0))

        # Accordion Toggle Button
        self.btn_toggle = ctk.CTkButton(
            self.header,
            text="Thu gọn ▲" if self.is_expanded else "Xem chi tiết ▼",
            width=96,
            height=26,
            font=("Segoe UI", 10, "bold"),
            fg_color=UIThemeTokens.BG_ROOT,
            hover_color=UIThemeTokens.BG_HOVER,
            text_color=UIThemeTokens.TEXT_PRIMARY,
            command=self._on_toggle_clicked,
        )
        self.btn_toggle.pack(side="right", padx=(8, 0))

        # Body Frame (chứa các block nội dung)
        self.body = ctk.CTkFrame(self.card, fg_color="transparent")
        self._populate_body()

        # Áp dụng trạng thái ban đầu
        if self.is_expanded:
            self.body.pack(fill="x", padx=4, pady=(0, 10))

    def _populate_body(self) -> None:
        step_index = 0
        for block in self.section_data.get("blocks", []):
            if isinstance(block, dict):
                if block.get("kind") == "step":
                    step_index += 1
                _render_block_item(self.body, block, step_index, self.on_navigate)

    def _on_toggle_clicked(self) -> None:
        """Người dùng chủ động click nút Thu gọn / Xem chi tiết."""
        self.is_expanded = not self.is_expanded
        self.user_manual_expanded = self.is_expanded
        self._apply_expanded_state()

    def set_expanded_state(self, expanded: bool) -> None:
        """Đặt trạng thái mở/đóng tạm thời (dùng khi tìm kiếm từ khóa)."""
        self.is_expanded = bool(expanded)
        self._apply_expanded_state()

    def restore_user_manual_state(self) -> None:
        """Khôi phục lại trạng thái mở/đóng mà người dùng đã thiết lập trước đó."""
        self.is_expanded = bool(self.user_manual_expanded)
        self._apply_expanded_state()

    def _apply_expanded_state(self) -> None:
        if self.is_expanded:
            if not self.body.winfo_manager():
                self.body.pack(fill="x", padx=4, pady=(0, 10))
            self.btn_toggle.configure(text="Thu gọn ▲")
        else:
            if self.body.winfo_manager():
                self.body.pack_forget()
            self.btn_toggle.configure(text="Xem chi tiết ▼")

    def show(self) -> None:
        if not self.card.winfo_manager():
            self.card.pack(fill="x", padx=2, pady=5)

    def hide(self) -> None:
        if self.card.winfo_manager():
            self.card.pack_forget()


def build_guide_workspace(
    parent: Any,
    on_navigate: Optional[Callable[[str], None]] = None,
) -> ctk.CTkScrollableFrame:
    """
    Xây dựng toàn bộ giao diện Tab Hướng Dẫn:
    - Banner thương hiệu
    - Ô tìm kiếm từ khóa
    - Hàng nút Filter Chips danh mục
    - Nhãn đếm số lượng kết quả
    - Accordion các thẻ bài nội dung
    - Khung Empty State khi không có kết quả
    """
    scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent", corner_radius=0)
    scroll.pack(fill="both", expand=True, padx=6, pady=6)

    # 1. Header Card
    intro_card = ctk.CTkFrame(
        scroll,
        corner_radius=12,
        fg_color=UIThemeTokens.BG_CARD,
        border_width=1,
        border_color=UIThemeTokens.BORDER_LIGHT,
    )
    intro_card.pack(fill="x", padx=2, pady=(0, 6))

    intro_header = ctk.CTkFrame(intro_card, fg_color="transparent")
    intro_header.pack(fill="x", padx=14, pady=(12, 10))

    # Top Brand Banner
    top_banner = ctk.CTkFrame(intro_header, fg_color="transparent")
    top_banner.pack(fill="x", pady=(0, 6))

    logo_path = Path(__file__).resolve().parent / "assets" / "donglao_browser_logo.png"
    if logo_path.exists():
        try:
            from PIL import Image
            pil_img = Image.open(logo_path)
            logo_ctk = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(46, 46))
            lbl_logo = ctk.CTkLabel(top_banner, image=logo_ctk, text="")
            lbl_logo.pack(side="left", padx=(0, 10))
            scroll._guide_logo_ref = logo_ctk
        except Exception:
            pass

    text_container = ctk.CTkFrame(top_banner, fg_color="transparent")
    text_container.pack(side="left", fill="x", expand=True)

    ctk.CTkLabel(
        text_container,
        text="📖 Trung Tâm Hướng Dẫn & Tài Liệu Vận Hành",
        font=("Segoe UI", 13, "bold"),
        text_color=UIThemeTokens.TEXT_PRIMARY,
        anchor="w",
    ).pack(anchor="w")

    ctk.CTkLabel(
        text_container,
        text="Cẩm nang hướng dẫn thao tác từng bước: thêm tài khoản, đăng video tự động, đồng bộ YouTube và theo dõi thu nhập.",
        font=UIThemeTokens.FONT_SUBTITLE,
        text_color=UIThemeTokens.TEXT_MUTED,
        anchor="w",
    ).pack(anchor="w", pady=(1, 0))

    # Search Entry
    search_var = ctk.StringVar(value="")
    search_entry = ctk.CTkEntry(
        intro_header,
        textvariable=search_var,
        placeholder_text="🔍 Tìm hướng dẫn, ví dụ: đăng nhập, proxy, YouTube, KYC, video...",
        height=32,
        corner_radius=8,
        font=("Segoe UI", 11),
    )
    search_entry.pack(fill="x", pady=(2, 6))

    # Filter Chips Frame
    chips_frame = ctk.CTkFrame(intro_header, fg_color="transparent")
    chips_frame.pack(fill="x", pady=(2, 4))

    selected_category = ctk.StringVar(value="all")
    chip_buttons: Dict[str, ctk.CTkButton] = {}

    def _on_chip_selected(cat_key: str) -> None:
        selected_category.set(cat_key)
        _update_chip_styles()
        _refresh_display()

    def _update_chip_styles() -> None:
        current = selected_category.get()
        for key, btn in chip_buttons.items():
            is_active = (key == current)
            btn.configure(
                fg_color=UIThemeTokens.ACCENT_PRIMARY if is_active else UIThemeTokens.BG_ROOT,
                text_color=UIThemeTokens.TEXT_PRIMARY,
                border_width=0 if is_active else 1,
            )

    for cat_key, cat_label, cat_icon in FILTER_CATEGORIES:
        btn_chip = ctk.CTkButton(
            chips_frame,
            text=f"{cat_icon} {cat_label}",
            font=("Segoe UI", 10, "bold" if cat_key == "all" else "normal"),
            height=24,
            width=68,
            corner_radius=6,
            fg_color=UIThemeTokens.ACCENT_PRIMARY if cat_key == "all" else UIThemeTokens.BG_ROOT,
            hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
            border_width=0 if cat_key == "all" else 1,
            border_color=UIThemeTokens.BORDER_LIGHT,
            command=lambda k=cat_key: _on_chip_selected(k),
        )
        btn_chip.pack(side="left", padx=(0, 4), pady=2)
        chip_buttons[cat_key] = btn_chip

    # Summary Result Label
    summary_label = ctk.CTkLabel(
        intro_header,
        text=format_guide_summary(len(GUIDE_SECTIONS), len(GUIDE_SECTIONS)),
        font=("Segoe UI", 10),
        text_color=UIThemeTokens.TEXT_MUTED,
        anchor="w",
    )
    summary_label.pack(anchor="w", pady=(2, 0))

    # 2. Render Cards List
    cards_map: Dict[str, GuideCardView] = {}
    for idx, section in enumerate(GUIDE_SECTIONS):
        # Mặc định chỉ mở thẻ đầu tiên (idx == 0)
        is_first = (idx == 0)
        card_view = GuideCardView(
            scroll_parent=scroll,
            section_data=section,
            initially_expanded=is_first,
            on_navigate=on_navigate,
        )
        cards_map[section["id"]] = card_view
        card_view.show()

    # 3. Empty State View Frame
    empty_state_frame = ctk.CTkFrame(
        scroll,
        corner_radius=10,
        fg_color=UIThemeTokens.BG_CARD,
        border_width=1,
        border_color=UIThemeTokens.BORDER_LIGHT,
    )
    lbl_empty_icon = ctk.CTkLabel(empty_state_frame, text="🔍", font=("Segoe UI", 28))
    lbl_empty_icon.pack(pady=(16, 4))

    lbl_empty_msg = ctk.CTkLabel(
        empty_state_frame,
        text="Không tìm thấy hướng dẫn phù hợp.",
        font=("Segoe UI Semibold", 12),
        text_color=UIThemeTokens.TEXT_PRIMARY,
    )
    lbl_empty_msg.pack(pady=(0, 2))

    lbl_empty_sub = ctk.CTkLabel(
        empty_state_frame,
        text="Hãy thử tìm kiếm với từ khóa khác hoặc chọn xem 'Tất cả' danh mục.",
        font=UIThemeTokens.FONT_SUBTITLE,
        text_color=UIThemeTokens.TEXT_MUTED,
    )
    lbl_empty_sub.pack(pady=(0, 10))

    def _reset_search() -> None:
        search_var.set("")
        selected_category.set("all")
        _update_chip_styles()
        _refresh_display()

    btn_reset = ctk.CTkButton(
        empty_state_frame,
        text="🔄 Xóa tìm kiếm",
        width=120,
        height=28,
        font=("Segoe UI", 10, "bold"),
        fg_color=UIThemeTokens.ACCENT_PRIMARY,
        hover_color=UIThemeTokens.ACCENT_PRIMARY_HOVER,
        command=_reset_search,
    )
    btn_reset.pack(pady=(0, 16))

    # 4. Refresh & Search Logic
    def _refresh_display(*_args: Any) -> None:
        query = search_var.get().strip()
        cat = selected_category.get()

        matching_sections = filter_guide_sections(GUIDE_SECTIONS, query=query, category_tag=cat)
        matching_ids = {s["id"] for s in matching_sections}

        summary_label.configure(text=format_guide_summary(len(GUIDE_SECTIONS), len(matching_sections)))

        has_query = bool(query)

        for sec_id, card_view in cards_map.items():
            if sec_id in matching_ids:
                card_view.show()
                if has_query:
                    # Tạm thời mở các card khớp khi có từ khóa tìm kiếm
                    card_view.set_expanded_state(True)
                else:
                    # Khôi phục trạng thái mở/đóng thủ công của người dùng khi xóa query
                    card_view.restore_user_manual_state()
            else:
                card_view.hide()

        # Hiển thị hoặc ẩn Empty State
        if not matching_sections:
            if not empty_state_frame.winfo_manager():
                empty_state_frame.pack(fill="x", padx=2, pady=10)
        else:
            if empty_state_frame.winfo_manager():
                empty_state_frame.pack_forget()

    search_var.trace_add("write", _refresh_display)

    return scroll