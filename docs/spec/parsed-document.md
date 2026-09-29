# ParsedDocument — cấu trúc dữ liệu sau khi parse

**Code:** [`src/parsing/`](../../src/parsing/) · **Vào:** `data/raw/<file>.pdf` (hoặc `.pptx` + `.pdf` cùng tên)
· **Ra:** `out/parsed/<doc_id>/document.json` — **một file**, vừa để người đọc vừa để pipeline chạy

---

## 0. Bối cảnh v0 ★

Một file duy nhất vừa là **deck** (robot nói gì) vừa là **KB** (robot tra gì) — xem
[CLAUDE.md §3.0](../../CLAUDE.md). Hệ quả: không có `chart_data`/`tables` chuẩn từ XML, robot
chỉ trả lời ở mức **mô tả slide**. Lối thoát duy nhất là đưa tài liệu nguồn thật vào.

```
data/raw/<file>.pdf                      file gốc
out/parsed/<doc_id>/docling.json         ① docling: chữ + toạ độ + vùng ảnh/bảng      docling_run.py
out/parsed/<doc_id>/layout.json          ② VLM sắp bố cục từng trang (chỉ trỏ id)      layout.py
out/parsed/<doc_id>/document.json        ③ ParsedDocument — FILE NÀY                   build.py
```

`<doc_id>` = tên file bỏ dấu, viết thường: `Thời gian làm việc & Chính sách nhân sự.pdf` →
`thoi_gian_lam_viec_chinh_sach_nhan_su`.

---

## 1. Cấu trúc

```
ParsedDocument
├── doc_id
├── source      {path, sha256}
├── parser      {docling_version, vlm_model, do_ocr, picture_area_threshold, options_hash}
├── sections[]  {id, title, pages: [đầu, cuối], source, confidence}
├── flags[]     {kind, severity, page_no, block_id, detail}
└── pages[]
     ├── page_no · title · section_id · page_hash
     ├── layout_error    chỉ có khi VLM sắp bố cục trượt kiểm tra -> trang dùng block docling
     └── blocks[]        NỘI DUNG, theo thứ tự đọc
          └── id · kind · role · content · hrefs · polygon · provenance  (+ trường riêng từng loại)
```

Header / footer / số trang lặp ở mọi trang **không có trong file** — docling gắn nhãn
`page_header` / `page_footer`, `from_docling` bỏ luôn: không phải nội dung, chương đã lấy từ
mục lục.

### Tầng tài liệu

| field | nghĩa |
|---|---|
| `doc_id` | định danh — chui vào mọi `chunk_id`, tên thư mục `out/parsed/<doc_id>/` |
| `source` | file gốc + hash |
| `parser.vlm_model` | model đã sắp bố cục trang (`layout.json`) — mọi khối `vlm` đến từ nó |
| `sections` | chương — xem dưới |
| `flags` | chỗ người cần xem — §4 |

### Tầng trang

| field | nghĩa |
|---|---|
| `page_no` | số trang thật, từ 1 |
| `title` | tiêu đề trang = khối `role: title` ĐẦU TIÊN (không có thì vắng) |
| `section_id` | trỏ lên `sections[].id`; trang trước chương đầu (bìa, mục lục) thì vắng |
| `page_hash` | hash nội dung + vị trí mọi block — đổi thì kịch bản trang đó viết lại |
| `layout_error` | lý do VLM sắp bố cục trượt kiểm tra (sót chữ / id bịa / bỏ quên vùng) |

### Tầng block — ba câu mọi block đều trả lời

| | field | |
|---|---|---|
| nói gì | `content` | chữ · mô tả hình · markdown bảng — thứ đem đi chunk làm KB |
| nằm đâu | `polygon` | 4 góc `[[l,t],[r,t],[r,b],[l,b]]`, `[0,1]`, gốc **trên-trái**, làm tròn 3 chữ số |
| tin được không | `provenance` | xem bảng dưới |

**`provenance`** (NT2):

| giá trị | nghĩa |
|---|---|
| `text_layer` | chữ đọc thẳng từ text layer PDF — **đúng 100%**. Kể cả khối VLM sắp (`vNN`): VLM chỉ trỏ id, chữ vẫn là chữ docling nguyên văn |
| `vlm` | chữ VLM **tự đọc từ ảnh** (bảng chụp màn hình, dòng chữ nằm trong ảnh) hoặc **mô tả hình** — CÓ THỂ SAI, chỗ người cần soi |
| `manual` | người gõ tay (`data/patches/`) — tin được như `text_layer` |
| `ocr` | đọc từ pixel — chỉ khi bật OCR (mặc định tắt) |

**`id`**: `p<trang>.<loại><số>`, ổn định — chunk và câu kịch bản trỏ vào đây.

