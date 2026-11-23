# Phiên bản 1.4.0

## Điểm mới
- **Kho Tài Khoản YouTube (Multi-Account Cookie Pool) & Tải Video Ngắn Ổn Định**:
  - Hỗ trợ thêm/quản lý nhiều tài khoản YouTube độc lập (`YT_Acc_01`, `YT_Acc_02`...), mở trình duyệt Chrome riêng cho từng tài khoản để đăng nhập và tự động trích xuất cookie qua CDP, hoặc nhập nhanh từ file `.txt`.
  - Hỗ trợ gắn Proxy riêng (tùy chọn) cho từng tài khoản YouTube trong Kho Cookie.
  - Tự động xoay vòng tài khoản theo LRU (chia đều lượt tải) và cách ly tạm thời 5 phút (`Cooldown`) ngay lập tức khi 1 tài khoản gặp `HTTP 403` hoặc `The page needs to be reloaded.`, đồng thời đánh dấu `Hết hạn` nếu cần đăng nhập lại.
  - Tự động kế thừa file `youtube_cookies.txt` cũ làm tài khoản mặc định đầu tiên trong Pool.
- **Nâng Cấp Hệ Thống Lên Lịch Quy Mô Lớn (800+ Tài Khoản) & Giao Diện Thuần Việt 100%**:
  - Chuẩn hóa toàn bộ giao diện Tạo Lịch & Quản Lý Lịch sang tiếng Việt tự nhiên cho người dùng cuối, loại bỏ hoàn toàn các thuật ngữ kỹ thuật (`ORIGINAL`, `SERIES`, `SHUFFLE`, `APPEND`, `OVERWRITE`, `Jitter`, `SERVER_SCHEDULE`).
  - Sử dụng mặc định duy nhất chế độ **Treo máy tự động đăng qua API**; chuyển các bản ghi `SERVER_SCHEDULED` cũ sang nhóm lưu trữ.
  - **Đồng bộ Múi Giờ Proxy & Random Giờ Vàng**: Tự động nhận diện múi giờ theo khu vực Proxy của từng tài khoản (kèm nút `🔄 Đồng bộ giờ Proxy`), tự chọn phút ngẫu nhiên bên trong khung giờ vàng và hiển thị cột `Khu Vực (Proxy)` trên bảng Xem trước.
  - **Chống Trùng IP Quy Mô 800 Acc**: Tự động rải các tài khoản chung Proxy sang các khung giờ vàng khác nhau (giãn cách tối thiểu 15 phút) và nâng cấp Daemon lên Worker Pool đa luồng (`1–15` luồng) có khóa **Per-Proxy Lock & Cooldown**.
  - **Tùy chọn `Bắt đầu: Hôm nay / Ngày mai`** trên Modal Lên Lịch giúp chủ động chọn ngày khởi chạy đồng bộ, kết hợp chặn tuyệt đối lên lịch trong quá khứ.
- **Thuật Toán Đăng Bù Thông Minh Theo Múi Giờ Proxy**:
  - Khi mở lại máy hoặc đánh thức từ Sleep: nếu đang trong khung giờ hoạt động (`07:00 – 22:59` giờ Proxy), cho phép đăng bù ngay 1 bài (giãn cách tự động giữa các acc chung Proxy) và dàn đều các bài còn lại; nếu rơi vào giờ ngủ ban đêm (`23:00 – 06:59` giờ Proxy), tự động dời sang khung giờ vàng sáng gần nhất để tránh flop kênh.
- **Nới Lỏng Kiểm Duyệt Video (60 Phút) & Cửa Sổ Kiểm Tra Video Bị Bỏ Qua**:
  - Nâng giới hạn thời lượng video hợp lệ lên **60 phút (3600 giây)** và hỗ trợ thêm các codec phổ biến (`vp9`, `vp8`, `mpeg4`).
  - Bổ sung nút `⚠️ X video bị bỏ qua [🔍 Xem chi tiết]` mở bảng phân loại lỗi tiếng Việt, cho phép lọc theo nhóm lỗi, mở vị trí file trong Explorer, sao chép đường dẫn và xuất báo cáo `.txt`.

## Cải thiện & Sửa lỗi
- **Khắc phục triệt để lỗi cắt ngang chuỗi Fallback YouTube ở `[3/6]`**: Khi gặp lỗi `The page needs to be reloaded.` (`FAILURE_RETRY`), hệ thống tự động xoay sang cookie khác trong Pool rồi nối tiếp xuống nhánh không cookie (`mweb`/`android`/`ios`) và Proxy fallback thay vì lặp vô tận với cookie lỗi.
- **Giới hạn tối đa 3 chu kỳ retry (`MAX_RETRY_CYCLES = 3`)**: Ngăn tình trạng 1 video lỗi ngâm chết worker gây trễ/miss các video ngắn mới từ WebSub/RSS.
- **Dọn dẹp tàn dư UI thông báo YouTube cũ**: Loại bỏ cụm chọn file `cookies.txt` rời rạc và thông báo *"bật chuông kênh ở chế độ Tất cả"*, thay bằng khối Kho Tài Khoản YouTube gọn đẹp.

# Phiên bản 1.3.0

## Điểm mới
- **Động cơ Tải Video YouTube Kiên cố hoá 100% (Hardened YouTube Downloader Engine - Spec 2026)**:
  - **Tự động Bóc tách Cookie khi Tắt Trình duyệt Login**: Người dùng chỉ cần bấm *"Mở Browser Login YouTube"*, đăng nhập Google/YouTube trên cửa sổ Chrome riêng biệt rồi **tắt cửa sổ**. Hệ thống tự động bắt sự kiện đóng qua Generation Token, bóc tách cookie phiên bằng CDP headless sang `Auto_Data/youtube_cookies.txt` chuẩn Netscape 7 cột (tuân thủ nghiêm ngặt điều kiện `domain_specified == initial_dot` của Python `http.cookiejar`) và nạp ngay lên giao diện.
  - **Chiến lược Tải Ưu tiên Cookie (Cookie-First)**: Tự động đưa lượt tải qua Cookie lên vị trí ưu tiên số 1 (`direct-cookies` & `direct-cookies-alt-format`), giúp tải ngay lập tức với độ ổn định cao nhất, đồng thời bảo toàn chuỗi fallback ẩn danh phía sau.
  - **Tự động Giải mã Dynamic JS Challenge n-sig (`--js-runtimes node`)**: Tự động nhận diện runtime Node.js trên máy để giải mã thuật toán n-sig động của YouTube, tránh triệt để lỗi bóp băng thông hoặc 403 Forbidden.
  - **Tự phục hồi Khóa File Windows (`.temp.mp4`) & Đa container (`.webm`, `.mkv`, `.m4v`)**: Vượt qua tình trạng Windows Search Indexer giữ file lock sau khi ghép FFmpeg, đồng thời tự động remux/transcode các định dạng video sang MP4 chuẩn cho TikTok.
- **Thuật toán Tự động Cứu vãn & Dàn đều Bài đăng Lỡ hẹn (Smart Staggered Catch-up)**:
  - Khi người dùng tắt máy tính và mở lại, video đầu tiên của mỗi tài khoản bị lỡ hẹn sẽ được tự động kích hoạt **Đăng bù ngay lập tức** mà không cần chờ đợi.
  - Các video dồn ứ tiếp theo sẽ được thuật toán thông minh tự động phân bổ vào các khung giờ vàng còn trống trong ngày (cách nhau tối thiểu 2 giờ) hoặc dàn đều sang các ngày kế tiếp (tối đa 2 video/ngày), loại bỏ hoàn toàn nguy cơ tài khoản bị bóp tương tác do đăng dồn dập sau khi mở máy.
