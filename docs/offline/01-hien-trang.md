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
        │ ① docling: chữ + toạ độ + vùng ảnh/bảng, TẮT OCR      miễn phí, CPU
        ▼
out/parsed/<ten>/docling.json                       docling thô — không ai đọc trực tiếp
        │
        │ ② VLM nhìn CẢ trang, sắp mẩu chữ thành khối          💰 1 lần gọi/trang
        │    (chỉ TRỎ id mẩu chữ, không chép lại chữ)
        │ ②b cắt ảnh từng bảng, VLM chép ra ô,                 💰 1 lần gọi/bảng
        │    chữ trong ô nắn về text layer
        ▼
out/parsed/<ten>/layout.json                        cache phản hồi VLM: "pages" + "tables"
        │
        │ ③ ghép + kiểm bố cục · thay ô bảng · link ẩn trong PDF   miễn phí
        │    · chương từ trang mục lục · vá tay data/patches/ · cờ
        ▼
out/parsed/<ten>/document.json            ★ ParsedDocument — NGUỒN của mọi bước sau
        │
        ├──④ chunk + nhúng vector + nạp kho ─────── 💰 embedding (cache theo nội dung)
        │      ▼
        │   out/kb/<ten>/chunks.json                 KBChunk[]  → để TÌM
        │   out/kb/<ten>/vectors__<model>.{npy,json}
        │   out/kb/chroma/                           kho vector — BẢN SAO từ .npy
        │      │
        │      └─ eval.py / tune.sh ──► out/kb/<ten>/audit/   đo chất lượng tìm
        │
        ├──⑤ deck_map ───────────────────────────── miễn phí, luật
        │      ▼
        │   out/deck/<ten>/deck_map.txt              ~180 token: danh sách chương → prompt runtime
        │
        └──⑥ S4 kịch bản ──────────────────────────── 💰 LLM, chỉ trang đổi
               đọc: block của trang + kịch bản trang trước cùng chương
               ▼
            out/deck/<ten>/scenario.json · scenario.md
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
bash scripts/run_deck.sh "data/raw/<file>.pdf"            # ①②②b③ ④ ⑤ — phần offline của một deck
bash scripts/run_deck.sh "data/raw/<file>.pdf" --eval     # + đo bộ câu hỏi có nhãn
.venv/Scripts/python.exe scripts/try_ask.py <ten>         # hỏi đáp thử trên KB vừa dựng
```

| Bước | Tốn | Chạy lại khi nào |
|---|---|---|
| ① docling | ~2s/trang CPU, miễn phí | đổi file gốc (`--redo`) |
| ② bố cục VLM | 1 lần gọi/trang; đã có trong cache → 0 | đổi file gốc, `VLM_MODEL`, hoặc ép bằng `--pages` |
| ②b chép bảng | 1 lần gọi/bảng; đã có → 0 | khung bảng đổi, `TABLE_MODEL`, hoặc `--pages` |
| ③ dựng document.json | vài giây, miễn phí | luôn dựng lại |
| ④ chunk + nhúng + nạp kho | vài giây; chữ không đổi → 0 lần gọi API | sau ③, đổi luật chunk, đổi `EMBED_MODEL` |
| ⑤ deck_map | tức thì | luôn dựng lại |
| S4 kịch bản | ~1–2 lần gọi LLM mỗi trang | trang có `page_hash` / prompt / model đổi |

---

## 3. Dữ liệu từng tầng

### ③ `ParsedDocument` — tài liệu → trang → block

Chi tiết: [spec/parsed-document.md](../spec/parsed-document.md) · code: `src/parsing/`

```
ParsedDocument   doc_id · source · parser · sections[] · flags[]
 └─ pages[]      page_no · title · section_id · page_hash · layout_error
     ├─ slide_type    section_divider · exercise · content — LUẬT, tính lại mỗi lần nạp
     └─ blocks[]      NỘI DUNG, theo thứ tự đọc
          └─ id · kind · role · content · hrefs · polygon · provenance
