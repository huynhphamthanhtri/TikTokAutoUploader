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
