# Thiết Kế Nâng Cấp & Tối Ưu Hệ Thống Lên Lịch Đăng TikTok (Audit & 800-Account Scale)

**Ngày tạo:** 2026-11-23  
**Trạng thái:** Đã duyệt thiết kế (Validated Design — Sẵn sàng triển khai)  
**Phạm vi file liên quan:** `schedule_contracts.py`, `schedule_calculator.py`, `schedule_manager.py`, `schedule_service.py`, `schedule_ui.py`, `proxy_diagnostics.py`, `main.py`

---

## 1. Tóm Tắt Hiểu Biết (Understanding Summary)

- **Mục tiêu:** Audit và nâng cấp toàn diện hệ thống Lên lịch đăng TikTok nhằm biến nó thành công cụ trực quan, thuần tiếng Việt 100%, hoạt động ổn định và tối ưu tương tác kênh theo đúng múi giờ địa phương của từng tài khoản, đáp ứng được quy mô lớn lên đến **800 tài khoản dùng chung một lượng proxy hữu hạn**.
- **Loại bỏ triệt để `SERVER_SCHEDULE`:** Bỏ chế độ đẩy lịch lên server TikTok ở giao diện tạo lịch, bộ lọc và cột hiển thị. Chỉ sử dụng mặc định duy nhất cơ chế **Treo máy đến giờ tự đăng qua Direct API** (`LOCAL_QUEUE`). Các task `SERVER_SCHEDULED` cũ trong DB (nếu có) được tự động ẩn/chuyển sang trạng thái lưu trữ (`ARCHIVED`).
- **Tối giản & Việt hóa UI:** Loại bỏ toàn bộ thuật ngữ kỹ thuật tiếng Anh (`ORIGINAL`, `SERIES`, `SHUFFLE`, `APPEND`, `OVERWRITE`, `LOCAL_QUEUE`, `Jitter`, mã status tiếng Anh trong bộ lọc) trên giao diện người dùng cuối, thay bằng tiếng Việt tự nhiên, dễ hiểu.
- **Giờ Vàng theo Proxy & Random thông minh:** Dùng cơ chế **Hybrid Cache + Auto GeoIP** trích xuất múi giờ quốc gia từ Proxy của từng profile (fallback về giờ máy nếu không có proxy/lỗi mạng). Bỏ ô nhập Jitter ± phút, tự động chọn ngẫu nhiên 1 phút bên trong khoảng giờ vàng của từng khung giờ đã chọn.
- **Chống Trùng IP & Đa Luồng cho quy mô 800 Acc:** Rải lịch khác khung giờ vàng cho các acc chung Proxy (`proxy_key = host:port`), kết hợp nâng cấp Daemon từ đơn luồng (`limit=1`) lên **Worker Pool đa luồng** có khóa **Per-Proxy Lock & Cooldown**.
- **Thuật toán Đăng Bù / Dời Lịch thông minh:** Khi mở máy lại hoặc đánh thức từ Sleep sau khi lỡ lịch:
  - Nếu thời điểm mở máy nằm trong **giờ hoạt động (07:00 – 22:59 theo múi giờ Proxy của kênh)**: Cho phép **đăng bù ngay 1 bài** (có giãn cách `proxy_cooldown_minutes` nếu nhiều acc chung 1 proxy), các bài lỡ còn lại dàn đều vào các khung giờ vàng tiếp theo (tuân thủ giới hạn bài/ngày).
  - Nếu rơi vào **giờ ngủ ban đêm (23:00 – 06:59 theo múi giờ Proxy của kênh)**: Không đăng bù ngay mà dời toàn bộ sang khung giờ vàng sáng hôm sau.

---

## 2. Yêu Cầu Phi Chức Năng & Giả Định (Assumptions)