- **Trợ lý Nhận diện & Khử Lặp Tiêu đề Thông minh (Title Stutter Stripper)**:
  - Tự động phát hiện hiện tượng lặp từ khóa tiêu đề (ví dụ: `Title Part 1 | Title Part 1 #fyp`) khi người dùng vừa áp dụng công thức đặt tên vừa có tên file trùng lặp.
  - Tích hợp cảnh báo trực tiếp (Live Warning) trên hộp thoại lên lịch và nút bấm `[Khử Lặp]`, tự động làm sạch tiêu đề đồng thời bảo toàn 100% các hashtag quan trọng.
- **Tái cấu trúc Giao diện Lịch Đăng (Tab Schedule UI Redesign)**:
  - **Bộ lọc Trạng thái Chuyên biệt**: Dễ dàng chuyển đổi giữa `Đang hoạt động (Active)` (các bài chờ đăng, đang tải hoặc cần xử lý) và `Đã lưu trữ (Archived)` (các bài đã đăng thành công, đã ghim server hoặc đã hủy), giúp bảng lịch luôn gọn gàng, trực quan.
  - **Bộ lọc Dự án Đồng bộ (Project Binding)**: Tự động lọc danh sách bài đăng theo đúng Dự án (Project) đang chọn ở thanh điều hướng bên trái với cơ chế bảo vệ Fail-Closed an toàn.
  - **Bộ lọc Khoảng thời gian ISO**: Hỗ trợ lọc bài đăng theo ngày cụ thể hoặc khoảng thời gian từ ngày - đến ngày.
  - **Dark Treeview Tooltip**: Di chuột để soi nhanh tiêu đề đầy đủ, đường dẫn file và thông báo lỗi chi tiết mà không làm vỡ bố cục bảng.
- **Tự động Phân tích & Tái học Lịch đăng YouTube Định kỳ (Periodic Schedule Relearner)**:
  - Động cơ YouTube Monitor tự động phân tích lại lịch sử đăng video của các kênh sau mỗi 3 ngày (tiêu tốn đúng 1 unit quota API), tự động cập nhật khung giờ đăng mới của Creator mà không cần thao tác thủ công.
  - Bảo lưu tuyệt đối 100% các khung giờ do người dùng tự tạo (`MANUAL`) hoặc đã ghim khóa (`LOCKED`).

## Cải thiện
- **Kiên cố hóa Động cơ Lên lịch (Hardened Scheduler Engine)**:
  - **Tự động chuyển đổi mượt mà (Auto-Fallback)**: Khi TikTok API trả về lý do chặn lên lịch từ xa (`schedule_disabled` hoặc `ab_value='0'`), hệ thống tự động chuyển bài đăng sang chế độ Hàng đợi máy tính (`LOCAL_QUEUE`) thay vì báo lỗi thất bại.
  - **Cơ chế Bảo toàn Hashtag Tiếng Việt & Dấu gạch dưới**: Chuẩn hóa thuật toán làm sạch tiêu đề, đảm bảo toàn bộ hashtag có dấu tiếng Việt (ví dụ: `#hàhài`, `#ẩmthực`) và dấu gạch dưới (`#review_phim`) được giữ nguyên 100% khi đăng lên TikTok.
  - **Tối ưu Cooldown Linh hoạt**: Tự động nhận diện chu kỳ nghỉ giữa 2 lần đăng (5-10s cho cùng profile, 3-5s cho khác profile) và cho phép ngắt nghỉ tức thì khi người dùng bấm Tạm dừng hoặc Dừng tiến trình.
  - **Bảo vệ File Nguồn Đa Lớp**: Chỉ thực hiện xóa video gốc sau khi hệ thống đã ghi nhận chắc chắn trạng thái thành công vào SQLite, và tự động bỏ qua xóa nếu file vẫn đang được bài đăng khác tham chiếu dùng chung.
  - **Gia hạn Lease Heartbeat Multipart**: Tự động gia hạn quyền xử lý định kỳ trong lúc upload video dung lượng lớn, loại bỏ hoàn toàn hiện tượng trôi lease gây đăng trùng bài.

## Sửa lỗi
- **Khắc phục lỗi Đăng Ngay Lập Tức khi Epoch không hợp lệ (Intent Guard)**: Ngăn chặn triệt để tình trạng một số bài đăng chế độ `SERVER_SCHEDULE` bị âm thầm chuyển thành đăng ngay khi epoch thiếu hoặc không hợp lệ.
- **Khắc phục lỗi Deadlock Task Phục hồi (`MISSED_RESCHEDULED`)**: Bổ sung đầy đủ trạng thái `MISSED_RESCHEDULED` vào bộ quét worker claim trong SQLite, giúp các bài lỡ hẹn sau khi được cứu vãn sẽ được tự động thực thi trơn tru.
- **Khắc phục lỗi Nút 'Đăng Ngay Lập Tức' bị từ chối**: Cho phép claim và xử lý ngay cả với các task đang ở trạng thái `FAILED`, đồng thời tự động hủy lịch hẹn giờ tương lai để xuất bản bài đăng ngay theo đúng lệnh của người dùng.
- **Khắc phục lỗi Khởi tạo Lịch học YouTube làm mất khung giờ hiện có**: Khắc phục hiện tượng thuật toán học lịch ghi đè làm mất các khung giờ dự đoán đang kích hoạt khi kênh chỉ mới ghi nhận 1 video mới.

# Phiên bản 1.2.9

## Điểm mới
- **Hệ Thống Lên Lịch Đăng TikTok Tự Động (TikTok Auto-Scheduler Engine)**:
  - Bổ sung không gian làm việc chuyên biệt (Tab Lịch Đăng) quản lý tập trung toàn bộ hàng đợi bài đăng đa kênh với đầy đủ chức năng: Bắt đầu chạy lịch, Tạm dừng, Đăng ngay lập tức, Thử lại lỗi, Xóa bản ghi và Dọn dẹp task cũ.
  - Hộp thoại **1-Click Lên Lịch Tự Động** (`BulkScheduleModal`): Nạp video từ thư mục hoặc từng file, tự động phân bổ đều video cho hàng chục tài khoản theo thuật toán Round-Robin công bằng.
  - **4 Khung Giờ Vàng Đăng Bài & Jitter Chống Bot**: Tùy chọn phân bổ video vào các khung giờ vàng (Sáng 06:00-08:00, Trưa 11:00-13:00, Chiều 17:00-19:00, Tối 19:00-22:00) kèm cơ chế lệch ngẫu nhiên 5-30 phút để các kênh không đăng trùng giây, loại bỏ hoàn toàn dấu hiệu máy móc.
  - **Chế độ Hẹn Giờ Trên Server TikTok (Server Schedule)**: Đăng tải trước video lên TikTok Studio qua Direct API và ghim lịch phát hành chính thức (tối đa 10 ngày). Người dùng có thể **TẮT MÁY TÍNH HOÀN TOÀN**, máy chủ TikTok sẽ tự động công khai video đúng giờ hẹn.
  - **Cơ chế Chống Trùng Lịch (Conflict Guard)**: Tự động phát hiện tài khoản đã có bài chờ đăng và cung cấp 3 giải pháp xử lý xung đột linh hoạt: Nối tiếp (Append - Khuyên dùng), Ghi đè (Overwrite) hoặc Bỏ qua (Skip).
  - **Tự động Cứu Vãn Bài Đăng Lỡ Hẹn (Auto-Recovery)**: Tự động dời lịch sang ngày tiếp theo nếu máy tính bị tắt đột ngột lúc đến giờ hẹn đối với hàng đợi local.
  - **Công thức Tiêu Đề Thông Minh**: Tự động sinh tiêu đề hấp dẫn theo mẫu thẻ `{filename}`, `{index}`, `{date}`, `{time}`, `{profile}` kèm bảng xem trước mẫu tức thì.
