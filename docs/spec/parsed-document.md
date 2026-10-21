# ParsedDocument — cấu trúc dữ liệu sau khi parse

**Code:** [`src/offline/parser/`](../../src/offline/parser/) · **Vào:** `data/raw/<file>.pdf` (hoặc `.pptx` + `.pdf` cùng tên)
· **Ra:** `src/output/parser/<doc_id>/document.json` — **một file**, vừa để người đọc vừa để pipeline chạy

---

## 0. Bối cảnh v0 ★

Một file duy nhất vừa là **deck** (robot nói gì) vừa là **KB** (robot tra gì) — xem
[CLAUDE.md §3.0](../../CLAUDE.md). Hệ quả: không có `chart_data`/`tables` chuẩn từ XML, robot
chỉ trả lời ở mức **mô tả slide**. Lối thoát duy nhất là đưa tài liệu nguồn thật vào.

```
data/raw/<file>.pdf                      file gốc
src/output/parser/<doc_id>/docling_vlmpipeline.json  cache ① VLM đọc từng trang       parser/variants/docling.py
src/output/parser/<doc_id>/tables.json · pictures.json  cache ②b chép bảng · ② xét ảnh  parser/tables.py · pictures.py
logs/vlm_pipeline/<doc_id>/pNNN.txt           phản hồi thô của VLM từng trang
src/output/parser/<doc_id>/document.json             ParsedDocument — FILE NÀY        parser/base.py (save)
```

`<doc_id>` = tên file bỏ dấu, viết thường: `Thời gian làm việc & Chính sách nhân sự.pdf` →
`thoi_gian_lam_viec_chinh_sach_nhan_su`.

---

## 1. Cấu trúc

```
ParsedDocument
├── doc_id
├── source      {path, sha256}
├── parser      {docling_version, vlm_model, picture_area_threshold}
├── sections[]  {id, title, pages: [đầu, cuối], source}   ← dò lúc parse (sections.py), GHI vào file
└── pages[]
     ├── page_no · title · section_id · page_hash
     └── blocks[]        NỘI DUNG, theo thứ tự đọc
          └── id · page_no · kind · role · content · polygon  (+ trường riêng từng loại)
```

Header / footer / số trang lặp ở mọi trang **không có trong file** — docling gắn nhãn
`page_header` / `page_footer`, parser bỏ luôn: không phải nội dung, chương đã lấy từ
mục lục.

### Tầng tài liệu

| field | nghĩa |
|---|---|
| `doc_id` | định danh — chui vào mọi `chunk_id`, tên thư mục `src/output/parser/<doc_id>/` |
| `source` | file gốc + hash |
| `parser.vlm_model` | model đã đọc trang (VlmPipeline) — mọi khối `vlm` đến từ nó |
| `sections` | chương — xem dưới |
| `flags` | chỗ người cần xem — §4 |

### Tầng trang

| field | nghĩa |
|---|---|
| `page_no` | số trang thật, từ 1 |
| `title` | tiêu đề trang = khối `role: title` ĐẦU TIÊN (không có thì vắng) |
| `section_id` | trỏ lên `sections[].id`; trang trước chương đầu (bìa, mục lục) thì vắng |
| `page_hash` | hash nội dung + vị trí mọi block — đổi thì kịch bản trang đó viết lại |

### Tầng block — hai câu mọi block đều trả lời

| | field | |
|---|---|---|
| nói gì | `content` | chữ · mô tả hình · markdown bảng — thứ đem đi chunk làm KB |
| nằm đâu | `polygon` | 4 góc `[[l,t],[r,t],[r,b],[l,b]]`, `[0,1]`, gốc **trên-trái**, làm tròn 3 chữ số |

**Tin được không:** MỌI chữ do VLM đọc từ ảnh trang (chữ slide, bảng) hoặc là **mô tả hình** —
CÓ THỂ SAI. Chữ slide hay mô tả ảnh: xem `kind` (`image` = mô tả). Trường `provenance` đã bỏ (2026-10-12) — mọi khối đều `vlm`, nó không phân biệt gì;
document.json cũ còn khoá này thì nạp vẫn được, khoá bị bỏ qua.

**`id`**: `p<trang>.b<số>` (vd `p007.b04` = trang 7, khối thứ 4 theo thứ tự đọc của bước ①),
ổn định — chunk và câu kịch bản trỏ vào đây. Id KHÔNG đánh lại khi bỏ khối (ảnh trang trí ở ②, khối
rỗng ở cuối bước parse) → số có thể nhảy (`b01`, `b03`, `b04`…).

**Trường riêng theo `kind`:**

| `kind` | `content` là | thêm |
|---|---|---|
| `paragraph` | chữ | `role` (luôn có) · `urls` khi `role: links` |
| `image` | mô tả hình (VLM) | `why_empty` khi `content: null` |
| `table` | markdown **sinh từ** `cells` lúc nạp — **không ghi ra file** (trùng `cells`) | `cells` (hàng đầu là header; ô gộp dọc đã chép tên xuống hàng trống) |

