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
| `VLM_MODEL` | `gemini-3.5-flash-lite` | ② bố cục trang |
| `TABLE_MODEL` | `gemini-3.8-flash` | ②b chép bảng từ ảnh bảng — bảng ô gộp cần model mạnh hơn |
| `LLM_MODEL` | `gpt-5-mini` | S4 kịch bản, hỏi đáp |
| `EMBED_MODEL` | `text-embedding-3-small` | ④ nhúng vector, tìm kiếm |
| `VECTOR_DB` | `chroma` hoặc `inmem` | ④ + tìm kiếm — kho vector (`src\kb\store\`), bỏ trống = `inmem` |
| `CHROMA_PATH` | `out/kb/chroma` | chỗ Chroma ghi đĩa (bỏ trống = giá trị này) |

Trong các lệnh dưới, `<ten>` = tên file bỏ dấu, viết thường:
`Thời gian làm việc & Chính sách nhân sự.pdf` → `thoi_gian_lam_viec_chinh_sach_nhan_su`.
Tên file có dấu cách thì bọc trong ngoặc kép.

---

## A. Xử lý file raw

Đặt file vào `data/raw/` (`.pdf`, hoặc `.pptx` kèm bản `.pdf` CÙNG TÊN để có ảnh trang —
xuất bằng `scripts\pptx2pdf.ps1`).

**Hai lệnh là đủ.** Lệnh 1 làm hết phần offline: parse → chunk + nhúng → `deck_map`. Bước
nào có cache thì không tốn API — chạy lại thoải mái:

```bash
bash scripts/run_deck.sh "data/raw/<file>.pdf"               # -> document.json, chunks.json + vector + kho, deck_map.txt
bash scripts/run_deck.sh "data/raw/<file>.pdf" --no-vlm      # không gọi VLM, dùng cache layout + bảng
bash scripts/run_deck.sh "data/raw/<file>.pdf" --pages 22    # gọi lại VLM riêng trang 22 (trang + bảng)
bash scripts/run_deck.sh "data/raw/<file>.pdf" --eval        # thêm bước đo bộ câu hỏi có nhãn -> audit/eval.json
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
# ①②②b③ đọc file -> out\parsed\<ten>\{docling.json, layout.json, document.json}   [② ②b tốn API]
.venv\Scripts\python.exe src\parsing\cli.py run "data\raw\<file>.pdf"

# ④ chunk + nhúng vector + nạp kho -> out\kb\<ten>\{chunks.json, vectors__<model>.npy} + out\kb\chroma\  [tốn API]
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>\document.json -o out\kb\<ten>\chunks.json --embed

# ⑤ bản đồ bộ slide cho prompt runtime -> out\deck\<ten>\deck_map.txt
.venv\Scripts\python.exe src\kb\deck_map.py out\parsed\<ten>\document.json
```

Bên trong `parsing\cli.py run`:

| Bước | Ra | Tốn | Bỏ qua khi |
|---|---|---|---|
| ① docling: chữ + toạ độ + vùng ảnh/bảng | `docling.json` | ~2s/trang CPU | đã có file (trừ `--redo`) |
| ② VLM nhìn cả trang, sắp chữ thành khối | `layout.json` (`pages`) | 1 lần gọi/trang | trang đã có, chữ và model không đổi |
| ②b cắt ảnh từng bảng, VLM chép ra `cells`, chữ nắn về text layer | `layout.json` (`tables`) | 1 lần gọi/bảng | bảng đã có, khung bảng và model không đổi |
| ③ ghép + kiểm + bảng + link + chương + vá tay + cờ | `document.json` | không | không bao giờ — luôn dựng lại |

Ghi chú:

- Thử trước vài trang cho đỡ tốn: `run "<file>" --pages 7,10` — CHỈ gọi VLM các trang đó,
  trang khác dùng block docling. `--no-vlm` = không gọi API, dùng `layout.json` sẵn có.
  **Nhớ chạy lại `run "<file>"` không kèm `--pages` để phủ cả deck** — Onboarding từng chỉ có
  8/51 trang qua VLM vì quên bước này.
- ② ②b ghi `layout.json` ngay sau MỖI trang / bảng — đứt giữa chừng vẫn giữ phần đã xong.
- Sửa prompt `s0_page_layout.md` / `s0_table.md` KHÔNG làm cache cũ mất hiệu lực: trang đã có
  vẫn dùng bố cục cũ, log ③ in `prompt cu [...]` để biết trang nào chưa hưởng luật mới. Muốn
  áp luật mới cho trang nào thì `run "<file>" --pages N`.
- VLM chỉ SẮP XẾP, chữ vẫn lấy nguyên văn từ docling. Trang VLM sắp sai (sót chữ, bịa id, bỏ
  quên vùng) tự quay về docling + cờ `layout_failed`. Gọi lại: `run "<file>" --pages N`.
- Log ②b `anh chup (chu vlm): [...]` = bảng là ẢNH chụp, chữ trong ô do VLM đọc — chỗ cần soi.
- ③ tự áp file vá tay `data\patches\<ten>.json` nếu có (xem đầu `src\parsing\patch.py`).
- `run` thoát mã `1` khi có cờ mức `error` — vẫn ghi file bình thường, mã lỗi để CI bắt.
  `run_deck.sh` gặp mã 1 vẫn chạy tiếp.
- ④ chữ không đổi thì lấy vector từ cache (`out\kb\.embed_cache\`), không gọi API.
- ④ `--embed` BẮT BUỘC đi kèm `-o` — vector ghi theo thứ tự chunk, chunk không lưu thì
  vector không khớp file nào.
- ④ nạp kho theo `VECTOR_DB`:
  - `chroma` — nạp luôn ở bước này. Cuối lệnh in `kho chroma (kb__<model>): nap N vector`
    hoặc `da khop, khong doi`. Mọi tài liệu chung MỘT collection, tách bằng `doc_id`.
  - `inmem` — không ghi gì; mỗi lần tìm kiếm tự nạp từ `.npy` vào RAM (vài ms).
- Kho chỉ là BẢN SAO. Nguồn là `chunks.json` + `.npy`: xoá `out\kb\chroma\` thì lần tìm kiếm
  sau tự dựng lại, **không gọi API**.

### Kịch bản (S4) — chạy riêng, không nằm trong `run_deck.sh`

Tốn ~1–2 lần gọi LLM mỗi trang, nên chỉ chạy khi cần kịch bản:

```powershell
# viết kịch bản                       -> out\deck\<ten>\scenario.json + scenario.md   [tốn API]
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json

# chỉ ghi lại scenario.md (sau khi sửa tay scenario.json), không gọi API
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --md

# bảng phát âm — gom từ kịch bản NÓI mà kho chưa có -> data\pronunciation.json (KHO CHUNG)
.venv\Scripts\python.exe scripts\extract_terms.py out\deck\<ten>\scenario.json
#     MỞ FILE RA SỬA "say", đổi by "auto" -> "nguoi", rồi chạy lại S4 (chỉ đếm lại, không gọi LLM)
```

- S4 chỉ viết lại trang có `page_hash`, prompt hoặc model đổi. Lưu dần sau MỖI trang.
- Câu người đã sửa tay (`edited_by: "nguoi"`) không bao giờ bị ghi đè.
- `extract_terms.py` chạy lại **không mất** mục người đã duyệt (`by: "nguoi"`), chỉ thêm mục mới.

---

## B. Đọc kết quả

### Nội dung một trang

```powershell
.venv\Scripts\python.exe src\parsing\cli.py show <ten>                       # tổng quan + chương + cờ
.venv\Scripts\python.exe src\parsing\cli.py show <ten> --page 7              # một trang
.venv\Scripts\python.exe src\parsing\cli.py show <ten> --page 2-5 --full     # nhiều trang, không cắt chữ
```

```
--- trang 7 | CÁCH TÍNH LÀM THÊM GIỜ | chuong: sec_02 (LÀM THÊM GIỜ, p6-8)
    hash=b56b4b44a58afe10  slide_type=content
    p007.v00   para/title     1.62% text_layer CÁCH TÍNH LÀM THÊM GIỜ
    p007.v01   table         13.66% vlm        |  | GIỜ LÀM THÊM BAN NGÀY | GIỜ LÀM THÊM BAN ĐÊM | ⏎ |---|---|---| ⏎ | …
    p007.v02   para/list    100.00% vlm        Đảm bảo số giờ làm thêm không quá 40 giờ trong 01 tháng, không quá 200 g…
```

Mỗi dòng: `id` · loại · % diện tích trang · nguồn · nội dung. `vNN` = khối VLM sắp, `bNN` =
khối docling (trang chưa có / trượt bố cục VLM), `mNN` = vá tay. Nguồn `vlm` = chữ VLM tự
đọc từ ảnh — chỗ cần soi. Block có link ẩn in thêm dòng `↳ link <chữ> -> <url>`.
`--page` nhận `11` · `9,11` · `9-15`.

Xem cả deck bằng mắt: mở thẳng `out\parsed\<ten>\document.json` trong VS Code — file đã gọn, đọc được.

### Chunk trong KB

```powershell
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>\document.json --page 9 --full   # chunk của trang 9
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>\document.json --stats           # thống kê cả KB
```

```
  thoi_gian_lam_viec_chinh_sach_nhan_su#p009.1 page   265tok  vlm=  0%  [LƯƠNG THƯỞNG · 4. CHẾ ĐỘ LƯƠNG THƯỞNG · trang 9/17] …
  thoi_gian_lam_viec_chinh_sach_nhan_su#p009.2 page   279tok  vlm=  0%  [LƯƠNG THƯỞNG · 4. CHẾ ĐỘ LƯƠNG THƯỞNG · trang 9/17] 3 Thưởng …
```

Đầu dòng `✗` = chunk trang phân mục, lọc khỏi tìm kiếm nội dung.

> Không thêm `-o` khi chỉ muốn xem — có `-o` là ghi đè file KB.

### Kịch bản

```powershell
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --show            # cả deck
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --show --page 7   # một trang
```

```
--- trang 7 · content · 6 câu · 112 âm tiết · ~34s · pass2  CỜ: too_long,vlm_number
    [delivery  6] Cách tính cụ thể ra sao?
    [content  19] Làm thêm ngày lễ tết tính hệ số 3 ban ngày và hệ số 3.9 ban đêm.  → p007.v01
```

- `[content 19]` = câu mang thông tin, 19 âm tiết · `→ p007.v01` = block làm nguồn
- `[delivery 6]` = câu dẫn dắt, không mang thông tin mới
- `pass2` = lần đầu viết bị trượt bộ kiểm, đã sửa một lần — đáng soi
- `CỜ:` = lỗi cần xem. Cuối bảng có tổng: thời lượng, tỉ lệ câu thiếu nguồn, cờ đỏ

Đọc cho dễ: mở `out\deck\<ten>\scenario.md` (Ctrl+Shift+V trong VS Code).

---

## C. Thử tìm kiếm

```powershell
.venv\Scripts\python.exe src\kb\search.py out\kb\<ten>\chunks.json "làm thêm ngày lễ hệ số bao nhiêu" -k 3 --explain
```

```
HOI (hybrid): làm thêm ngày lễ hệ số bao nhiêu
 1. trang 8   0.1250  [LÀM THÊM GIỜ · TRƯỜNG HỢP 1: Làm thêm ngày OFF (Hệ số 2) · tr
      dense hang 1    cos=0.498  |  bm25 hang 1    diem=8.37  |  gop 1 chunk  |  vlm 11%
 2. trang 7   0.1176  [LÀM THÊM GIỜ · CÁCH TÍNH LÀM THÊM GIỜ · trang 7/17] CÁCH TÍNH
      dense hang 2    cos=0.480  |  bm25 hang 2    diem=7.94  |  gop 1 chunk  |  vlm 67%
```

`--explain` in hạng của từng nhánh — `dense hang 7 | bm25 hang 2` là biết kết quả do nhánh
nào kéo lên. `--sparse-only` = chỉ BM25, không gọi API · `--dense-only` · `--no-filter` = giữ
cả trang phân mục (kiểu tìm của câu điều hướng) · `--no-group` = không gộp chunk cùng trang.

Dòng log đầu mỗi lần tìm cho biết kho có khớp không:

```
nap 18 chunk | kho chroma | tu khoa rank_bm25 | model text-embedding-3-small                      <- khớp, dùng luôn
nap 18 chunk | kho chroma (nap lai 18 vector) | tu khoa rank_bm25 | model text-embedding-3-small  <- vừa tự nạp lại từ .npy
```

`nap lai` lặp lại MỌI lần chạy = `chunks.json` bị đổi mà chưa chạy ④ `--embed`.

Hỏi đáp trọn vòng (tìm → LLM → code kiểm nguồn): `scripts\try_ask.py`, xem mục A.

---

## D. Đo chất lượng

```powershell
# bộ câu hỏi có nhãn — số đo THẬT. Chạy cả dense / sparse / hybrid
.venv\Scripts\python.exe src\kb\eval.py out\kb\<ten>\chunks.json --queries data\eval\<ten>.queries.json
# thử cấu hình gộp khác (mặc định = hằng số của search.py: K=15, dense 1 : sparse 1)
.venv\Scripts\python.exe src\kb\eval.py out\kb\<ten>\chunks.json --queries data\eval\<ten>.queries.json --rrf-k 7 --wd 1.25

# self-retrieval — gần như luôn 100%, chỉ chứng minh không có 2 chunk trùng nhau
.venv\Scripts\python.exe src\kb\audit.py out\kb\<ten>\chunks.json --mode hybrid
```

```bash
# quét K + trọng số dense/sparse của RRF
bash scripts/tune.sh out/kb/<ten>/chunks.json
bash scripts/tune.sh out/kb/<ten>/chunks.json --k 5,10,20 --wd 1,1.5,2
```

- `eval.py` không truyền `--queries` thì dùng bộ chung `data\eval\queries.json` (của
  `3_datavisualization`). `run_deck.sh --eval` tự chọn `data\eval\<ten>.queries.json`.
- `tune.sh` ra ba file, đặt theo tên file vào, trong `<thư mục chunks>\audit\`:
  `<tên chunks>_tune.md` (người đọc) · `_tune.json` (máy đọc) · `_misses.json` (câu trượt
  top-5 của cấu hình tốt nhất, cùng dạng file câu hỏi — đưa thẳng vào `--queries` được).
- `tune.sh` lấy câu hỏi ở `data\eval\<ten>.queries.json` (không có thì `queries.json`, đổi bằng
  `--queries`). Đổi cách chunk thì chỉ đổi file vào — bộ chunk chưa nhúng thì tự nhúng.
  Chỉ ĐO, không đổi mặc định của `search.py`. Thoát mã 1 khi kho vector trả pool lệch — chạy
  lại. Chi tiết: `docs\spec\search.md` §4.
- Toàn bộ câu eval hiện do AI viết rồi tự chấm — số thiên vị, log in `CANH BAO`.

---

## E. Chạy lại một phần

```powershell
# viết lại kịch bản vài trang, kể cả khi trang không đổi
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --page 2,5 --force

# xem prompt sẽ gửi LLM, không gọi API
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --page 2 --dry-run
```

| Vừa sửa | Chạy lại |
|---|---|
| file slide gốc | `run_deck.sh "<file>" --redo` (→ S4 nếu cần kịch bản) |
| `data\patches\<ten>.json` (vá tay) | `run_deck.sh "<file>" --no-vlm` → S4 |
| `prompts\s0_page_layout.md` / `s0_table.md` | `run_deck.sh "<file>" --pages N` cho trang muốn áp luật mới |
| luật chunk (`src\kb\chunk.py`) | `run_deck.sh "<file>" --no-vlm` — chỉ chunk có chữ đổi mới gọi API nhúng |
| `EMBED_MODEL` | `run_deck.sh` — nhúng lại TOÀN BỘ, ra file vector + collection mới mang tên model mới |
| `VECTOR_DB` / xoá `out\kb\chroma\` | không cần chạy gì — lần tìm sau tự nạp kho từ `.npy`, 0 lần gọi API |
| `data\pronunciation.json` | S4 — trang không đổi chỉ được đếm lại âm tiết, không gọi LLM |
| `prompts\s4_scenario.md` hoặc `LLM_MODEL` | S4 — tự nhận ra prompt/model đổi, viết lại mọi trang |

`--force` chỉ cần khi muốn viết lại dù không có gì đổi (ví dụ thử lại cho câu hay hơn).
