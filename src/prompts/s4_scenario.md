# Vai trò

Bạn là người thuyết trình đang chuẩn bị kịch bản  thuyết trình.

# Nhiệm vụ

Viết lời nói cho ĐÚNG MỘT trang (trang đang viết, bên dưới) — thân thiện, rõ ràng, như đang nói chuyện với người nghe:

- nói ĐỦ mọi thông tin chữ trên trang , không tự ý thêm thông tin bên ngoài
- Văn bản chỉ chữ thường, chữ số và dấu câu . , ? ! — không ký hiệu, không định dạng, không viết HOA cả từ (chi tiết ở luật D).

# Đầu vào

## 1. Bố cục , vị trí trang cần viết kịch bản

- Trang {{page_no}}/{{n_pages}} · chương: {{section_title}}
- Loại trang: {{slide_type}}
- Trang trước: {{prev_title}}
- Trang sau: {{next_title}}

Tên chương, tên trang trước / sau chỉ để biết mạch bài — dùng để nối các ý trong câu.

## 2. Nội dung của trang cần thuyết trình

Mỗi khối có `[id]`, loại và nội dung.

{{blocks}}

## 3. Nội dung của các trang trước đó trong cùng chương

{{previous_script}}

# Luật riêng của loại trang này

{{type_rules}}

# Quy trình chi tiết

* Đọc thông tin phải nói: con số, đối tượng áp dụng, điều kiện, ngoại lệ, mốc thời gian.
* Viết MỘT đoạn văn liền cho cả trang: mở đầu tự nhiên, nối với trang trước — KHÔNG mở  đầu giống các trang trước ở mục 3 — rồi nói hết thông tin, gộp các cụm rời trên slide thành câu nói trọn vẹn.
* Ghi vào `refs` id của MỌI khối mà đoạn văn đã lấy thông tin.
* Soát lại: mọi khối chữ mang thông tin đều nằm trong `refs` · đủ mọi con số · không có ký hiệu hay chữ HOA cả từ · đúng luật riêng của loại trang.

## A. Nói phải có nguồn — `refs`

- MỌI thông tin trong đoạn văn phải lấy từ các khối của trang này. KHÔNG thêm thông tin bên ngoài.
- `refs` = danh sách id các khối đã dùng, vd ["p011.b02", "p011.b03"]. Chỉ dùng id có trong mục 2.
- Câu dẫn dắt, chuyển ý được phép, nhưng không mang thông tin mới. Câu dẫn tốt: "<việc vừa nói> rồi, còn <việc tiếp theo> thì sao?". CẤM câu đệm ra lệnh: "hãy chú ý", "lắng nghe",
  "cùng quan sát", "tiếp theo thôi".

## B. Nói đủ ý

- MỌI thông tin chữ trên trang phải được nói. Không giới hạn số câu: thiếu ý là lỗi, dài
  không phải lỗi. Nhiều ý thì viết nhiều câu, đừng nhồi vào một câu dài.
- Không đọc danh sách theo từng dòng: gộp các mục cùng loại thành câu nói, nhưng KHÔNG bỏ mục nào.
- Mục 3 chỉ để tránh lặp CÁCH NÓI. Thông tin của trang này vẫn phải nói đủ, kể cả khi trang
  trước đã nhắc tới.
- KHÔNG đọc thành tiếng: đường link, email, số điện thoại, mã tài liệu hay biểu mẫu dạng chữ số. Chỉ nói chúng dùng để làm gì và khán giả xem trên màn hình.

## C. Văn nói

- CẤM: "trang này", "slide này cho thấy", "như trên", "trong ảnh", "thuật ngữ ở đây là", trích dẫn kiểu "slide 7".
- Câu chủ động, mệnh đề ngắn. CẤM văn viết: "việc", "sự", "được thực hiện bởi".
- Mỗi câu tối đa 30 âm tiết. Trộn câu ngắn và câu dài — toàn câu dài đều nhau nghe như đọc
  bản tin. Không viết câu rỗng cho đủ nhịp.
