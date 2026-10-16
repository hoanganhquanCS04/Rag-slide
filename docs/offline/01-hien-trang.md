# Nhánh offline — hiện trạng đang chạy (v0)

> Đây là **cái đã code và chạy thật** (cập nhật 2026-10-02). Thiết kế đích (pptx, nguồn
> riêng, Qdrant, nhiều deck…) ở [00-overview.md](./00-overview.md). Chỗ nào hai file lệch
> nhau, **file này đúng với code**.

Offline làm một việc: biến **một file slide** thành mọi thứ robot cần lúc thuyết trình —
biết slide nói gì, tìm được trang khi bị hỏi, và có sẵn kịch bản để nói. Không giới hạn
thời gian, nên mọi thứ tính trước được đều làm ở đây.

---

## 1. Luồng

```
data/raw/<file>.pdf  (| .pptx + .pdf CÙNG TÊN)     MỘT file vừa là DECK vừa là KB
        │
        │ ① docling VlmPipeline: VLM nhìn CẢ trang, đọc bố cục     💰 1 lần gọi/trang
        │    + chữ + bảng (HTML) + mô tả ảnh trong cùng lượt
        ▼
src/output/parser/<ten>/docling_vlmpipeline.json           cache phản hồi VLM (raw: logs/vlm_pipeline/)
        │
        │ ③ đối chiếu text layer (khớp nguyên văn -> text_layer)   miễn phí
        │    · chương từ trang mục lục · cờ
        ▼
src/output/parser/<ten>/document.json            ★ ParsedDocument — NGUỒN của mọi bước sau
        │
        ├──④a chunk ──► src/output/chunker/<ten>/chunks.json      KBChunk[]  → để TÌM
        │   ④b index: nhúng + nạp kho ────────────── 💰 embedding (cache theo nội dung)
        │      ▼
        │   src/output/embedders/<ten>/vectors__<model>.{npy,json}
        │   src/output/vectordb/chroma/                        kho vector — BẢN SAO từ .npy
        │      │
        │      └─ eval.py / tune.sh ──► src/output/evaluation/<ten>/   đo chất lượng tìm
        │
        └──⑥ S4 kịch bản ──────────────────────────── 💰 LLM, chỉ trang đổi
               đọc: block của trang + kịch bản trang trước cùng chương
               ▼
            src/output/scenario/<ten>/scenario.json · scenario.md
               │
               └─ scripts/extract_terms.py ──► data/pronunciation.json   KHO CHUNG mọi deck
```

**`scripts/run_deck.sh` chạy ①→⑥ bằng một lệnh** (thêm `--eval` thì đo luôn, `--no-scenario` thì
bỏ S4). S4 incremental: trang có `page_hash` / prompt / model không đổi thì không gọi LLM.

KB (④) và kịch bản (S4) **độc lập nhau**. S4 không đọc chunk — nó đọc thẳng block của
trang, vì mỗi câu phải trỏ về đúng một block.

---

## 2. Lệnh

Đủ lệnh chạy, đọc kết quả, chạy lại một phần: [02-lenh.md](./02-lenh.md).

```bash
bash scripts/run_deck.sh "data/raw/<file>.pdf"            # ①③ ④ ⑤ — phần offline của một deck
bash scripts/run_deck.sh "data/raw/<file>.pdf" --eval     # + đo bộ câu hỏi có nhãn
.venv/Scripts/python.exe scripts/try_ask.py <ten>         # hỏi đáp thử trên KB vừa dựng
```

| Bước | Tốn | Chạy lại khi nào |
|---|---|---|
| ① VlmPipeline | 1 lần gọi/trang (~9s/trang Gemma local); có `docling_vlmpipeline.json` → 0 | đổi file gốc, `VLM_MODEL`, prompt — ép bằng `--redo` (cả file) |
| ③ dựng document.json | vài giây, miễn phí | luôn dựng lại |
| ④ chunk + nhúng + nạp kho | vài giây; chữ không đổi → 0 lần gọi API | sau ③, đổi luật chunk, đổi `EMBED_MODEL` |
| S4 kịch bản | ~1–2 lần gọi LLM mỗi trang | trang có `page_hash` / prompt / model đổi |

---

## 3. Dữ liệu từng tầng

### ③ `ParsedDocument` — tài liệu → trang → block

