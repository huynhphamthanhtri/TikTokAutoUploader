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
