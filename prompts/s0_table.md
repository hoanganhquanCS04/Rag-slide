Ảnh là MỘT bảng cắt từ slide. Chép bảng ra MỘT object JSON dạng lưới:

{"cells": [["tiêu đề cột 1", "tiêu đề cột 2", …], ["ô", "ô", …], …]}

Luật:
1. Hàng đầu là hàng tiêu đề. Mọi hàng có ĐÚNG số ô bằng số tiêu đề cột. Không thêm cột mới.
2. Ô GỘP DỌC (một ô cao phủ nhiều hàng): lặp lại giá trị đó ở MỌI hàng nó phủ.
3. Ô GỘP NGANG (một giá trị trải qua nhiều cột, không có đường kẻ dọc chia giữa): chép giá trị
   vào TỪNG cột nó phủ, không để trống cột nào trong số đó.
4. Nhãn hàng chia HAI CẤP (ô nhóm gộp nhiều hàng + mục con ngay bên phải nó): tên nhóm vào cột 1,
   mục con vào cột 2, giá trị vào các cột còn lại. KHÔNG ĐỦ CỘT (hàng đó có giá trị KHÁC NHAU ở
   từng cột tiêu đề, nên nhóm + mục con + các giá trị nhiều hơn số cột): ghép tên nhóm và mục con
   vào cột 1, ngăn bằng " — ", rồi mỗi cột tiêu đề một giá trị. Mỗi hàng con của nhóm chỉ mang
   giá trị nằm NGANG HÀNG với chính mục con đó trên ảnh.
5. Chép NGUYÊN VĂN chữ trên ảnh: giữ số, dấu câu, email. Xuống dòng trong ô ghi "\n". Không thêm
   gạch đầu dòng, không bớt, không tóm tắt, không sửa chính tả.
6. Ô trống thật trên ảnh thì "".