Chi tiết: [spec/parsed-document.md](../spec/parsed-document.md) · code: `src/parser/`

```
ParsedDocument   doc_id · source · parser · sections[] · flags[]
 └─ pages[]      page_no · title · section_id · page_hash
     ├─ slide_type    section_divider · exercise · content — LUẬT, tính lại mỗi lần nạp
     └─ blocks[]      NỘI DUNG, theo thứ tự đọc
          └─ id · page_no · kind · role · content · polygon
```

Block lưu `page_no` thẳng trong JSON; chương thì hỏi `doc.section_of(block.page_no)`.

Mỗi block trả lời hai câu, loại nào cũng vậy:

| | field | |
|---|---|---|
| nói gì | `content` | chữ (`paragraph`) · mô tả hình (`image`) · markdown sinh từ `cells` (`table`) |
| nằm đâu | `polygon` | 4 góc, `[0,1]`, gốc trên-trái |

Mọi chữ do VLM đọc từ ảnh trang — có thể sai. `id` = `p<trang>.b<số>` theo thứ tự đọc (vd `p007.b04`
= trang 7, khối thứ 4); số có thể nhảy vì ảnh trang trí bị bỏ.

```json
{"id": "p007.b00", "kind": "paragraph", "role": "title", "content": "CÁCH TÍNH LÀM THÊM GIỜ",
 "polygon": [[0.499, 0.094], [0.843, 0.094], [0.843, 0.141], [0.499, 0.141]]}
```

- **Chương** dựng từ trang mục lục trong 5 trang đầu: trang đầu tiên có tiêu đề khớp một
  mục là trang mở chương. Không có mục lục khớp → 0 chương, không đoán.
- Block có ≥ nửa số dòng là URL in ra chữ → `role: "links"` — robot không đọc địa chỉ thành tiếng.
  Link ẩn sau chữ (annotation PDF) không đọc nữa (bỏ 2026-10-12).
- **Cờ** cho người duyệt: `empty_page` · `image_not_described` · `table_empty` ·
  `no_sections`. Block rỗng không có cờ (ảnh nhỏ, ảnh trang trí) bị bỏ.

### ④ `KBChunk` + vector — để TÌM

Chi tiết: [spec/kb-chunk.md](../spec/kb-chunk.md) · [spec/embedding.md](../spec/embedding.md) ·
[spec/search.md](../spec/search.md) · code: `src/chunker/` (chunk) · `src/embedders/` (nhúng) · `src/runtime/retrieve.py` + `src/sparse/` (tìm) · `src/evaluation/` (đo)

```
1 trang = 1 chunk        (mô tả ảnh nằm trong chunk trang, không có vector ảnh phụ)
text_enriched = "[<chương> · <tiêu đề trang> · trang N/M] " + các block.content nối lại
> 500 token   -> cắt ÍT khúc nhất, chọn chỗ cắt: trước đề mục > giữa block > giữa dòng /
                 hàng bảng > giữa câu. Bảng cắt theo hàng, khúc nào cũng lặp hàng tiêu đề
< 100 token   -> khúc vụn, gộp vào khúc bên cạnh (được vượt tới 600)
KHÔNG overlap · trang phân mục -> content_type "section_divider", lọc lúc tìm, KHÔNG xoá
```

```json
{"chunk_id": "thoi_gian_lam_viec_chinh_sach_nhan_su#p007", "page_no": 7,
 "section_title": "LÀM THÊM GIỜ", "vector_role": "page", "content_type": "content",
 "text_enriched": "[LÀM THÊM GIỜ · CÁCH TÍNH LÀM THÊM GIỜ · trang 7/17] CÁCH TÍNH LÀM THÊM GIỜ\n|  | GIỜ LÀM THÊM BAN NGÀY | ...",
 "token_count": 237, "block_ids": ["p007.b00", "p007.b01", "p007.b02"], "n_images": 0}
```

Trang bị cắt thì chunk mang đuôi `.1`, `.2` (`…#p009.1`). Mọi chunk đều trỏ về `page_no` —
điều hướng theo TRANG.

Chữ và vector ở **hai file riêng**, nối bằng thứ tự hàng:

