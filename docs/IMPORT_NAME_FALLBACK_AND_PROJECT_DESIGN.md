# Design Spec: Nâng Cấp Import Hàng Loạt (Fallback Tên & Chọn Dự Án)

Ngày chốt thiết kế: 2026-11-18  
Trạng thái: Đã xác nhận qua Brainstorming (Ready for Implementation)

---

## 1. Understanding Summary (Tóm Tắt Yêu Cầu)

- **Tính năng**: Nâng cấp luồng **Import Tài Khoản Hàng Loạt** (`account_io.py` và `main.py`) với 2 cải tiến:
  1. **Tự động suy diễn tên (`name` fallback)**: Khi format không có cột `name` hoặc dòng dữ liệu bỏ trống `name`, tự động lấy `idTiktok` đã chuẩn hóa (ưu tiên 1), nếu không có thì lấy nguyên văn `email` (ưu tiên 2). Thiếu cả 3 (`name`, `idTiktok`, `email`) thì báo lỗi dòng.
  2. **Chọn / Tạo mới Dự án khi Import**: Hộp thoại Import cho phép chọn từ các dự án hiện có hoặc gõ tên dự án mới qua `CTkComboBox`, tự động điền sẵn dự án đang lọc ở màn hình chính (nếu đang xem `ALL_OPTION == "Default"` thì mặc định là `'Mặc định'`).
- **Mục tiêu**: Giảm thao tác thủ công chuẩn bị file dữ liệu và phân loại dự án ngay lúc nhập tài khoản hàng loạt.
- **Phạm vi ngoài luồng (Non-Goals)**:
  - Không thay đổi luồng thêm profile đơn lẻ (`add_profile`) hoặc luồng xuất tài khoản (`export_profiles`).
  - Không thay đổi schema 11 trường chuẩn `DEFAULT_FIELDS` (không thêm cột `project` theo từng dòng).
  - Không fallback sang `mailBackup` và không sinh tên ngẫu nhiên khi thiếu cả 3 trường định danh.

---

## 2. Non-Functional Requirements & Assumptions (Yêu Cầu Phi Chức Năng & Giả Định)

- **Hiệu năng**: Xử lý mượt lô lên tới ~1.000 dòng khi xem trước (`do_preview`) và nhập (`run_import`).
- **Toàn vẹn dữ liệu (Bắt buộc)**:
  - Đảm bảo bất biến 3 trường dự án cho mọi profile mới tạo:
    1. `profiles[name]['project'] == target_project`
    2. `profiles[name]['config']['project_name'] == target_project`
    3. `name in projects[target_project]` và không trùng membership ở dự án khác.
  - Rollback nguyên tử khi `save_configs()` thất bại: hoàn nguyên cả profile lẫn dự án vừa tạo mới nếu rỗng.
  - Chống va chạm thư mục Windows trong `Auto_Data/` khi 2 email khác nhau chuẩn hóa ra cùng tên folder.
- **Bảo mật**: Bảng xem trước tiếp tục che giấu (`masked_record`) các trường nhạy cảm (`password`, `cookie_str`, `auth2fa`, `passmail`, `pass_mail_backup`, `proxy_string`).
- **Tương thích ngược & An toàn test**:
  - Giữ nguyên hành vi 100% với các file import đã có sẵn cột `name`.
  - Mọi bài kiểm thử tuân thủ Rule 14 (Zero Live Data Mutation — dùng `tempfile.TemporaryDirectory`, tuyệt đối không ghi đè `configs.json`, `youtube_config.json`, `Auto_Data` thật).
- **Giả định đầu vào**:
  - Ô Dự án bị xóa trắng -> tự quy về `'Mặc định'`.
  - Ô Dự án nhập giá trị dành riêng `ALL_OPTION` (`"Default"`) -> từ chối và báo lỗi rõ ràng.

---

## 3. Decision Log (Nhật Ký Quyết Định)

| ID | Quyết định | Phương án đã cân nhắc | Lý do chọn |
|---|---|---|---|
| D1 | Fallback tên: `name` -> `normalize_tiktok_id(idTiktok)` -> `email` (full); thiếu cả 3 báo lỗi | Cắt phần trước `@` của email; fallback thêm `mailBackup`; giữ raw `idTiktok` | Tránh trùng prefix email (`info@a.com` vs `info@b.com`), sạch tên TikTok, không tạo tên rác |
| D2 | Đặt logic fallback tại tầng parser thuần `account_io.py` (`parse_data_into_records`) | Đặt trong `main.py` (`_parse_current`) | Đồng bộ 100% giữa Preview, trùng tên (`plan_import`) và Import; dễ unit test độc lập |
| D3 | Chọn Dự án bằng `CTkComboBox` (chọn có sẵn hoặc nhập mới), mặc định theo dự án đang lọc ở màn hình chính | Chỉ `OptionMenu`; luôn mặc định `'Mặc định'`; thêm cột `project` theo từng dòng | Linh hoạt, nhanh cho thao tác thực tế, giữ nguyên schema format (YAGNI) |
| D4 | Dự án chỉ áp dụng cho tài khoản `add`; tài khoản `update` giữ nguyên dự án cũ | Chuyển dự án cả tài khoản `update` | Tránh vô tình di chuyển tài khoản đang vận hành ở dự án khác |

---

## 4. Final Design (Thiết Kế Chi Tiết)

### 4.1. Tầng Parser (`account_io.py`)
- **`normalize_tiktok_id(value)`**:
  - Chuẩn hóa ID TikTok: cắt khoảng trắng, bỏ dấu `/` cuối, cắt tiền tố `tiktok.com/@` (không phân biệt hoa thường), bỏ `@` đầu, cắt phần `?query` và `#fragment`.
  - Tại `main.py`, `_normalize_tiktok_id` gọi lại `account_io.normalize_tiktok_id` (Single Source of Truth).
