# BÁO CÁO AUDIT KIẾN TRÚC & TÍNH ĐÚNG ĐẮN NGHIỆP VỤ: TÍNH NĂNG LÊN LỊCH ĐĂNG BÀI (TIKTOK AUTO-SCHEDULER)

**Ngày lập:** 2026-11-06  
**Trạng thái kiểm chứng ban đầu:** `LIVE VERIFIED (Partial)` & `STATIC ONLY (Audit)`  
**Tác giả:** Primary Lead Architect & Multi-Agent Review Team  

---

## 1. TỔNG QUAN HIỆN TRẠNG & BẰNG CHỨNG LIVE RUN

Qua đối chiếu mã nguồn thực tế tại workspace và dữ liệu nhật ký chạy thực tế (Live Execution Log) vào lúc 00:08:34 - 00:17:24 ngày 2026-11-06:

### 1.1 Điểm sáng đã được kiểm chứng sống (Live Verified 100%)
* Hai profile **AUTO 31** và **AUTO 25** đã hoàn thành xuất sắc 5/5 bước API TikTok Studio:
  * Xác thực VOD tải lên đa luồng song song (Direct IP Bypass Proxy, 4 workers).
  * Ký số SigV4, X-Bogus, X-Gnarly.
  * Tự động gửi ACK điều khoản lưu trữ video hẹn giờ (`post_schedule/ack status=200/0`).
  * TikTok Server chấp nhận lịch hẹn (`status=200`) và trả về link video thật:
    * `https://www.tiktok.com/video/7704735487210163458` (AUTO 31, hẹn 21 phút)
    * `https://www.tiktok.com/video/7704735648892259607` (AUTO 25, hẹn 355 phút)
  * Dọn dẹp file nguồn an toàn ngay sau khi server xác nhận.
* Cơ chế tự động nhận diện Hashtag từ tên file:
  * Tên file chứa chuỗi hashtag (ví dụ: `#thisorthat #britishhumour #viral #fyp`) đã được `format_caption_and_markup()` bóc tách tự động thành `text_extra` (type 1) và bọc thẻ `<h id="">#tag</h>` vào `markup_text`.
  * Trên link video thật, toàn bộ các hashtag này đã hiển thị chuẩn dưới dạng clickable hashtag màu xanh.

---

## 2. DANH MỤC LỖ HỔNG & TỬ HUYỆT NGHIỆP VỤ

### 2.1 Mức độ P0 (Blocker / Critical)

#### P0-1: Biến lịch hẹn giờ thành "Đăng ngay lập tức" (Silent Degradation to Post Now)
* **Vị trí:** `main.py#L9184-L9186`
* **Hiện trạng (FACT):**
  ```python
  raw_epoch = task.get("scheduled_epoch")
  sched_epoch = int(raw_epoch) if (exec_mode == "SERVER_SCHEDULE" and raw_epoch and int(raw_epoch) > 0) else None
  ```
* **Cơ chế thất bại:** Nếu task ở chế độ `SERVER_SCHEDULE` nhưng `raw_epoch` bị thiếu hoặc $\le 0$, `sched_epoch` âm thầm nhận `None`. Hàm `run_direct_upload(..., schedule_time=None)` sẽ **đăng video lên TikTok ngay lập tức**, vi phạm ý định bảo mật/thời gian của người dùng.
* **Giải pháp:** Chặn đứng (Fail-fast), đánh dấu task `status = 'FAILED'` kèm thông báo lỗi rõ ràng nếu epoch không hợp lệ.

#### P0-2: Task phục hồi bị "đóng băng vĩnh viễn" trong SQLite (Missed Rescheduled Deadlock)
* **Vị trí:** `schedule_manager.py#L1646` đối chiếu `L890-L893` và `L920-L923`
* **Hiện trạng (FACT):** `recover_missed_tasks` đổi trạng thái task lỡ hẹn sang `'MISSED_RESCHEDULED'`. Tuy nhiên, `claim_due_tasks()` và `claim_task_for_execution()` chỉ quét:
  ```sql
  WHERE status IN ('PENDING', 'QUEUED') OR (status = 'UPLOADING' AND lease_until < ?)
  ```
* **Cơ chế thất bại:** Task `MISSED_RESCHEDULED` không bao giờ được worker claim để thực thi.
* **Giải pháp:** Bổ sung `'MISSED_RESCHEDULED'` vào danh sách trạng thái hợp lệ trong mệnh đề `WHERE` của các hàm claim.