1. **Hiệu năng Preview:** Việc tính toán ma trận xem trước (Preview) cho tối đa 800 acc phải diễn ra tức thì (`< 1–2 giây`), không bị treo UI do đợi kiểm tra mạng Proxy (ưu tiên đọc múi giờ từ cache/config; dò GeoIP mới chạy bất đồng bộ hoặc qua nút đồng bộ riêng).
2. **An toàn IP & Thông lượng (Throughput):**
   - Tốc độ upload trung bình ~15–30s/video ngắn qua Direct API.
   - Với **Worker Pool 5–10 luồng song song** cho các proxy khác nhau, 200 bài/khung giờ vàng hoàn tất trong ~10–20 phút (800 bài/ngày hoàn tất trong ~40–80 phút), hoàn toàn kịp trong khung giờ vàng.
   - Tuyệt đối không có 2 luồng chạy chung 1 `proxy_key` tại cùng 1 thời điểm.
3. **Tương thích ngược (DB & Test):** Giữ nguyên cấu trúc bảng SQLite và các giá trị enum nội bộ ở tầng DB/Contracts (`LOCAL_QUEUE`, `ORIGINAL`, `SERIES`, `SHUFFLE`, `APPEND`, `OVERWRITE`) để không làm gãy migration hay các test nền tảng.
4. **Giả định khung giờ hoạt động để đăng bù:** Mặc định `07:00 – 22:59` giờ địa phương Proxy là khung giờ hợp lệ để đăng bù 1 bài; `23:00 – 06:59` là giờ ngủ ban đêm (tránh đăng).

---

## 3. Nhật Ký Quyết Định (Decision Log)

| Hạng mục | Quyết định đã chọn | Phương án thay thế đã xét | Lý do lựa chọn |
| :--- | :--- | :--- | :--- |
| **Kiến trúc tổng thể** | Tái cấu trúc theo tầng (UI Mapping + Nâng cấp Calculator/Recovery/Daemon), giữ schema DB tương thích | Viết lại toàn bộ DB schema & bỏ hết enum cũ | Rủi ro thấp, không làm hỏng DB người dùng cũ, tránh vi phạm YAGNI |
| **Xử lý lỡ hẹn (Backfill)** | Phương án B: Đăng bù 1 bài nếu trong `07:00–22:59` giờ Proxy + Dàn đều phần còn lại; Ban đêm dời sang sáng hôm sau | Phương án A (Không bao giờ đăng bù ngay) / C (Popup hỏi người dùng) | Cân bằng tốt nhất giữa tốc độ bù bài và bảo vệ view/tương tác kênh đúng múi giờ |
| **Lấy múi giờ Proxy** | Hybrid Cache + Auto GeoIP bất đồng bộ + Fallback giờ máy | Query GeoIP trực tiếp mỗi lần Preview | Tránh treo/đơ UI khi tính lịch cho hàng trăm acc |
| **Random giờ đăng** | Bỏ Jitter ± phút, random đều bên trong toàn bộ khoảng giờ vàng của slot | Giữ ô Jitter ± phút | Trực quan hơn cho người dùng cuối, phân phối tự nhiên hơn trong khung 75–90 phút |
| **Xử lý `SERVER_SCHEDULE`** | Loại bỏ triệt để ở UI & bộ lọc; chuyển task cũ sang `ARCHIVED` | Giữ tab đọc-chỉ trên UI | Giao diện sạch sẽ 100% thuần Việt, không gây nhầm lẫn cho người dùng |
| **Scale 800 acc / ít proxy** | Slot-Spreading theo `proxy_key` + Multi-Worker Daemon với Per-Proxy Lock & Cooldown | Chạy tuần tự 1 luồng (`limit=1`) | 1 luồng mất ~6.7h cho 800 bài (tràn giờ vàng); Worker Pool 5–10 luồng xử lý kịp trong ~40–80 phút |

---

## 4. Thiết Kế Chi Tiết Theo Thành Phần

