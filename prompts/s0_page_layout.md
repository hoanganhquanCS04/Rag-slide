Bạn nhận MỘT trang slide gồm:

1. Ảnh chụp CẢ trang.
2. Danh sách MẨU CHỮ đã trích sẵn từ text layer (chữ đúng 100%), mỗi mẩu có id `T1, T2…`
   và khung `[trái, trên, phải, dưới]` (toạ độ trong [0,1], gốc trên-trái).
3. Danh sách VÙNG ẢNH `P1…` và VÙNG BẢNG `B1…` mà bộ tách layout khoanh được.

Bộ tách layout nhìn từng vùng riêng lẻ nên hay làm sai: băm một câu thành nhiều mẩu, trộn
dòng của hai cột, tách nhãn khỏi nội dung của nó. Việc của bạn là NHÌN CẢ TRANG rồi SẮP
các mẩu chữ thành khối đúng như người đọc thấy.

KHÔNG chép lại chữ của mẩu T — chỉ trỏ id. Code tự ghép đúng nguyên văn.

Trả về MỘT object JSON:

{
  "blocks": [ …các khối theo THỨ TỰ ĐỌC… ],
  "decorative": ["P2", "P3"],
  "skip": ["T9"]
}

Các loại khối:

- `{"kind": "title", "ids": ["T1"]}` — tiêu đề trang, tiêu đề mục, nhãn của một nhóm.
- `{"kind": "text", "ids": ["T4", "T5"]}` — một đoạn văn. Các mẩu thuộc CÙNG một câu thì gộp
  vào một khối theo đúng thứ tự đọc.
- `{"kind": "list", "items": [["T6"], ["T7", "T8"]]}` — bó gạch đầu dòng. Mỗi phần tử là MỘT
  gạch đầu dòng; một gạch có thể gồm nhiều mẩu.
- `{"kind": "table", "source": "B1"}` — GIỮ NGUYÊN bảng B1 khi chữ trong bảng đã đúng cột, đúng hàng.
- `{"kind": "table", "source": "B1", "rows": [[ô, ô], [ô, ô]]}` — dựng lại bảng (bảng B1 sai
  cột, rỗng, hoặc bảng nằm trong ảnh P). `source` = vùng B hoặc P chứa bảng. Hàng đầu là header.
  Ô là `{"ids": ["T3"]}` khi chữ có trong danh sách mẩu, hoặc `{"read": "chữ"}` khi chữ nằm
  TRONG ẢNH và không có mẩu T nào chứa nó.
- `{"kind": "figure", "source": "P1", "ids": ["T10", "T11"], "describe": "…"}` — hình / sơ đồ
  CÓ nội dung. `ids` = các mẩu chữ nằm bên trong hình (nhãn, số thứ tự từng bước).
  `describe` = 2–4 câu nói hình thể hiện điều gì.

Luật:

1. Mỗi id T dùng ĐÚNG MỘT lần. Mẩu nào không thuộc nội dung (số trang, chữ trang trí lặp lại)
   thì đưa vào `skip`. Không được bỏ quên id nào. Mỗi vùng P và B cũng phải xuất hiện đúng
   một lần: làm `source` của một khối, hoặc nằm trong `decorative`.
2. Hai cột hoặc hai nhóm đặt cạnh nhau (DO / DON'T, nhãn bên trái — hộp nội dung bên phải):
   mỗi nhóm một khối riêng, hoặc một bảng. TUYỆT ĐỐI không trộn dòng của hai nhóm vào một khối.
   Nhãn nhóm đứng NGAY TRƯỚC nội dung của nhóm đó.
3. Chữ vụn nằm trong một sơ đồ (số 1 2 3, nhãn từng bước, mốc giờ trên trục thời gian): gom
   vào `figure` của sơ đồ đó và nói rõ trình tự hoặc quan hệ trong `describe`.
4. Ảnh chỉ để minh hoạ hoặc trang trí (ảnh người, ảnh đồ vật, icon, ảnh nền, ảnh minh hoạ
   cho câu đã có chữ): đưa id P vào `decorative`, không mô tả.
5. Ảnh hoặc bảng chứa thông tin KHÔNG có trong chữ (ảnh chụp bảng, sơ đồ có số liệu, ảnh chụp
   màn hình có chữ): dựng `table` với ô `read`, hoặc `figure` với `describe`. Chép số liệu
   ĐÚNG như trên ảnh, không làm tròn.
6. Mỗi nội dung xuất hiện MỘT lần. Bảng nằm trong ảnh thì dựng thành MỘT `table` riêng, header
   là hàng tiêu đề của CHÍNH bảng đó. Nhãn tab hay khung bao ngoài (vd "Website | App") là
   `title`, không phải header — không chép bảng vào nhiều ô.
7. `describe` viết tiếng Việt, gọi đối tượng bằng TÊN. Cấm "hình này", "nó", "như trên",
   "ở phần trước". Không mở đầu bằng "Hình ảnh này…".
8. Chỉ ghi cái nhìn thấy trên trang. Không suy diễn, không thêm kiến thức ngoài trang.
