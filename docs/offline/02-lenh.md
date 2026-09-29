# Nhánh offline — lệnh dùng hằng ngày

Mọi lệnh chạy ở thư mục gốc repo, trong PowerShell, bằng Python của `.venv`.
Luồng và cấu trúc dữ liệu: [01-hien-trang.md](./01-hien-trang.md).

Cài môi trường lần đầu (Python 3.12, bản thư viện ghim trong `requirements.txt`):

```powershell
uv venv --python 3.12
uv pip install -r requirements.txt
```

Mỗi terminal mới:

```powershell
$env:PYTHONIOENCODING = "utf-8"      # không có thì lỗi in chữ Việt
```

`.env` cần có `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `VLM_MODEL`, `LLM_MODEL`.

Trong các lệnh dưới, thay `<ten>` bằng tên deck, ví dụ `tetnguyendan`.

---

## A. Xử lý file raw — chạy theo thứ tự

Đặt file vào `data/raw/` (`.pdf`, hoặc `.pptx` kèm bản `.pdf` CÙNG TÊN để có ảnh trang), rồi:

```powershell
# ①②③ đọc file -> out\parsed\<ten>\{docling.json, layout.json, document.json}      [② tốn API]
.venv\Scripts\python.exe src\parsing\cli.py run "data\raw\<file>.pdf"

# ④ chunk + nhúng vector                                     -> out\kb\<ten>.chunks.json + .vectors.npy
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>\document.json -o out\kb\<ten>.chunks.json --embed

# ④b bảng phát âm (nháp)                                     -> out\deck\<ten>\pronunciation.json
.venv\Scripts\python.exe scripts\extract_terms.py out\kb\<ten>.chunks.json
#     MỞ FILE RA SỬA: xoá từ robot không nói ra miệng, sửa "say", đổi by "auto" -> "nguoi"

# ⑤ viết kịch bản                                            -> out\deck\<ten>\scenario.json  [tốn API]
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json

# ⑤b xuất kịch bản ra markdown để đọc                        -> out\deck\<ten>\scenario.md
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --md
```

Ghi chú:

- `<ten>` = tên file bỏ dấu, viết thường: `Thời gian làm việc….pdf` -> `thoi_gian_lam_viec_…`.
  Tên file có dấu cách thì bọc trong ngoặc kép.
- Bên trong `run`:

  | Bước | Ra | Tốn | Bỏ qua khi |
  |---|---|---|---|
  | ① docling: chữ + toạ độ + vùng ảnh/bảng | `docling.json` | ~2s/trang CPU | đã có file (trừ `--redo`) |
  | ② VLM nhìn cả trang, sắp chữ thành khối | `layout.json` | 1 lần gọi/trang | trang đã có và không đổi gì |
  | ③ ghép + kiểm + link + vá tay + cờ | `document.json` | không | không bao giờ — luôn dựng lại |

- Thử trước vài trang cho đỡ tốn: `run "<file>" --pages 7,10` — CHỈ gọi VLM các trang đó,
  trang khác dùng block docling. `--no-vlm` = không gọi API, dùng `layout.json` sẵn có.
- VLM chỉ SẮP XẾP, chữ vẫn lấy nguyên văn từ docling. Trang VLM sắp sai (sót chữ, bịa id, bỏ
  quên vùng) tự quay về docling + cờ `layout_failed`. Gọi lại: `run "<file>" --pages N`.
- ③ tự áp file vá tay `data\patches\<ten>.json` nếu có.
- `run` thoát mã `1` khi có cờ mức `error` — vẫn ghi file bình thường, mã lỗi để CI bắt.
- ④ chữ không đổi thì lấy vector từ cache, không gọi API.
- ④b chạy lại **không mất** mục người đã duyệt (`by: "nguoi"`), chỉ ghi đè mục `auto`.
- ⑤ chỉ viết lại trang có `page_hash` đổi. Câu người đã sửa tay (`edited_by: "nguoi"`)
  không bao giờ bị ghi đè.

---

## B. Đọc kết quả

### Nội dung một trang

```powershell
.venv\Scripts\python.exe src\parsing\cli.py show <ten>                       # tổng quan + cờ
.venv\Scripts\python.exe src\parsing\cli.py show <ten> --page 2              # một trang
.venv\Scripts\python.exe src\parsing\cli.py show <ten> --page 2-5 --full     # nhiều trang, không cắt chữ
```

```
--- trang 10 | Chuyển đổi xanh | chuong: —
    hash=af7f323b820c6106  slide_type=content
    p010.v01   para/title     0.94% text_layer CBNV KÝ HĐLĐ CHÍNH THỨC
    p010.v02   para/list     20.33% text_layer CBNV không sử dụng xe xăng … ⏎ Nếu sử dụng ô tô/xe máy…
    p020.v05   table          4.63% vlm        | CÁCH TÍNH: | GIỜ LÀM THÊM BAN NGÀY | …
               ↳ link Cẩm nang phân quyền -> https://camnangtt.vingroup.net/…
