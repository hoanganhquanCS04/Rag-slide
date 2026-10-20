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
| ✅ | S0 Parse          | `src/offline/parser/`                | docling đọc chữ + toạ độ (tắt OCR) · VLM nhìn cả trang sắp chữ thành khối · VLM chép bảng từ ảnh bảng · chương từ trang mục lục · cờ cho người duyệt |
| ✅ | S5 KB             | `src/offline/chunker/` · `src/components/embedders/` · `src/components/vectordb/` · `src/components/keyword_search.py` | 1 trang = 1 chunk (cắt khi > 500 token) · nhúng`text-embedding-3-small` qua API, cache theo nội dung · kho vector Chroma (vector + chữ + metadata) · index BM25                                                    |
| ✅ | Tìm kiếm        | `src/utils/ensemble_search.py`            | hybrid vector + BM25, gộp bằng RRF (K=10, vector 1.5 : BM25 1), trả**top-5 trang**                                                                                                                                |
| ✅ | Đo chất lượng | `src/evaluation/{eval,tune}.py` | bộ câu hỏi có nhãn · quét tham số RRF                                                                                                                                                            |
| 🟡 | Hỏi đáp thử   | `scripts/try_ask.py`          | tìm → 1 lần gọi LLM → code kiểm nguồn, trong terminal                                                                                                                                               |
| 🟡 | S4 kịch bản     | `src/offline/scenario/`               | bước 4 của`run_deck.sh` (`--no-scenario` để bỏ), chỉ viết lại trang đổi                                                                                                                    |
|    |                   |                                 |                                                                                                                                                                                                            |
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
   │ ① docling VlmPipeline: VLM đọc từng trang — bố cục + chữ        API + cache
   │   + bảng (HTML) + mô tả ảnh          (raw -> logs/vlm_pipeline/)
   │ ③ link + chương + cờ (enrich, mỗi lần nạp)                        CPU
   ▼
src/output/parser/<ten>/document.json      ParsedDocument — nguồn của mọi bước sau
   │ ④a chunk                                                        miễn phí
   ▼
   │ ④b index: nhúng vector + nạp kho                               💰 API+  có cache
   ▼
src/output/vectordb/chroma/   kho chunk (chữ + vector + metadata), không có file chunk riêng
   │ ⑥ S4 kịch bản: chỉ trang đổi mới gọi LLM                       💰 API (~1–2 lần/trang)
   ▼
src/output/scenario/<ten>/scenario.json · scenario.md
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
| `VLM_MODEL`                           | `gemini-3.8-flash`          | ① đọc trang (bố cục, chữ, bảng, ảnh)    |
| `LLM_MODEL`                           | `gemini-3.5-flash-lite`     | hỏi đáp thử, kịch bản                |
| `EMBED_MODEL`                         | `text-embedding-3-small`    | ④ nhúng vector, tìm kiếm               |
| `VECTOR_DB`                           | `chroma`                 | kho vector (bỏ trống =`chroma`)        |
| `CHROMA_PATH`                         | `src/output/vectordb/chroma`          | chỗ Chroma ghi đĩa, tính từ gốc repo |

Windows: terminal PowerShell mới cần `$env:PYTHONIOENCODING = "utf-8"` (script `.sh` tự đặt).

---

## Chạy

Lệnh `bash` chạy trong Git Bash; `python` là Python trong `.venv`
(`.venv\Scripts\python.exe` trên Windows).

```bash
# 1. Cả nhánh offline của một deck: parse -> chunk + nhúng -> kịch bản
bash scripts/run_deck.sh "data/raw/<file>.pdf"
## các tuỳ chọn khác 
bash scripts/run_deck.sh "data/raw/<file>.pdf" --redo       # gọi lại VLM cả file (mặc định dùng cache)
bash scripts/run_deck.sh "data/raw/<file>.pdf" --eval       # chạy xong thì đo luôn
bash scripts/run_deck.sh "data/raw/<file>.pdf" --no-scenario  # bỏ bước kịch bản (S4)

# 2. Hỏi đáp thử trên KB vừa dựng (Ctrl+C để thoát)
python scripts/try_ask.py <ten>
python scripts/try_ask.py "data/raw/<file>.pdf"            # tên file gốc cũng được
python scripts/try_ask.py <ten> "nghỉ phép năm được mấy ngày"

# 3. Chỉ tìm, xem trang nào ra và do nhánh nào kéo lên
python scripts/try_ask.py <ten> "làm thêm ngày lễ hệ số bao nhiêu"     # log [1] = trang tìm được + hạng dense / BM25

# Xem nội dung đã parse / chunk của một trang
python src/offline/parser/show.py <ten> --page 7
python src/offline/chunker/cli.py src/output/parser/<ten>/document.json --page 7 --full
```

