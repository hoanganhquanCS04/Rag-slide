# Nhánh offline — lệnh dùng hằng ngày

Mọi lệnh chạy ở thư mục gốc repo, bằng Python của `.venv`. Lệnh `bash …` chạy trong Git
Bash, lệnh `.venv\Scripts\python.exe …` chạy trong PowerShell (Git Bash thì đổi `\` thành `/`).
Luồng và cấu trúc dữ liệu: [01-hien-trang.md](./01-hien-trang.md).

Cài môi trường lần đầu (Python 3.12, bản thư viện ghim trong `requirements.txt`):

```powershell
uv venv --python 3.12
uv pip install -r requirements.txt
```

Mỗi terminal PowerShell mới (các script `.sh` tự đặt sẵn):

```powershell
$env:PYTHONIOENCODING = "utf-8"      # không có thì lỗi in chữ Việt
```

`.env` cần có:

| Biến | Ví dụ | Dùng ở |
|---|---|---|
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | `https://api.yescale.io/v1` | mọi lần gọi API (VLM, LLM, embedding) |
| `VLM_MODEL` | `gemma4` | ① VlmPipeline đọc cả trang: bố cục + chữ + bảng + mô tả ảnh |
| `VLM_BASE_URL`, `VLM_SOFT_TOKENS` | `http://localhost:8000/v1`, `1120` | cổng VLM riêng (Gemma local qua vLLM) |
| `LLM_MODEL` | `gpt-5-mini` | S4 kịch bản, hỏi đáp |
| `EMBED_MODEL` | `text-embedding-3-small` | ④ nhúng vector, tìm kiếm |
| `VECTOR_DB` | `chroma` hoặc `inmem` | ④ + tìm kiếm — kho vector (`src\components\vectordb\`), bỏ trống = `inmem` |
| `CHROMA_PATH` | `src/output/vectordb/chroma` | chỗ Chroma ghi đĩa (bỏ trống = giá trị này) |

Trong các lệnh dưới, `<ten>` = tên file bỏ dấu, viết thường:
`Thời gian làm việc & Chính sách nhân sự.pdf` → `thoi_gian_lam_viec_chinh_sach_nhan_su`.
Tên file có dấu cách thì bọc trong ngoặc kép.

---

## A. Xử lý file raw

Đặt file vào `data/raw/` (`.pdf`, hoặc `.pptx` kèm bản `.pdf` CÙNG TÊN để có ảnh trang —
xuất bằng `scripts\pptx2pdf.ps1`).

**Hai lệnh là đủ.** Lệnh 1 làm hết phần offline: parse → chunk + nhúng → kịch
bản (S4). Bước nào có cache thì không tốn API — chạy lại thoải mái:

```bash
bash scripts/run_deck.sh "data/raw/<file>.pdf"               # -> document.json, chunk vào kho Chroma, scenario.json
bash scripts/run_deck.sh "data/raw/<file>.pdf" --redo        # gọi lại VLM cả file (mặc định dùng cache)
bash scripts/run_deck.sh "data/raw/<file>.pdf" --eval        # thêm bước đo bộ câu hỏi có nhãn -> src/output/evaluation/<ten>/eval.json
```

Lệnh 2 — hỏi đáp, hỏi bao nhiêu câu cũng được, **Ctrl+C** để thoát. Mỗi câu in log từng bước
(chunk tìm được + hạng dense/BM25, đoạn vào prompt, LLM trả thô, code kiểm); `-q` chỉ in câu trả
lời, `--prompt` in nguyên văn prompt:

```powershell
.venv\Scripts\python.exe scripts\try_ask.py <ten>
.venv\Scripts\python.exe scripts\try_ask.py <ten> "nghỉ phép năm được mấy ngày"
```

Từng bước riêng lẻ (chính là những gì `run_deck.sh` gọi):

```powershell
# ①③ đọc file -> src\output\parser\<ten>\{docling_vlmpipeline.json, document.json}   [① tốn API]
.venv\Scripts\python.exe src\offline\parser\cli.py "data\raw\<file>.pdf"

# ④ chunk -> nhúng vector -> ghi thẳng kho src\output\vectordb\chroma\  [tốn API nếu chữ đổi]
.venv\Scripts\python.exe src\offline\chunker\cli.py src\output\parser\<ten>\document.json
# chỉ xem chunk (không nhúng, không ghi kho)
.venv\Scripts\python.exe src\offline\chunker\cli.py src\output\parser\<ten>\document.json --dry-run --page 19
```

Bên trong `parser\cli.py`:

| Bước | Ra | Tốn | Bỏ qua khi |
|---|---|---|---|
| ① docling VlmPipeline: VLM đọc cả trang — bố cục + chữ + bảng (HTML) + mô tả ảnh | `docling_vlmpipeline.json` (raw: `logs\vlm_pipeline\<ten>\`) | 1 lần gọi/trang | đã có file (trừ `--redo`) |
| ③ link + chương + cờ (enrich) | trong bộ nhớ | không | không bao giờ — tính lại mỗi lần nạp |

Ghi chú:

- ① chạy CẢ file (VlmPipeline không chạy từng trang lẻ); cache là cả file `docling_vlmpipeline.json`.
  Sửa `src\prompts\s0_vlm_layout_prompt.md` hoặc `VLM_MODEL` KHÔNG tự gọi lại — phải `run "<file>" --redo`.
- Mọi chữ do VLM đọc (`vlm`), KHÔNG đối chiếu text layer PDF — chữ sai chỉ thấy khi người soi
  (ảnh khung `--draw-only`, `show --page N --full`).
- Log ① `vlm pN: ...` = đã nắn khung nhiều dòng / bỏ Markdown. Trang hỏng JSON thành trang rỗng
  + cờ `empty_page`: xem phản hồi thô `logs\vlm_pipeline\<ten>\pNNN.txt`.
- Xem khung vẽ lên trang: `python scripts\try_docling_gemma.py "<file>" --draw-only` ->
  `src\output\parser\<ten>\boxes\pages\`.
- `run` thoát mã `3` khi có cờ mức `error` — vẫn ghi file bình thường, mã lỗi để CI bắt.
  `run_deck.sh` gặp mã 3 vẫn chạy tiếp; mã khác (không thấy file, thiếu khoá API…) thì DỪNG.
- ④ chữ không đổi thì lấy vector từ cache (`src\output\embedders\cache\`), không gọi API.
- ④ KHÔNG ghi file chunk nào: chunk nằm trong bộ nhớ rồi vào thẳng Chroma (chữ + vector + metadata).
  Cuối lệnh in `kho chroma (kb__<model>): nap N ban ghi, xoa M ban ghi cu`. Mọi tài liệu chung MỘT
  collection, tách bằng `doc_id`. `--dry-run` thì chỉ in chunk, không nhúng, không ghi.
- Kho là nơi DUY NHẤT chứa chunk. Xoá `src\output\vectordb\chroma\` thì chạy lại ④ — cache nhúng có
  sẵn nên **không gọi API**.

### Kịch bản (S4) — bước 4 của `run_deck.sh`

`run_deck.sh` gọi lệnh đầu tiên dưới đây sau bước index (`--no-scenario` để bỏ). Tốn ~1–2 lần gọi
LLM mỗi trang ĐỔI — trang không đổi thì giữ nguyên. Chạy riêng khi chỉ muốn làm kịch bản:

```powershell
# viết kịch bản                       -> src\output\scenario\<ten>\scenario.json + scenario.md   [tốn API]
.venv\Scripts\python.exe src\offline\scenario\cli.py src\output\parser\<ten>\document.json

# chỉ ghi lại scenario.md (sau khi sửa tay scenario.json), không gọi API
.venv\Scripts\python.exe src\offline\scenario\cli.py src\output\parser\<ten>\document.json --md

# bảng phát âm — gom từ kịch bản NÓI mà kho chưa có -> data\pronunciation.json (KHO CHUNG)
.venv\Scripts\python.exe scripts\extract_terms.py src\output\scenario\<ten>\scenario.json
#     MỞ FILE RA SỬA "say", đổi by "auto" -> "nguoi", rồi chạy lại S4 (chỉ đếm lại, không gọi LLM)
```

- S4 chỉ viết lại trang có `page_hash`, prompt hoặc model đổi. Lưu dần sau MỖI trang.
- Sửa tay `scenario.json` sẽ bị máy viết đè khi trang / prompt / model đổi hoặc chạy `--force` (không còn `edited_by`).
- `extract_terms.py` chạy lại **không mất** mục người đã duyệt (`by: "nguoi"`), chỉ thêm mục mới.

---

## B. Đọc kết quả

### Nội dung một trang

```powershell
.venv\Scripts\python.exe src\offline\parser\show.py <ten>                       # tổng quan + chương + cờ
.venv\Scripts\python.exe src\offline\parser\show.py <ten> --page 7              # một trang
.venv\Scripts\python.exe src\offline\parser\show.py <ten> --page 2 3 4 5 --full     # nhiều trang, không cắt chữ
```

```
--- trang 7 | CÁCH TÍNH LÀM THÊM GIỜ | chuong: sec_02 (LÀM THÊM GIỜ, p6-8)
    hash=b56b4b44a58afe10  slide_type=content
    p007.b00   para/title     1.62% CÁCH TÍNH LÀM THÊM GIỜ
    p007.b01   table         13.66% |  | GIỜ LÀM THÊM BAN NGÀY | GIỜ LÀM THÊM BAN ĐÊM | ⏎ |---|---|---| ⏎ | …
    p007.b02   para/list    100.00% Đảm bảo số giờ làm thêm không quá 40 giờ trong 01 tháng, không quá 200 g…
```

Mỗi dòng: `id` · loại · % diện tích trang · nội dung. `pNNN.bNN` = trang NNN, khối thứ NN theo thứ
tự đọc (số có thể nhảy: ảnh trang trí đã bị bỏ). Mọi chữ đều do VLM đọc từ ảnh
— chỗ cần soi.
`--page` nhận một hoặc nhiều số cách nhau bằng dấu cách: `11` · `9 11` · `{9..15}` (bash tự bung dải).

Xem cả deck bằng mắt: mở thẳng `src\output\parser\<ten>\document.json` trong VS Code — file đã gọn, đọc được.

### Chunk trong KB

```powershell
.venv\Scripts\python.exe src\offline\chunker\cli.py src\output\parser\<ten>\document.json --page 9 --full   # chunk của trang 9
.venv\Scripts\python.exe src\offline\chunker\cli.py src\output\parser\<ten>\document.json --stats           # thống kê cả KB
```

```
  thoi_gian_lam_viec_chinh_sach_nhan_su#p009.1 page   265tok  vlm=  0%  [LƯƠNG THƯỞNG · 4. CHẾ ĐỘ LƯƠNG THƯỞNG · trang 9/17] …
  thoi_gian_lam_viec_chinh_sach_nhan_su#p009.2 page   279tok  vlm=  0%  [LƯƠNG THƯỞNG · 4. CHẾ ĐỘ LƯƠNG THƯỞNG · trang 9/17] 3 Thưởng …
```

Đầu dòng `✗` = chunk trang phân mục, lọc khỏi tìm kiếm nội dung.

> Không thêm `-o` khi chỉ muốn xem — có `-o` là ghi đè file KB.

### Kịch bản

```powershell
.venv\Scripts\python.exe src\offline\scenario\cli.py src\output\parser\<ten>\document.json --show            # cả deck
.venv\Scripts\python.exe src\offline\scenario\cli.py src\output\parser\<ten>\document.json --show --page 7   # một trang
```

```
--- trang 7 · content · 6 câu · 112 âm tiết · ~34s · pass2  CỜ: too_long,vlm_number
    [delivery  6] Cách tính cụ thể ra sao?
    [content  19] Làm thêm ngày lễ tết tính hệ số 3 ban ngày và hệ số 3.9 ban đêm.  → p007.b01
```

- `[content 19]` = câu mang thông tin, 19 âm tiết · `→ p007.b01` = block làm nguồn
- `[delivery 6]` = câu dẫn dắt, không mang thông tin mới
- `pass2` = lần đầu viết bị trượt bộ kiểm, đã sửa một lần — đáng soi
- `CỜ:` = lỗi cần xem. Cuối bảng có tổng: thời lượng, tỉ lệ câu thiếu nguồn, cờ đỏ

Đọc cho dễ: mở `src\output\scenario\<ten>\scenario.md` (Ctrl+Shift+V trong VS Code).

---

## C. Thử tìm kiếm

```bash
python scripts/try_ask.py <ten> "làm thêm ngày lễ hệ số bao nhiêu"     # log [1] in hạng dense / BM25 từng trang
python src/evaluation/eval.py <ten> --mode sparse                      # chỉ BM25, không gọi API
```

Hỏi đáp trọn vòng (tìm → LLM → code kiểm nguồn): `scripts\try_ask.py`, xem mục A.

---

## D. Đo chất lượng

```powershell
# bộ câu hỏi có nhãn — số đo THẬT. Chạy cả dense / sparse / hybrid (đọc kho Chroma)
.venv\Scripts\python.exe src\evaluation\eval.py <ten>
# chỉ BM25 — không gọi API, chạy được khi không có mạng / khoá
.venv\Scripts\python.exe src\evaluation\eval.py <ten> --mode sparse
# thử cấu hình gộp khác (mặc định = hằng số của utils/ensemble_search.py: K=10, dense 1.5 : sparse 1)
.venv\Scripts\python.exe src\evaluation\eval.py <ten> --rrf-k 7 --wd 1.25
```

```bash
# quét K + trọng số dense/sparse của RRF
bash scripts/tune.sh <ten>
bash scripts/tune.sh <ten> --k 5,10,20 --wd 1,1.5,2
```

- `eval.py`, `tune.sh`, `run_deck.sh --eval` không truyền `--queries` thì cùng lấy
  `data\eval\<ten>.queries.json` (không có thì bộ chung `queries.json` của `3_datavisualization`).
  Dòng `===` đầu log in tên file câu hỏi đang dùng.
- `tune.sh` ra ba file trong `src\output\evaluation\<ten>\`:
  `tune.md` (người đọc) · `tune.json` (máy đọc) · `tune_misses.json` (câu trượt
  top-5 của cấu hình tốt nhất, cùng dạng file câu hỏi — đưa thẳng vào `--queries` được).
- `tune.sh` lấy câu hỏi ở `data\eval\<ten>.queries.json` (không có thì `queries.json`, đổi bằng
  `--queries`). Đo cách chunk khác mà không đè kho thật: `CHROMA_PATH=<thư mục khác>` cho cả ④ lẫn tune.
  Chỉ ĐO, không đổi mặc định của `utils/ensemble_search.py`. Thoát mã 1 khi kho vector trả pool lệch — chạy
  lại. Chi tiết: `docs\spec\search.md` §4.
- Toàn bộ câu eval hiện do AI viết rồi tự chấm — số thiên vị, log in `CANH BAO`.

---

## E. Chạy lại một phần

```powershell
# viết lại kịch bản vài trang, kể cả khi trang không đổi
.venv\Scripts\python.exe src\offline\scenario\cli.py src\output\parser\<ten>\document.json --page 2 5 --force

# xem prompt sẽ gửi LLM, không gọi API
.venv\Scripts\python.exe src\offline\scenario\cli.py src\output\parser\<ten>\document.json --page 2 --dry-run
```

| Vừa sửa | Chạy lại |
|---|---|
| file slide gốc | `run_deck.sh "<file>" --redo` (→ S4 nếu cần kịch bản) |
| `src\prompts\s0_vlm_layout_prompt.md` / `VLM_MODEL` | `run_deck.sh "<file>" --redo` — gọi lại VLM cả file |
| luật chunk (`src\offline\chunker\`) | `run_deck.sh "<file>"` — chỉ chunk có chữ đổi mới gọi API nhúng |
| `EMBED_MODEL` | `run_deck.sh` — nhúng lại TOÀN BỘ, ra collection mới mang tên model mới |
| xoá `src\output\vectordb\chroma\` | `run_deck.sh "<file>"` (hoặc chỉ ④) — dựng lại kho từ cache nhúng, 0 lần gọi API |
| `data\pronunciation.json` | S4 — trang không đổi chỉ được đếm lại âm tiết, không gọi LLM |
| `src\prompts\s4_scenario.md` hoặc `LLM_MODEL` | S4 — tự nhận ra prompt/model đổi, viết lại mọi trang |

`--force` chỉ cần khi muốn viết lại dù không có gì đổi (ví dụ thử lại cho câu hay hơn).
