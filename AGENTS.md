# QUY TẮC PHÁT TRIỂN, KIỂM THỬ VÀ BÀN GIAO CHO AGENT (BẮT BUỘC TUÂN THỦ 100%)

## 1. Nghiêm Cấm Báo Cáo Ảo & Pass Giả Tạo (Zero Tolerance for False Passes)
- **TUYỆT ĐỐI KHÔNG ĐƯỢC** kết luận "Pass 100% / Hoàn hảo / Đã fix triệt để" nếu chỉ dựa trên unit test tự viết nông cạn (shallow tests) mà chưa kiểm chứng logic nghiệp vụ thực tế.
- **Không tự viết mock dễ dãi để tự cho mình pass**: Mock test phải phản ánh đúng 100% schema và edge case thực tế của server/hệ thống (bao gồm các trạng thái chờ duyệt, lỗi, mã lỗi đặc thù, tài liệu bị từ chối, resubmit, v.v.).
- Nếu một tính năng chưa kiểm chứng được trên tài khoản/môi trường thật, **BẮT BUỘC PHẢI BÁO RÕ**: "Đã pass unit test logic nhưng chưa test trên account live; cần kiểm chứng thêm các trường hợp X, Y".

## 2. Kiểm Thử Tính Đúng Đắn Nghiệp Vụ (Semantic & Business Logic Verification)
- Viết code không chỉ nhằm "hàm chạy qua không văng exception", mà **PHẢI ĐÚNG ĐẮN VỀ MẶT NGHIỆP VỤ**:
  - Đối với API bên thứ ba (TikTok, Payout, KYC, CRP, WebGL, WebRTC): Phải đối chiếu trực tiếp với raw response hoặc mã nguồn tham chiếu chuẩn xác thực tế (như TTM), không được suy diễn chủ quan hoặc tự bịa logic.
  - Phải bao phủ đầy đủ toàn bộ lifecycle trạng thái (ví dụ: KYC không chỉ có Approved/Rejected mà còn có CDD pending, POA resubmit, ID resubmit; CRP không chỉ có Active/Rejected mà còn có Reapply date, Appeal deadline).
- Khi sửa một lỗi logic: Phải viết test tái hiện được case SAI trước đó và chứng minh case MỚI xử lý ĐÚNG trên các mẫu payload thực tế.

## 3. Bàn Giao Kết Quả Thật 100% (Zero Fabrication)
- Mọi kết quả kiểm thử, dữ liệu profile, mã trạng thái HTTP, số liệu log đưa vào báo cáo/walkthrough **PHẢI LÀ KẾT QUẢ THỰC THI THẬT TỪ MÁY**.
- Nghiêm cấm bịa kết quả, bịa số lượng test, bịa trạng thái pass khi chưa chạy lệnh hoặc lệnh bị fail.

## 4. Quy Tắc Đóng Gói (Packaging Rule)
- **KHÔNG TỰ Ý ĐÓNG GÓI** phần mềm khi người dùng chưa yêu cầu rõ ràng.

## 5. Phân Loại Mức Độ Kiểm Chứng (Verification Levels)
- Mọi kết luận về tính đúng đắn hoặc trạng thái hoàn thành **PHẢI** ghi đúng mức kiểm chứng phù hợp:
  - `STATIC ONLY`: Chỉ đọc/phân tích code, chưa chạy chương trình hoặc test.
  - `UNIT TESTED`: Đã chạy unit test, chưa kiểm tra tích hợp hoặc hệ thống thật.
  - `INTEGRATION TESTED`: Đã kiểm tra các thành phần tích hợp trong môi trường cục bộ.
  - `DRY-RUN LIVE`: Đã chạy bằng account/môi trường thật nhưng cố ý không thực hiện hành động cuối.
  - `LIVE VERIFIED`: Đã thực hiện hành động thật và xác nhận kết quả thật từ hệ thống đích.
  - `NOT VERIFIED`: Chưa thể kiểm chứng.
- **KHÔNG ĐƯỢC** nâng mức kết luận nếu không có bằng chứng tương ứng. Ví dụ: `DRY-RUN LIVE` không được báo thành `LIVE VERIFIED`.

## 6. Bằng Chứng Kiểm Thử Bắt Buộc (Evidence Required)
- Khi báo cáo kết quả kiểm thử, agent **PHẢI** cung cấp các dữ kiện có thật và phù hợp với tác vụ:
  - Lệnh thực tế đã chạy.
  - Kết quả/exit status thực tế của lệnh.
  - Số test đã chạy, pass, fail, error và skip nếu output có cung cấp.
  - Tên test hoặc bước bị lỗi nếu có.
  - Account/profile/môi trường thật đã dùng, nếu là kiểm tra live.
  - Đường dẫn log, screenshot, diagnostics hoặc raw response dùng làm bằng chứng, nếu có.
  - Hành động cuối đã thực sự xảy ra hay chỉ dừng ở dry-run.
- Không được báo `OK`, `PASS`, `đã đăng`, `đã cập nhật`, `đã phát hành` hoặc `đã sửa hoàn toàn` nếu không có bằng chứng tương ứng từ lần thực thi hiện tại.
- Không được lấy output của lần chạy cũ để giả làm kết quả của code hiện tại.

