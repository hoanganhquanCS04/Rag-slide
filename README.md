# Robot Slide RAG

Robot tự thuyết trình một bộ slide và trả lời câu hỏi của khán giả: hỏi gì đáp nấy, muốn
xem lại trang nào thì quay về trang đó, câu trả lời nào cũng lần ngược được về đúng mẩu
trên slide.

> **Giai đoạn hiện tại: nhánh OFFLINE — dựng kho tri thức (KB) để tìm kiếm.**
> Đầu vào là **một file PDF duy nhất**, vừa là bộ slide vừa là KB. Mọi model (VLM, LLM,
> embedding) đều gọi **qua API**, không chạy model local nào. Phần nói (TTS), ngắt lời,
> duyệt bởi người chưa làm.

---

## Đã làm đến đâu

|    | Bước            | Code                            | Làm gì                                                                                                                                                                                                   |
| -- | ----------------- | ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ✅ | S0 Parse          | `src/parsing/`                | docling đọc chữ + toạ độ (tắt OCR) · VLM nhìn cả trang sắp chữ thành khối · VLM chép bảng từ ảnh bảng · chương từ trang mục lục · link ẩn · vá tay · cờ cho người duyệt |
| ✅ | S5 KB             | `src/kb/`                     | 1 trang = 1 chunk (cắt khi > 500 token) · nhúng`text-embedding-3-small` qua API, cache theo nội dung · kho vector Chroma hoặc RAM · index BM25                                                    |
| ✅ | Tìm kiếm        | `src/kb/search.py`            | hybrid vector + BM25, gộp bằng RRF (K=15, 1:1), trả**top-5 trang**                                                                                                                                |
| ✅ | Đo chất lượng | `src/kb/{eval,audit,tune}.py` | bộ câu hỏi có nhãn · self-retrieval + quét chunk trùng · quét tham số RRF                                                                                                                       |
| ✅ | S6a`deck_map`   | `src/kb/deck_map.py`          | bản đồ chương ~180 token cho prompt runtime, bằng luật                                                                                                                                              |
| 🟡 | Hỏi đáp thử   | `scripts/try_ask.py`          | tìm → 1 lần gọi LLM → code kiểm nguồn, trong terminal                                                                                                                                               |
| 🟡 | S4 kịch bản     | `src/scenario/`               | bước 4 của`run_deck.sh` (`--no-scenario` để bỏ), chỉ viết lại trang đổi                                                                                                                    |
| ⬜ | Chưa làm        |                                 | S2`time_budget` · S6b TTS · S7 người duyệt · runtime thật (giọng nói, ngắt lời)                                                                                                               |
|    |                   |                                 |                                                                                                                                                                                                            |

Số hiện tại (2026-10-02), hai deck đang làm:

|                                             | Onboarding Kit     | Thời gian làm việc & Chính sách nhân sự |
| ------------------------------------------- | ------------------ | ---------------------------------------------- |
| trang · chương                           | 51 · 5            | 17 · 5                                        |
| chunk                                       | 104                | 18                                             |
| tìm đúng trang ở top-1 / top-5 (hybrid) | 157/200 · 195/200 | 28/30 · 30/30                                 |

---

## Luồng offline

```
data/raw/<file>.pdf
   │ ① docling: chữ + toạ độ + vùng ảnh/bảng                       CPU
   │ ② VLM sắp bố cục từng trang · ②b VLM chép từng bảng            API + cache
   │ ③ ghép + kiểm + chương + link + vá tay + cờ            
   ▼
out/parsed/<ten>/document.json      ParsedDocument — nguồn của mọi bước sau
   │ ④ chunk + nhúng vector + nạp kho                               💰 API+  có cache
   ▼
out/kb/<ten>/chunks.json + vectors__<model>.npy  (+ out/kb/chroma/)
   │ ⑤ deck_map                                                     miễn phí
   ▼
out/deck/<ten>/deck_map.txt
   │ ⑥ S4 kịch bản: chỉ trang đổi mới gọi LLM                       💰 API (~1–2 lần/trang)
   ▼
out/deck/<ten>/scenario.json · scenario.md
```

`<ten>` = tên file bỏ dấu, viết thường: `Thời gian làm việc & Chính sách nhân sự.pdf` →
`thoi_gian_lam_viec_chinh_sach_nhan_su`. Chạy lại bao nhiêu lần cũng được — bước nào có cache
thì không tốn API.

---

## Cài đặt

Python 3.12, phiên bản thư viện ghim trong `requirements.txt`:

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
```

Tạo file `.env` ở gốc repo từ file mẫu, rồi điền `OPENAI_API_KEY`:

```bash
cp .env.example .env
```

| Biến                                   | Ví dụ                       | Dùng ở                                   |
| --------------------------------------- | ----------------------------- | ------------------------------------------ |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | `https://api.yescale.io/v1` | mọi lần gọi API                         |
| `VLM_MODEL`                           | `gemini-3.8-flash`          | ② bố cục trang                          |
| `TABLE_MODEL`                         | `gemini-3.8-flash`          | ②b chép bảng                            |
| `LLM_MODEL`                           | `gemini-3.5-flash-lite`     | hỏi đáp thử, kịch bản                |
| `EMBED_MODEL`                         | `text-embedding-3-small`    | ④ nhúng vector, tìm kiếm               |
| `VECTOR_DB`                           | `chroma` hoặc `inmem`    | kho vector (bỏ trống =`inmem`)         |
| `CHROMA_PATH`                         | `out/kb/chroma`             | chỗ Chroma ghi đĩa, tính từ gốc repo |