- **Tích Hợp Ghi Chú Lịch Đăng Trực Quan Trên Bảng Profile Chính**:
  - Cột `Tiến Độ Đăng` (`upload`) tự động hiển thị ngày hẹn muộn nhất và số lượng video đang xếp hàng (ví dụ: `📅 Đến 20/10 (3 clip)`), giúp người dùng nhận diện ngay profile nào đang rảnh hoặc đã kín lịch, tránh lên lịch trùng lặp.
  - Nếu clip trước vừa đăng xong: Hiển thị `Đã đăng • 📅 20/10`. Khi đang thực tế upload, luôn ưu tiên hiển thị `Đang tải video` / `Đang đăng`.
  - Ô tìm kiếm `🔍` trên màn hình chính hỗ trợ lọc nhanh profile theo mốc ngày hẹn hoặc số clip.

## Cải thiện
- **Tối Ưu Hóa Tốc Độ Khởi Động Ứng Dụng (Startup Performance Optimization)**:
  - Xác thực bản quyền Cache-First: Đọc token bản quyền đã lưu từ ổ đĩa trong < 1ms, loại bỏ hoàn toàn hiện tượng đơ giao diện khi mở tool, tự động đồng bộ xác thực ngầm sau 3 giây.
  - Trì hoãn kiểm tra tài nguyên trình duyệt sang luồng nền, giúp giao diện chính xuất hiện nhanh hơn 1.5 - 2.5 giây.
  - Đồng bộ Google Sheets không đồng bộ (Asynchronous Sync), không block luồng UI.
- **Giao Diện Tinh Gọn, Tối Đa Diện Tích Hiển Thị Bảng Hồ Sơ**:
  - Loại bỏ thanh `header_bar` và 4 thẻ thống kê KPI trên cùng màn hình chính, mở rộng tối đa không gian hiển thị danh sách tài khoản.
  - Thiết lập phân trang mặc định là **50 dòng / trang** ngay khi khởi động phần mềm, giúp người dùng quan sát được nhiều tài khoản cùng lúc mà không phải chuyển trang liên tục.
  - Mở rộng cột `Tiến Độ Đăng` lên `145px` hiển thị trọn vẹn badge lịch hẹn mà không làm xô lệch 10 cột dữ liệu chuẩn.
- **Tối Ưu Trải Nghiệm Hộp Thoại Lên Lịch (BulkScheduleModal UX)**:
  - Cố định thanh nút bấm thao tác ở chân hộp thoại (Fixed Bottom Footer), luôn hiển thị sẵn sàng nút `🚀 1-Click Lên Lịch Tự Động` và `Hủy (Esc)` mà không cần người dùng phải cuộn chuột.
  - Cửa sổ modal kích thước chuẩn `720x780px` tự động căn giữa màn hình, cuộn mượt mà với `CTkScrollableFrame`.
- **Cập Nhật Mục Hướng Dẫn Sử Dụng (User Guide)**:
  - Bổ sung Mục 6 chuyên sâu về "Lên Lịch Đăng TikTok Tự Động & Hẹn Giờ Khung Giờ Vàng" trong tab Hướng Dẫn Sử Dụng (`ui_guide.py`), cập nhật 11 tình huống xử lý lỗi thường gặp trong vận hành.

## Sửa lỗi
- **Khắc phục lỗi Modal Lên Lịch bị che khuất nút thao tác**: Xử lý triệt để hiện tượng trên màn hình độ phân giải thấp phải kéo giãn cửa sổ thủ công mới thấy nút bấm.
- **Khắc phục lỗi Import và Đồng Bộ Đa Luồng**: Khởi tạo sớm Singleton `global_schedule_manager`, bổ sung `UploadState` trong import, tự động cập nhật bảng hồ sơ chính ngay khi có thao tác tạo lịch, hủy lịch hoặc hoàn tất đăng bài từ daemon nền.
- **Khắc phục cấu hình đóng gói PyInstaller**: Bổ sung đầy đủ các module scheduler mới vào cấu hình biên dịch để đảm bảo file `.exe` chạy trơn tru trên mọi máy người dùng.

# Phiên bản 1.2.7

## Điểm mới
- **Động cơ Đăng bài bằng API siêu tốc (TikTok Direct API Upload)**: Tải lên video trực tiếp qua ByteDance TOS REST API và xuất bản bài đăng TikTok Studio mà không cần mở trình duyệt, loại bỏ 100% chi phí ngốn RAM/CPU của Chromium.
- **Tải lên phân mảnh đa luồng song song (Adaptive Concurrent Slicing)**: Tự động điều chỉnh số luồng theo dung lượng video (1 stream cho video < 15MB, 4 luồng song song cho 15-60MB, tối đa 6 luồng cho video lớn), rút ngắn thời gian tải video xuống chỉ còn 3 - 7 giây.
- **Cơ chế Kiểm tra API sẵn sàng Mức 1 (Offline Check)**: Tự động xác minh cookie `sessionid` từ SQLite chỉ trong < 15ms khi khởi động profile, 100% không gửi request mạng thừa thãi, bảo toàn tuyệt đối Trust Score cho tài khoản và proxy.
- **Chế độ Upload Engine linh hoạt**: Bổ sung tùy chọn trong cấu hình hồ sơ: `Tự động (Ưu tiên API, fallback Browser)`, `Chỉ API` và `Chỉ Trình duyệt`.

## Cải thiện
- **Bypass Proxy cho luồng truyền video (Direct IP)**: Toàn bộ byte dữ liệu video được truyền trực tiếp bằng đường truyền mạng gốc, tiết kiệm 100% dung lượng proxy mà vẫn bảo vệ an toàn IP đăng nhập ở các bước xác thực.
- **Tự động đóng trình duyệt Fallback**: Khi chạy chế độ tự động, trình duyệt fallback chỉ mở khi có lỗi API và sẽ tự động đóng ngay khi hết video trong hàng chờ để giải phóng tài nguyên máy tính.
- **Tối ưu tốc độ lấy Metadata Video (fast_mp4_meta)**: Bộ trích xuất thuần Python đọc atom moov lấy kích thước và thời lượng video trong < 5ms mà không cần phụ thuộc ffprobe hay cv2.

## Sửa lỗi
- **Khắc phục lỗi trình duyệt bị co nhỏ màn hình khi tải trang**: Bổ sung cờ `--start-maximized` và kích hoạt `no_viewport=True` cho Patchright, đảm bảo trình duyệt luôn mở và duy trì kích thước toàn màn hình (Maximized) mặc định.
- **Khắc phục lỗi Batch Start Timeout sau 3 phút**: Cập nhật logic kiểm tra sẵn sàng thông minh, giúp các profile chạy chế độ API chuyển sang trạng thái chờ chỉ sau 0.5 giây và duy trì trạng thái lắng nghe video 24/7.