```
src/output/chunker/<ten>/chunks.json                              chữ + metadata
src/output/embedders/<ten>/vectors__text-embedding-3-small.npy   ma trận (số chunk × 1536), chuẩn hoá L2
src/output/embedders/<ten>/vectors__text-embedding-3-small.json  rows[i] = chunk_id, keys[i] = hash chữ của hàng i
src/output/embedders/cache/<model>/<sha1>.npy             cache dùng chung, khoá = hash(model + chữ)
```

Tên model nằm trong tên file vector: đổi model mà quên nhúng lại thì tìm kiếm trả rác
**không báo lỗi** — nhúng tên vào là để không lẫn được.

**Kho vector** (`src/vectordb/`, chọn bằng `VECTOR_DB`): `chroma` ghi đĩa, một collection
`kb__<model>` cho mọi tài liệu, lọc bằng `doc_id` · `inmem` giữ trong RAM, nạp lại mỗi lần
chạy. Kho chỉ lo nhánh vector và là BẢN SAO của `.npy` — lệch với `chunks.json` thì tự nạp
lại, xoá đi thì tự dựng lại, không gọi API.

**Tìm** (`src/runtime/retrieve.py`) = hai nhánh, gộp bằng RRF:

```
câu hỏi ─┬─ dense: nhúng câu hỏi (API) → kho vector → top 50 ─┐
         └─ sparse: BM25 (src/sparse/, dựng trong RAM) → top 50 ┴─ RRF K=10, dense 1.5 : sparse 1
                                                                    → gộp theo trang (điểm max)
                                                                    → top-5
```

Hằng số RRF đo bằng `scripts/tune.sh`, không đoán. Hỏi nội dung thì **lọc** trang phân mục;
hỏi điều hướng ("quay lại phần…") thì **không lọc** — trang mở chương là đáp án đúng.
API nhúng lỗi lúc chạy → tự lùi về chỉ BM25.

### ~~⑤ `deck_map.txt`~~ — BỎ 2026-10-13

Chỉ viết lại `doc.sections` thành chữ cho prompt runtime. Chương vẫn dò ở S0 (`parser/sections.py`)
và có trong bộ nhớ sau `enrich()`; prompt runtime không còn danh sách chương — câu hỏi cấu trúc phải
tìm ra trang mục lục (xem CLAUDE.md §5 S6a).

### S4 `Scenario` — robot NÓI gì

Chi tiết: [spec/scenario.md](../spec/scenario.md) · code: `src/scenario/`

```
Scenario        doc_id · model · prompt_hash · pronunciation_hash
 └─ slides[]    page_no · page_hash · slide_type · flags[] · passes
     └─ sentences[]   kind · text · grounding · syllables · prosody
```

```json
{"kind": "delivery", "text": "Cách tính cụ thể ra sao?",
 "grounding": {"type": "structure", "ref": null}, "syllables": 6}
{"kind": "content", "text": "Làm thêm ngày lễ tết tính hệ số 3 ban ngày và hệ số 3.9 ban đêm.",
 "grounding": {"type": "kb_chunk", "ref": "p007.b01"}, "syllables": 19,
 "prosody": {"emphasis": ["3", "3.9"], "pause_before_ms": 0, "speed": 1.0}}
```

- `content` = câu mang thông tin → **bắt buộc** trỏ về một block của trang. Không trỏ = cờ đỏ.
- `delivery` = câu dẫn dắt → không được mang sự thật mới; code kiểm (không số, không thuật ngữ).
- Pass 1 viết; **pass 2 chỉ chạy cho trang trượt bộ kiểm** (đủ ý, nhịp, văn nói…), giữ bản ít lỗi hơn.
- `syllables` do **code** đếm theo `data/pronunciation.json`, không để LLM khai.
- Không có cờ "người đã sửa tay" (`edited_by` bỏ 2026-10-12): sửa tay `scenario.json` sẽ bị máy viết
  đè khi trang / prompt / model đổi hoặc chạy `--force`.

### Bảng phát âm — `data/pronunciation.json`

Chi tiết: [spec/pronunciation.md](../spec/pronunciation.md)

```json
{"terms": {"CBNV": {"say": "xê bê en vê", "mode": "spell", "by": "auto"}}}
```

**MỘT kho cho mọi deck.** Sinh TỪ KỊCH BẢN (`extract_terms.py`), không từ chunk: chỉ từ robot
thật sự nói mới cần cách đọc. `by: "auto"` = máy đề xuất; `"nguoi"` = đã chốt, chạy lại
không bao giờ đè. Sửa kho rồi chạy lại S4 = chỉ đếm lại âm tiết, không gọi LLM.