Windows: terminal PowerShell mới cần `$env:PYTHONIOENCODING = "utf-8"` (script `.sh` tự đặt).

---

## Chạy

Lệnh `bash` chạy trong Git Bash; `python` là Python trong `.venv`
(`.venv\Scripts\python.exe` trên Windows).

```bash
# 1. Cả nhánh offline của một deck: parse -> chunk + nhúng -> deck_map -> kịch bản
bash scripts/run_deck.sh "data/raw/<file>.pdf"
## các tuỳ chọn khác 
bash scripts/run_deck.sh "data/raw/<file>.pdf" --no-vlm     # không gọi VLM, dùng cache
bash scripts/run_deck.sh "data/raw/<file>.pdf" --pages 22   # gọi lại VLM riêng trang 22
bash scripts/run_deck.sh "data/raw/<file>.pdf" --eval       # chạy xong thì đo luôn
bash scripts/run_deck.sh "data/raw/<file>.pdf" --no-scenario  # bỏ bước kịch bản (S4)

# 2. Hỏi đáp thử trên KB vừa dựng (Ctrl+C để thoát)
python scripts/try_ask.py <ten>
python scripts/try_ask.py "data/raw/<file>.pdf"            # tên file gốc cũng được
python scripts/try_ask.py <ten> "nghỉ phép năm được mấy ngày"

# 3. Chỉ tìm, xem trang nào ra và do nhánh nào kéo lên
python src/kb/search.py out/kb/<ten>/chunks.json "làm thêm ngày lễ hệ số bao nhiêu" --explain

# Xem nội dung đã parse / chunk của một trang
python src/parsing/cli.py show <ten> --page 7
python src/kb/cli.py out/parsed/<ten>/document.json --page 7 --full
```

### Ví dụ — 2 deck đang làm, copy chạy luôn

Chạy ở **thư mục gốc repo**; đường dẫn file luôn kèm `data/raw/`. File gốc đã đổi tên đúng bằng
`<ten>` (2026-10-06) nên tên file và `doc_id` trùng nhau.

> ⚠️ 2 deck này đã dựng bố cục bằng `gemini-3.5-flash-lite`; `VLM_MODEL` nay là `gemini-3.8-flash`
> (2026-10-06). Cache bố cục khoá theo tên model → chạy lại **không kèm `--no-vlm`** là gọi lại
> VLM cho CẢ deck (51 + 17 trang). Chỉ cần chunk / nhúng lại thì dùng dòng `--no-vlm`.
>
> Bước kịch bản (S4) chạy mặc định: Thời gian làm việc đã có kịch bản → 0 lần gọi LLM;
> Onboarding **chưa có** → lần đầu viết 51 trang (~1–2 lần gọi LLM mỗi trang). Thêm
> `--no-scenario` nếu chưa cần.

```bash
# Thời gian làm việc & Chính sách nhân sự   ->   <ten> = thoi_gian_lam_viec_chinh_sach_nhan_su
bash scripts/run_deck.sh data/raw/thoi_gian_lam_viec_chinh_sach_nhan_su.pdf

bash scripts/run_deck.sh data/raw/thoi_gian_lam_viec_chinh_sach_nhan_su.pdf --no-vlm

python scripts/try_ask.py thoi_gian_lam_viec_chinh_sach_nhan_su



# Onboarding Kit   ->   <ten> = onboarding_kit
bash scripts/run_deck.sh data/raw/onboarding_kit.pdf

bash scripts/run_deck.sh data/raw/onboarding_kit.pdf --no-vlm
python scripts/try_ask.py onboarding_kit
```

## Cấu trúc repo

```
src/
├── parsing/      S0 — file gốc -> ParsedDocument          cli.py run | show
├── kb/           S5 — chunk, nhúng, kho vector, BM25, tìm, đo
│   ├── store/    kho vector: inmem | chroma (VECTOR_DB)
│   └── sparse/   index từ khoá: rank_bm25 (SPARSE_INDEX)
├── runtime/      hỏi đáp thử bản chữ (try_ask.py dùng retrieve + router)
├── scenario/     S4 kịch bản — ngoài giai đoạn hiện tại
└── llm.py        gọi LLM/VLM qua API, retry, log vào logs/
scripts/          run_deck.sh · try_ask.py · tune.sh · pptx2pdf.ps1 · extract_terms.py
prompts/          mọi prompt (không hardcode trong .py)
config/           runtime.json — tham số hỏi đáp
data/             raw/ file gốc · eval/ câu hỏi có nhãn · patches/ vá tay · pronunciation.json
out/              mọi thứ sinh ra: parsed/ · kb/ · deck/                     (gitignore)
docs/             tài liệu — xem dưới
```

---

## Tài liệu

| Đọc khi                                                                           | File                                                                                            |
| ----------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Muốn hiểu cái**đang chạy thật**: luồng, dữ liệu từng tầng, số đo | [docs/offline/01-hien-trang.md](./docs/offline/01-hien-trang.md)                                 |
| Cần**lệnh**: chạy, đọc kết quả, chạy lại một phần                  | [docs/offline/02-lenh.md](./docs/offline/02-lenh.md)                                             |
| Cần schema / lý do từng quyết định                                            | [docs/spec/](./docs/spec/) — `parsed-document` · `kb-chunk` · `embedding` · `search` |
| Muốn xem thiết kế đích (pptx, runtime R1–R7, TTS, duyệt)                     | [docs/README.md](./docs/README.md)                                                               |
| Sắp viết code                                                                     | [CLAUDE.md](./CLAUDE.md) — luật bắt buộc của dự án                                        |
