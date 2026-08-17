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