```

Mỗi block trả lời ba câu, loại nào cũng vậy:

| | field | |
|---|---|---|
| nói gì | `content` | chữ (`paragraph`) · mô tả hình (`image`) · markdown sinh từ `cells` (`table`) |
| nằm đâu | `polygon` | 4 góc, `[0,1]`, gốc trên-trái |
| tin được không | `provenance` | `text_layer` đúng 100% · `vlm` máy đọc/tả từ ảnh, có thể sai · `manual` người gõ |

`id` cho biết khối từ đâu ra: `p007.v01` = VLM sắp (②), `p007.b03` = docling (trang trượt
kiểm tra bố cục), `p015.m00` = vá tay.

```json
{"id": "p007.v00", "kind": "paragraph", "role": "title", "content": "CÁCH TÍNH LÀM THÊM GIỜ",
 "polygon": [[0.499, 0.094], [0.843, 0.094], [0.843, 0.141], [0.499, 0.141]],
 "provenance": "text_layer"}
```

- **Chương** dựng từ trang mục lục trong 5 trang đầu: trang đầu tiên có tiêu đề khớp một
  mục là trang mở chương. Không có mục lục khớp → 0 chương, không đoán.
- **Link ẩn** (annotation PDF) gắn vào `hrefs` của block chồng lên nó. Block có ≥ nửa số
  dòng là link → `role: "links"` — robot không đọc địa chỉ thành tiếng.
- **Cờ** cho người duyệt: `empty_page` · `image_not_described` · `table_empty` ·
  `layout_failed` · `no_sections`. Block rỗng không có cờ (ảnh nhỏ, ảnh trang trí) bị bỏ.

### ④ `KBChunk` + vector — để TÌM

Chi tiết: [spec/kb-chunk.md](../spec/kb-chunk.md) · [spec/embedding.md](../spec/embedding.md) ·
[spec/search.md](../spec/search.md) · code: `src/kb/`

```
1 trang = 1 chunk chính   (+ 1 vector phụ mỗi ảnh, nếu trang có ≥ 2 ảnh được mô tả)
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
 "token_count": 237, "block_ids": ["p007.v00", "p007.v01", "p007.v02"],
 "provenance": {"text_layer": 1, "vlm": 2}}
```

Trang bị cắt thì chunk mang đuôi `.1`, `.2` (`…#p009.1`); vector phụ của ảnh mang id block
(`…#p019.v02`). Mọi chunk đều trỏ về `page_no` — điều hướng theo TRANG.

Chữ và vector ở **hai file riêng**, nối bằng thứ tự hàng:

```
out/kb/<ten>/chunks.json                              chữ + metadata
out/kb/<ten>/vectors__text-embedding-3-small.npy      ma trận (số chunk × 1536), chuẩn hoá L2
out/kb/<ten>/vectors__text-embedding-3-small.json     rows[i] = chunk_id của hàng i
out/kb/.embed_cache/<model>/<sha1>.npy                cache dùng chung, khoá = hash(model + chữ)
```

Tên model nằm trong tên file vector: đổi model mà quên nhúng lại thì tìm kiếm trả rác
**không báo lỗi** — nhúng tên vào là để không lẫn được.

**Kho vector** (`src/kb/store/`, chọn bằng `VECTOR_DB`): `chroma` ghi đĩa, một collection
`kb__<model>` cho mọi tài liệu, lọc bằng `doc_id` · `inmem` giữ trong RAM, nạp lại mỗi lần
chạy. Kho chỉ lo nhánh vector và là BẢN SAO của `.npy` — lệch với `chunks.json` thì tự nạp
lại, xoá đi thì tự dựng lại, không gọi API.

**Tìm** (`src/kb/search.py`) = hai nhánh, gộp bằng RRF:

```
câu hỏi ─┬─ dense: nhúng câu hỏi (API) → kho vector → top 50 ─┐
         └─ sparse: BM25 (src/kb/sparse/, dựng trong RAM) → top 50 ┴─ RRF K=15, dense 1 : sparse 1
                                                                    → gộp theo trang (điểm max)
                                                                    → top-5
```

Hằng số RRF đo bằng `scripts/tune.sh`, không đoán. Hỏi nội dung thì **lọc** trang phân mục;
hỏi điều hướng ("quay lại phần…") thì **không lọc** — trang mở chương là đáp án đúng.
API nhúng lỗi lúc chạy → tự lùi về chỉ BM25.

### ⑤ `deck_map.txt` — bản đồ bộ slide

```
Bộ slide thoi_gian_lam_viec_chinh_sach_nhan_su · 17 trang · 5 chương
- trang 1–2: Mở đầu (trước chương 1, không phải chương)
- trang 3: Chương 1: THỜI GIAN LÀM VIỆC
- trang 4–5: Chương 2: CHẤM CÔNG
...
```