```

Mỗi dòng: `id` · loại · % diện tích trang · nguồn · nội dung. `vNN` = khối VLM sắp, `bNN` =
khối docling (trang chưa có bố cục VLM). Nguồn `vlm` = chữ VLM tự đọc từ ảnh — chỗ cần soi.
`--page` nhận `11` · `9,11` · `9-15`.

Xem cả deck bằng mắt: mở thẳng `out\parsed\<ten>\document.json` trong VS Code — file đã gọn, đọc được.

### Kịch bản

```powershell
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --show            # cả deck
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --show --page 2   # một trang
```

```
--- trang 2 · content · 4 câu · 42 âm tiết · ~13s · pass2  CỜ: monotone_rhythm
    [delivery  8] Vậy, mốc nào quan trọng trong dịp này?
    [content  10] Tháng Giêng âm lịch là tên gọi của tháng này.  → p002.b02
```

- `[content 10]` = câu mang thông tin, 10 âm tiết · `→ p002.b02` = block làm nguồn
- `[delivery 8]` = câu dẫn dắt, không mang thông tin mới
- `pass2` = lần đầu viết bị trượt kiểm tra, đã sửa một lần — đáng soi
- `CỜ:` = lỗi cần xem. Cuối bảng có tổng: thời lượng, tỉ lệ câu thiếu nguồn, cờ đỏ

Đọc cho dễ: mở `out\deck\<ten>\scenario.md` (sinh bằng lệnh ⑤b).

### Chunk trong KB

```powershell
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>\document.json --page 2 --full   # chunk của trang 2
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>\document.json --stats           # thống kê cả KB
```

> Không thêm `-o` khi chỉ muốn xem — có `-o` là ghi đè file KB.

---

## C. Thử tìm kiếm

```powershell
.venv\Scripts\python.exe scripts\try_search.py <ten> "lì xì là gì"      # ra trang + các block của trang
.venv\Scripts\python.exe scripts\try_search.py <ten>                    # hỏi liên tục, Enter trống để thoát
.venv\Scripts\python.exe scripts\try_search.py <ten> "savefig" --bm25   # chỉ BM25, không gọi API

.venv\Scripts\python.exe src\kb\search.py out\kb\<ten>.chunks.json "lì xì" -k 3 --explain
```

`--explain` in hạng của từng nhánh — `dense hang 7 | bm25 hang 2` là biết kết quả do nhánh
nào kéo lên.

---

## D. Đo chất lượng

```powershell
# bộ câu hỏi có nhãn (data\eval\queries.json) — số đo THẬT
.venv\Scripts\python.exe src\kb\eval.py out\kb\<ten>.chunks.json

# self-retrieval — gần như luôn 100%, chỉ chứng minh không có 2 chunk trùng nhau
.venv\Scripts\python.exe src\kb\audit.py out\kb\<ten>.chunks.json --mode hybrid
```

---

## E. Chạy lại một phần

```powershell
# viết lại kịch bản vài trang, kể cả khi trang không đổi
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --page 2,5 --force

# xem prompt sẽ gửi LLM, không gọi API
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --page 2 --dry-run

```

| Vừa sửa | Chạy lại từ |
|---|---|
| file slide gốc | `run --redo` rồi ④ → ⑤ |
| `data\patches\<ten>.json` (vá tay) | `run --no-vlm` → ④ → ⑤ |
| `pronunciation.json` | ⑤ — trang không đổi chỉ được đếm lại âm tiết, không gọi LLM |
| `prompts\s4_scenario.md` hoặc `LLM_MODEL` | ⑤ — tự nhận ra prompt/model đổi, viết lại mọi trang |

`--force` chỉ cần khi muốn viết lại dù không có gì đổi (ví dụ thử lại cho câu hay hơn).