**`role`** — `kind` nói mẩu này **là gì**, `role` nói robot **đối xử với chữ đó thế nào**:

| `role` | nghĩa |
|---|---|
| `title` | tiêu đề trang / tiêu đề mục / nhãn một nhóm |
| `body` | đoạn văn thường |
| `list` | bó gạch đầu dòng, MỘT block, các dòng ngăn bằng xuống dòng — S4 diễn đạt lại, không đọc bullet |
| `links` | ≥ nửa số dòng là URL in ra chữ — **không đọc URL thành tiếng**. Chỉ body/list mới đổi; title có link vẫn là title. Luật, tự gán cả lúc nạp lại. Link ẩn sau chữ (annotation PDF) KHÔNG đọc (bỏ 2026-10-12) |
| `caption` | chú thích |


Khác `urls` (URL in ra thành chữ). Không vào hash, không vào KB — runtime hiện ra khi được hỏi.

**`why_empty`** — ảnh không có `content` thì phải biết VÌ SAO:

| giá trị | nghĩa | ghi ra file? |
|---|---|---|
| `decorative` | ảnh trang trí | không — bị bỏ |
| `area_below_threshold` | ảnh nhỏ hơn 5% trang | không — bị bỏ |
| `not_described` | ảnh đủ to mà không có mô tả (trang chưa có bố cục VLM) | **có** + cờ `image_not_described` |
| `api_error` | gọi VLM mà lỗi | **có** + cờ |

**Chỉ block rỗng CÓ cờ mới được ghi.** Block `content: null` mà không cờ bị bỏ ở bước ③ —
không vào KB, không vào kịch bản, chỉ là nhiễu. Trang có bố cục VLM thì ảnh trang trí không
bao giờ thành block (VLM xếp vào `decorative`).

### Chương (`sections`)

SUY RA chứ không parse ra — nên bắt buộc khai `source`. Nguồn duy nhất là
**trang mục lục** (`source: outline_page`; `manual` dành cho người sửa tay):

- Tìm trong 5 trang đầu một trang có ≥ 3 dòng mà ≥ 60% dòng tìm được trang mở chương phía sau
  — không dựa vào chữ "Nội dung", chính các trang sau xác nhận đó là mục lục.
- Đọc tên chương, dò tiêu đề các trang phía sau: trang ĐẦU TIÊN có tiêu đề khớp một mục là
  trang bắt đầu chương, kéo tới trước trang bắt đầu chương kế tiếp.
- Khớp: bỏ số thứ tự ("01.", "4."), không phân biệt hoa thường, tiêu đề CHỨA tên mục cũng
  tính ("CHẾ ĐỘ LƯƠNG THƯỞNG" ∋ "LƯƠNG THƯỞNG").
- Trang trước chương đầu (bìa, mục lục) không có `section_id`.

Không có mục lục khớp → `sections: []`. KHÔNG đoán.

### `slide_type` — KHÔNG ghi ra JSON

Loại trang vẫn tính bằng luật trong code (`ParsedPage.slide_type`) cho kịch bản, KB, cờ dùng
— nhưng không ghi ra file: deck mới gần như toàn `content`, ghi ra chỉ là nhiễu. Xem bằng
`cli.py show`.

| loại | luật |
|---|---|
| `section_divider` | đúng 1 mẩu chữ, là tiêu đề, nằm giữa trang → kịch bản chỉ nói câu chuyển, KB lọc khỏi tìm kiếm |
| `exercise` | tiêu đề mở đầu bằng "Bài tập" → robot đọc yêu cầu, không giảng |
| `content` | còn lại |

### Ghi JSON gọn

Trường rỗng (`null` / `[]` / `{}`) không ghi — trừ `content`, để nhìn là thấy block không có
nội dung. Nạp lại thì trường vắng lấy giá trị mặc định. Mảng ngắn (polygon, hàng bảng, footer)
viết trên một dòng.

---

## 2. Ví dụ thật

**Thời gian làm việc, trang 7** — bảng hệ số làm thêm giờ và 2 dòng lưu ý đều nằm TRONG ẢNH
(text layer chỉ có tiêu đề). VLM đọc ra → `vlm`; 2 ảnh trang trí bị bỏ:

```json
{
  "page_no": 7,
  "title": "CÁCH TÍNH LÀM THÊM GIỜ",
  "section_id": "sec_02",
  "page_hash": "dfb5bdf1a09d1feb",
  "blocks": [
    {
      "id": "p007.b00", "kind": "paragraph", "role": "title",
      "content": "CÁCH TÍNH LÀM THÊM GIỜ",
      "polygon": [[0.499, 0.094], [0.843, 0.094], [0.843, 0.141], [0.499, 0.141]]
    },
    {
      "id": "p007.b01", "kind": "table",
      "content": "|  | GIỜ LÀM THÊM BAN NGÀY | GIỜ LÀM THÊM BAN ĐÊM |\n|---|---|---|\n| Ngày thường | Hệ số 1.5 | Hệ số 2.1 |\n...",
      "cells": [["", "GIỜ LÀM THÊM BAN NGÀY", "GIỜ LÀM THÊM BAN ĐÊM"], ["Ngày thường", "Hệ số 1.5", "Hệ số 2.1"], "..."],
      "polygon": [[0.407, 0.287], [0.96, 0.287], [0.96, 0.534], [0.407, 0.534]]
    },
    {
      "id": "p007.b02", "kind": "paragraph", "role": "list",
      "content": "Đảm bảo số giờ làm thêm không quá 40 giờ trong 01 tháng, không quá 200 giờ trong 01 năm\nGiờ làm việc ban đêm được tính từ 22 giờ đến 06 giờ sáng ngày hôm sau.",
      "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
    }
  ]
}
```

