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

SAFE_NAVIGATION_TARGETS: Set[str] = {"profiles", "youtube", "monetization", "statistics", "dedup", "schedule"}

FILTER_CATEGORIES: List[Tuple[str, str, str]] = [
    ("all", "Tất cả", "📋"),
    ("start", "Bắt đầu", "🚀"),
    ("accounts", "Tài khoản", "👤"),
    ("upload", "Đăng video", "🎬"),
    ("schedule", "Lịch Đăng", "📅"),
    ("youtube", "YouTube", "📺"),
    ("kyc", "Thu nhập & KYC", "💰"),
    ("stats", "Thống kê", "📊"),
    ("dedup", "Quét trùng & Gậy", "🛡️"),
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
        "subtitle": "Chuẩn bị, kích hoạt bản quyền và khởi động phần mềm",
        "blocks": [
            {
                "kind": "intro",
                "text": "DONGLAO-TIKTOK là bộ công cụ tự động hóa toàn diện giúp bạn quản lý đa tài khoản TikTok, đăng video tự động qua API hoặc trình duyệt, lên lịch phát hành thông minh, theo dõi kênh YouTube, giám sát gậy bản quyền và tổng hợp thu nhập tập trung.",
            },
            {
                "kind": "step",
                "text": "Tải phần mềm về máy tính và giải nén toàn bộ ra một thư mục riêng biệt (khuyên dùng ổ D hoặc E).",
            },
            {
                "kind": "step",
                "text": "Chạy file TikTokAutoUploader.exe để mở ứng dụng. Luôn giữ nguyên các file và thư mục đi kèm cạnh file exe.",
            },
            {
                "kind": "step",
                "text": "Kích hoạt bản quyền: Nhập License Key được cấp ở lần mở đầu tiên. Phần mềm tự động ghi nhớ và xác thực siêu tốc từ bộ nhớ đệm an toàn trên máy tính của bạn ở những lần mở sau.",
            },
            {
                "kind": "step",
                "text": "Nếu Windows hiển thị cửa sổ bảo mật hỏi quyền kết nối mạng, hãy chọn 'Cho phép' (Allow) để các tính năng tự động hoạt động bình thường.",
            },
            {
                "kind": "step",
                "text": "Tự động cập nhật: Phần mềm tích hợp tính năng kiểm tra phiên bản mới từ máy chủ. Khi có bản phát hành mới, ứng dụng sẽ thông báo để bạn cập nhật chỉ với 1 click.",
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
        "title": "2. Thêm & Nhập Tài Khoản Hàng Loạt",
        "subtitle": "Tạo tài khoản đơn lẻ, nhập hàng loạt theo danh sách và gán Proxy",
        "blocks": [
            {
                "kind": "intro",
                "text": "Mỗi tài khoản được quản lý trong một hồ sơ riêng biệt với môi trường mạng và dấu vân tay trình duyệt cách ly an toàn.",
            },
            {
                "kind": "step",
                "text": "Thêm từng tài khoản: Bấm nút 'Thêm' trên thanh công cụ, điền Tên tài khoản, TikTok ID (ví dụ @username), Proxy (nếu có) và chọn Thư mục video tương ứng.",
            },
            {
                "kind": "step",
                "text": "⚡ Nhập Hàng Loạt (Batch Import): Bấm nút 'Nhập Hàng Loạt' trên thanh công cụ để thêm cùng lúc hàng chục tài khoản. Bạn có thể dán danh sách tài khoản theo mẫu 11 trường chuẩn bên dưới:",
            },
            {
                "kind": "example",
                "text": "TenTaiKhoan|email@example.com|<MAT_KHAU>|@tiktok_id|IP:Port:<USER>:<PASS>|<COOKIE_NEU_CO>|<KHOA_2FA>|<PASS_MAIL>|<MAIL_BACKUP>|<PASS_MAIL_BACKUP>|GhiChu",
                "copy_text": "TenTaiKhoan|email@example.com|<MAT_KHAU>|@tiktok_id|IP:Port:<USER>:<PASS>|<COOKIE_NEU_CO>|<KHOA_2FA>|<PASS_MAIL>|<MAIL_BACKUP>|<PASS_MAIL_BACKUP>|GhiChu",
            },
            {
                "kind": "step",
                "text": "⚡ Mẫu Rút Gọn: Nếu chỉ có thông tin cơ bản, chọn định dạng 'Format cũ' và dán theo mẫu 4 trường sau:",
            },
            {
                "kind": "example",
                "text": "TenTaiKhoan|<COOKIE_NEU_CO>|IP:Port:<USER>:<PASS>|@tiktok_id",
                "copy_text": "TenTaiKhoan|<COOKIE_NEU_CO>|IP:Port:<USER>:<PASS>|@tiktok_id",
            },
            {
                "kind": "step",
                "text": "⚡ Gán Proxy Hàng Loạt: Chọn các tài khoản cần đổi mạng, bấm nút 'Gán Proxy' trên thanh công cụ, dán danh sách proxy và bấm 'Áp Dụng' để phân bổ đều cho từng tài khoản.",
            },
            {
                "kind": "bullet",
                "text": "Bật tùy chọn 'Tự động sinh thư mục chuẩn' để phần mềm tự tạo thư mục lưu trữ video riêng biệt và gọn gàng cho từng tài khoản.",
            },
            {
                "kind": "warn",
                "text": "Mỗi tài khoản TikTok nên sử dụng một địa chỉ Proxy riêng cùng quốc gia với tài khoản để đạt độ ổn định và an toàn cao nhất.",
            },
            {
                "kind": "shortcut",
                "text": "Đi đến Danh sách tài khoản",
                "target": "profiles",
            },
        ],
    },
    {
        "id": "login_cookie",
        "tag": "accounts",
        "icon": "🍪",
        "title": "3. Đăng Nhập Và Quản Lý Cookie",
        "subtitle": "Cơ chế đăng nhập ưu tiên cookie, tự động tiêm cookie và duy trì phiên làm việc",
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
                "text": "Tự động tiêm cookie: Khi khởi động trình duyệt hoặc nạp hồ sơ, hệ thống tự động giải mã và nạp cookie vào phiên duyệt web để duy trì đăng nhập liền mạch.",
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
        "title": "4. Quản Lý Danh Sách Tài Khoản, Dự Án & Check Post",
        "subtitle": "Tìm kiếm thông minh, phân trang, kiểm tra bài đăng và menu chuột phải",
        "blocks": [
            {
                "kind": "intro",
                "text": "Bảng điều khiển danh sách tài khoản hiển thị đầy đủ thông tin: Tên, TikTok ID, Trạng thái Cookie, Thu nhập, Proxy và Thư mục video.",
            },
            {
                "kind": "step",
                "text": "Bộ phân trang mượt mà: Lựa chọn hiển thị 10, 20, 50 hoặc 100 tài khoản trên mỗi trang ở góc dưới bảng điều khiển, giúp phần mềm phản hồi nhanh và cuộn mượt mà ngay cả khi quản trị hàng trăm profile.",
            },
            {
                "kind": "step",
                "text": "Tìm kiếm nhanh: Gõ tên tài khoản, TikTok ID hoặc ghi chú vào ô tìm kiếm ở đầu bảng để lọc danh sách tức thì.",
            },
            {
                "kind": "step",
                "text": "Kiểm tra bài đăng nhanh (Check Post): Bấm nút 'Check Post' trên thanh công cụ hoặc nhấp chuột phải chọn 'Check Post' để mở bảng kiểm tra toàn diện các video đã đăng trên kênh (phát hiện video gốc, trùng lặp, bài bị bóp tương tác hoặc giam duyệt).",
            },
            {
                "kind": "step",
                "text": "Thao tác chuột phải: Nhấp chuột phải vào một dòng tài khoản để mở menu tiện ích: Sửa thông tin, Mở trình duyệt, Lên lịch đăng bài tự động, Xem chi tiết thu nhập, Kiểm tra trùng lặp, Đổi tên hoặc Xóa.",
            },
            {
                "kind": "step",
                "text": "Phân nhóm Dự Án: Sử dụng danh sách Dự Án ở cột bên trái để gom nhóm các tài khoản theo chiến dịch hoặc nhóm quản lý. Có thể chọn nhiều tài khoản và bấm 'Gán Dự Án' để di chuyển nhanh.",
            },
            {
                "kind": "step",
                "text": "Bộ chọn tài khoản thông minh (Searchable Profile Picker): Khi gán tài khoản cho kênh YouTube hoặc thao tác tải video, giao diện tìm kiếm thông minh giúp bạn chọn chính xác tài khoản chỉ trong 1 giây.",
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
        "title": "5. Đăng Video Tự Động Theo Thư Mục",
        "subtitle": "Cơ chế canh thư mục tải lên siêu tốc qua Direct API và dự phòng trình duyệt",
        "blocks": [
            {
                "kind": "intro",
                "text": "Phần mềm tự động theo dõi thư mục video đã chọn, nhận diện video mới và tiến hành đăng tải tự động lên TikTok với tốc độ tối ưu.",
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
                "text": "Chế độ tải lên Direct API siêu tốc (v1.2.7): Mặc định phần mềm áp dụng chế độ Tự động thông minh (Auto). Video được tải lên trực tiếp qua hệ thống API ngầm, giúp đăng video siêu nhanh, không chiếm chuột/bàn phím và tiết kiệm tối đa bộ nhớ máy tính.",
            },
            {
                "kind": "step",
                "text": "Dự phòng thông minh (Lazy Browser Fallback): Nếu TikTok yêu cầu giải câu đố xác minh hoặc kiểm tra đặc biệt, phần mềm sẽ tự động chuyển sang trình duyệt an toàn để hoàn tất việc đăng bài mà không làm gián đoạn tiến trình.",
            },
            {
                "kind": "bullet",
                "text": "Theo dõi tiến độ qua các trạng thái: Đang chờ video mới, Đang tải video (API), Đang xử lý, Đã đăng thành công hoặc Báo lỗi chi tiết.",
            },
            {
                "kind": "bullet",
                "text": "Tự động tối ưu: Phần mềm tự động điều chỉnh độ dài một số video ngắn khi cần thiết để phù hợp với quy định đăng video của TikTok.",
            },
            {
                "kind": "bullet",
                "text": "Lên lịch đăng theo giờ hẹn: Nếu bạn muốn phân bổ hàng loạt video theo các khung giờ vàng cố định trong nhiều ngày và có thể tắt máy tính hoàn toàn, hãy sử dụng tính năng Lên Lịch Đăng (Mục 6).",
            },
            {
                "kind": "warn",
                "text": "Không tắt phần mềm khi đang có tiến trình đăng video để tránh việc gián đoạn phiên tải lên.",
            },
        ],
    },
    {
        "id": "auto_schedule",
        "tag": "schedule",
        "icon": "📅",
        "title": "6. Lên Lịch Đăng TikTok & Hẹn Giờ Server",
        "subtitle": "1-Click lên lịch tự động, hẹn giờ máy chủ TikTok, phân bổ khung giờ vàng và chống trùng lịch",
        "blocks": [
            {
                "kind": "intro",
                "text": "Tính năng Lịch Đăng giúp bạn lên lịch xuất bản hàng chục, hàng trăm video cho nhiều tài khoản TikTok cùng lúc theo các khung giờ vàng chỉ với 1 cú nhấp chuột.",
            },
            {
                "kind": "step",
                "text": "Khởi động lên lịch: Tích chọn các tài khoản trên Bảng Hồ Sơ → Bấm nút 'Lên Lịch' trên thanh công cụ (hoặc nhấp chuột phải chọn 'Lên lịch đăng bài tự động...').",
            },
            {
                "kind": "step",
                "text": "Nạp video: Bấm 'Chọn thư mục' để nạp toàn bộ video trong thư mục, hoặc bấm 'Chọn từng file' để nạp danh sách file video mong muốn. Hệ thống tự động phân bổ đều video cho các tài khoản theo thuật toán công bằng.",
            },
            {
                "kind": "step",
                "text": "Công thức tiêu đề thông minh: Tự động tạo tiêu đề hấp dẫn với các thẻ: {filename} (tên file gốc), {index} (số thứ tự), {date} (ngày đăng), {time} (giờ đăng), {profile} (tên hồ sơ) kèm bảng xem trước mẫu tức thì.",
            },
            {
                "kind": "step",
                "text": "Khung giờ vàng & Độ trễ chống bot (Jitter): Lựa chọn các khung giờ vàng (Sáng 06:00-08:00, Trưa 11:00-13:00, Chiều 17:00-19:00, Tối 19:00-22:00) và cài đặt độ lệch ngẫu nhiên (ví dụ 10-15 phút) để các kênh không đăng trùng giây, tránh bị thuật toán TikTok nhận diện là hành vi máy móc.",
            },
            {
                "kind": "step",
                "text": "☁️ Hẹn giờ trên Server TikTok: Video được tải trước lên máy chủ TikTok và ghim lịch phát hành chính thức (hẹn trước tối đa 10 ngày). Sau khi lên lịch xong, bạn có thể TẮT MÁY TÍNH HOÀN TOÀN, TikTok sẽ tự động công khai video đúng giờ hẹn.",
            },
            {
                "kind": "step",
                "text": "💻 Quản lý hàng đợi trên máy tính: Video lưu trữ trên máy tính, phần mềm chạy ngầm và tự động tải video lên khi đến mốc thời gian đã định.",
            },
            {
                "kind": "step",
                "text": "Giải quyết xung đột trùng lịch: Nếu tài khoản đã có bài chờ đăng từ trước, phần mềm sẽ hiển thị hộp thoại cảnh báo với 3 giải pháp an toàn: Nối tiếp (xếp lịch mới bắt đầu từ ngày hôm sau của lịch cũ - Khuyên dùng), Ghi đè (hủy lịch cũ, áp dụng lịch mới) hoặc Bỏ qua (chỉ lên lịch cho tài khoản còn trống).",
            },
            {
                "kind": "bullet",
                "text": "Tác vụ điều khiển tại Tab Lịch Đăng: Quản lý chi tiết toàn bộ hàng đợi bài đăng; bạn có thể bấm 'Bắt Đầu Chạy Lịch', 'Tạm Dừng', 'Đăng Ngay Lập Tức', 'Thử Lại Lỗi' hoặc 'Hủy Lịch'.",
            },
            {
                "kind": "warn",
                "text": "Đối với chế độ Hẹn giờ trên Server TikTok, thời gian hẹn cần cách thời điểm hiện tại tối thiểu 15 phút và tối đa 10 ngày theo đúng quy định của TikTok.",
            },
            {
                "kind": "shortcut",
                "text": "Đi đến Tab Lịch Đăng",
                "target": "schedule",
            },
        ],
    },
    {
        "id": "youtube_sync",
        "tag": "youtube",
        "icon": "📺",
        "title": "7. Tải Video YouTube & Lọc Shorts",
        "subtitle": "Tự động bắt video mới từ YouTube, lọc thời lượng Shorts và tải hàng loạt",
        "blocks": [
            {
                "kind": "intro",
                "text": "Hệ thống YouTube Studio tự động phát hiện khi các kênh YouTube bạn theo dõi phát hành video mới, tự tải về và chuyển vào thư mục chờ đăng của tài khoản TikTok tương ứng.",
            },
            {
                "kind": "step",
                "text": "Thêm kênh theo dõi: Dán đường link kênh YouTube cần theo dõi và sử dụng bộ tìm kiếm nhanh để chọn tài khoản TikTok nhận video.",
            },
            {
                "kind": "step",
                "text": "Thiết lập thời lượng Shorts cho từng kênh: Bạn có thể cài đặt ngưỡng thời lượng tối thiểu cho video Short (mặc định 30 giây). Các video dưới ngưỡng này sẽ tự động được chuyển vào thư mục riêng để bạn kiểm duyệt, tránh đăng video quá ngắn vi phạm chính sách kiếm tiền.",
            },
            {
                "kind": "step",
                "text": "Bấm 'Bắt Đầu' trong tab YouTube Studio để kích hoạt chế độ tự động nhận thông báo video mới.",
            },
            {
                "kind": "step",
                "text": "Tải Hàng Loạt (Batch Download): Khi cần tải nhiều video cùng lúc theo danh sách, chuyển sang tab Tải Hàng Loạt, dán danh sách link YouTube, chọn tài khoản nhận và bấm 'Tải'.",
            },
            {
                "kind": "step",
                "text": "Khi gặp thông báo YouTube yêu cầu xác minh bot: Bấm chọn đường dẫn file cookie YouTube trong cài đặt hoặc gán proxy cho tài khoản rồi tải lại.",
            },
            {
                "kind": "shortcut",
                "text": "Đi đến YouTube Studio",
                "target": "youtube",
            },
        ],
    },
    {
        "id": "monetization_kyc",
        "tag": "kyc",
        "icon": "💰",
        "title": "8. Thu Nhập, Quỹ Kiếm Tiền & KYC",
        "subtitle": "Theo dõi số dư, Quỹ sáng tạo CRP, xác minh danh tính và thuế",
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
                "text": "Theo dõi 4 nhóm trạng thái xác minh: Đã xác minh (xanh lá) | Đang chờ duyệt (vàng) | Cần gửi lại giấy tờ | Cần kiểm tra / Kháng nghị.",
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
        "id": "statistics_report",
        "tag": "stats",
        "icon": "📊",
        "title": "9. Thống Kê & Báo Cáo Hiệu Suất",
        "subtitle": "Bảng điều khiển KPI tải/đăng video, tra cứu lỗi nhanh và xuất báo cáo CSV",
        "blocks": [
            {
                "kind": "intro",
                "text": "Tab Thống Kê cung cấp cái nhìn toàn cảnh về hiệu suất vận hành: số lượng video YouTube đã tải về, số video TikTok đã đăng thành công và tỷ lệ hoàn thành theo thời gian thực.",
            },
            {
                "kind": "bullet",
                "text": "Thẻ KPI tổng quan: Theo dõi tổng số video Tải OK, Tải Lỗi, Đăng OK, Đăng Lỗi, sản lượng Hôm Nay, Hôm Qua và Tỷ Lệ Thành Công (%) toàn hệ thống.",
            },
            {
                "kind": "step",
                "text": "Bộ lọc thời gian linh hoạt: Dễ dàng xem số liệu theo các mốc: Tất cả thời gian, Hôm nay, Hôm qua, 7 ngày qua hoặc 30 ngày qua.",
            },
            {
                "kind": "step",
                "text": "Bảng chi tiết từng tài khoản: Hiển thị sản lượng tải và đăng cụ thể của từng hồ sơ, kèm thời điểm hoạt động gần nhất để kịp thời phát hiện tài khoản bị đứng hoặc ngưng hoạt động.",
            },
            {
                "kind": "step",
                "text": "Tra cứu lỗi nhanh (Quick Error Inspector): Khi nhấp chuột vào một tài khoản có video bị lỗi, bảng bên dưới sẽ liệt kê chính xác tên video, mã lỗi/nguyên nhân thất bại, đường dẫn file và đường link gốc. Bạn có thể bấm mở trực tiếp video hoặc file để kiểm tra.",
            },
            {
                "kind": "step",
                "text": "Xuất báo cáo dữ liệu: Bấm nút 'Xuất Báo Cáo' để tải file bảng tính CSV (chuẩn tiếng Việt có dấu UTF-8) phục vụ báo cáo doanh thu và đối soát nội bộ.",
            },
            {
                "kind": "shortcut",
                "text": "Đi đến Tab Thống Kê",
                "target": "statistics",
            },
        ],
    },
    {
        "id": "dedup_watchdog",
        "tag": "dedup",
        "icon": "🛡️",
        "title": "10. Quét Trùng Lặp & Gậy Bản Quyền (Watchdog)",
        "subtitle": "Tự động phát hiện video trùng lặp, bảo vệ kênh khỏi flop và dính gậy TikTok",
        "blocks": [
            {
                "kind": "intro",
                "text": "Module Dedup Watchdog là lớp phòng vệ tự động giúp bảo vệ toàn bộ dàn kênh TikTok khỏi rủi ro bị bóp tương tác (shadowban), trùng lặp nội dung hoặc bị phạt gậy bản quyền (mã cảnh báo 611).",
            },
            {
                "kind": "bullet",
                "text": "3 Chế độ hành động an toàn:\n- Chạy thử (Dry-Run): Chỉ ghi nhận và phát cảnh báo trong nhật ký mà không tác động vào kênh (khuyên dùng khi mới làm quen).\n- Tự động Ẩn Private: Tự động chuyển các video bị dính trùng lặp sang chế độ Riêng tư để kênh không bị bóp tương tác.\n- Tự động Xóa: Tự động xóa vĩnh viễn video vi phạm để giữ kênh sạch 100%.",
            },
            {
                "kind": "step",
                "text": "Kích hoạt Watchdog: Tại tab Quét Trùng / Gậy, chọn chế độ xử lý mong muốn và bấm 'Bật Watchdog' để phần mềm tự động rà soát định kỳ theo chu kỳ bạn chọn.",
            },
            {
                "kind": "step",
                "text": "Quản lý Danh Sách Theo Dõi (Watchlist): Bật hoặc tắt theo dõi cho từng tài khoản cụ thể. Bạn cũng có thể bấm 'Quét ngay' để kiểm tra tức thì một kênh bất kỳ.",
            },
            {
                "kind": "step",
                "text": "Kiểm tra trực tiếp từ Bảng Tài Khoản: Nhấp chuột phải vào một dòng tài khoản ở Bảng Hồ Sơ → chọn 'Kiểm Tra Trùng / Gậy' để xem ngay trạng thái bài đăng của kênh đó.",
            },
            {
                "kind": "warn",
                "text": "Nên sử dụng chế độ 'Chạy thử (Dry-Run)' trước để quan sát độ chính xác của các bài đăng trước khi chuyển sang chế độ tự động Ẩn Private hoặc Xóa.",
            },
            {
                "kind": "shortcut",
                "text": "Đi đến Quét Trùng / Gậy",
                "target": "dedup",
            },
        ],
    },
    {
        "id": "proxy_maintenance",
        "tag": "proxy",
        "icon": "🌐",
        "title": "11. Proxy & Bảo Trì Hệ Thống",
        "subtitle": "Thiết lập kết nối mạng an toàn, tối ưu bộ nhớ và cập nhật công cụ",
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
                "text": "Bảo trì trình duyệt: Nếu trình duyệt gặp sự cố hiển thị hoặc chiếm nhiều dung lượng, vào menu Cài Đặt → chọn 'Reset Browser' để dọn dẹp bộ nhớ đệm an toàn mà không làm mất thông tin tài khoản.",
            },
            {
                "kind": "step",
                "text": "Cập nhật công cụ tải: Khi việc tải video YouTube gặp trục trặc, bấm nút 'Cập nhật yt-dlp' trên giao diện YouTube Studio để nhận bản nâng cấp mới nhất.",
            },
        ],
    },
    {
        "id": "troubleshooting",
        "tag": "errors",
        "icon": "🛠️",
        "title": "12. Xử Lý Lỗi Thường Gặp",
        "subtitle": "Hướng dẫn xử lý nhanh 11 tình huống thực tế thường gặp theo từng bước",
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
                "text": "5. Không tải được video từ YouTube:\n- Dấu hiệu: Bấm tải nhưng báo lỗi hoặc tiến trình dừng lại.\n- Cách xử lý: Bấm nút 'Cập nhật yt-dlp' trên giao diện YouTube Studio; kiểm tra đường link video có tồn tại công khai hay không.\n- Khi nào cần liên hệ hỗ trợ: Khi link video mở xem bình thường trên web nhưng phần mềm không tải được.",
            },
            {
                "kind": "bullet",
                "text": "6. Không đăng được video lên TikTok:\n- Dấu hiệu: Tiến trình đăng báo lỗi hoặc video không xuất hiện trên kênh.\n- Cách xử lý: Kiểm tra trạng thái Cookie có màu xanh 'Cookie Sống' không; kiểm tra định dạng file video (MP4) và dung lượng; đảm bảo tài khoản không bị dính án phạt đăng bài từ TikTok.\n- Khi nào cần liên hệ hỗ trợ: Khi cookie sống và video hợp lệ nhưng liên tục thất bại ở bước tải lên.",
            },
            {
                "kind": "bullet",
                "text": "7. Chuyển từ API sang Trình duyệt (Browser Fallback):\n- Dấu hiệu: Tiến trình báo 'API Upload chưa hoàn tất, chuyển sang Browser Fallback'.\n- Cách xử lý: Đây là cơ chế dự phòng tự động an toàn của hệ thống khi TikTok yêu cầu xác minh captcha. Bạn không cần làm gì, phần mềm sẽ tự mở trình duyệt ngầm để hoàn tất đăng bài.\n- Khi nào cần liên hệ hỗ trợ: Khi cả trình duyệt dự phòng cũng báo lỗi không thể vượt qua xác minh.",
            },
            {
                "kind": "bullet",
                "text": "8. Video bị gắn cờ trùng lặp hoặc dính gậy 611:\n- Dấu hiệu: Video đăng lên bị 0 lượt xem hoặc Watchdog phát hiện video trùng khớp với video gốc.\n- Cách xử lý: Bật tính năng Dedup Watchdog ở chế độ 'Tự động Ẩn Private' để gỡ video khỏi trang công khai; giãn cách thời gian giữa các lần đăng bài.\n- Khi nào cần liên hệ hỗ trợ: Khi toàn bộ video mới đều bị TikTok từ chối dù nội dung đã chỉnh sửa.",
            },
            {
                "kind": "bullet",
                "text": "9. Không nhận được thông báo video mới từ YouTube:\n- Dấu hiệu: Kênh YouTube đã đăng video mới nhưng phần mềm chưa tự động nhận.\n- Cách xử lý: Kiểm tra trạng thái YouTube Studio xem đã bấm 'Bắt Đầu' chưa; đảm bảo máy tính có kết nối mạng ổn định.\n- Khi nào cần liên hệ hỗ trợ: Khi monitor đang chạy bình thường nhưng quá 30 phút vẫn chưa nhận video mới.",
            },
            {
                "kind": "bullet",
                "text": "10. Lỗi hẹn giờ Server TikTok:\n- Dấu hiệu: Báo lỗi 'Thời gian hẹn giờ không hợp lệ' hoặc không ghim được lịch lên TikTok.\n- Cách xử lý: Đảm bảo thời gian hẹn cách thời điểm hiện tại ít nhất 15 phút và không quá 10 ngày; kiểm tra xem múi giờ máy tính có bị lệch không.\n- Khi nào cần liên hệ hỗ trợ: Khi thời gian hẹn hợp lệ nhưng máy chủ TikTok liên tục từ chối.",
            },
            {
                "kind": "bullet",
                "text": "11. Tác vụ lịch đăng báo lỗi (Failed):\n- Dấu hiệu: Trong bảng Lịch Đăng, tác vụ chuyển sang màu đỏ 'FAILED' hoặc cookie hết hạn khi đến giờ hẹn.\n- Cách xử lý: Kiểm tra lại cookie của tài khoản (nếu die thì mở trình duyệt login lại); chọn dòng tác vụ lỗi rồi bấm nút 'Thử Lại Lỗi' trên thanh công cụ để đưa video trở lại hàng đợi.\n- Khi nào cần liên hệ hỗ trợ: Khi tài khoản cookie sống nhưng tác vụ liên tục báo lỗi khi đăng bài.",
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
        text="Cẩm nang hướng dẫn thao tác từng bước: thêm tài khoản, đăng video tự động qua API, tải YouTube, giám sát gậy bản quyền và theo dõi thu nhập.",
        font=UIThemeTokens.FONT_SUBTITLE,
        text_color=UIThemeTokens.TEXT_MUTED,
        anchor="w",
    ).pack(anchor="w", pady=(1, 0))

    # Search Entry
    search_var = ctk.StringVar(value="")
    search_entry = ctk.CTkEntry(
        intro_header,
        textvariable=search_var,
        placeholder_text="🔍 Tìm hướng dẫn, ví dụ: đăng nhập, proxy, YouTube, KYC, thống kê, quét trùng, API, lỗi...",
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