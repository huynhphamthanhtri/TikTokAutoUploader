# Bàn giao tab API và quét video

## Phạm vi triển khai

Tab **⚡ API & Quét Video** nằm trong khu vực YouTube, cùng hàng với các tab giám sát, tải hàng loạt và lịch sử. Nút **🔑 Nhóm API Key** trong màn hình giám sát chuyển đến tab này; khi màn hình giám sát được dùng độc lập, nút mở cùng giao diện trong cửa sổ riêng.

- Nhóm API hiển thị key đã che, trạng thái tiếng Việt và thời điểm thử lại đối với hạn mức/cooldown. Có thêm, xóa và kiểm tra key; thao tác mạng chạy ở luồng nền, kết quả được chuyển về luồng giao diện.
- Ba trường số có kiểm tra hợp lệ, cho phép nhập dấu phẩy thập phân. Khoảng quét phải hữu hạn và lớn hơn 0; thời gian trước/sau phải hữu hạn và không âm.
- Ước tính cập nhật khi nhập, theo `(trước + sau) × 60 / khoảng quét`. Cảnh báo khi ước tính vượt 1.200 lượt mỗi kênh/khung; cảnh báo không tự thay đổi giá trị. Bấm **Lưu cài đặt** để áp dụng và lưu.
- Scheduler đọc khoảng quét từ cấu hình sau mỗi vòng. Khung tự học tính lại theo giờ đăng dự kiến; giờ thủ công giữ nguyên. Khung qua nửa đêm dùng ngày của giờ đăng dự kiến đối với lịch tự học, ngày bắt đầu đối với lịch thủ công.
- Chỉ video được cơ chế chống trùng hiện có tiếp nhận mới hoàn tất khung. Kiểm tra `last_known_video_id`, `seen`, pending và SQLite của luồng xử lý hiện có; video cũ hoặc trước phiên không gây dừng giả.
- Sau khi tiếp nhận video, lưu ID mới nhất và đánh dấu các khung hiện hành của riêng kênh đó. Chặn lời gọi API tiếp theo, kể cả fast-track và API dự phòng. Khung sau/kênh khác hoạt động bình thường. Tắt tùy chọn dừng thì vẫn tiếp tục quét.
- Luồng reconciliation cũ nhường kênh đang trong khung dự đoán cho scheduler, tránh API dự phòng gọi lại sau khi khung hoàn tất. Phát hiện được tiếp nhận qua WebSub/RSS cũng thông báo cho scheduler.
- Hiển thị trạng thái tiếng Việt, giờ phát hiện và số giây quét đã bỏ qua. Các enum backend của key vẫn là `ACTIVE`, `QUOTA_EXCEEDED`, `RATE_LIMITED`, `INVALID`, `DISABLED`.
- Làm mới danh sách key giữ cooldown đang có trong bộ nhớ, thay vì nạp đè metadata cũ từ cấu hình mỗi giây. Giữ nguyên mã nguồn `key_manager.py`.

## Các tệp thuộc thay đổi này

| Tệp | Thay đổi |
|---|---|
| `app_ui.py` | Gắn tab và liên kết nút chuyển tab |
| `youtube_monitor/ui.py` | Chuyển nút quản lý key sang giao diện tập trung |
| `youtube_monitor/core.py` | Nạp/lưu cấu hình, trạng thái UI, lưu ID chống trùng, phối hợp các luồng phát hiện/quét |
| `youtube_monitor/predictive_scheduler.py` | Khoảng quét động, khung theo ngày/kênh, dừng sớm, trạng thái thời gian chạy |
| `youtube_monitor/polling_settings.py` | Tệp mới: mặc định, kiểm tra số, nhãn tiếng Việt, ước tính |
| `youtube_monitor/polling_ui.py` | Tệp mới: giao diện tab |
| `test_predictive_polling_settings.py` | Tệp mới: test chức năng và tích hợp cục bộ |

Thư mục đã có thay đổi chưa commit trước khi bắt đầu, bao gồm `main.py`, các test YouTube, `core.py`, `ui.py`, `state_store.py` và các module scheduler/key manager chưa được Git theo dõi. Bảng trên chỉ mô tả phần thực hiện trong tác vụ này, không nhận toàn bộ diff có sẵn là thay đổi mới. Không commit, push, release hay đóng gói.

## Cấu hình cuối

Trong `youtube_config.json`:

```json
{
  "predictive_polling": {
    "poll_interval_seconds": 1.0,
    "window_before_minutes": 10,
    "window_after_minutes": 10,
    "stop_after_detection": true
  }
}
```