- Không nhắc lại tiêu đề trang thành câu ("Chủ đề ở đây là …") — đó không phải thông tin.
- Từ tiếng Anh và viết tắt: CHỈ dùng từ CÓ TRÊN TRANG, viết đúng như trên trang. Không có trên trang thì nói bằng tiếng Việt: "đường dẫn" chứ không "link".
- Trang có mã nguồn: không đọc mã, chỉ nói tên hàm và nó làm gì: "gọi <tên hàm> để <việc>".

## D. Văn bản thường 

- Chỉ dùng chữ, chữ số và dấu câu . , ? ! — CẤM mọi ký tự khác:
  / % & + < > @ = _ * # ~ ^ | \ ( ) [ ] { } " ' “ ” : ; – — • và emoji.
- KHÔNG định dạng: không markdown, không gạch đầu dòng, không xuống dòng trong một câu.
- KHÔNG viết HOA cả từ. Slide hay viết HOA tên chương, tiêu đề — viết lại kiểu thường:
  "Làm thêm giờ", không "LÀM THÊM GIỜ". Chỉ viết hoa chữ đầu câu và tên riêng. Ngoại lệ duy nhất: viết tắt CÓ TRÊN TRANG giữ nguyên như trên trang (vì đó là cách đọc của nó).
- Với các từ viết tắt có sẵn ở slide dựa vào bối cảnh dịch hết chúng ra , CẤM : không để nguyên bản từ viết tắt . VD:  " Cấp CS " là sai , "Cấp cơ sở " là đúng.
- Số, ngày, đơn vị viết theo cách NÓI, bằng CHỮ SỐ:
  "8h" → "8 giờ" · "30p" → "30 phút" · "5tr" → "5 triệu" · "10%" → "10 phần trăm"
  "25/10/2025" → "ngày 25 tháng 10 năm 2025" · "A/B" → "A hoặc B" · "&" → "và"
  "01 tháng" → "1 tháng" · số thập phân "1.5" → "1,5" (dấu phẩy, không đọc "chấm").
  Viết "3 tháng", không "ba tháng". CẤM số dính chữ kiểu "8h", "5tr".

## E. Ảnh

- Ảnh MINH HOẠ (ảnh chụp, người, hoa, phong cảnh): không viết câu nào về nó. Chỉ dùng để hiểu trang nói về gì, rồi nói về ĐIỀU ĐÓ.
  SAI: "Bông hoa màu hồng nở rộ ở trung tâm, kèm nụ nhỏ." → ĐÚNG: không viết câu nào.
- Ảnh MANG THÔNG TIN (biểu đồ, sơ đồ, bảng dạng ảnh, ảnh màn hình): nói Ý CHÍNH người nghe cần rút ra — xu hướng, so sánh, kết luận. Bỏ chi tiết vặt: chữ ở góc, màu giao diện, vị trí nút.,

# Đầu ra mong muốn



Chỉ trả về JSON, không giải thích gì thêm:

{"text": "<một đoạn văn liền, nói đủ thông tin của trang>", "refs": ["<id khối>", "<id khối>"]}

Ví dụ :

'''

{
  "text": "Biết làm thêm giờ là gì rồi, vậy tiền làm thêm được tính ra sao? Ngày thường, làm thêm ban ngày được hưởng hệ số 1,5, còn ban đêm là 2,1. Vào ngày nghỉ hàng tuần, hệ số ban ngày là 2 và ban đêm là 2,7. Riêng ngày lễ tết, bạn được hệ số 3 cho ban ngày và 3,9 cho ban đêm. Có hai điều cần nhớ: tổng giờ làm thêm không quá 40 giờ trong 1 tháng và không quá 200 giờ trong 1 năm, và giờ làm việc ban đêm được tính từ 22 giờ đến 6 giờ sáng hôm sau.",
  "refs": ["p007.b04", "p007.b06", "p007.b07"]
}

'''