- **`resolve_record_name(record)`**:
  - Trả về tên đã chốt theo thứ tự ưu tiên:
    1. `record.get('name', '').strip()`
    2. `normalize_tiktok_id(record.get('tiktok_id', ''))`
    3. `record.get('email', '').strip()`
  - Trả về `""` nếu cả 3 đều rỗng.
- **`parse_data_into_records(text, fields, skip_header=False)`**:
  - Sau `record = build_record(fields, values)`, gọi `resolved_name = resolve_record_name(record)`.
  - Nếu `resolved_name` rỗng -> thêm lỗi `(line_no, 'Thiếu tên (cần có Name, ID TikTok hoặc Email)')` và `continue`.
  - Gán `record['name'] = resolved_name` trước khi đưa vào `records`.

### 4.2. Tầng Giao Diện & Điều Phối (`main.py`)
- **`_resolve_import_project(raw_value)`**:
  - `cleaned = str(raw_value or '').strip()`
  - Nếu rỗng -> trả về `'Mặc định'`.
  - Nếu `cleaned == ALL_OPTION` (`"Default"`) -> raise `ValueError("Tên dự án không được trùng giá trị hệ thống 'Default'.")`.
  - Trả về `cleaned`.
- **`_default_import_project()`**:
  - Đọc `selected_project_var.get()`. Nếu tồn tại trong `projects` và khác `ALL_OPTION` -> trả về giá trị đó; ngược lại trả về `'Mặc định'`.
- **`_allocate_unique_profile_dirs(name, auto_data_root)`**:
  - Tạo tên thư mục an toàn `safe = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip() or f"Profile_{uuid.uuid4().hex[:8]}"`.
  - Kiểm tra `chrome_dir = os.path.join(auto_data_root, safe, "Profile")`.
  - Nếu `_find_profile_with_data_dir(chrome_dir)` đã có chủ sở hữu (ví dụ va chạm `a.b@gmail.com` và `ab@gmail.com`) -> tự động nối hậu tố hash ổn định `_{sha1(name)[:6]}` (và hậu tố số `_2`, `_3` nếu vẫn trùng) để cấp phát cặp `(video_dir, chrome_dir)` duy nhất.
- **`_apply_import_plans(plans, proxy_type='http', default_project='Mặc định')`**:
  - Chuẩn hóa `target_project = _resolve_import_project(default_project)`.
  - Chỉ tạo `projects[target_project] = set()` (và ghi `created_project_name = target_project`) khi thực sự có bản ghi `action == 'add'` được thêm thành công (tránh tạo dự án rỗng cho lô chỉ có `update`/`skip`).
  - Với mỗi profile `add`:
    - Gán đầy đủ 3 trường bất biến: `config['project_name'] = target_project`, `profiles[name]['project'] = target_project`, `projects[target_project].add(name)`.
  - Với mỗi profile `update`:
    - Không thay đổi dự án hiện tại của profile.
  - Trả về thêm `'target_project': target_project` và `'created_project_name': created_project_name`.
- **`_rollback_import(result)`**:
  - Hoàn nguyên các profile đã thêm/cập nhật và xóa thư mục rỗng.
  - Nếu `created_project_name` được tạo trong đợt import và hiện đang rỗng -> xóa khỏi `projects` (thay thế logic hardcode `'Mặc định'` cũ).
- **UI trong `batch_add_profiles()`**:
  - Trong `opt`, bố trí hàng tùy chọn gọn (2 dòng nhỏ nếu cần để không tràn ở `min_w=700`):
    - Dòng 1: Checkbox `Dòng đầu là tiêu đề`, `Loại Proxy`, `Khi trùng tên`.
    - Dòng 2: Nhãn `Dự án:` + `CTkComboBox` cho phép chọn dự án hiện có hoặc gõ tên dự án mới, kèm chú thích ngắn `(chỉ áp dụng cho tài khoản thêm mới)`.
  - Trong `run_import()`:
    - Gọi `_resolve_import_project(project_var.get())` (bắt `ValueError` -> `messagebox.showerror`).
    - Truyền `default_project=target_project` vào `_apply_import_plans(...)`.
    - Sau `save_configs()`: gọi `update_project_dropdown()`, `update_profile_list()`, `_refresh_status_bar()`, `request_profile_refresh()`.
    - Thông báo hoàn tất hiển thị thêm dòng `Dự án (thêm mới): <target_project>` khi `len(result['added']) > 0`.

### 4.3. Chiến Lược Kiểm Thử
- **Unit tests (`test_account_io.py`)**:
  - Format không có `name`: ưu tiên `idTiktok` chuẩn hóa -> `email` -> báo lỗi khi thiếu cả 3.
  - ID TikTok dạng URL/ký tự `@`/rỗng sau chuẩn hóa (`@`, `https://www.tiktok.com/@`) rơi xuống `email`.
  - Duplicate planning (`plan_import`) trên tên đã suy diễn.
- **Integration / Logic tests (`test_batch_add_profiles.py`)**:
  - `_resolve_import_project` (rỗng, `ALL_OPTION`, tên mới).
  - `_apply_import_plans` với `tempfile.TemporaryDirectory`: kiểm chứng bất biến 3 trường dự án cho `add`, giữ nguyên dự án cũ cho `update`, không tạo dự án rỗng khi chỉ có `update`, chống va chạm thư mục cho `a.b@gmail.com` vs `ab@gmail.com`.
  - `_rollback_import`: hoàn nguyên sạch kể cả dự án tùy chỉnh vừa tạo mới.
  - Kiểm tra cấu trúc UI ComboBox chọn dự án trong `batch_add_profiles()`.