#### P0-3: Nút "Đăng ngay lập tức" bị từ chối với thông báo sai lệch & Không hủy hẹn giờ
* **Vị trí:** `main.py#L9387-L9398` đối chiếu `schedule_manager.py#L890-L893`
* **Hiện trạng (FACT):**
  1. Khi người dùng bấm "Đăng ngay lập tức" cho các task bị lỗi (`FAILED`), `claim_task_for_execution` từ chối vì chỉ nhận `PENDING` hoặc `QUEUED`. Hệ thống log gây hiểu nhầm: *"Bỏ qua bài đăng vì đang được tiến trình khác xử lý"*.
  2. `_handle_schedule_post_now` truyền nguyên bản task vào `_execute_schedule_task_upload`, không xóa `scheduled_epoch` hay gán cờ `force_immediate = True`.
* **Giải pháp:** Cho phép claim task `FAILED` khi có hành động can thiệp thủ công từ người dùng; ép `schedule_time = None` khi thực thi lệnh Đăng ngay.

#### P0-4: Trôi Lease khi Upload file lớn gây Đăng trùng bài (Duplicate Post Race Condition)
* **Vị trí:** `main.py#L9327-L9331`, `main.py#L9476`
* **Hiện trạng (FACT):** Lease cố định 300s, không có heartbeat gia hạn trong lúc upload multipart file lớn.
* **Giải pháp:** Bổ sung worker heartbeat định kỳ (60s) hoặc kiểm tra quyền sở hữu trước khi ghi nhận hoàn tất.

---

### 2.2 Mức độ P1 (High Priority)

#### P1-1: 6/8 Profile bị TikTok từ chối hẹn giờ (`ab_value='0'`) thiếu cơ chế Auto-Fallback
* **Vị trí:** `tiktok_publish_client.py#L81` và `main.py#L9242`
* **Hiện trạng (FACT):** Các tài khoản không đủ điều kiện Creator/Business bị TikTok trả về `ab_value='0'`. Hệ thống đánh rớt toàn bộ task sang `FAILED`.
* **Giải pháp:** Tự động chuyển đổi `execution_mode` sang `'LOCAL_QUEUE'` (Hàng đợi máy tính). Máy tính sẽ giữ task và tự động tải lên khi đến đúng giờ hẹn.

#### P1-2: Dọn dẹp File nguồn xóa nhầm video cần Retry hoặc dùng chung
* **Vị trí:** `main.py#L9221-L9237` đối chiếu `schedule_manager.py#L971-L975`
* **Hiện trạng (FACT):** Bỏ qua kiểm tra kết quả `update_task()`; so sánh chuỗi thô `video_path = ?` thay vì `canonical_video_path`; bỏ qua task `FAILED` đang chờ retry.
* **Giải pháp:** Chỉ xóa khi `canonical_video_path` không còn task nào tham chiếu (kể cả FAILED chờ retry và MISSED_RESCHEDULED).

#### P1-3: Xóa vĩnh viễn task đang Upload (`delete_tasks` thiếu State Guard)
* **Vị trí:** `schedule_manager.py#L828`
* **Hiện trạng (FACT):** `DELETE FROM scheduled_tasks WHERE id IN (...)` không chặn trạng thái `status = 'UPLOADING'`.
* **Giải pháp:** Thêm điều kiện `AND status != 'UPLOADING'`.

#### P1-4: Conflict Guard bỏ sót task `UPLOADING` và `MISSED_RESCHEDULED`
* **Vị trí:** `schedule_manager.py#L999`
* **Hiện trạng (FACT):** `check_profile_conflicts` chỉ quét `('PENDING', 'SERVER_SCHEDULED', 'QUEUED')`.
* **Giải pháp:** Mở rộng quét toàn bộ các task đang chiếm slot: bao gồm cả `UPLOADING` và `MISSED_RESCHEDULED`.

#### P1-5: Bóp méo bộ đếm "Đã đăng hôm nay" do dùng cột `updated_at`
* **Vị trí:** `schedule_manager.py#L1711-L1713`
* **Hiện trạng (FACT):** Lệnh đếm dùng `(completed_at LIKE ? OR updated_at LIKE ?)`. Sửa ghi chú của bài cũ làm tăng số bài đã đăng hôm nay.
* **Giải pháp:** Bỏ `updated_at`, chỉ căn cứ vào `completed_at` hoặc `published_at`.