# Phiên bản 1.2.6

## Điểm mới
- Có thể đặt ngưỡng thời lượng Shorts riêng cho từng kênh YouTube.
- Có thể chọn thư mục tập trung để lưu video ngắn hơn ngưỡng của kênh.

## Cải thiện
- Ngưỡng mặc định để kéo dài Shorts lên 61 giây là 30 giây; video dưới ngưỡng được lưu riêng và không đưa vào hàng đợi đăng TikTok.
- Bổ sung các điều khiển cấu hình Shorts theo kênh trong giao diện YouTube Monitor.

## Sửa lỗi
- Lưu và nạp lại ngưỡng Shorts theo kênh qua các lần khởi động, tránh mất cấu hình đã chọn.
- Sửa script cập nhật chạy ngầm trên Windows: chờ ứng dụng đóng đúng thời gian và trả mã kết quả chính xác sau khi cập nhật.

# Phiên bản 1.2.5

## Điểm mới
- **Hệ thống Dedup Watchdog Engine (Phát hiện video trùng lặp)**: Tích hợp công nghệ phân tích dữ liệu TikTok Mobile API (`group_id != aweme_id`) để nhận diện chính xác các video trùng lặp hoặc reup bị đánh bản quyền trên kênh TikTok.
- **Hộp thoại Kiểm tra bài đăng (Check Post / Check Trùng)**: Cho phép kiểm tra trạng thái bài đăng, nhận diện video trùng lặp trên profile được chọn hoặc kênh bất kỳ (`@username`), hiển thị chi tiết mã Aweme ID, Group ID và ghi nhận vào Audit Log.
- **Giao diện Giám sát Trùng lặp (Dedup Watchdog Workspace)**: Bổ sung không gian làm việc chuyên biệt để theo dõi tiến trình quét bài đăng ngầm, lịch sử phát hiện trùng và cảnh báo tức thì.

## Cải thiện
- **Tối ưu tốc độ khởi động Profile (Khởi động tức thì)**: Loại bỏ hoàn toàn bước đo `psutil.cpu_percent` gây block 0.5 giây cho mỗi lần mở profile, giúp quá trình mở hàng chục profile diễn ra trơn tru, liền mạch.
- **Cập nhật cấu hình đóng gói TikTokAutoUploader**: Bổ sung `tiktok_dedup_engine` và `tiktok_dedup_ui` vào danh sách module nội bộ của ứng dụng để đảm bảo bản build đóng gói chạy ổn định.

## Sửa lỗi
- **Khắc phục lỗi bỏ qua profile khi khởi động hàng loạt (Low Res)**: Sửa triệt để hiện tượng Batch Start tự động bỏ qua profile (`Bỏ qua (Low Res)`) do hệ điều hành tạm thời tăng tải CPU hoặc RAM.
- **Khắc phục lỗi System Resource Low**: Loại bỏ hoàn toàn ngoại lệ `SessionSetupError("System Resource Low")` và trạng thái `Bị lỗi` giả tạo khi mở profile đơn lẻ.

# Phiên bản 1.2.4

## Điểm mới
- **Hệ thống Quét Video Thông Minh Dự Đoán (Predictive API Polling)**: Phát hiện video YouTube mới gần như tức thì mà không phụ thuộc vào WebSub hay tunnel ngrok.
- **Tự động học lịch đăng video (Schedule Learner)**: Phân tích 30-50 video gần nhất của kênh để phát hiện giờ vàng đăng video, tự động phân cụm thời gian và sinh khung giờ quét dày (Micro-Polling) ±10 phút quanh giờ đăng dự kiến.
- **Quản lý nhóm Multi-API Key (ApiKeyManager)**: Hỗ trợ nạp nhiều YouTube Data API v3 key, tự động xoay vòng Round-Robin, tự chuyển key khi chạm hạn mức ngày (quotaExceeded) hoặc giới hạn tần suất (rateLimitExceeded), tự hồi phục sau 00:00 UTC.
- **Tab giao diện chuyên biệt `⚡ API & Quét Video`**: Theo dõi danh sách key, trạng thái hoạt động, thời gian thử lại, ước tính số lượt gọi API cho từng khung giờ và trạng thái quét thực tế của từng kênh.
- **Tự động dừng quét sớm khi có video mới**: Khi bắt được video trong khung giờ, hệ thống lập tức ngừng gửi request API cho kênh đó trong phần thời gian còn lại của khung quét để bảo toàn hạn mức.

## Cải thiện
- **Bộ nhớ đệm YouTube Client**: Tái sử dụng client Google API theo từng key trong bộ nhớ đệm, đưa thao tác xây dựng client ra khỏi thread lock để loại bỏ hoàn toàn độ trễ discovery qua mạng.
- **Bộ nhớ đệm cấu hình RAM**: Tích hợp cache kiểm tra mtime tệp cấu hình cho toàn bộ tiến trình, giảm hàng trăm lượt đọc ổ đĩa lặp lại mỗi giây trên Main Thread.
- **Kéo dài timeout xác nhận WebSub**: Tăng timeout đăng ký WebSub lên 60 giây và thời gian ân hạn bắt tay async lên 600 giây để tăng tỷ lệ đăng ký thành công trên các kết nối quốc tế.
- **Cơ chế giãn cách WebSub**: Khi WebSub hub không phản hồi, hệ thống tự động đặt lịch thử lại sau 5 giờ, không dồn dập request và nhường quyền phát hiện video cho Quét API dự đoán.

## Sửa lỗi
- **Khắc phục lỗi đơ lag giao diện (Not Responding)**: Loại bỏ triệt để hiện tượng luồng chính Tkinter bị chặn do tranh chấp lock trong quá trình quét API tần suất cao.
- **Khắc phục hiện tượng giật màn hình khi quét**: Sửa lỗi so sánh snapshot khiến giao diện liên tục phá hủy và khởi tạo lại toàn bộ widget danh sách key mỗi giây trên Main Loop.
- **Triệt tiêu nhật ký nhiễu**: Loại bỏ thông báo cảnh báo XML trống không cần thiết trong quá trình cào feed định kỳ, giúp console sạch sẽ và dễ theo dõi.

# Phiên bản 1.2.3

## Điểm mới
- YouTube Monitor tự động khởi động khi mở ứng dụng.
- Bổ sung polling reconciliation chạy song song với WebSub để bắt lại video khi callback/ngrok gián đoạn.
- Lưu bền trạng thái phát hiện, queue và retry để tiếp tục an toàn sau khi khởi động lại.

## Cải thiện
- WebSub hoạt động như đường phát hiện nhanh; polling tự chuyển chu kỳ phù hợp khi WebSub khỏe hoặc degraded.
- Polling xử lý feed theo thứ tự cũ đến mới và dùng chung dedup/pending/watermark với WebSub.
- Khi ngrok chưa sẵn sàng, Monitor vẫn chạy bằng polling fallback và tự khôi phục WebSub trong nền.
- Giãn chu kỳ polling mặc định và thêm jitter để giảm tải mạng khi nhiều máy khởi động cùng lúc.