| loại | nghĩa |
|---|---|
| `p007.v01` | khối VLM sắp (trang có bố cục VLM) |
| `p007.b03` | khối docling (trang chưa có bố cục VLM, hoặc trượt kiểm tra) |
| `p007.m00` | khối vá tay |

Id không đánh lại khi bỏ block rỗng → có thể nhảy số (`b03` → `b18`).

**Trường riêng theo `kind`:**

| `kind` | `content` là | thêm |
|---|---|---|
| `paragraph` | chữ | `role` (luôn có) · `urls` khi `role: links` |
| `image` | mô tả hình (VLM) | `why_empty` khi `content: null` |
| `table` | markdown **sinh từ** `cells` | `cells` (bản gốc, hàng đầu là header) · `structure_provenance` |

**`role`** — `kind` nói mẩu này **là gì**, `role` nói robot **đối xử với chữ đó thế nào**:

| `role` | nghĩa |
|---|---|
| `title` | tiêu đề trang / tiêu đề mục / nhãn một nhóm |
| `body` | đoạn văn thường |
| `list` | bó gạch đầu dòng, MỘT block, các dòng ngăn bằng xuống dòng — S4 diễn đạt lại, không đọc bullet |
| `links` | ≥ nửa số dòng là link (URL in ra chữ, hoặc chữ có link ẩn) — **không đọc URL thành tiếng**. Chỉ body/list mới đổi; title có link vẫn là title. Luật, tự gán cả lúc nạp lại |
| `caption` | chú thích |

**`hrefs`** — link ẨN sau chữ, đọc thẳng từ annotation PDF (`links.py`), gắn vào block chồng
lên vùng link. Mỗi link kèm chữ nằm dưới nó — một block list có thể mang nhiều link:

```json
"hrefs": [{"text": "Cẩm nang phân quyền", "url": "https://camnangtt.vingroup.net/..."}]
```

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

SUY RA chứ không parse ra — nên bắt buộc khai `source` + `confidence`. Nguồn duy nhất là
**trang mục lục** (`source: outline_page`; `manual` dành cho người sửa tay):

- Tìm trong 5 trang đầu một trang có ≥ 3 dòng mà ≥ 60% dòng tìm được trang mở chương phía sau
  — không dựa vào chữ "Nội dung", chính các trang sau xác nhận đó là mục lục.
- Đọc tên chương, dò tiêu đề các trang phía sau: trang ĐẦU TIÊN có tiêu đề khớp một mục là
  trang bắt đầu chương, kéo tới trước trang bắt đầu chương kế tiếp.
- Khớp: bỏ số thứ tự ("01.", "4."), không phân biệt hoa thường, tiêu đề CHỨA tên mục cũng
  tính ("CHẾ ĐỘ LƯƠNG THƯỞNG" ∋ "LƯƠNG THƯỞNG").
- `confidence` = tỉ lệ mục khớp. Trang trước chương đầu (bìa, mục lục) không có `section_id`.

Không có mục lục khớp → `sections: []` + cờ `no_sections`. KHÔNG đoán.

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
      "id": "p007.v00", "kind": "paragraph", "role": "title",
      "content": "CÁCH TÍNH LÀM THÊM GIỜ",
      "polygon": [[0.499, 0.094], [0.843, 0.094], [0.843, 0.141], [0.499, 0.141]],
      "provenance": "text_layer"
    },
    {
      "id": "p007.v01", "kind": "table",
      "content": "|  | GIỜ LÀM THÊM BAN NGÀY | GIỜ LÀM THÊM BAN ĐÊM |\n|---|---|---|\n| Ngày thường | Hệ số 1.5 | Hệ số 2.1 |\n...",
      "cells": [["", "GIỜ LÀM THÊM BAN NGÀY", "GIỜ LÀM THÊM BAN ĐÊM"], ["Ngày thường", "Hệ số 1.5", "Hệ số 2.1"], "..."],
      "polygon": [[0.407, 0.287], [0.96, 0.287], [0.96, 0.534], [0.407, 0.534]],
      "provenance": "vlm", "structure_provenance": "vlm"
    },
    {
      "id": "p007.v02", "kind": "paragraph", "role": "list",
      "content": "Đảm bảo số giờ làm thêm không quá 40 giờ trong 01 tháng, không quá 200 giờ trong 01 năm\nGiờ làm việc ban đêm được tính từ 22 giờ đến 06 giờ sáng ngày hôm sau.",
      "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
      "provenance": "vlm"
    }
  ]
}
```

Khối `v02` không trỏ mẩu chữ docling nào, cũng không có vùng nguồn → không biết nằm đâu →
`polygon` là cả trang. Số trong khối `vlm` phải được người đối chiếu với slide ở S7.

**Onboarding Kit, `sections`** — dựng từ mục lục p2, 5/5 mục khớp:

```json
"sections": [
  {"id": "sec_00", "title": "Các thủ tục, quy trình, quy định, thông tin chung", "pages": [3, 13], "source": "outline_page", "confidence": 1.0},
  {"id": "sec_01", "title": "Nhân sự", "pages": [14, 36], "source": "outline_page", "confidence": 1.0},
  "..."
]
```

---

## 3. Trong code

```python
from parsing.models import ParsedDocument