### Ví dụ — 2 deck đang làm, copy chạy luôn

Chạy ở **thư mục gốc repo**; đường dẫn file luôn kèm `data/raw/`. File gốc đã đổi tên đúng bằng
`<ten>` (2026-10-06) nên tên file và `doc_id` trùng nhau.

> ⚠️ Có `src/output/parser/<ten>/docling_vlmpipeline.json` rồi thì parse KHÔNG gọi VLM (chỉ dựng lại ③);
> `--redo` mới gọi lại VLM cho CẢ deck (51 + 17 trang).
>
> Bước kịch bản (S4) chạy mặc định: Thời gian làm việc đã có kịch bản → 0 lần gọi LLM;
> `--no-scenario` nếu chưa cần.

```bash
# Thời gian làm việc & Chính sách nhân sự   ->   <ten> = thoi_gian_lam_viec_chinh_sach_nhan_su
bash scripts/run_deck.sh data/raw/thoi_gian_lam_viec_chinh_sach_nhan_su.pdf

bash scripts/run_deck.sh data/raw/thoi_gian_lam_viec_chinh_sach_nhan_su.pdf --redo

python scripts/try_ask.py thoi_gian_lam_viec_chinh_sach_nhan_su



# Onboarding Kit   ->   <ten> = onboarding_kit
bash scripts/run_deck.sh data/raw/onboarding_kit.pdf

bash scripts/run_deck.sh data/raw/onboarding_kit.pdf --redo
python scripts/try_ask.py onboarding_kit
```

## Cấu trúc repo

```
src/
├── chat_engine.py   HỎI ĐÁP: vector_search · keyword_search -> ensemble_search -> 1 lần LLM -> kiểm nguồn
│                    (try_ask.py gọi ChatEngine)
├── models/          schema Pydantic dùng chung: ParsedDocument · KBChunk · Scenario · Reply/Answer
├── components/      LINH KIỆN thay được, không biết gì về slide
│   ├── embedders/     chữ -> vector: openai (EMBEDDER), cache bọc ngoài
│   ├── vectordb/      kho vector: chroma (VECTOR_DB)
│   └── keyword_search.py   BM25 trong RAM, dựng từ bản ghi của kho vector
├── offline/         CHUẨN BỊ DỮ LIỆU, chạy một lần, mỗi bước một cli.py
│   ├── parser/        S0 — file gốc -> ParsedDocument   cli.py (parse) · show.py (xem) · enrich (chương · cờ)
│   ├── chunker/       S5 — ParsedDocument -> chunk -> nhúng -> ghi thẳng Chroma
│   ├── embedder/      S5 — cli.py nhúng chunks + nạp kho
│   └── scenario/      S4 kịch bản
├── utils/           dùng chung: config (đường dẫn, .env) · llm · vectors · tokens · syllables ·
│                    ensemble_search (gộp RRF)
├── evaluation/      đo tìm kiếm: eval (câu hỏi có nhãn) · tune (quét tham số RRF)
├── prompts/         mọi prompt (không hardcode trong .py)
└── output/          mọi thứ sinh ra: parser/ chunker/ embedders/ … (đường dẫn cố định trong utils/config.py, gitignore)
scripts/          run_deck.sh · try_ask.py · tune.sh · pptx2pdf.ps1 · extract_terms.py
config/           runtime.json — tham số hỏi đáp
data/             raw/ file gốc · eval/ câu hỏi có nhãn · pronunciation.json
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