## Sửa lỗi
- Khắc phục video đăng trong lúc ứng dụng tắt bị đánh dấu baseline và bỏ qua sau khi mở lại.
- Khắc phục nguy cơ tải trùng khi WebSub và polling cùng phát hiện một video.
- Khắc phục retry/queue tạm thời bị mất khi Monitor restart.
- Khắc phục auto-start bị vô hiệu ngoài ý muốn khi một dependency nạp `unittest` trong runtime production.

# Phiên bản 1.2.2

## Điểm mới
- Chọn chất lượng tải YouTube trực tiếp trong Monitor: **Tối đa 720p (Nhanh)** hoặc **Tối đa 1080p (Sắc nét)**.
- Video được xuất bản tối đa 5 phút trước lúc bắt đầu Monitor vẫn được xét xử lý để giảm nguy cơ bỏ lỡ do độ trễ WebSub.

## Cải thiện
- Mọi lượt tải lại trực tiếp, định dạng tương thích, cookie, player client và proxy đều giữ cùng mức chất lượng đã chọn.
- WebSub gia hạn lease sớm hơn (trước 24 giờ) và kiểm tra lại mỗi 15 phút.
- Giá trị chất lượng không hợp lệ tự trở về 720p an toàn.

## Sửa lỗi
- Khắc phục video mới bị loại chỉ vì chênh lệch nhỏ giữa thời điểm xuất bản và lúc Start Monitor.
- Khắc phục các lượt fallback tải video không đồng bộ với chất lượng người dùng đã chọn.

# Phiên bản 1.2.1

## Điểm mới
- **Bộ Tối Ưu Tốc Độ Tải YouTube**: Tăng tốc độ tải mạng gấp 3-5x với HTTP chunk 10MB và stream buffer 16KB.
- **Client Throttling Bypass**: Tự động áp dụng player client iOS/Android để vượt qua cơ chế bóp băng thông của YouTube và hạn chế 90% lỗi HTTP 403 Forbidden.
- **Nút Kiểm Tra Cookie Live**: Thêm nút *Kiểm tra Live* trực quan trong YouTube Monitor giúp người dùng kiểm tra ngay độ sẵn sàng của file `cookies.txt` với YouTube.
- **Active Hardware Probing cho GPU**: Tự động kiểm tra tính tương thích thực tế của card GPU (NVENC/AMF/QSV) trước khi sử dụng, tự động chuyển CPU an toàn khi máy không có card rời.

## Cải thiện
- **Quy Trình 1-Pass Faststart**: Tích hợp cờ `+faststart` ngay khi tải video, giảm thời gian xử lý FFmpeg từ 3.8s xuống < 0.2s.
- **Tối Ưu Giao Diện YouTube Monitor**: Tách khu vực Cookie thành 2 tầng rõ ràng (*Chọn...* ở trên, *Lưu*, *Kiểm tra Live*, *Xóa* ở dưới) không bao giờ bị tràn viền.
- **Card Vận Hành Hệ Thống**: Thiết kế lại 2 nút bấm chính to rõ ràng (*Bắt Đầu Giám Sát* & *Dừng*), loại bỏ nút phụ thừa.
- **Hỗ Trợ Mọi Định Dạng Netscape Cookie**: Bộ parser cookie tự động nhận diện định dạng chuẩn kể cả khi thiếu dòng comment header.

## Sửa lỗi
- **Sửa Lỗi Nạp Cookie yt-dlp**: Khắc phục triệt để lỗi truyền sai tham số `cookiefile` khiến file cookie bị bỏ qua.
- **Sửa Lỗi FFmpeg Slow-Mo**: Khắc phục lỗi trích xuất stderr bị che bởi banner bản quyền FFmpeg và xung đột pixel format.
- **Khắc phục nút Xóa Cookie bị ẩn**: Xử lý triệt để lỗi tràn viền giao diện trên các màn hình độ phân giải khác nhau.

# Phiên bản 1.2.0

## Điểm mới
- Tab **Thống Kê & Phân Tích (📊 Analytics)** chuyên biệt: KPI tổng, bảng chi tiết theo tài khoản, bộ lọc Dự án/Thời gian, panel kiểm tra lỗi tức thì (click tài khoản → xem ngay video fail, lý do, link).
- Hệ thống thống kê thuần (Pure Logic Engine) tách biệt hoàn toàn khỏi UI, testable 100% trên CI không cần màn hình.

## Cải thiện
- Tab **Hướng Dẫn** viết lại hoàn toàn ngôn ngữ end-user: 9 chủ đề, Filter Chips, Accordion, nút Copy mẫu an toàn, nút điều hướng đến tab tương ứng.
- Loại bỏ thuật ngữ kỹ thuật nội bộ khỏi tài liệu người dùng.
- Cải tiến FFmpeg helper và core logic xử lý video.

## Sửa lỗi
- Khắc phục context menu test treo CI trên Windows headless.
- Các regression liên quan đến tải video, xử lý proxy, profile picker.

# Phiên bản 1.1.5

## Điểm mới
- Tự động chuẩn bị browser khi WebSub phát hiện video mới.
- Bổ sung owned ngrok agent, tự phục hồi tunnel và Retry thủ công.

## Cải thiện
- Tái sử dụng browser và upload surface đã sẵn sàng để giảm thời gian chuẩn bị đăng.
- Context menu YouTube Monitor cập nhật trạng thái ngay sau thao tác.
- Ngrok chỉ dừng process do ứng dụng sở hữu.

## Sửa lỗi
- Khắc phục context menu bị hủy trước khi callback chạy.
- Khắc phục action áp dụng sai kênh khi selection thay đổi.
- Khắc phục lỗi mở link/thư mục không có phản hồi.
- Khắc phục tunnel ngrok gián đoạn nhưng monitor không thể phục hồi.

# Phiên bản 1.1.4

## Điểm mới
- Điều chỉnh xử lý Shorts để video đúng 60 giây cũng được làm chậm thành 61 giây.

## Cải thiện
- Quy tắc xử lý thời lượng áp dụng nhất quán cho toàn bộ khoảng từ 40 đến 60 giây.

## Sửa lỗi
- Khắc phục video đúng 60 giây bị giữ nguyên thay vì xử lý khi tính năng điều chỉnh Shorts được bật.

# Phiên bản 1.1.3

## Điểm mới
- Tự động nhận biết trạng thái TikTok tạm khóa đăng ở cấp tài khoản và dừng queue để tránh gửi thêm video.

## Cải thiện
- Startup watchdog chỉ phục hồi video hợp lệ của đúng profile và dùng đường dẫn chính xác khi đọc lịch sử tải.
- Danh sách dự án cập nhật tại chỗ, giảm tạo lại widget khi trạng thái profile thay đổi.
- Cảnh báo Profile-Patchright thuộc tài khoản khác có thêm hướng dẫn xử lý an toàn.

## Sửa lỗi
- Khắc phục profile vừa Start tự nhận và đăng hàng loạt video đã có sẵn trong thư mục.
- Khắc phục queue tiếp tục thử video sau khi TikTok báo tài khoản tạm thời không được đăng.
- Khắc phục TclError "invalid command name ... ctkcanvas" khi ProjectList refresh liên tục.

# Phiên bản 1.1.2

