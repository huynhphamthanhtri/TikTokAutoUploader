# Design Spec: Nâng Cấp Chính Sách Kỹ Thuật Video & Hộp Thoại Xem Chi Tiết Video Bị Bỏ Qua

Ngày chốt thiết kế: 2026-11-19  
Trạng thái: Đã xác nhận qua Brainstorming (Ready for Implementation)

---

## 1. Tóm Tắt Yêu Cầu (Understanding Summary)

- **Vấn đề thực tế**: Khi người dùng sử dụng tính năng *1-Click Lên Lịch Tự Động*, màn hình Ma trận hiển thị cảnh báo: `⚠️ Bỏ qua 469 video không hợp lệ.` nhưng người dùng không biết lý do vì sao và không xem được danh sách file bị bỏ qua. Đồng thời, chính sách kỹ thuật cũ giới hạn trần thời lượng ở mức 10 phút (600s) và thiếu các codec phổ biến (như VP9 của WebM).
- **Giải pháp toàn diện**:
  1. **Nới lỏng chính sách kiểm tra kỹ thuật lõi (`VideoValidationPolicy`)**:
     - Trần thời lượng nâng từ 10 phút (600s) lên **60 phút (3.600s)** theo chuẩn TikTok Web Studio.
     - Bổ sung các codec phổ biến: **`vp9`**, **`vp8`**, **`mpeg4`** bên cạnh `h264`, `hevc`, `av1`.
     - Giữ nguyên trần 4GB và tối thiểu 3 giây.
  2. **Nút kích hoạt trên Ma trận xem trước (`schedule_ui.py`)**:
     - Hiển thị nút bấm cảnh báo nổi bật: `⚠️ X video bị bỏ qua [🔍 Xem chi tiết]` khi có video unassigned.
  3. **Hộp thoại chuyên biệt `UnassignedVideosModal` (`ui_dialogs.py`)**:
     - Thanh lọc nhanh (Filter Chips): Tất cả, Quá thời lượng, Sai codec, File hỏng / 0 Byte, Xung đột.
     - Bảng Treeview 4 cột: STT, Tên File, Nhóm Lỗi, Chi Tiết Kỹ Thuật (Việt hóa thân thiện).
     - Menu chuột phải: Sao chép đường dẫn, Mở thư mục chứa file.
     - Tác vụ chân trang: Sao chép danh sách vào Clipboard, Xuất báo cáo ra file TXT (`utf-8-sig`).

---

## 2. Decision Log (Nhật Ký Quyết Định)

| ID | Quyết định | Phương án đã cân nhắc | Lý do lựa chọn |
|---|---|---|---|
| **D1** | Nâng trần thời lượng lên **60 phút (3.600s)** và bổ sung **`vp9`, `vp8`, `mpeg4`** | Giữ nguyên 10 phút; Nới lỏng 30 phút; Bỏ kiểm tra codec | Khớp hoàn toàn với giới hạn tải lên thực tế của TikTok Web Studio 2026, loại bỏ triệt để hiện tượng loại trừ oan video hợp lệ |
| **D2** | Thiết kế hộp thoại chuyên biệt **`UnassignedVideosModal`** | Thêm Tab phụ trong Preview; Chỉ hiện Messagebox | Giữ không gian màn hình chính rộng rãi, trải nghiệm xem danh sách và lọc lỗi trực quan, tiện lợi |
| **D3** | Bổ sung **Filter Chips** phân loại lỗi | Bảng phẳng không lọc | Người dùng có ngay cái nhìn tổng quan lỗi nào chiếm đa số để xử lý nhanh |
| **D4** | Xuất file TXT chuẩn **`utf-8-sig`** | UTF-8 thường; CSV | Tương thích 100% với Windows Notepad tiếng Việt, người dùng copy paste đường dẫn dễ dàng |

---

## 3. Chi Tiết Kỹ Thuật (Architecture & Implementation)

### 3.1. Core Policy (`schedule_contracts.py` & `video_probe.py`)
- Sửa `VideoValidationPolicy`:
  - `max_duration_seconds: float = 3600.0`
  - `allowed_codecs: Tuple[str, ...] = ("h264", "hevc", "av1", "vp9", "vp8", "mpeg4")`
- Cập nhật `canonical_codec_variants` trong `video_probe.py`:
  - Thêm `"vp9": {"vp9", "vp09"}`, `"vp8": {"vp8"}`, `"mpeg4": {"mpeg4", "mp4v", "mp4v-es"}`.
- Bổ sung helper phân loại & Việt hóa lỗi: `humanize_unassigned_reason(reason: str, details: str) -> Tuple[str, str]` (trả về `(category_vi, details_vi)`).

### 3.2. Hộp Thoại `UnassignedVideosModal` (`ui_dialogs.py`)
- Kế thừa `SafeCTkToplevel`.
- Khởi tạo nhận `parent` và `unassigned_videos: Sequence[UnassignedVideo]`.
- Bảng `Treeview` hiển thị: STT, Tên file, Nhóm lỗi, Chi tiết kỹ thuật.
- Filter chips động dựa trên các nhóm lỗi thực tế xuất hiện trong danh sách.
- Hỗ trợ export file TXT với encoding `utf-8-sig`.

### 3.3. Tích Hợp UI (`schedule_ui.py`)
- Trong `BulkScheduleModal._render_preview()`:
  - Nếu `len(plan.unassigned_videos) > 0`: hiển thị `self.btn_unassigned_details` cạnh `lbl_preview_summary`.
  - Bấm nút mở `UnassignedVideosModal(self, plan.unassigned_videos)`.
  - Nếu `len(plan.unassigned_videos) == 0`: ẩn nút.

---

## 4. Kế Hoạch Kiểm Thử
- `test_schedule_contracts.py`: Kiểm tra giá trị policy mới.
- `test_video_probe.py`: Kiểm tra nhận diện codec `vp9`, `vp8`, `mpeg4`.
- `test_unassigned_videos_modal.py`: Kiểm tra dữ liệu nạp modal, lọc lỗi và xuất TXT.
- Kiểm tra Zero Live Data Mutation (`git status --short configs.json youtube_config.json channels.json`).
