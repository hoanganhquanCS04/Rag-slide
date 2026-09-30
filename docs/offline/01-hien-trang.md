# Nhánh offline — hiện trạng đang chạy (v0)

> Đây là **cái đã code và chạy thật**. Thiết kế đích (S1, S3, Qdrant, nhiều deck…) ở
> [00-overview.md](./00-overview.md). Chỗ nào hai file lệch nhau, **file này đúng với code**.

Offline làm một việc: biến **một file slide** thành mọi thứ robot cần lúc thuyết trình —
biết slide nói gì, tìm được trang khi bị hỏi, và có sẵn kịch bản để nói. Không giới hạn
thời gian, nên mọi thứ tính trước được đều làm ở đây.

---

## 1. Luồng

```
data/raw/<ten>.pdf  (| .pptx + .pdf cùng tên)   MỘT file vừa là DECK vừa là KB
        │
        │ ① docling: chữ + toạ độ + vùng ảnh/bảng     miễn phí (~2s/trang CPU)
        ▼
out/parsed/<ten>/docling.json                     docling thô — không ai đọc trực tiếp
        │
        │ ② VLM nhìn CẢ trang, sắp chữ thành khối     💰 1 lần gọi/trang, có cache
        ▼
out/parsed/<ten>/layout.json                      bố cục từng trang (chỉ trỏ id, không chép chữ)
        │
        │ ③ ghép chữ docling theo bố cục + kiểm       miễn phí
        │    + link ẩn trong PDF + chương + vá tay data/patches/<ten>.json + cờ
        ▼
out/parsed/<ten>/document.json            ★ ParsedDocument — NGUỒN của mọi bước sau, mở ra đọc được luôn
        │
        ├──④ chunk + nhúng vector + nạp kho ─────── 💰 embedding (có cache)
        │      ▼
        │   out/kb/<ten>/chunks.json               KBChunk[]      → để TÌM
        │   out/kb/<ten>/vectors__<model>.{npy,json}
        │   out/kb/chroma/                         kho vector (VECTOR_DB=chroma) — bản sao từ .npy
        │      │
        │      └─ audit.py / eval.py ──────► out/kb/<ten>/audit/*.json   đo chất lượng tìm
        │
        └──⑤ viết kịch bản từng trang ─────────────── 💰 LLM
               đọc: block của trang  (đếm âm tiết theo data/pronunciation.json)
               ▼
            out/deck/<ten>/scenario.json            Scenario       → robot NÓI
            out/deck/<ten>/scenario.md              bản đọc cho người
               │
               └─⑥ scripts/extract_terms.py ──► data/pronunciation.json   KHO CHUNG mọi deck
                     gom từ Anh/viết tắt kịch bản NÓI mà kho chưa có
                     máy ĐỀ XUẤT, NGƯỜI chốt → chạy lại ⑤ = chỉ đếm lại, không gọi LLM
```

Hai nhánh từ `ParsedDocument` **độc lập nhau**: KB (④) để trả lời câu hỏi, kịch bản (⑤)
để tự thuyết trình. ⑤ không đọc chunk — nó đọc thẳng block của trang, vì mỗi câu phải trỏ
về đúng một block.

---

## 2. Lệnh

Đủ lệnh chạy, đọc kết quả, chạy lại một phần: [02-lenh.md](./02-lenh.md).

```powershell
.venv\Scripts\python.exe src\parsing\cli.py run "data\raw\<file>.pdf"                              # ①②③
.venv\Scripts\python.exe src\kb\cli.py out\parsed\<ten>\document.json -o out\kb\<ten>\chunks.json --embed  # ④
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json                                # ⑤
.venv\Scripts\python.exe src\scenario\cli.py out\parsed\<ten>\document.json --md                           # ⑤ bản đọc
.venv\Scripts\python.exe scripts\extract_terms.py out\deck\<ten>\scenario.json                          # ⑥ phát âm (nháp)
```

Thử tìm kiếm: `.venv\Scripts\python.exe scripts\try_search.py <ten> "câu hỏi"`

| Bước | Tốn | Chạy lại khi nào |
|---|---|---|
| ① docling | ~2s/trang CPU, miễn phí | đổi file gốc (`--redo`) |
| ② bố cục VLM | 1 lần gọi/trang; trang không đổi → 0 lần | đổi file gốc, prompt, `VLM_MODEL` |
| ③ dựng document.json | vài giây, miễn phí | luôn chạy lại trong `run` |
| ④ chunk + embed + nạp kho | vài giây; chữ không đổi → 0 lần gọi API | sau ③, đổi luật chunk, đổi `EMBED_MODEL` |
| ⑤ kịch bản | ~1 lần gọi LLM mỗi trang | trang có `page_hash` đổi |