---

## 4. Các tầng nối nhau bằng `block_id`

```
 ParsedDocument               KB                                  Scenario
 page 7                       chunk …#p007                        trang 7
  p007.b01 (bảng hệ số)  ◄─── block_ids: [p007.b00, p007.b01, …]  câu "Làm thêm ngày lễ tết… hệ số 3"
                         ◄──────────────────────────────────────── grounding.ref: "p007.b01"
```

Từ bất kỳ câu robot nói hay kết quả tìm kiếm nào cũng lần ngược được về đúng mẩu trên
slide: nó là chữ trên slide hay mô tả ảnh (`kind`), nằm ở đâu (`polygon`).

---

## 5. Build lại phần đổi thôi

| Cơ chế | Nằm ở | Tác dụng |
|---|---|---|
| có `docling_vlmpipeline.json` | `src/output/parser/<ten>/` | không gọi lại VLM. Cache theo FILE, không theo trang: đổi model / prompt phải `--redo` cả file |
| `page_hash` = hash(nội dung + vị trí mọi block) | `ParsedPage` | S4 chỉ viết lại trang có hash đổi |
| cache vector theo hash(model + `text_enriched`) | `src/output/embedders/cache/` | chữ không đổi → 0 lần gọi API |
| `sync` so metadata từng chunk | kho vector, BM25 | lệch thì nạp lại từ `.npy`, khớp thì thôi |
| `pronunciation_hash` | `Scenario` | đổi bảng phát âm → biết timing đã lệch |

Đo được (2026-10-02): chạy lại `run_deck.sh` trọn luồng cho cả hai deck → **0 lần gọi
VLM, 0 lần gọi API nhúng**, Onboarding 22 giây.

---

## 6. Số hiện tại (2026-10-02)

Hai deck đang làm:

| | Onboarding Kit | Thời gian làm việc & Chính sách nhân sự |
|---|---|---|
| trang | 51 | 17 |
| block | 272 | 71 |
| bố cục VLM / dùng docling | 51 / 0 | 17 / 0 |
| bảng · ảnh có mô tả · link ẩn (đo trước 2026-10-12, nay bỏ) | 18 · 26 · 57 | 1 · 3 · 1 |
| chương (từ mục lục) | 5 | 5 |
| cờ cho người duyệt | 0 | 0 |
| chunk (tìm được) | 104 (104) — 85 trang + 19 ảnh, 25 trang bị cắt | 18 (17) — 1 trang phân mục |
| eval hybrid top-1 / top-5 | 157/200 · 195/200 | 28/30 · 30/30 |
| eval dense / sparse top-1 | 131 / 145 | 29 / 29 |
| kịch bản | chưa sinh | 17 trang · 114 câu · ~10.8 phút · 0 câu thiếu nguồn · 0 cờ đỏ |

⚠️ Toàn bộ câu eval do AI viết rồi tự chấm — số **thiên vị**, chưa đủ để kết luận. Cần bộ
câu người thật viết (giọng nói, có dấu, văn nói đủ chữ).

---

## 7. Chưa có

| | Việc | Ghi chú |
|---|---|---|
| ⬜ S4 Onboarding | kịch bản 51 trang | chưa có lệnh thuyết trình thử — `src/runtime/` v0 chỉ hỏi đáp (`scripts/try_ask.py`) |
| 🚫 luật nói tự nhiên | nhịp, tỉ lệ delivery, văn viết… | BỎ khỏi bộ kiểm S4 (2026-10-06) — chỉ còn hướng dẫn trong prompt, xem [spec/scenario.md §11](../spec/scenario.md) |
| ⬜ S2 | `time_budget` theo chương | luật, không gọi model. S4 hiện không có trần độ dài trang — đi theo nội dung |
| ⬜ `slide_type` đủ loại | `title` · `agenda` | đã có `section_divider` · `exercise` · `content` |
| ⬜ S6b | TTS theo từng câu, một giọng duy nhất | |
| ⬜ S7 | người duyệt **chỉ phần bị cờ**, đọc + nghe | |
| 🚫 reranker | đã chốt bỏ (2026-10-01) | cố định top-5; cải thiện ở RRF / dữ liệu / viết lại câu hỏi |
