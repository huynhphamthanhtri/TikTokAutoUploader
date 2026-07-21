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