Đây là một mục trong cấu hình hiện có, không thay thế toàn bộ tệp. Cấu hình cũ thiếu mục hoặc thiếu trường nhận mặc định lúc đọc. Các khóa không liên quan và khóa mở rộng được giữ lại. Khi lưu dùng luồng ghi tệp tạm rồi thay thế tệp hiện có. Metadata kênh bổ sung `last_known_video_id`; dữ liệu cũ không có trường này vẫn nạp được.

## Nguồn và mức kiểm chứng

**INTEGRATION TESTED — cục bộ, chưa kiểm chứng account live.** Test dùng SQLite chống trùng và hàng chờ thật của ứng dụng trong thư mục tạm; widget CustomTkinter thật; lưu/nạp JSON và một tiến trình Python mới để kiểm tra khởi động lại. Biên gọi YouTube được thay bằng response dựng theo tài liệu, không phải raw response từ tài khoản thật.

Nguồn schema: [PlaylistItems](https://developers.google.com/youtube/v3/docs/playlistItems) và [PlaylistItems.list](https://developers.google.com/youtube/v3/docs/playlistItems/list). Dùng `contentDetails.videoPublishedAt` làm giờ video được phát hành, dự phòng bằng `snippet.publishedAt` khi thiếu. Tài liệu phân biệt trường thứ hai là thời điểm thêm mục vào playlist.

Test mới bao phủ mặc định, khoảng quét động, biên tùy chỉnh, dữ liệu số không hợp lệ, khung qua ngày, video cũ/seen/pending/SQLite, video trước phiên, dừng ngay, chặn API dự phòng, WebSub/RSS, kênh khác, khung sau, tắt dừng, lỗi mạng, cấu hình cũ/null, khởi động lại, che key, nhãn UI và enum backend. Test UI kiểm tra widget và tương tác nhập/lưu; chưa kiểm tra ảnh bố cục trên mọi kích thước màn hình.

## Giới hạn và rủi ro còn lại

- Chưa dùng account live để xác nhận phản hồi YouTube, xoay key, hết hạn mức/rate-limit hoặc độ trễ thực tế; chưa upload/post video live.
- Trạng thái hoàn tất khung nằm trong bộ nhớ phiên scheduler. Khởi động lại monitor giữa khung có thể quét lại; ID mới nhất và cơ chế chống trùng được lưu bền vẫn ngăn tiếp nhận lại video cũ.
- Thời gian chờ thực tế gồm thời gian mạng và xử lý; con số trên UI là ước tính lý thuyết, không phải cam kết số request/giây.
- Giữ cách giải quyết múi giờ có sẵn của dự án; chưa kiểm chứng DST live.
- Kết quả hồi quy có lỗi/crash/timeout được liệt kê trong báo cáo kết quả bên dưới; không kết luận toàn hệ thống ổn định hoặc pass toàn bộ.

## Bằng chứng chạy

Các lượt đầu được giữ riêng: `predictive_targeted_results.xml` ghi 12 pass/12 lỗi khởi tạo fixture (sai tên `_pending`; đã sửa thành `_pending_video_ids`), `predictive_targeted_results_2.xml` ghi 24 pass. Sau đó bổ sung test, không xóa assertion hoặc thêm skip.

`scratch/predictive-regression/cooldown_regression_proof.log` ghi việc chạy lại đúng getter cũ để tái hiện mất trạng thái `RATE_LIMITED`, rồi cùng assertion đạt với getter mới. Đây là phép tái hiện riêng cho getter, không phải toàn bộ chương trình trước sửa.

Lượt hồi quy chạy chung bằng `python -m pytest @testFiles -q -p no:cacheprovider --junitxml=predictive_full_regression.xml` bị Windows access violation, exit `-1073741819`; log: `predictive_full_regression.log`. Không có kết quả JUnit hoàn tất cho lượt này.

Lượt gộp `predictive_final_verified.xml` ghi 120 pass, 2 skip với thông báo `Tkinter display not available`. Các lượt trước chạy test hợp đồng UI đạt. Đây là hiện tượng môi trường/lifecycle Tk chưa xác định nguyên nhân đầy đủ; không xem một lần chạy lại đạt là đã sửa.

Lượt hồi quy từng file được chạy bằng `python scratch/run_predictive_regression.py scratch/predictive-regression-final`. Runner gọi `python -m pytest <tệp> -q -p no:cacheprovider --junitxml=<đường dẫn>` cho mỗi file `test_*.py` ở gốc dự án, giới hạn 180 giây/file, giữ nguyên test/assertion. Mỗi lệnh, exit status và thời gian được ghi trong `results.json`; log và JUnit riêng đi kèm. Không cộng các file crash/timeout thành test đã pass.