## 7. Cấm Đánh Đồng Dry-Run Với Thành Công Live
- Trạng thái `prepared`, editor sẵn sàng hoặc nút Post khả dụng **KHÔNG CÓ NGHĨA** video đã đăng thành công.
- HTTP mock `200` **KHÔNG CÓ NGHĨA** API thật đã trả `200`.
- UI hiển thị nút hoặc click thành công **KHÔNG CÓ NGHĨA** server đã chấp nhận nghiệp vụ.
- Chỉ được kết luận upload/post thành công khi có xác nhận đáng tin cậy từ hệ thống thật, ví dụ raw response thành công, DOM/URL xác nhận hợp lệ hoặc nội dung xuất hiện trong trang quản lý.
- Nếu người dùng chỉ cho phép dry-run, báo cáo phải ghi rõ `KHÔNG BẤM POST` / `CHƯA ĐĂNG LIVE`.

## 8. Cấm Test Tự Chứng Minh (No Self-Validating Tests)
- Không viết mock khớp với chính implementation rồi dùng mock đó làm bằng chứng duy nhất rằng nghiệp vụ đúng.
- Mock/payload/DOM fixture phải dựa trên schema tài liệu chính thức hoặc dữ liệu thật đã ghi nhận; phải ghi nguồn của fixture khi ý nghĩa nghiệp vụ không hiển nhiên.
- Khi sửa regression, test phải tái hiện được lỗi trước đó và chứng minh hành vi mới đúng. Không được chỉ kiểm tra rằng hàm "không ném exception".
- Không được hạ assertion, xóa edge case, bỏ test hoặc thêm `skip` chỉ để làm suite xanh.
- Không được mock bỏ qua các điểm rủi ro chính như xác thực, trạng thái server, lifecycle, retry, timeout, popup, rejection hoặc resubmit.

## 9. Quy Tắc Báo Cáo Test Flaky
- Nếu cùng một code có cả lần fail và lần pass, **KHÔNG ĐƯỢC** chỉ báo cáo lần pass cuối cùng.
- Phải ghi rõ:
  - Tên test hoặc bước flaky.
  - Lỗi thực tế đã gặp.
  - Số lần chạy pass/fail đã quan sát.
  - Trạng thái nguyên nhân: đã xác định, nghi ngờ hay chưa xác định.
- Một lần chạy lại thành công không chứng minh lỗi đã được sửa.
- Nếu chưa xử lý nguyên nhân, phải ghi rõ `FLAKY / CHƯA XỬ LÝ` và không được gọi toàn bộ hệ thống là ổn định tuyệt đối.

## 10. Trung Thực Khi Thiếu Điều Kiện Kiểm Chứng
- Agent **PHẢI** nói rõ khi thiếu bất kỳ điều kiện nào sau đây:
  - Không có account live hoặc dữ liệu thật.
  - Không đủ quyền thực hiện hành động cuối.
  - Không thể truy cập API, mạng hoặc dịch vụ bên thứ ba.
  - Không có raw response/tài liệu/schema đáng tin cậy.
  - Chưa bấm hành động cuối hoặc chưa xác nhận kết quả phía server.
  - Kết quả mới chỉ là suy luận từ code, mock hoặc UI.
- Không được tự lấp khoảng trống bằng giả định rồi trình bày giả định đó như sự thật.

## 11. Cấm Bịa Đặt, Che Giấu Và Đánh Tráo Khái Niệm
- Nghiêm cấm bịa lệnh đã chạy, log, commit, URL, HTTP status, số test, screenshot, profile, payload hoặc trạng thái release.
- Không được cắt bỏ phần output chứa lỗi để tạo cảm giác test đã pass.
- Không được gọi warning/error là "không ảnh hưởng" nếu chưa có bằng chứng chứng minh.
- Không được khẳng định "nguyên nhân gốc" nếu mới chỉ có giả thuyết.
- Trong phân tích và báo cáo phải phân biệt rõ:
  - `FACT`: Dữ kiện đã quan sát trực tiếp.
  - `OBSERVATION`: Hiện tượng ghi nhận từ lần chạy.
  - `HYPOTHESIS`: Giả thuyết cần kiểm chứng.
  - `ASSUMPTION`: Giả định đang sử dụng do thiếu dữ liệu.

## 12. Điều Kiện Được Phép Kết Luận Hoàn Thành
- Chỉ được nói tác vụ "hoàn thành" khi tất cả điều kiện áp dụng đã đạt:
  - Code yêu cầu đã được thay đổi thật.
  - Test liên quan đã được chạy thật trên code hiện tại.
  - Regression test tái hiện đúng lỗi cũ nếu đây là bug fix.
  - Mọi failure, flaky test, giới hạn kiểm chứng và rủi ro còn lại đã được báo cáo.
  - Nếu cần live verification thì đã kiểm tra live; nếu chưa, phải ghi rõ chưa kiểm tra.
  - Working tree và phạm vi file thay đổi đã được rà soát để không nhận công thay đổi của người khác.
  - Không tự ý commit, release, push hoặc package nếu người dùng chưa yêu cầu.

## 13. Mẫu Bàn Giao Bắt Buộc Cho Tác Vụ Có Kiểm Thử
- Báo cáo cuối phải thể hiện tối thiểu bốn dòng sau (có thể bổ sung chi tiết):
  - `Đã kiểm chứng:` Những gì đã quan sát/chạy thật.
  - `Chưa kiểm chứng:` Những gì chưa thể xác nhận, đặc biệt là live account/server.
  - `Bằng chứng:` Lệnh, test count, raw response hoặc đường dẫn artifact có thật.
  - `Rủi ro còn lại:` Flaky test, edge case, môi trường chưa kiểm tra hoặc giả định còn tồn tại.