#### P1-6: Bảo toàn dấu gạch dưới `_` trong Hashtag từ tên file
* **Vị trí:** `schedule_calculator.py#L166`
* **Hiện trạng (FACT):** `stem.replace("_", " ")` làm bẻ gãy các hashtag có dấu gạch dưới (ví dụ `#tiktok_uk` thành `#tiktok uk`).
* **Giải pháp:** Giữ nguyên dấu `_` nếu nằm trong cụm hashtag `#[\w]+`.

---

## 3. NHẬT KÝ QUYẾT ĐỊNH (DECISION LOG)

| ID | Quyết định đã thống nhất | Lý do kỹ thuật |
|---|---|---|
| **DEC-01** | Chặn đứng (Fail-fast) task `SERVER_SCHEDULE` nếu epoch lỗi $\le 0$ | Bảo vệ người dùng, tuyệt đối không tự ý đăng bài live. |
| **DEC-02** | Mở rộng claim chấp nhận `MISSED_RESCHEDULED` | Chống treo hàng đợi vĩnh viễn sau khi phục hồi máy tắt đột ngột. |
| **DEC-03** | Mở rộng claim chấp nhận `FAILED` cho thao tác thủ công (Post Now, Retry) | Cho phép người dùng cứu bài bị lỗi mà không bị chặn vô lý. |
| **DEC-04** | Nút "Đăng ngay lập tức" ép `schedule_time = None` | Đúng bản chất nghiệp vụ đăng ngay. |
| **DEC-05** | **Auto-fallback sang `LOCAL_QUEUE` khi `ab_value='0'`** | Cứu bài đăng cho các tài khoản không được cấp quyền Server Schedule. |
| **DEC-06** | Bảo vệ file video nguồn bằng `canonical_video_path` & guard task retry | Ngăn chặn xóa nhầm file khi bài khác đang cần retry. |
| **DEC-07** | Cấm xóa task đang mang trạng thái `UPLOADING` | Chống hiện tượng bài mồ côi trên TikTok. |
| **DEC-08** | Mở rộng Conflict Guard bao gồm cả `UPLOADING` và `MISSED_RESCHEDULED` | Chống tính trùng slot giờ vàng khi có bài đang chạy. |
| **DEC-09** | Bảo toàn dấu `_` trong hashtag và giới hạn caption 2,200 ký tự | Giữ tính toàn vẹn của hashtag mà không phát sinh tác dụng phụ. |
| **DEC-10** | Tối ưu hóa mốc giãn cách: Khác Profile (3s-5s), Cùng Profile (5s-10s) | Tăng tốc triển khai x8-x10 lần mà vẫn bảo đảm an toàn chống Rate-limit TikTok. |

---

## 4. CHIẾN LƯỢC KIỂM CHỨNG & AN TOÀN (ZERO LIVE DATA MUTATION)

Mọi kịch bản kiểm thử trong quá trình triển khai bắt buộc tuân thủ:
1. **Cô lập dữ liệu 100%:** Dùng `tempfile.mkdtemp` hoặc `tmp_path`, cấm ghi đè lên `data/scheduled_posts.db` và `configs.json`.
2. **Regression Test Suite:**
   * Test 1: Claim task `MISSED_RESCHEDULED` thành công trong `claim_due_tasks`.
   * Test 2: Claim task `FAILED` thành công với worker `post_now` và từ chối nếu không có quyền.
   * Test 3: Đăng ngay lập tức (`force_immediate=True`) ép `schedule_time=None`.
   * Test 4: Auto-fallback sang `LOCAL_QUEUE` khi nhận cờ `ab_value='0'`.
   * Test 5: Bảo vệ file nguồn không bị xóa khi còn task `FAILED` chờ retry.
   * Test 6: Chặn xóa task `UPLOADING` trong `delete_tasks`.
   * Test 7: Giữ nguyên hashtag có dấu gạch dưới `#tiktok_uk` trong tiêu đề.

---

## 4. YouTube Monitor: Tự Động Tính Lại Lịch Đăng Định Kỳ (DEC-11 đến DEC-14)