Khối `v02` không trỏ mẩu chữ docling nào, cũng không có vùng nguồn → không biết nằm đâu →
`polygon` là cả trang. Số trong khối VLM đọc phải được người đối chiếu với slide ở S7.

**Onboarding Kit, `sections`** — dựng từ mục lục p2, 5/5 mục khớp:

```json
"sections": [
  {"id": "sec_00", "title": "Các thủ tục, quy trình, quy định, thông tin chung", "pages": [3, 13], "source": "outline_page"},
  {"id": "sec_01", "title": "Nhân sự", "pages": [14, 36], "source": "outline_page"},
  "..."
]
```

---

## 3. Trong code

```python
from models.parsed_document import ParsedDocument

doc = ParsedDocument.load("src/output/parser/onboarding_kit/document.json")   # file cũ -> báo 1 câu
page = doc.page(10)
doc.section_of(10)                          # chương của trang

for b in page.blocks:
    b.page_no, b.content, b.polygon               # field lưu
    b.box, b.center, b.area                       # tính từ polygon
    doc.section_of(b.page_no)                     # chương chứa block

page.paragraphs · page.images · page.tables # lọc theo loại
page.slide_type                             # loại trang — tính, không lưu
page.compute_hash()                         # nội dung + vị trí

doc.to_json()                               # đúng định dạng ghi ra file
```

Luật dễ quên:

- **Block LƯU `page_no`** (lấy block ra khỏi trang vẫn biết nó ở đâu). Thứ tự đọc là thứ tự trong
  mảng `page.blocks` — không lưu riêng.
- **Lưới hàng/cột của bảng có thể sai** (VLM xếp nhầm hàng), dù chữ trong ô đúng.
- **`content` của bảng, `urls` và `role: links` được tính lại mỗi lần NẠP** — không lệch được
  với `cells` / chữ gốc. `content` của bảng không ghi vào `document.json`.

---

## 4. Cờ — BỎ (2026-10-20)

`flags.py` đã xoá. Trang VLM trả JSON hỏng ở ① vẫn thấy qua log `vlm pN: JSON hong -> mat ca trang`
lúc parse; phản hồi thô ở `logs/vlm_pipeline/<doc_id>/pNNN.txt`.

---

## 5. Chạy

```powershell
# ①②b② file gốc -> src\output\parser\<doc_id>\document.json   (gọi VLM; có cache thì không gọi)
.venv\Scripts\python.exe src\offline\parser\cli.py "data\raw\<file>.pdf"
.venv\Scripts\python.exe src\offline\parser\cli.py "data\raw\<file>.pdf" --redo               # gọi lại VLM cả file
.venv\Scripts\python.exe src\offline\parser\cli.py "data\raw\<file>.pdf" --table-pages 21 22  # chỉ chép lại bảng 2 trang
```

```
parser.run(src, pdf)                       parser/base.py
  ① parse_layout     VLM -> DoclingDocument -> ParsedDocument (thứ tự đọc docling, toạ độ [0,1], bỏ header/footer)
  ② post_processing  ②b tables.py chép lại bảng · ② pictures.py xét ảnh -> áp vào doc
                     -> bỏ block rỗng -> sections.py dò chương
parser.save(doc)  -> document.json   (BẢN CUỐI: các bước sau chỉ `ParsedDocument.load`)
```

- Sửa luật dò chương: chạy lại `parser/cli.py` (trúng cache VLM, không tốn API) rồi chạy lại chunker.

---

## 6. Số đã kiểm — chạy lại phải ra đúng, lệch là có lỗi

| | Onboarding Kit | Thời gian làm việc |
|---|---|---|
| trang | 51 | 17 |
| trang có bố cục VLM | 8 (còn 43 trang dùng docling) | 17 |
| block | 281: 111 title · 81 list · 30 body · 12 caption · 5 links · 26 ảnh · 16 bảng | 71: 34 title · 19 list · 14 body · 3 ảnh · 1 bảng |
| block `vlm` | 28 | 5 |
| link ẩn (đo trước 2026-10-12, nay bỏ) | 57 | 1 |
| chương | 5, từ mục lục p2 (p3 · p14 · p37 · p42 · p47) | 5, từ mục lục p2 (p3 · p4 · p6 · p9 · p12) |
| cờ | 21 — 16 `image_not_described` · 4 `empty_page` · 1 `table_empty`, đều ở trang chưa có bố cục VLM | **0** |
| kích thước | 185 KB | 33 KB |
