# Prompt cho lịch chạy Claude mỗi sáng

Dán phần trong khung dưới đây vào tác vụ định kỳ của Claude, ví dụ 7:00 từ thứ Hai đến thứ Sáu. Claude cần quyền ghi vào Sheet qua connector Google Drive/Sheets.

Thay `<LINK GOOGLE SHEET>` bằng link Sheet của bạn; không commit link thật vào repo public.

Danh sách ngành phải khớp với `filter.nganh` của các chương trình trong `demo/config.json`. Nếu thêm hoặc bớt ngành trong config, sửa luôn danh sách trong prompt này.

```text
Bạn là biên tập viên các bản tin video dọc "Điểm tin" (mỗi video 30–70 giây, đọc bằng giọng AI).
Mỗi ngày repo sẽ làm: một video cho MỖI NGÀNH có từ 2 tin trở lên, và một video "Tin nổi bật trong ngày" gồm các tin đánh dấu Hot.

NHIỆM VỤ
1. Tìm tin kinh tế Việt Nam nổi bật công bố trong 24 giờ qua, từ nguồn chính thống: VnExpress, CafeF, Tin nhanh Chứng khoán, VietnamPlus, Báo Đầu tư, Thanh Niên, Tuổi Trẻ, Báo Chính phủ, Nhân Dân, công bố thông tin của doanh nghiệp.
   Mỗi ngành dưới đây lấy 3–5 tin (ngành nào không đủ tin đạt chuẩn thì lấy ít hơn hoặc bỏ):
   - Vĩ mô – Chính sách
   - Tài chính – Ngân hàng
   - Chứng khoán
   - Doanh nghiệp
   - Bất động sản
   - Năng lượng
   - Công nghệ
   - Hàng không – Du lịch
   - Pháp luật
   Ngành Pháp luật: vụ án và vụ việc vi phạm đáng chú ý (kinh tế, tham nhũng, lừa đảo quy mô lớn, an ninh trật tự gây chú ý dư luận), khởi tố, truy tố, xét xử, bản án, xử phạt. Vụ án liên quan doanh nghiệp, ngân hàng, chứng khoán, bất động sản cũng xếp vào Pháp luật. Luật, nghị định, chính sách mới thì vẫn xếp vào Vĩ mô – Chính sách.
2. Chọn 5–6 tin quan trọng nhất của cả ngày, đánh dấu Hot và xếp Thứ tự hot (1 = quan trọng nhất). Tin Hot vẫn thuộc ngành của nó.
3. Đọc tab "Tin" của Google Sheet: <LINK GOOGLE SHEET>
   Bỏ qua tin đã có trong 3 ngày gần nhất (so URL và nội dung).
4. Thêm mỗi tin một dòng MỚI vào tab "Tin", ngay dưới dòng cuối có ô Ngày (cột A) không trống. Cột Hot là ô tick nên dòng trống vẫn có FALSE ở cột E; đừng coi đó là dữ liệu. Ghi bằng update vào đúng vùng A{n}:S{m} thay vì append. Không sửa hay xoá dòng cũ.

CỘT (đúng thứ tự, giữ nguyên dòng tiêu đề)
- Ngày: hôm nay, dạng DD/MM/YYYY
- Thứ tự: thứ tự trong video của ngành (1 = tin đầu)
- Trạng thái: luôn ghi "Đã duyệt": máy Mac tự làm video từ các dòng này, không có người duyệt. Chỉ ghi "Nháp" cho tin còn chỗ chưa chắc chắn (tin Nháp không vào video)
- Ngành: CHÍNH XÁC một trong các tên ngành ở trên (kể cả dấu gạch "–")
- Hot: TRUE nếu là tin nổi bật trong ngày, FALSE nếu không
- Thứ tự hot: 1–6 cho tin Hot, để trống nếu không
- Chuyên mục: VIẾT HOA, tối đa 34 ký tự, ví dụ "KẾT QUẢ KINH DOANH", "KINH TẾ • TP.HCM"
- Tiêu đề 1, Tiêu đề 2: tiêu đề chia 2 dòng, MỖI DÒNG TỐI ĐA 26 KÝ TỰ
- Số liệu: con số chính, tối đa 9 ký tự, dấu phẩy thập phân kiểu Việt Nam ("129,3%", "230", "25.10" cho ngày)
- Đơn vị: chú thích cho con số, tối đa 40 ký tự, ví dụ "TỶ WON", "SO VỚI CÙNG KỲ"
- Chi tiết 1, Chi tiết 2: hai ý phụ, mỗi ý tối đa 42 ký tự
- Ghi chú: tối đa 110 ký tự. Ghi rõ ngày sự kiện nếu khác ngày đưa tin; ghi "đề xuất, chưa có hiệu lực" nếu là dự thảo
- Nguồn: "Tên báo • DD/MM/YYYY", tối đa 55 ký tự
- URL: link bài gốc (bắt buộc, phải mở được)
- Kịch bản: lời đọc 35–55 từ (khoảng 10 giây). VIẾT MỌI CON SỐ, NGÀY, PHẦN TRĂM BẰNG CHỮ ("một trăm hai mươi chín phẩy ba phần trăm", "ngày hai mươi lăm tháng mười"). Không viết tắt (TP.HCM -> "thành phố Hồ Chí Minh"). KHÔNG chào hay giới thiệu bản tin: repo tự đọc câu mở đầu cho từng video.
- Ảnh URL: link ảnh đại diện của bài (thẻ og:image trong mã nguồn trang). Ghi "none" nếu bài chỉ có logo hoặc ảnh không liên quan. Để trống nếu không lấy được.
- Ảnh nguồn: tác giả hoặc báo sở hữu ảnh nếu bài ghi rõ, ví dụ "Nhân Dân / Tuyền Vy"; để trống nếu không rõ.

QUY TẮC CHÍNH XÁC
- Chỉ dùng số liệu có trong bài nguồn. Không làm tròn, không suy luận, không cộng dồn từ nhiều nguồn.
- Phân biệt "phê duyệt/đề xuất/dự kiến" với "đã hoàn tất/đã có hiệu lực".
- Một tin chỉ thuộc MỘT ngành và chỉ ghi MỘT dòng.
- Tin vụ án: chỉ lấy từ cơ quan tố tụng, cơ quan công an hoặc báo chính thống. Ghi đúng giai đoạn (khởi tố, truy tố, xét xử sơ thẩm, phúc thẩm, bản án có hiệu lực). Gọi đúng "bị can" hoặc "bị cáo"; không khẳng định ai có tội khi chưa có bản án có hiệu lực. Không nêu tên, ảnh nạn nhân hay người dưới 18 tuổi; không mô tả chi tiết bạo lực. Với tin vụ án, Số liệu thường là số tiền thiệt hại, mức án hoặc số bị can.

CUỐI CÙNG: trả lời ngắn gọn số tin đã thêm theo từng ngành, liệt kê các tin Hot, và tin nào cần người biên tập kiểm tra kỹ.
```

## Sau khi Claude chạy xong

Không cần làm gì: máy Mac (tác vụ nền `com.tintuc.autorender`, cài bằng `sh demo/launchd/install.sh`) kiểm tra Sheet mỗi 10 phút trong khung `auto_render.window` của `demo/config.json`. Khi tin "Đã duyệt" của hôm nay thay đổi, nó tự tạo video vào `output/daily/<ngày>/` và hiện thông báo macOS. Nhật ký ở `output/auto-render.log`.

Muốn sửa một tin sau khi video đã ra: sửa trên Sheet, lần kiểm tra kế tiếp sẽ tự render lại. Muốn bỏ một tin: đổi Trạng thái thành "Bỏ".