### Quyết định kỹ thuật
* **DEC-11 (Periodic Re-learner Integration)**: Tích hợp cơ chế tự động tính lại lịch định kỳ cố định 3 ngày/lần (`RELEARN_INTERVAL_SECONDS = 259,200s`) vào `PredictiveScheduler`. Chỉ chạy trong thời gian rảnh rỗi (ngoài khung giờ micro-poll) với độ trễ giãn cách 10s giữa các kênh (`RELEARN_STAGGER_DELAY_SECONDS = 10.0`) nhằm tối ưu hoá quota API (chỉ tốn 1 unit quota/kênh).
* **DEC-12 (Manual UI Trigger Removal)**: Loại bỏ hoàn toàn nút bấm *"🔄 Phân Tích Lại Lịch Qua 30 Video Mới"* trong Schedule Dialog và command *"🔄 Phân tích lại lịch đăng qua API"* trong Context Menu (`youtube_monitor/ui.py`). Toàn bộ việc cập nhật lịch học chuyển giao 100% cho Scheduler chạy ngầm tự động.
* **DEC-13 (Recluster & Prune Unlocked)**: Bổ sung phương thức `recluster_and_prune_unlocked()` vào `schedule_learner.py`. Khi phân tích lại 30 video mới, hệ thống bảo lưu tuyệt đối 100% các khung giờ `MANUAL` và `LOCKED`, đồng thời dọn sạch toàn bộ khung giờ `LEARNED` cũ (unlocked) đã lỗi thời khi creator đổi giờ đăng.
* **DEC-14 (Dual Incremental Learning)**: Kích hoạt `_update_channel_learned_schedule()` ngay khi phát hiện video mới qua API micro-poll (song song với RSS), giúp hệ thống phản ứng tức thì với các video đăng đột xuất ngoài lịch.

### Bằng chứng kiểm thử (`tests/test_periodic_schedule_relearner.py`)
1. `test_recluster_and_prune_unlocked_preserves_locked_and_manual`: PASSED.
2. `test_recluster_and_prune_unlocked_avoids_locked_overlap`: PASSED.
3. `test_periodic_relearn_interval_gating`: PASSED.
4. `test_periodic_relearn_preemption_on_active_window`: PASSED.
5. `test_incremental_learning_on_api_detection`: PASSED.
6. `test_reanalyze_channel_schedule_execution_and_resilience`: PASSED.

---

## 5. Smart Staggered Catch-up & Cơ Chế Retry / Auto-Fallback (DEC-15 & DEC-16)

### Quyết định kỹ thuật
* **DEC-15 (Transient Failure Single Retry & Seamless Local Queue Fallback)**:
  * Khi gặp lỗi mạng/timeout tạm thời trong quá trình tải lên: Hệ thống tự động thử lại đúng 1 lần sau 3s (`retry_count = 1`).
  * Khi TikTok từ chối do tài khoản chưa được cấp quyền Server Schedule (`ab_value='0'`, `reason == 'schedule_disabled'`): Không retry vô ích, tự động chuyển đổi an toàn sang `LOCAL_QUEUE` với trạng thái `PENDING`, bảo lưu mốc giờ hẹn gốc và video trên đĩa.
* **DEC-16 (Smart Staggered Catch-up on Reconnect)**:
  * Khi mở tool sau khi máy tính bị tắt / offline qua giờ hẹn:
    * **Video 1 của mỗi profile**: Đặt `scheduled_epoch = now` để Daemon ngầm tự động đăng bù ngay lập tức (bất kể ban ngày hay đêm muộn; giãn cách 3-5s giữa các profile khác nhau).
    * **Các video dồn ứ tiếp theo (2 đến 5 video)**:
      * Trong ngày hôm nay: Nhận tối đa thêm 1 video vào khung giờ vàng tiếp theo nếu cách thời điểm mở máy >= 2 tiếng.
      * Các ngày tiếp theo: Tự động dàn đều tối đa 2 bài/ngày vào các khung giờ vàng quen thuộc (8:30, 12:15, 18:00, 20:30), bảo vệ tài khoản khỏi bị TikTok bóp tương tác hoặc gắn cờ spam.
* **Bug Fix (Queue Loop)**:
  * Sửa lỗi cú pháp dòng 9383 của `main.py`: `next_t = tasks[idx]` ➔ `next_t = ordered_tasks[idx]` để tránh `NameError` khi hàng đợi luân chuyển cooldown.

### Bằng chứng kiểm thử (`tests/test_smart_catchup_recovery.py`)
1. `test_catchup_single_missed_video`: PASSED.
2. `test_catchup_five_missed_videos_staggered`: PASSED.
3. `test_transient_failure_retries_and_succeeds`: PASSED.
4. `test_schedule_disabled_fallback_without_retry`: PASSED.

---