### 4.1. Proxy Timezone & Random Giờ Vàng (`schedule_calculator.py`, `proxy_diagnostics.py`)
- **`resolve_profile_timezone(cfg)`:** Đọc theo thứ tự:
  1. `cfg["proxy_geo"]["timezone"]` (cache từ GeoIP check).
  2. `cfg["fingerprint"]["timezone"]` hoặc `cfg["timezone"]`.
  3. Fallback về múi giờ local máy tính (`Asia/Ho_Chi_Minh`) thay vì hardcode `America/New_York`.
- **`extract_proxy_key(cfg)`:** Chuẩn hóa chuỗi `proxy_string` về khóa `host:port` (không chứa user/pass) để nhận diện các profile dùng chung IP/gateway.
- **Đồng bộ GeoIP ngầm:** Bổ sung hàm async trong `proxy_diagnostics.py` / `schedule_ui.py` dùng `probe_proxy()` lấy `timezone`, `country_code`, `country` lưu vào `cfg["proxy_geo"]`.
- **Random trong Slot + Proxy-Aware Slot Spreading:**
  - Trong `build_schedule_plan()` / `generate_profile_timeline()`:
    - Bỏ cộng/trừ `jitter_minutes` (mặc định `0`). Thay vào đó, với mỗi slot `(start_hour:start_min -> end_hour:end_min)`, dùng PRNG tất định từ `(seed, profile_name, date, slot_id)` chọn ngẫu nhiên 1 phút bất kỳ nằm trọn trong khoảng slot.
    - Theo dõi lịch đã xếp theo `proxy_key` trên từng ngày: ưu tiên xếp các profile chung `proxy_key` vào các `slot_id` khác nhau trong ngày; nếu bắt buộc chung slot (do số acc/proxy > số slot), ép giãn cách tối thiểu giữa các bài chung `proxy_key` (mặc định `min_proxy_gap_minutes`, VD: 20 phút, hoặc chia đều khoảng slot).

### 4.2. Đăng Bù Thông Minh & Daemon Đa Luồng (`schedule_manager.py`, `main.py`)
- **`recover_missed_tasks(max_daily_posts=2, profile_configs=None, proxy_cooldown_minutes=5)`:**
  - Chuyển `now` sang `now_local` theo múi giờ Proxy của từng profile.
  - Duy trì `proxy_immediate_slot_count[proxy_key]` để biết đã có bao nhiêu acc chung proxy được xếp đăng bù ngay trong đợt mở máy này.
  - **Nếu `7 <= now_local.hour < 23` (giờ hoạt động):**
    - Video lỡ đầu tiên của profile: xếp ở `now + proxy_immediate_slot_count[proxy_key] * proxy_cooldown_minutes * 60`. Tăng bộ đếm cho `proxy_key` đó.
    - Video lỡ thứ 2 trở đi: tìm khung giờ vàng còn trống cách hiện tại ≥ 2 tiếng trong ngày, hoặc dàn đều sang ngày tiếp theo (chọn phút ngẫu nhiên trong khung giờ vàng, tôn trọng `max_daily_posts`).
  - **Nếu `now_local.hour >= 23 or now_local.hour < 7` (giờ ngủ ban đêm):**
    - Không đăng bù ngay; dời toàn bộ sang khung giờ vàng Sáng (`08:00–09:15` giờ Proxy) gần nhất (ngày hôm nay nếu < 7h sáng và còn cách ≥ 1h, hoặc ngày mai).
- **Daemon Worker Pool (`main.py`):**
  - Thay `claim_due_tasks(..., limit=1)` bằng cơ chế Worker Pool (`ThreadPoolExecutor(max_workers=configured_workers)` với mặc định `5`, cho phép chỉnh `1–15`).
  - Quản lý `busy_proxies: Set[str]` và `proxy_last_done_ts: Dict[str, float]` dưới `threading.Lock`:
    - Chỉ claim/chạy các task có `proxy_key` không nằm trong `busy_proxies` và `(now - proxy_last_done_ts.get(proxy_key, 0)) >= proxy_cooldown_seconds`.
    - Các task khác proxy chạy song song tối đa `max_workers` luồng.
    - Khi task xong (thành công hoặc lỗi), giải phóng `busy_proxies` và cập nhật `proxy_last_done_ts[proxy_key] = time.time()`.