doc = ParsedDocument.load("out/parsed/onboarding_kit/document.json")   # file cũ -> báo 1 câu
page = doc.page(10)
doc.section_of(10)                          # chương của trang

for b in page.blocks:
    b.content, b.polygon, b.provenance, b.hrefs   # field lưu
    b.box, b.center, b.area                       # tính từ polygon

page.paragraphs · page.images · page.tables # lọc theo loại
page.slide_type                             # loại trang — tính, không lưu
page.compute_hash()                         # nội dung + vị trí

doc.to_json()                               # đúng định dạng ghi ra file
```

Luật dễ quên:

- **Không có `page_no` / `reading_order` / `layer` trong block.** Block nằm trong trang nào là
  biết trang; thứ tự đọc là thứ tự trong mảng.
- **Bảng có hai `provenance`**: chữ trong ô (`provenance`) và lưới hàng/cột
  (`structure_provenance`, do TableFormer hoặc VLM dựng — có thể xếp nhầm hàng).
- **`content` của bảng, `urls` và `role: links` được tính lại mỗi lần NẠP** — không lệch được
  với `cells` / chữ gốc / `hrefs`.
- **Toạ độ `.pptx`**: docling gắn nhãn `BOTTOMLEFT` nhưng số thật đo từ ĐỈNH — `from_docling`
  không tin nhãn với `.pptx`.

---

## 4. Cờ

| `kind` | mức | nghĩa |
|---|---|---|
| `empty_page` | error | không chữ, không mô tả ảnh → vào KB gần như rỗng. Không bắn ở trang `section_divider` |
| `image_not_described` | error | ảnh đủ to không có mô tả (trang chưa có bố cục VLM) |
| `table_empty` | error | docling khoanh được bảng mà 0 ô có chữ — thường là ảnh chụp bảng. Trang có bố cục VLM thì VLM đọc bảng từ ảnh |
| `layout_failed` | warn | VLM sắp bố cục trượt kiểm tra → trang dùng docling. Gọi lại: `run <file> --pages N` |
| `no_sections` | info | không tìm thấy trang mục lục khớp → không có chương |

---

## 5. Chạy

```powershell
# ①②③ file gốc -> out\parsed\<doc_id>\document.json   (② gọi VLM, 1 lần/trang, có cache)
.venv\Scripts\python.exe src\parsing\cli.py run "data\raw\<file>.pdf"
.venv\Scripts\python.exe src\parsing\cli.py run "data\raw\<file>.pdf" --pages 7   # VLM 1 trang
.venv\Scripts\python.exe src\parsing\cli.py run "data\raw\<file>.pdf" --no-vlm    # không gọi API

# xem
.venv\Scripts\python.exe src\parsing\cli.py show <doc_id> --page 9-11
```

```
docling.json ─► from_docling.py   theo body.children → thứ tự đọc; đổi toạ độ; bỏ header/footer lặp
layout.json  ─► layout.py         trang có bố cục VLM hợp lệ -> thay block (trượt -> giữ docling + cờ)
PDF gốc      ─► links.py          link ẩn -> hrefs; block >= nửa dòng là link -> role "links"
             ─► sections.py       trang mục lục → chương
             ─► patch.py          vá tay (nếu có)
             ─► flags.py          soi luật → cờ; block rỗng không cờ bị bỏ
             ─► build.py          ghi document.json  ─► S5 KB · S4 kịch bản
```

- ③ tự áp `data\patches\<doc_id>.json` nếu có. `document.json` luôn dựng mới từ `docling.json`
  nên vá không bị áp chồng.
- `run` thoát mã `1` khi có cờ mức `error` — vẫn ghi file, mã lỗi để CI bắt.

---

## 6. Số đã kiểm — chạy lại phải ra đúng, lệch là có lỗi

| | Onboarding Kit | Thời gian làm việc |
|---|---|---|
| trang | 51 | 17 |
| trang có bố cục VLM | 8 (còn 43 trang dùng docling) | 17 |
| block | 281: 111 title · 81 list · 30 body · 12 caption · 5 links · 26 ảnh · 16 bảng | 71: 34 title · 19 list · 14 body · 3 ảnh · 1 bảng |
| block `vlm` | 28 | 5 |
| link ẩn | 57 | 1 |
| chương | 5, từ mục lục p2 (p3 · p14 · p37 · p42 · p47) | 5, từ mục lục p2 (p3 · p4 · p6 · p9 · p12) |
| cờ | 21 — 16 `image_not_described` · 4 `empty_page` · 1 `table_empty`, đều ở trang chưa có bố cục VLM | **0** |
| kích thước | 185 KB | 33 KB |
