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

Đặt file vào `data/raw/<ten>.pptx` (hoặc `.pdf`), rồi:

```powershell
# ① docling + VLM mô tả ảnh                                  -> out\parse_api\<ten>.json      [tốn API]
.venv\Scripts\python.exe scripts\parse_api.py data\raw\<ten>.pptx

# ② ra cấu trúc của mình                                     -> out\parsed\<ten>.json
.venv\Scripts\python.exe src\parsing\cli.py out\parse_api\<ten>.json -o out\parsed\<ten>.json

# ③ chunk + nhúng vector                                     -> out\kb\<ten>.chunks.json + .vectors.npy
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>.json -o out\kb\<ten>.chunks.json --embed

# ③b bảng phát âm (nháp)                                     -> out\deck\<ten>\pronunciation.json
.venv\Scripts\python.exe scripts\extract_terms.py out\kb\<ten>.chunks.json
#     MỞ FILE RA SỬA: xoá từ robot không nói ra miệng, sửa "say", đổi by "auto" -> "nguoi"

# ④ viết kịch bản                                            -> out\deck\<ten>\scenario.json  [tốn API]
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>.json

# ④b xuất kịch bản ra markdown để đọc                        -> out\deck\<ten>\scenario.md
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>.json --md
```

Ghi chú:

- File `.pdf` thì ở ① thay `.pptx` bằng `.pdf`. Tên file có dấu cách thì bọc trong ngoặc kép:
  `"out\parse_api\3_DataVisualization (1).json"`.
- ② tự áp file vá tay `data\patches\<ten>.json` nếu có.
- Deck ảnh chỉ để trang trí: ghi `"vlm_pages": [15, 31, 41]` vào file vá **trước khi chạy ①**
  → ① chỉ gửi ảnh các trang đó cho VLM, ② ghi ảnh trang khác là `skipped` (không bắn cờ đỏ).
  Không có key = tả mọi ảnh. Ví dụ: `data\patches\onboarding_kit.json`.
- ② thoát mã `1` khi có cờ mức `error` — vẫn ghi file bình thường, mã lỗi để CI bắt.
- ③ chữ không đổi thì lấy vector từ cache, không gọi API.
- ③b chạy lại **không mất** mục người đã duyệt (`by: "nguoi"`), chỉ ghi đè mục `auto`.
- ④ chỉ viết lại trang có `page_hash` đổi. Câu người đã sửa tay (`edited_by: "nguoi"`)
  không bao giờ bị ghi đè.

---

## B. Đọc kết quả

### Nội dung một trang

```powershell
.venv\Scripts\python.exe src\parsing\cli.py out\parsed\<ten>.json --page 2           # một trang
.venv\Scripts\python.exe src\parsing\cli.py out\parsed\<ten>.json --page 2-5 --full  # nhiều trang, không cắt chữ
```

```
--- trang 2 | (khong tieu de) | chuong: —
    hash=2adf6b0331dbcc89  starved=False
    p002.b00   para/body      5.35% text_layer Khởi Nguồn Nam Mới
    p002.b01   para/body     11.90% text_layer Mùng 1
    ...
```

Mỗi dòng: `id` · loại · % diện tích trang · nguồn · nội dung.
`--page` nhận `11` · `9,11` · `9-15`.

Xem cả deck bằng mắt: mở thẳng `out\parsed\<ten>.json` trong VS Code — file đã gọn, đọc được.

### Kịch bản

```powershell
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>.json --show            # cả deck
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>.json --show --page 2   # một trang
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

Đọc cho dễ: mở `out\deck\<ten>\scenario.md` (sinh bằng lệnh ④b).

### Chunk trong KB

```powershell
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>.json --page 2 --full   # chunk của trang 2
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>.json --stats           # thống kê cả KB
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
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>.json --page 2,5 --force

# xem prompt sẽ gửi LLM, không gọi API
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>.json --page 2 --dry-run

```

| Vừa sửa | Chạy lại từ |
|---|---|
| file slide gốc | ① |
| `data\patches\<ten>.json` (vá tay) | ② → ③ → ④ |
| `pronunciation.json` | ④ — trang không đổi chỉ được đếm lại âm tiết, không gọi LLM |
| `prompts\s4_scenario.md` hoặc `LLM_MODEL` | ④ — tự nhận ra prompt/model đổi, viết lại mọi trang |

`--force` chỉ cần khi muốn viết lại dù không có gì đổi (ví dụ thử lại cho câu hay hơn).