---

## 3. Dữ liệu từng tầng

### ③ `ParsedDocument` — tài liệu → trang → block

Chi tiết: [spec/parsed-document.md](../spec/parsed-document.md)

```
ParsedDocument   doc_id · source · parser · sections[] · flags[]
 └─ pages[]      page_no · title · section_id · page_hash
     ├─ slide_type    section_divider · exercise · content — luật, S4 viết theo loại trang
     └─ blocks[]      NỘI DUNG, theo thứ tự đọc
          └─ id · kind · role · content · hrefs · polygon · provenance
```

Mỗi block trả lời ba câu, loại nào cũng vậy:

| | field | |
|---|---|---|
| nói gì | `content` | chữ (`paragraph`) · mô tả VLM (`image`) · markdown (`table`) |
| nằm đâu | `polygon` | 4 góc, `[0,1]`, gốc trên-trái |
| tin được không | `provenance` | `text_layer` đúng 100% · `vlm` máy tả, có thể sai · `manual` người sửa |

```json
{"id": "p002.b01", "kind": "paragraph", "role": "body", "content": "Mùng 1",
 "polygon": [[0.232, 0.33], [0.768, 0.33], [0.768, 0.552], [0.232, 0.552]],
 "provenance": "text_layer"}
```

### ④ `KBChunk` + vector — để TÌM

Chi tiết: [spec/kb-chunk.md](../spec/kb-chunk.md) · [spec/embedding.md](../spec/embedding.md) ·
[spec/search.md](../spec/search.md)

```
1 trang = 1 chunk   (+ 1 chunk phụ mỗi ảnh nếu trang có ≥2 ảnh được mô tả)
text_enriched = "[<chương> · <tiêu đề trang> · trang N/M] " + các block.content nối lại
> 500 token   -> cắt: trang theo block -> bảng theo hàng (lặp hàng tiêu đề) / chữ theo dòng -> câu
```

```json
{"chunk_id": "tetnguyendan#p002", "page_no": 2, "content_type": "content",
 "text_enriched": "[trang 2/10] Khởi Nguồn Nam Mới\nMùng 1\nTháng Giêng Âm Lịch\n...",
 "block_ids": ["p002.b00", "p002.b01", "p002.b02", "p002.b03"],
 "provenance": {"text_layer": 4}}
```

Chữ và vector ở **hai file riêng**, nối bằng thứ tự hàng:

```
out/kb/<ten>/chunks.json                              chữ + metadata
out/kb/<ten>/vectors__text-embedding-3-small.npy      ma trận (số chunk × 1536)
out/kb/<ten>/vectors__text-embedding-3-small.json     rows[i] = chunk_id của hàng i
```

Tên model nằm trong tên file vector: đổi model mà quên nhúng lại thì tìm kiếm trả rác
**không báo lỗi** — nhúng tên vào là để không lẫn được.

**Kho vector** (`src/kb/store/`, chọn bằng `VECTOR_DB`): `chroma` ghi đĩa, một collection
`kb__<model>` cho mọi tài liệu, lọc bằng `doc_id` · `inmem` giữ trong RAM, nạp lại mỗi lần
chạy. Kho chỉ lo nhánh vector và là BẢN SAO của `.npy` — lệch với `chunks.json` thì tự nạp
lại, xoá đi thì tự dựng lại, không gọi API.

Tìm = **vector + BM25, gộp bằng RRF**. Trang phân mục (`content_type: section_divider`)
được đánh dấu, lọc lúc tìm, **không xoá**.

### Bảng phát âm — `pronunciation.json`

Chi tiết: [spec/pronunciation.md](../spec/pronunciation.md)

```json
{"terms": {"CBNV": {"say": "xê bê en vê", "mode": "spell", "by": "auto"}}}
```

`data/pronunciation.json` — **MỘT kho cho mọi deck**. Robot đọc thuật ngữ thế nào, và ⑤
**đếm âm tiết theo đúng kho này**. Sinh TỪ KỊCH BẢN (⑥), không từ chunk: chỉ từ robot thật
sự nói mới cần cách đọc. `by: "auto"` = máy đề xuất, chưa ai duyệt; `"nguoi"` = đã chốt,
chạy lại ⑥ không bao giờ đè.