## Điểm mới
- Tải video YouTube ưu tiên kết nối trực tiếp (không qua proxy); proxy và cookie chỉ được dùng khi bật tùy chọn fallback `youtube_proxy_fallback`.
- Bổ sung kiểm tra file cookie YouTube (cấu trúc Netscape, ngày hết hạn) và hiển thị trạng thái cookie ngay trên màn hình cấu hình YouTube.
- Bổ sung tác vụ "Tải thử" video YouTube theo chế độ direct-first, cùng chuỗi thử lại linh hoạt (định dạng thay thế, client thay thế, cookie, proxy).
- Bổ sung bộ chọn hồ sơ có tìm kiếm khi danh sách hồ sơ lớn và cải tiến trạng thái chạy lô (Batch).

## Cải thiện
- Ưu tiên định dạng DASH (video `avc1`/mp4 kèm audio `m4a`) thay vì định dạng progressive 18 khi tải video YouTube.
- Cải thiện đồng bộ cấu hình (config service), nhật ký hiệu năng cao (log engine), bảng hồ sơ và quản lý cửa sổ taskbar.
- Nâng cấp tự động yt-dlp trong ứng dụng và cải thiện pipeline phân phối video thống nhất (watchdog).

## Sửa lỗi
- Khắc phục tải video YouTube bị HTTP 403 do YouTube chặn định dạng progressive 18; từ bản này ưu tiên DASH-first và chỉ rơi về progressive khi không có lựa chọn khác.
- Khắc phục một số trường hợp kênh/batch chạy sai trạng thái và dữ liệu cũ hiển thị không nhất quán.

# Phiên bản 1.1.1

## Điểm mới
- Phát hành Browser Resource v1.1.0: bộ engine Dong Lao Browser 144 mới đã loại bỏ kiểm tra chữ ký HMAC (6 NOPs) nên chấp nhận mọi tên profile, cho phép cấp phát cấu hình động theo profile mới.
- Công cụ vận hành browser engine: `scripts/patch_all_browser_engines.py` và `scripts/patch_chrome_version_info.py` (patch trên staging copy, có kiểm tra bytes, idempotent, tạo backup và xác minh sau patch) kèm bộ test đầy đủ.
- Quy tắc mới cho video YouTube Short khi bật xử lý: video dưới 40 giây giữ nguyên; video từ 40 đến dưới 60 giây được làm chậm đủ 61 giây; video từ 60 giây trở lên giữ nguyên.

## Cải thiện
- Cấu hình C++ engine (data.huynhthang / data.orbita) được sinh từ Base Template có sẵn trong ứng dụng, tự đồng bộ `profile_name` và proxy của từng profile thay vì phụ thuộc dữ liệu cục bộ của TikTok Manager.
- Bộ phát hiện đóng cửa sổ thủ công (manual close) gọi đóng session đúng một lần và giải phóng profile để có thể mở lại ngay trên cùng profile.

## Sửa lỗi
- Khắc phục profile mới (ví dụ tên tùy ý như BKT_8) bị engine cũ từ chối với lỗi `Antidetect license verification FAILED` khi khởi chạy; browser resource v1.1.0 đã xử lý triệt để.

# Phiên bản 1.1.0

## Điểm mới
- Tích hợp Dong Lao TikTok Browser 144 làm động cơ trình duyệt chính (native C++ anti-detect), kèm trình tải browser tự động khi lần đầu chạy: tải streaming, kiểm tra SHA-256, giải nén an toàn và hoàn tác khi lỗi.
- Bổ sung tab Hướ Dẫn với 9 nhóm nội dung hướng dẫn sử dụng phần mềm.
- Bổ sung phân trang (pagination) cho danh sách hồ sơ để quản lý nhiều profile dễ dàng hơn.
- Thiết kế lại hộp thoại kích hoạt bản quyền (License Modal) theo Design System và hỗ trợ kích hoạt ngầm từ dữ liệu đã lưu.

## Cải thiện
- Đổi thương hiệu Browser Engine sang tên Dong Lao TikTok Browser 144 kèm icon đồng nhất; ưu tiên dò tìm engine tại thư mục chuẩn và giữ các engine cũ làm dự phòng.
- Responsive và căn giữa các hộp thoại; đồng bộ icon ứng dụng.
- Cải thiện giao diện nhập hồ sơ hàng loạt.
- Cải thiện anti-detect và cấu hình profile fingerprint cho Dong Lao Browser 144.
- Thu thập diagnostics (ảnh chụp, HTML, JSON) và xử lý nhiều popup đồng thời khi đăng video.

## Sửa lỗi
- Tự động đóng các popup tiếng Anh/Nhật của TikTok khi đăng video (content check, hướng dẫn mới).
- Popup lạ dừng an toàn trước khi bấm Post và ghi lại diagnostics.
- Chế độ test khô (dry-run) không bấm Post, không tăng số liệu video đã đăng và tự dọn hồ sơ thử nghiệm.

# Phiên bản 1.0.19

## Điểm mới
- Bổ sung tính năng Xóa hồ sơ: xóa nhiều hồ sơ cùng lúc, tùy chọn xóa cả dữ liệu trên đĩa, và phím tắt Delete trên bảng profile.
- Bổ sung tự động tải và cài đặt ngrok.exe từ nguồn chính thức khi thiếu, không còn phụ thuộc tải tay.
- Nâng cấp VIBE Stealth Engine: cải thiện giả lập fingerprint và các chỉ số chống phát hiện cho browser.
- Cải tiến engine cấu hình profile với quy tắc mặc định và tùy chọn cấu hình rõ ràng hơn.

## Cải thiện
- Xóa hồ sơ an toàn hơn: chặn xóa khi hồ sơ đang chạy và cảnh báo rõ ràng khi xóa dữ liệu trên đĩa.
- Ngrok được tìm kiếm tại nhiều vị trí (thư mục ứng dụng, _internal, cache pyngrok, System PATH) trước khi tải mới.
- Cải thiện bảng Monetization và dữ liệu thu nhập, thông tin KYC hiển thị chính xác hơn.
- Dọn dẹp các tài nguyên không còn tải tự động (ngrok, service_account.json) khỏi danh sách tài nguyên GitHub.

## Sửa lỗi
- Khắc phục luồng kiểm tra tài nguyên lần chạy đầu khi ngrok thiếu không gây lỗi khởi động.
- Sửa một số trường hợp xác định tài khoản và cấu hình profile không nhất quán.

# Phiên bản 1.0.18

## Điểm mới
- Thiết kế lại hộp thoại Thêm hồ sơ theo bố cục card 4 phần: thông tin tài khoản, proxy, thư mục dữ liệu và vận hành.
- Bổ sung kiểm tra proxy trực tiếp (test live, hiển thị quốc gia/IP) ngay trong hộp thoại thêm hồ sơ.
- Bổ sung Quick Paste: dán nguyên chuỗi thông tin, ứng dụng tự trích xuất tên, email, cookie, proxy, 2FA và mật khẩu vào đúng ô.
- Bổ sung tự động sinh thư mục chuẩn Auto_Data/<Tên hồ sơ>/ theo tên hồ sơ.
- Bỏ hoàn toàn fallback trình duyệt Orbita, chỉ sử dụng Chromium chrome-win64 kèm stealth engine gốc.

## Cải thiện
- Tối ưu tham số khởi chạy browser để giảm RAM, chống treo và tăng hiệu năng khi chạy nhiều profile.
- Dọn dẹp tự động các cache tạm (GPU, shader, code, media) trong thư mục profile mà không ảnh hưởng cookie/session.
- Cải thiện kiểm tra trùng lặp thư mục Chrome Profile và thông báo lỗi rõ ràng khi thêm hồ sơ.
- Mở rộng kiểm thử hộp thoại thêm hồ sơ và luồng nhập hàng loạt profile.