## 6. Tái Cấu Trúc Toàn Diện Tab Lịch Đăng (DEC-17 đến DEC-21)

### Quyết định kỹ thuật
* **DEC-17 (Title Stutter Guard & Khử Lặp Thông Minh)**:
  * Khắc phục triệt để hiện tượng tiêu đề bị lặp đúp cụm từ (ví dụ: `British Food Clash #1392 British Food Clash #1392...`) khi người dùng đặt công thức `{filename} British Food Clash...`.
  * Thay vì tự ý sửa ngầm làm mất kiểm soát của người dùng, hệ thống bổ sung huy hiệu cảnh báo màu cam (`lbl_stutter_warn`) và nút bấm `[⚡ Khử Lặp]` (`btn_strip_stutter`) ngay cạnh ô công thức trong `BulkScheduleModal`.
  * Hàm `strip_title_stutter` sử dụng `re.escape(clean_stem)` và kiểm tra tính lũy đẳng (idempotent), chỉ khử đúng 1 lần lặp tiếp giáp.
* **DEC-18 (Phân Vùng Task Cũ & Lưu Trữ Độc Lập - Stale Tasks Partition)**:
  * Phân biệt rõ rệt giữa Task đang hoạt động (`ACTIVE`) và Task đã lưu trữ / đã hoàn thành (`ARCHIVED`).
  * 72 task `SERVER_SCHEDULED` từ tháng 10 không còn làm nghẽn giao diện chính nhờ bộ lọc `Phạm vi: [Đang hoạt động | Đã lưu trữ | Tất cả]`.
  * Hàm `clear_finished_tasks(clear_past_server=False)` mặc định giữ nguyên các task cũ để bảo đảm an toàn dữ liệu, nhưng khi chọn dọn dẹp trong chế độ lưu trữ thì sẽ giải phóng các task server đã quá hạn > 1 giờ.
* **DEC-19 (Project Binding & Fail-Closed Safety)**:
  * Đồng bộ trực tiếp bộ lọc của bảng Lịch đăng với Dự án đang chọn trên thanh điều hướng bên trái (`selected_project_var.trace('w', _on_schedule_project_changed)`).
  * Áp dụng nguyên tắc phòng vệ nghiêm ngặt (Fail-Closed): Khi một Dự án chưa có Profile nào (`filter_profiles == []`), câu lệnh SQLite ép điều kiện `WHERE 1=0` để hiển thị danh sách rỗng thay vì làm lộ task của tất cả tài khoản.
* **DEC-20 (Universal Log Prefix Collapse)**:
  * Khử triệt để lỗi lặp tên Profile trong nhật ký hoạt động (ví dụ: `[TUONG SIDA 1] [TUONG SIDA 1]` hoặc `[AutoSchedule][AUTO 30] [AUTO 30]`) bằng regex nén các cặp ngoặc vuông trùng lặp liên tiếp trong `update_status()`: `re.sub(r"(\[[^\]]+\])\s*\1", r"\1", str(message or ""))`.
* **DEC-21 (Đồng Bộ Hóa Bộ Lọc Thời Gian & Tooltip Soi Nội Dung Dài)**:
  * Tích hợp bộ lọc thời gian trực tiếp trên thanh công cụ: `Hôm nay`, `Ngày mai`, `7 ngày tới`, `Quá hạn`, `Tất cả thời gian`. So sánh ISO chuỗi chuẩn không bọc hàm `DATE()` để tận dụng tối đa chỉ mục SQLite.
  * Tích hợp `TreeviewTooltip` tối màu nổi bật khi di chuột qua các cột Tên video và Tiêu đề xuất bản, giúp người dùng dễ dàng kiểm tra các tiêu đề dài bị cắt cụt.
  * Bản địa hóa 100% các trạng thái và thay thế ký tự gạch ngang `-` bằng `Chưa liên kết` đối với các tài khoản TikTok chưa liên kết profile.

### Bằng chứng kiểm thử (`tests/test_title_stutter_helper.py`, `tests/test_schedule_manager_view_scope.py`, `tests/test_schedule_ui_redesign.py`)
1. `test_detect_and_strip_title_stutter`: PASSED (3/3 tests).
2. `test_schedule_manager_view_scope_and_filters`: PASSED (5/5 tests).
3. `test_schedule_ui_redesign`: PASSED (4/4 tests).
4. **Toàn bộ Regression Suite (64/64 tests)**: PASSED 100% in 9.904s.



