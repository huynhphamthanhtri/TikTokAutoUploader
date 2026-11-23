# Thiết Kế Kho Tài Khoản YouTube (Cookie Pool) & Tối Ưu Tải Video Ngắn (~1 Phút)

**Ngày tạo:** 2026-11-23  
**Trạng thái:** Đã duyệt thiết kế  
**Phạm vi file:** `youtube_monitor/cookie_pool.py` (mới), `youtube_monitor/core.py`, `youtube_monitor/ui.py`

---

## 1. Phân Tích Lỗi Thực Tế Từ Log
1. **Cắt ngang chuỗi Fallback ở `[3/6]` (`_select_next_attempt`):**
   - Video `YpcXsjWqIbs` thất bại ở `[1/6]` (`direct-cookies`) và `[2/6]` (`direct-cookies-alt-format`) với `HTTP 403 Forbidden`, rồi ở `[3/6]` (`direct-auto-cookies`) gặp `[youtube] YpcXsjWqIbs: The page needs to be reloaded.` -> bị phân loại là `FAILURE_RETRY`.
   - Các attempt `[4/6]` (`direct-primary` không cookie), `[5/6]`, `[6/6]` (`mweb`/`android`/`ios`) không khai báo `FAILURE_RETRY` trong `triggers`, nên `_select_next_attempt` trả về `None` và ngắt luôn chuỗi, lặp vô tận suốt 35 phút với đúng cookie đang lỗi.
2. **Điểm nghẽn 1 Cookie duy nhất:** Chỉ có 1 profile đăng nhập và 1 file cookie, không có cơ chế cách ly/xoay vòng sang tài khoản khác khi bị 403/reload.
3. **Tàn dư UI cũ:** Cụm `File Cookie YouTube (cookies.txt)` với 5 nút rời rạc và thông báo "bật chuông kênh ở chế độ Tất cả" gây rối cho người dùng.

---

## 2. Quyết Định Thiết Kế (Decision Log)
- **Tách `YouTubeCookiePoolManager` (`youtube_monitor/cookie_pool.py`):** Lưu trạng thái tại `youtube_cookie_pool.json`, file cookie trong `youtube_cookies_pool/`, profile Chrome trong `youtube_profiles/`. Hỗ trợ kế thừa tự động `youtube_cookies.txt` cũ.
- **Xoay vòng LRU & Cooldown tức thì:** Chọn các tài khoản `READY` ít dùng nhất gần đây; khi 1 cookie gặp `403` hoặc `reload` -> đưa vào `COOLDOWN` (300s) và nhảy ngay sang cookie khác trong Pool trong cùng lượt tải. Nếu cookie hết hạn/bot-check -> đánh dấu `EXPIRED`.
- **Sửa chuỗi Fallback (`_select_next_attempt`):** Cho phép `FAILURE_RETRY` kích hoạt chuyển tiếp sang cookie kế tiếp và nhánh không cookie (`mweb`/`android`/`ios`) + Proxy fallback.
- **Tối ưu video ngắn (~1 phút):** `http_chunk_size = 10MB` + `concurrent_fragment_downloads = 2` (tải gọn trong 1–2 request, hạn chế spam connection gây 403) và giới hạn tối đa `MAX_RETRY_CYCLES = 3` để không ngâm chết worker gây miss video mới.
- **UI gọn trực quan:** Xóa bỏ cụm `cookies.txt` rườm rà và thông báo bật chuông cũ; thay bằng khối **`🍪 KHO COOKIE YOUTUBE (XOAY VÒNG)`** với badge trạng thái trực tiếp + 2 nút (`Quản Lý Kho Cookie`, `+ Đăng Nhập Nhanh`).