## Sửa lỗi
- Sửa lỗi hộp thoại thêm hồ sơ không tạo được thư mục video khi hồ sơ được thêm mới.

# Phiên bản 1.0.17

## Điểm mới
- Bổ sung tab/bộ lọc nhanh theo trạng thái cookie và thu nhập: Tất cả, Cookie Sống, Cookie Die, Chưa Có Cookie, Đã KYC, Đã Khai Thuế, TKTBM, Đang Chạy.
- Mở rộng thông tin KYC và thu nhập Monetization: dữ liệu chi tiết thu nhập, quỹ tác giả (CRP), trạng thái đăng ký thuế và cảnh báo cookie hết hạn.
- Bổ sung kiểm tra cookie nhanh qua HTTP (Webcast/Passport API) không cần mở browser.
- Chuyển browser đóng gói sang Chromium chrome-win64.

## Cải thiện
- Nâng cấp dashboard Monetization thành 5 thẻ KPI tổng quan.
- Cải thiện hộp thoại chi tiết thu nhập với đầy đủ thông tin tài chính và quỹ tác giả.
- Ưu tiên dò tìm chrome-win64 tại nhiều vị trí root kèm tham số khởi chạy tối ưu RAM.
- Đổi tên asset release sang tiền tố DONGLAO-TIKTOK-v; vẫn giữ file cập nhật tương thích cho bản cũ.

## Sửa lỗi
- Khắc phục bộ lọc tìm kiếm và thống kê không khớp khi profile thiếu thông tin tiktok_id.
- Cải thiện xác định trạng thái cookie live dựa trên trạng thái session, trạng thái profile và thời gian xác thực.

# Phiên bản 1.0.16

## Điểm mới
- Bổ sung VIBE Stealth Engine độc lập cho browser profile.
- Bổ sung cấu hình nhận dạng profile theo tài khoản khi mở Patchright.

## Cải thiện
- Cập nhật thứ tự lựa chọn browser, ưu tiên Chromium đóng gói trước các bản Orbita tương thích.
- Cải thiện cấu hình User-Agent, timezone và profile identity khi tạo browser session.
- Cải thiện dừng callback server và thu hồi thread của YouTube Monitor.

## Sửa lỗi
- Khắc phục browser TikTok Manager tự đóng do kiểm tra license anti-detect khi khởi chạy.
- Loại bỏ launch flag gây lỗi license trên browser profile hiện tại.

# Phiên bản 1.0.15

## Điểm mới
- Nâng cấp giao diện quản lý theo bố cục Sidebar, Header và các workspace độc lập cho Profiles, YouTube, lịch sử và tài chính.
- Bổ sung thẻ tổng quan profile, danh sách dự án trên Sidebar, log drawer thu gọn và toast notification.
- Bổ sung giao diện tổng hợp Monetization cùng các hộp thoại profile, proxy và chi tiết tài chính.
- Bổ sung engine cấu hình profile Orbita 144 và cơ chế profile lease cấp hệ điều hành.

## Cải thiện
- Tăng chiều cao hàng và độ rõ của bảng profile, giữ nguyên multi-select, sort, context menu và incremental refresh.
- Ưu tiên Orbita 144 khi có sẵn, đồng thời duy trì các browser fallback hiện có.
- Bổ sung kiểm thử contract giao diện, UI components, dialogs, monetization client và profile configuration.

## Sửa lỗi
- Cải thiện tính nhất quán của bố cục khi chuyển workspace mà không tạo lại YouTube Monitor và Batch views.
- Giữ tương thích với các widget key và handler hiện có trong controller.

# Phiên bản 1.0.14

## Điểm mới
- Bổ sung TikTok Insights (Beta): kiểm tra trực tiếp thông tin tài khoản TikTok theo chế độ chỉ đọc.
- Xem nhanh Balance, Payout, trạng thái KYC và Payment Method ngay trong bảng kết quả kiểm tra.
- Lưu lịch sử kiểm tra vào cơ sở dữ liệu cục bộ (SQLite) trong thư mục dữ liệu ứng dụng, không ghi vào cấu hình.

## Cải thiện
- Các request kiểm tra tài khoản đều đi qua chính sách an toàn chỉ đọc: chỉ HTTPS, endpoint cố định, giới hạn kích thước phản hồi và không chứa cookie/token/chữ ký.
- Phân biệt rõ trạng thái từng mục: thành công, thành công không có dữ liệu, cần đăng nhập, giới hạn, không khả dụng hoặc lỗi.
- Các mục chưa hỗ trợ đầy đủ (Dashboard, RPM, Views, Creative Rewards, Traffic, Video Rank, Violations) hiển thị N/A và không phát request đoán.
- Cải thiện đóng browser đúng cách sau khi kiểm tra để tránh khóa profile.

## Sửa lỗi
- Khắc phục một số trường hợp giá trị số tiền bị hiểu sai (bool/đơn vị minor) và dữ liệu cũ không bị hiển thị như dữ liệu mới.

# Phiên bản 1.0.13

## Điểm mới
- Khôi phục cơ chế tự động tải Browser cần thiết để mở profile TikTok (Browser-v1.0.7.zip từ resource release).

## Cải thiện
- Patchright ưu tiên sử dụng Browser do ứng dụng quản lý; nếu chưa có, dùng Google Chrome hệ thống làm dự phòng.
- Hiển thị hướng dẫn tiếng Việt rõ ràng khi máy chưa có browser phù hợp, thay vì yêu cầu chạy "patchright install".

## Sửa lỗi
- Khắc phục lỗi mở browser thủ công báo "Executable doesn't exist ... .local-browsers" do bản phát hành thiếu Chromium mặc định.
- Khắc phục không thể chạy upload hoặc mở browser trên máy người dùng mới.

# Phiên bản 1.0.12

## Điểm mới
- Bổ sung quản lý browser profile riêng theo từng tài khoản, hạn chế dùng nhầm hoặc chia sẻ dữ liệu đăng nhập giữa các tài khoản.
- Bổ sung Reset Browser an toàn: browser/profile cũ được đưa vào quarantine và cho phép khôi phục trong 7 ngày.
- Bổ sung theo dõi môi trường proxy/GEO và cảnh báo khi quốc gia, ASN hoặc múi giờ thay đổi so với lần chạy trước.
- Bổ sung import/export tài khoản với xem trước, kiểm tra hợp lệ và che dữ liệu nhạy cảm.
- Bổ sung công cụ chẩn đoán proxy, môi trường login và anti-detect dành cho hỗ trợ kỹ thuật.

## Cải thiện
- Tối ưu quy trình login thủ công một lần và tái sử dụng session/profile ổn định.
- Cải thiện xác minh trạng thái đăng nhập trước khi upload.
- Làm gọn giao diện quản lý profile, chuyển các thao tác phụ vào menu và ưu tiên không gian cho danh sách tài khoản.
- Bổ sung TikTok ID, khu vực và trạng thái sức khỏe tổng hợp vào bảng profile.
- Cải thiện dừng browser và lưu trạng thái YouTube Monitor khi thoát ứng dụng.
- Bảo vệ browser profile bằng ownership marker và kiểm tra đường dẫn an toàn.