### 4.3. Giao Diện Tối Giản & Thuần Việt 100% (`schedule_ui.py`)
- **Bảng ánh xạ UI Tiếng Việt (`BulkScheduleModal`):**
  - Thứ tự phân bổ:
    - `"Theo thứ tự file"` ↔ `"ORIGINAL"`
    - `"Theo bộ tập (1, 2, 3...)"` ↔ `"SERIES"`
    - `"Xáo trộn ngẫu nhiên"` ↔ `"SHUFFLE"`
  - Xử lý lịch cũ:
    - `"Nối tiếp sau lịch cũ"` ↔ `"APPEND"`
    - `"Ghi đè (Hủy lịch cũ)"` ↔ `"OVERWRITE"`
- **Loại bỏ trên `BulkScheduleModal`:**
  - Xóa/ẩn hoàn toàn option chọn `SERVER_SCHEDULE` / `LOCAL_QUEUE` (gán cứng `LOCAL_QUEUE`).
  - Xóa ô nhập `Độ lệch ngẫu nhiên (Jitter)`.
  - Hiển thị 1 dòng mô tả gọn: *"💻 Tự động đăng qua API khi đến giờ (giờ vàng tự động theo khu vực Proxy)"*.
  - Bổ sung nút **"🔄 Đồng bộ giờ Proxy"**, cột **"Khu vực / Múi giờ"** trên bảng Xem trước, và 2 ô cấu hình gọn: **Luồng chạy song song (mặc định 5)** & **Nghỉ cùng Proxy (mặc định 5 phút)**.
- **Dọn gọn `ScheduleMonitorView`:**
  - Bỏ cột **"Chế độ"** (`☁️ TikTok` / `💻 Máy`).
  - Bộ lọc trạng thái thuần tiếng Việt: `"Tất cả"`, `"Đang chờ đăng"`, `"Đang tải lên"`, `"Đã đăng"`, `"Đăng lỗi"`, `"Dời lịch (tắt máy)"`, `"Đã hủy"`.
  - Trong `schedule_manager.py`, filter `ACTIVE` và các query hiển thị chính không còn lấy `SERVER_SCHEDULED` (đưa về `ARCHIVED`).

---

## 5. Chiến Lược Kiểm Thử (Verification Strategy)

- **Tuân thủ Rule 14 (Zero Live Data Mutation):** Mọi test dùng `tmp_path` và SQLite tạm, không sửa `configs.json`, `youtube_config.json`, hay DB live.
- **Các nhóm test cần viết & chạy:**
  1. **Unit test Proxy Timezone & Slot Randomization:** Test `resolve_profile_timezone` (cache geo -> fingerprint -> fallback), random nằm trong `[start, end]` của slot, và mô phỏng **800 acc / 100 proxy** đảm bảo rải khác slot và giữ đúng khoảng cách tối thiểu giữa các acc chung proxy.
  2. **Unit test `recover_missed_tasks` (Phương án B):** Test múi giờ Mỹ vs VN lúc 14:00 VN (02:00 AM US -> dời sáng hôm sau; 14:00 VN -> đăng bù ngay 1 bài + stagger theo proxy).
  3. **Unit test Daemon Worker Pool & Per-Proxy Lock:** Test các acc khác proxy chạy song song, 2 acc chung proxy không bao giờ chạy chồng lên nhau và tuân thủ `cooldown`.
  4. **Integration test UI (`schedule_ui.py`):** Test mapping Tiếng Việt 2 chiều, xác nhận không còn `SERVER_SCHEDULE` hay ô Jitter trên UI, và dữ liệu `SERVER_SCHEDULED` cũ được ẩn sang `ARCHIVED`.