TRẠNG THÁI gọn đưa thẳng vào prompt runtime — LLM biết bộ slide có những phần nào mà không
phải nhồi cả deck. Dựng bằng luật từ `sections` + `slide_type`, không gọi model.

### S4 `Scenario` — robot NÓI gì

Chi tiết: [spec/scenario.md](../spec/scenario.md) · code: `src/scenario/`

```
Scenario        doc_id · model · prompt_hash · pronunciation_hash
 └─ slides[]    page_no · page_hash · slide_type · flags[] · passes · edited_by
     └─ sentences[]   kind · text · grounding · syllables · prosody
```

```json
{"kind": "delivery", "text": "Cách tính cụ thể ra sao?",
 "grounding": {"type": "structure", "ref": null}, "syllables": 6}
{"kind": "content", "text": "Làm thêm ngày lễ tết tính hệ số 3 ban ngày và hệ số 3.9 ban đêm.",
 "grounding": {"type": "kb_chunk", "ref": "p007.v01"}, "syllables": 19,
 "prosody": {"emphasis": ["3", "3.9"], "pause_before_ms": 0, "speed": 1.0}}
```

- `content` = câu mang thông tin → **bắt buộc** trỏ về một block của trang. Không trỏ = cờ đỏ.
- `delivery` = câu dẫn dắt → không được mang sự thật mới; code kiểm (không số, không thuật ngữ).
- Pass 1 viết; **pass 2 chỉ chạy cho trang trượt bộ kiểm** (đủ ý, nhịp, văn nói…), giữ bản ít lỗi hơn.
- `syllables` do **code** đếm theo `data/pronunciation.json`, không để LLM khai.
- `edited_by: "nguoi"` = đã sửa tay → máy không bao giờ viết đè.

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
  p007.v01 (bảng hệ số)  ◄─── block_ids: [p007.v00, p007.v01, …]  câu "Làm thêm ngày lễ tết… hệ số 3"
                         ◄──────────────────────────────────────── grounding.ref: "p007.v01"
```

Từ bất kỳ câu robot nói hay kết quả tìm kiếm nào cũng lần ngược được về đúng mẩu trên
slide: nó là chữ thật (`text_layer`) hay máy đọc từ ảnh (`vlm`), nằm ở đâu (`polygon`).

---

## 5. Build lại phần đổi thôi

| Cơ chế | Nằm ở | Tác dụng |
|---|---|---|
| khoá trang = hash(model + mẩu chữ của trang) | `layout.json` `pages` | chữ trang không đổi → không gọi lại VLM. Sửa prompt KHÔNG làm cache mất hiệu lực — log ③ báo `prompt cu [...]` |
| khoá bảng = hash(model + id + khung bảng) | `layout.json` `tables` | bảng không đổi → không gọi lại |
| `page_hash` = hash(nội dung + vị trí mọi block) | `ParsedPage` | S4 chỉ viết lại trang có hash đổi |
| cache vector theo hash(model + `text_enriched`) | `out/kb/.embed_cache/` | chữ không đổi → 0 lần gọi API |
| `sync` so metadata từng chunk | kho vector, BM25 | lệch thì nạp lại từ `.npy`, khớp thì thôi |
| `edited_by: "nguoi"` | `SlideScript` | câu người sửa không bị máy ghi đè |
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
| bảng · ảnh có mô tả · link ẩn | 18 · 26 · 57 | 1 · 3 · 1 |
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
| ⬜ S4 Onboarding | kịch bản 51 trang | `src/runtime/cli.py` (thuyết trình thử) cần `scenario.json` |
| ⚠️ nhịp kịch bản | độ lệch chuẩn âm tiết/câu ≥ 6 mỗi trang | Thời gian làm việc: cả deck 6.8 nhưng 9/17 trang còn cờ `monotone_rhythm` |
| ⬜ S2 | `time_budget` theo chương | luật, không gọi model. S4 hiện không có trần độ dài trang — đi theo nội dung |
| ⬜ `slide_type` đủ loại | `title` · `agenda` | đã có `section_divider` · `exercise` · `content` |
| ⬜ S6b | TTS theo từng câu, một giọng duy nhất | |
| ⬜ S7 | người duyệt **chỉ phần bị cờ**, đọc + nghe | |
| 🚫 reranker | đã chốt bỏ (2026-10-01) | cố định top-5; cải thiện ở RRF / dữ liệu / viết lại câu hỏi |