## Sửa lỗi
- Khắc phục nguy cơ nhiều tài khoản dùng chung browser profile.
- Khắc phục trạng thái session cũ còn được xem là hợp lệ sau khi reset hoặc đổi môi trường.
- Khắc phục một số trường hợp browser/profile không được đóng hoặc dọn sạch hoàn toàn.
- Hạn chế lỗi cấu hình proxy và thay đổi IP không được cảnh báo.

# Phiên bản 1.0.11

## Điểm mới
- Chuyển hệ thống tự động hóa TikTok sang nền tảng Patchright, thay thế toàn bộ trình điều khiển cũ.
- Tự động di trú dữ liệu profile khi mở lần đầu, giữ phiên đăng nhập và cấu hình hiện có.

## Cải thiện
- Nhập cookie đăng nhập tin cậy hơn, nhận diện đúng trạng thái "đã đăng nhập" trước khi đăng video.
- Tối ưu tốc độ chờ và kiểm tra trang để đăng video nhanh hơn; bổ sung số liệu đo thời gian từng bước để dễ theo dõi.
- Cải thiện khả năng đóng browser và profile khi dừng hoặc tắt ứng dụng.

## Sửa lỗi
- Khắc phục cửa sổ hướng dẫn của TikTok che nút Đăng khiến video không lên được.
- Phân biệt rõ lỗi xảy ra trước hay sau khi gửi bài đăng, tránh đăng trùng khi kết quả không chắc chắn.

# Phiên bản 1.0.10

## Điểm mới
- Không có.

## Cải thiện
- Cải thiện khả năng đăng video lên TikTok.

## Sửa lỗi
- Sửa lỗi bấm Đăng rồi nhưng video không lên TikTok.

# Phiên bản 1.0.9

## Điểm mới
- YouTube Monitor giờ chỉ tải video được đăng sau khi monitor bắt đầu chạy.
- Khi thêm kênh YouTube mới hoặc mở monitor lần đầu với kênh chưa có lịch sử, ứng dụng sẽ ghi nhận các video hiện có làm mốc ban đầu thay vì tải lại ngay.

## Cải thiện
- Cải thiện cơ chế ghi nhớ video đã thấy của từng kênh, giúp hạn chế tải lại video cũ sau khi cập nhật hoặc mở lại tool.
- Khi cập nhật phiên bản, ứng dụng sẽ dừng YouTube Monitor đúng cách trước khi thay file để lưu trạng thái kênh an toàn hơn.
- Nếu một kênh YouTube bị lỗi API, monitor sẽ bỏ qua kênh đó và tiếp tục kiểm tra các kênh còn lại.

## Sửa lỗi
- Khắc phục lỗi sau khi cập nhật, tool có thể tải lại video cũ nếu danh sách video đã xử lý bị rỗng hoặc chưa có mốc thời gian.
- Khắc phục trường hợp video đã đăng trước lúc mở tool vài phút vẫn bị tải xuống. Từ bản này, các video có trước thời điểm monitor bắt đầu chạy sẽ chỉ được ghi nhận là đã thấy, không tải lại.
- Khắc phục nguy cơ mất trạng thái YouTube Monitor khi cập nhật app trong lúc monitor đang chạy.

# Phiên bản 1.0.8

## Điểm mới
- Kiểm tra container video khi hoàn tất tải xuống: remux WebM/MKV sang MP4, transcode codec không tương thích.
- Xác minh SHA-256 bắt buộc khi tải FFmpeg — nếu không lấy được checksum sẽ không cài đặt.
- Sao lưu tài nguyên hiện tại trước khi thay thế (Browser/ngrok), khôi phục nếu thất bại.
- Xác thực chữ ký WebSub HMAC-SHA256 để chống callback giả mạo.
- Ghi JSON dạng nguyên tử (temp → fsync → replace) để chống hỏng file khi mất điện.
- Dependency được pin version để build tái lập được.

## Cải thiện
- FFmpeg: xác minh cả ffprobe trước khi cài đặt, giữ bản cũ nếu bản mới thất bại.
- Container: probe format/codec bằng ffprobe, không giả định .mp4 extension.
- Tài nguyên: download vào file .part, kiểm tra ZIP traversal, validate trước khi swap.
- WebSub callback: giới hạn body 1MB, từ chối request thiếu signature.
- CI workflow: compile toàn bộ source, smoke test bản frozen, kiểm tra artifact không chứa secret.
- Loại bỏ Selenium Wire/request trace debug để tránh dependency cũ và giảm kích thước/rủi ro build.

## Sửa lỗi
- Khắc phục cài đặt FFmpeg ngay cả khi SHA-256 không tải được (fail-open → fail-closed).
- Khắc phục download tài nguyên ghi đè file đích trước khi xác minh.
- Khắc phục WebSub callback server không kiểm tra chữ ký payload.
- Khắc phục ghi JSON config/channels không dùng atomic write.

# Phiên bản 1.0.7

## Cải thiện
- Dọn dẹp browser Orbita/Chrome triệt để hơn khi dừng profile hoặc tắt ứng dụng.
- Browser mở thủ công (chuột phải → Mở trình duyệt) nay cũng được đóng tự động khi bấm Stop hoặc tắt app.
- Tắt ứng dụng: quét toàn bộ profiles, không phụ thuộc vào project đang chọn, có timeout dự phòng.
- Cải thiện nhận diện chromedriver mồ côi theo đúng đường dẫn driver riêng của từng profile.

## Sửa lỗi
- Khắc phục browser thủ công không được đóng khi dừng profile (chỉ kill process, thiếu driver.quit() sạch sẽ).

# Phiên bản 1.0.6

## Sửa lỗi
- Loại bỏ kiểm tra tài nguyên hệ thống (RAM/CPU) trước khi đăng video để tránh chặn upload khi máy tạm thời cao tải. Upload vẫn có cơ chế retry và phục hồi nếu xảy ra lỗi runtime.

# Phiên bản 1.0.5

## Điểm mới
- Thông báo rõ ràng khi có phiên bản mới và cho phép cập nhật trực tiếp trong ứng dụng.
- Bổ sung lựa chọn nhắc lại sau hoặc bỏ qua một phiên bản cụ thể.
- Tự động đồng bộ môi trường vị trí và múi giờ phù hợp cho từng hồ sơ.
- Hỗ trợ chế độ chỉ mở trình duyệt khi có video mới xuất hiện.

## Cải thiện
- Cải thiện độ ổn định khi khởi động trình duyệt và xử lý video mới.
- Tăng độ chính xác khi xác nhận bài đăng đã được TikTok tiếp nhận.
- Tối ưu tốc độ phát hiện video, tải video và bấm Đăng khi TikTok cho phép.
- Cải thiện tính nhất quán của môi trường trình duyệt theo từng hồ sơ.
- Nội dung cập nhật được trình bày ngắn gọn, dễ hiểu bằng tiếng Việt.

## Sửa lỗi
- Khắc phục một số trường hợp cửa sổ hướng dẫn che nút thao tác.
- Hạn chế thao tác lặp khi trạng thái đăng video chưa được xác định rõ ràng.
- Khắc phục trường hợp video lớn bị nhận nhầm là đã ngừng xử lý.
- Bổ sung cảnh báo rõ ràng khi TikTok từ chối hoặc tạm hạn chế quyền đăng bài.