### ⑤ `Scenario` — robot NÓI gì

Chi tiết: [spec/scenario.md](../spec/scenario.md)

```
Scenario        doc_id · model · prompt_hash · pronunciation_hash
 └─ slides[]    page_no · page_hash · slide_type · flags[] · edited_by
     └─ sentences[]   kind · text · grounding · syllables · prosody
```

```json
{"kind": "delivery", "text": "Vậy, mốc nào quan trọng trong dịp này?",
 "grounding": {"type": "structure", "ref": null}, "syllables": 8}
{"kind": "content",  "text": "Tháng Giêng âm lịch là tên gọi của tháng này.",
 "grounding": {"type": "kb_chunk", "ref": "p002.b02"}, "syllables": 10,
 "prosody": {"emphasis": [], "pause_before_ms": 300, "speed": 1.0}}
```

- `content` = câu mang thông tin → **bắt buộc** trỏ về một block. Không trỏ = cờ đỏ.
- `delivery` = câu dẫn dắt, chuyển ý → không được mang sự thật mới; code kiểm (không số…).
- `syllables` do **code** đếm, không để LLM khai.
- `edited_by: "nguoi"` = đã sửa tay → máy không bao giờ viết đè.

---

## 4. Các tầng nối nhau bằng `block_id`

```
 ParsedDocument               KB                           Scenario
 page 2                       chunk tetnguyendan#p002      trang 2
  p002.b02 "Tháng Giêng  ◄─── block_ids: [.., p002.b02,..]  câu "Tháng Giêng âm lịch là…"
            Âm Lịch"     ◄──────────────────────────────── grounding.ref: "p002.b02"
```

Từ bất kỳ câu robot nói hay kết quả tìm kiếm nào cũng lần ngược được về đúng mẩu trên
slide: nó là chữ thật (`text_layer`) hay máy tả (`vlm`), nằm ở đâu (`polygon`).

---

## 5. Build lại phần đổi thôi

| Cơ chế | Nằm ở | Tác dụng |
|---|---|---|
| `page_hash` = hash(nội dung + vị trí mọi block của trang) | `ParsedPage` | ⑤ chỉ viết lại trang có hash đổi |
| cache vector theo hash của `text_enriched` | `out/kb/.embed_cache/` | chữ không đổi → 0 lần gọi API |
| `edited_by: "nguoi"` | `SlideScript` | câu người sửa không bị máy ghi đè |
| `pronunciation_hash` | `Scenario` | đổi bảng phát âm → biết timing đã lệch |

Đo được: build lại `tetnguyendan` sau khi sửa toạ độ → ④ **10/10 vector từ cache, 0 lần
gọi API**; ⑤ viết lại cả 10 trang vì toạ độ nằm trong `page_hash`.

---

## 6. Số hiện tại

| | `3_datavisualization` (.pdf) | `tetnguyendan` (.pptx) |
|---|---|---|
| trang | 40 | 10 |
| block (chữ + ảnh) | 138 | 58 |
| ảnh VLM mô tả được | 27 / 71 | 6 / 15 |
| chương | 7 | 0 (không có thanh header) |
| chunk (tìm được) | 52 (45) | 10 (10) |
| kịch bản | 40 trang | 10 trang · 0 câu thiếu nguồn · 0 cờ đỏ |

---

## 7. Chưa có

| | Việc | Ghi chú |
|---|---|---|
| ⬜ S2 | `time_budget` theo chương | luật, không gọi model. Hiện trần số câu theo `slide_type` là phanh duy nhất |
| ⬜ `slide_type` đủ loại | `title` · `agenda` | đã có `section_divider` · `exercise` · `content` |
| ⬜ S6a | `deck_map.txt` (~150 token) cho prompt runtime | audit đã có |
| ⬜ S6b | TTS theo từng câu, một giọng duy nhất | |
| ⬜ S7 | người duyệt **chỉ phần bị cờ**, đọc + nghe | |
| ⚠️ nhịp kịch bản | độ lệch chuẩn âm tiết/câu ≥ 6 | `tetnguyendan` mới 4.0 — câu dài đều nhau, nghe đều đều |
| ⏳ reranker | confidence gate khi điều hướng | API key chưa được bật quyền `rerank` (403) |
