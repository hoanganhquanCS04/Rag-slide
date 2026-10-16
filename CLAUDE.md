# CLAUDE.md — Context cho AI coding agent

Đọc file này trước khi sinh bất kỳ code nào trong repo.

---

## 1. Hệ thống này là gì

Agent tự thuyết trình một bộ slide và xử lý tương tác thời gian thực từ khán giả:
trả lời câu hỏi và điều hướng slide, trong khi luôn đồng bộ với trạng thái trình chiếu
và có nguồn truy nguyên được.

**Insight cốt lõi chi phối mọi thiết kế:**

> Slide là bản nén mất mát của kiến thức. Người thuyết trình là nơi chứa phần bị mất.
> Toàn bộ nhánh offline tồn tại để tái tạo phần đó.

Đặc điểm deck mục tiêu: ~20 trang, **ít chữ nhiều hình** (trung bình < 20 từ/trang).
Đây không phải chi tiết phụ — nó là lý do RAG text thuần thất bại và phải có tầng VLM.

---

## 2. Bốn nguyên tắc bất di bất dịch

1. **Offline không có ràng buộc thời gian, online thì có.** Mọi thứ tính trước được
   phải đẩy vào offline. Runtime budget: < 2.5s tới byte audio đầu tiên.
2. **Dữ liệu chính xác KHÔNG BAO GIỜ để model sinh lại.** Title, chart series, ô bảng
   đã có bản đúng 100% từ parsing pptx. Đưa cho LLM là tự tạo cơ hội bịa lại cái đang đúng.
   Nguồn gốc ghi theo LOẠI khối (ảnh = `ParsedImage`), không còn
   trường `provenance` riêng (bỏ 2026-10-12 — v0 mọi chữ đều do VLM đọc, trường đó không phân biệt gì).
3. **Ảo giác offline nguy hiểm gấp nhiều lần online.** Bịa runtime sai một lần;
   bịa trong `Scenario` thì robot nói sai ở MỌI buổi thuyết trình.
   → Mọi câu **nội dung** phải có `grounding`; `null` = flag đỏ bắt người duyệt.
4. **Tự nhiên là YÊU CẦU, không phải điểm cộng.** Robot nghe như đọc bản tin thì
   hệ thống thất bại dù mọi chỉ số khác đạt.
   → S4 tách hai loại câu, hai chế độ grounding khác nhau:

   ```
   kind: "content"   grounding ∈ {slide_repr, kb_chunk, speaker_notes}   null = CỜ ĐỎ
   kind: "delivery"  grounding ∈ {structure, style}
                     KHÔNG mang thông tin sự thật mới — validate BẰNG CODE
   ```

   `ungrounded_rate` chỉ tính trên câu `content`. Câu `delivery`: **10–25% tổng âm tiết**,
   10% là SÀN CỨNG (pass 2 không được cắt xuống dưới).

**NT1 và NT4 không mâu thuẫn**: mọi thứ làm cho tự nhiên đều tính trước được ở offline.
**NT3 và NT4 thì có** — gỡ bằng đúng cách tách hai loại câu ở trên, KHÔNG nới NT3.

---

## 3. Kiến trúc

### 3.0 HIỆN TRẠNG v0 — đọc trước khi dùng phần còn lại ★

Thiết kế gốc giả định **hai nguồn tách biệt**: deck `.pptx` để trình chiếu, và
`source/*.pdf` làm tài liệu nền. Thực tế đang làm **KHÔNG như vậy**:

```
MỘT FILE PDF DUY NHẤT  →  vừa là DECK vừa là KB
```

Ba hệ quả bắt buộc phải chấp nhận, không được lờ đi:

1. **Mất bản đúng 100% của `chart_data`, `tables`, `build_steps`.** Chúng đến từ XML
   của pptx; PDF không có. NT2 vẫn giữ nguyên nhưng phạm vi co lại về **0**: S0 không đọc
   text layer PDF (bỏ 2026-10-08 — text layer Onboarding thiếu dấu cách, đem thay chữ VLM làm
   dính 126 từ), nên MỌI chữ do VLM đọc, không khối nào chắc đúng 100%. Phân biệt
   "chữ trên slide" với "mô tả ảnh" bằng LOẠI khối (`ParsedImage`).
2. **S3 Alignment suy biến.** Align slide với nguồn mà nguồn chính là slide → mỗi
   slide map vào chính nó, không sinh thêm tri thức nào. Chưa có tài liệu nguồn
   riêng thì **S3 chạy cũng vô nghĩa**.
3. **Hệ thống co lại thành "robot mô tả slide"** đúng như
   [s5 §8](docs/offline/s5-kb-construction.md) đã cảnh báo. Độ sâu trả lời giới hạn
   ở mức mô tả, ngưỡng từ chối phải nâng cao. **Tuyệt đối không để LLM tự phình
   kiến thức từ slide ra để bù** — đó là nướng ảo giác vào hệ thống vĩnh viễn.

Muốn thoát khỏi giới hạn này chỉ có một cách: **đưa vào tài liệu nguồn thật**
(giáo trình, paper, tài liệu kỹ thuật) tách khỏi deck. Không có thì chấp nhận
mức "mô tả slide".

**Đã làm được đến đâu:**

```
✅ S0   PDF → ParsedDocument      src/offline/parser/      2 deck: Onboarding 51 trang, Thời gian làm việc 17 — chương từ mục lục
✅ S5   chunk                     src/offline/chunker/     89 + 18 chunk, 1 trang = 1 chunk (không vector ảnh phụ) — khuôn như src/offline/parser/
✅ S5   vector                    src/components/embedders/ · src/components/vectordb/ — khuôn như src/offline/parser/
✅ S5   tìm                       src/utils/ensemble_search.py · src/components/keyword_search.py   hybrid dense+BM25 gộp RRF, cố định top-5 — đo ở src/evaluation/
🚫 S1   BỎ — gộp vào S0                            xem §5
🚫 S3   BỎ ở v0 — align vào chính mình thì vô nghĩa
✅ S2a  slide_type               luật, ParsedPage.slide_type — divider · exercise · content
⬜ S2b  time_budget              luật, chưa code
🟡 S4   kịch bản                  src/offline/scenario/, gpt-5-mini — đủ 2 deck, nhịp chưa đạt gate
🟡 S6b  pronunciation.json       KHO CHUNG data/pronunciation.json, sinh TỪ KỊCH BẢN — TTS chưa có
⬜ S7   người duyệt phần bị flag  chưa có dòng nào
```

### 3.1 Sơ đồ đích (khi đã có tài liệu nguồn riêng)

```
NHÁNH DECK                          NHÁNH NGUỒN
deck.pdf  ← v0: CÙNG MỘT FILE →     source/*.pdf
  S0 Ingest                           S5 KB Construction
  S1 Slide Understanding              (chunk, enrich, index)
  S2 Deck Structure                         │
     ├──────────────┐                       ▼
     │              │                 [kb_chunks] SHARED
     ▼              ▼                       │
 S6a BUILD      S3 Alignment ◄──────────────┘
 SLIDEINDEX         │                  ← v0: suy biến, bỏ qua
     │              ▼
[slide_index_   S4 Scenario
  {deck}]           │
     │              ▼
     │          S6b TTS + qa_cache
     └──────┬───────┘
            ▼
        S7 HITL Review  (đọc + NGHE)
            │
       DeckBundle → Runtime (R1–R7)
```

**Thứ tự chạy thật** (số stage là lớp khái niệm, KHÔNG phải thứ tự):

```
S0 → S5 (chunk+embed) ─┬─► S6a (dùng lại index S5) ──────────┐
    → S2 (luật)        └─► S4 kịch bản → phát âm → S6b TTS ──┴─► S7
```

- **S0 làm hết phần hiểu trang**: bố cục + ảnh (VLM cả trang), `sections` (từ trang mục lục),
  `slide_type` (luật). Không còn stage S1 riêng.
- **S2 không gọi model**: `sections` đã có từ S0, `time_budget` chia theo số trang.
- **S4 đọc từng trang một** — không nhồi cả deck vào prompt.
- **MỘT index duy nhất** ở v0: KB và slide index là một, vì KB đến từ chính file slide.

---

## 4. Stage: input → output

| Stage                       | Input                                    | Output                                                                                      | Model                         |
| --------------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------- | ----------------------------- |
| S0 Ingest                   | `deck.pdf` (v0; đích: `.pptx`)     | `ParsedDocument` (đích:`RawSlide[]`)                                                  | docling + VLM qua API         |
| ~~S1 Slide Understanding~~ | —                                       | **BỎ** — gộp vào S0 (xem §5)                                                     | —                            |
| S2 Deck Structure           | `ParsedDocument`                       | `sections` (đã có từ S0) + `time_budget`                                            | — (luật, không gọi model) |
| S5 KB Construction          | `source/*.pdf`                         | `KBChunks[]` + `KBIndex`                                                                | LLM + VLM + embedding         |
| ~~S3 Alignment~~           | —                                       | **BỎ ở v0** — §3.0: nguồn chính là deck, align vào chính nó thì vô nghĩa | —                            |
| S4 Scenario                 | tất cả trên                           | `Scenario[]`                                                                              | LLM, 2 pass                   |
| S6a Build SlideIndex        | `KBChunk[]` (dùng lại index của S5) | — (dùng lại index S5; `deck_map.txt` BỎ 2026-10-13)                                   | embedding qua API             |
| S6b Precompute              | `Scenario[]` + `pronunciation.json`  | `precomputed/`                                                                            | TTS + embedding               |
| S7 HITL                     | mọi artifact + flags                    | `DeckBundle` verified                                                                     | — (người, đọc + nghe)    |

---

## 5. Luật riêng từng stage (agent hay làm sai chỗ này)

### S0 — v0: PDF qua docling (`src/offline/parser/`)

Xem [docs/spec/parsed-document.md](docs/spec/parsed-document.md) cho schema đầy đủ.

- Thứ tự đọc đi theo `body.children` của docling, **KHÔNG** đọc tuần tự mảng `texts[]`
- Chuẩn hoá toạ độ: gốc DƯỚI-TRÁI của docling → **TRÊN-TRÁI**, `[0,1]`, lưu thành `polygon`
  4 góc. **Ngoại lệ `.pptx`**: docling gắn nhãn `BOTTOMLEFT` nhưng số đo từ ĐỈNH — không lật
- **Bỏ header/footer/số trang lặp** (nhãn `page_header`/`page_footer` của docling) — không phải
  nội dung, không lưu. Chương lấy từ **trang mục lục** (`sections.py`), không từ thanh header
- **Một đường duy nhất: docling VlmPipeline** (`src/offline/parser/variants/docling.py`, `src/prompts/s0_vlm_layout_prompt.md`),
  VLM **qua API**, mỗi **TRANG** một request: nhìn ảnh CẢ trang, trả bố cục + chữ + bảng (HTML →
  `cells`). Mọi chữ là `vlm`. Xét ảnh (`TRANG_TRI` / sơ đồ / ảnh có chữ) là bước ② riêng
  (`pictures.py`), chép lại bảng là ②b (`tables.py`) — `BaseParser.post_processing`
- Prompt tiếng Việt (người dùng chốt 2026-10-09), khoá chữ `paragraph`, loại khối viết thường —
  `normalize_elements` đổi về dạng dots_ocr (loại lạ thì `parse_dots_json` bỏ IM LẶNG).
  ⚠️ **Chấp nhận toạ độ `[y,x]`**: prompt tiếng Việt làm Gemma trả `[y,x]` cho một phần khối, trộn
  ngay trong một trang. Thêm câu dặn toạ độ tiếng Anh: Onboarding 30% → **16%** khối chữ/bảng (74/471,
  21/51 trang; p24–p26 gần cả trang). Prompt tiếng Anh từng cho 279/282 đúng. Hệ quả: sai vùng cắt
  bảng ②b, khung đỏ ②, `slide_type`, gắn link — chữ vẫn đúng. KHÔNG tự lật bằng code (không biết khối nào)
- `GemmaVlmPipeline` vá: toạ độ 0–1000 (docling tưởng pixel Qwen2-VL) · bbox nhiều khung / 2 điểm →
  hộp bao (không vá thì mất trang bìa + 3 ví dụ có số, Thời gian làm việc p1, p8) · bỏ ký hiệu
  Markdown prompt gốc sinh ra · mảng bọc trong object `{"elements": [...]}` (p4, p7 mất cả trang) ·
  khoá `content` thay `text`. JSON hỏng → mất CẢ trang → cờ `empty_page`; raw ở `logs/vlm_pipeline/`
- Mô tả TỪNG ảnh cắt rời đã bỏ: không biết ảnh nào chỉ để trang trí (Onboarding: ảnh Tết tả thành
  Trung Thu) — mô tả ảnh viết trong lượt đọc cả trang
- Ảnh không có mô tả phải ghi `why_empty` — phân biệt *chưa gọi* (`area_below_threshold`)
  với *gọi mà fail* (`api_error`) và *đủ to mà không có* (`not_described`)
- KHÔNG đọc text layer PDF, KHÔNG OCR (OCR chậm 8.4×, phá dấu tiếng Việt). PDF chỉ dùng để vẽ ảnh
  trang. Link ẩn sau chữ (annotation) KHÔNG đọc (bỏ 2026-10-12): `role: links` chỉ bắt URL in ra chữ
- `page_hash` = SHA(nội dung + polygon từng block), cho incremental build
- Mỗi block có `page_no` · `content` · `polygon`. MỘT file, vừa để người đọc vừa để
  pipeline chạy — trường rỗng không ghi, không lưu thứ suy ra được

**Mấy luật dưới đây chỉ áp dụng khi quay lại `.pptx`** (đích, chưa làm):

- Lọc hidden slide (`show="0"`) — không lọc là lệch số trang
- Chart data lấy từ **XML**, cấm để VLM đọc từ pixel
- Cắt ảnh từ **PNG render theo bbox**, không lấy file trong `ppt/media/`
- Duyệt group **đệ quy**, giữ `group_path`
- Hash = SHA(XML shape tree normalized + bytes ảnh), KHÔNG hash cả file pptx
- Chuẩn hoá bbox EMU → `[0,1]`

### S1 — BỎ, gộp vào S0 ★

Bản cũ đặt S1 là một vòng gọi LLM riêng mỗi trang, sinh `message` (trang *muốn nói gì*),
`relations`, `visual_elements[].role`. **Bỏ hết.** Lý do: hai thứ đã có sẵn, hai thứ làm
được bằng luật, một thứ sinh ra chỉ để nhồi ngữ cảnh toàn cục vào prompt — mà thiết kế
này không nhồi.

| S1 cũ định sinh | nay lấy ở đâu                                                                                                      |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `description`    | VLM mô tả ảnh ngay ở**S0** — đã có, đo được là dùng tốt                                           |
| `slide_type`     | **LUẬT** trên `ParsedDocument`, không gọi model — `section_divider` ĐÃ CÓ ở `src/offline/chunker/base.py` |
| `entities`       | trích ở S0 (thuật ngữ, tên hàm, tên riêng)                                                                     |
| `message`        | **BỎ**                                                                                                          |
| `relations`      | **BỎ** — "sơ đồ bên trái" giải bằng `bbox`, không cần model                                         |

**Luật `slide_type`** — đo trên `3_datavisualization`, **40/40 đúng**, không tốn API:

```
title            trang số 1                                           ⬜ chưa code
agenda           có mẩu chữ mở đầu bằng "1 " và >= 3 dòng             ⬜ chưa code
section_divider  đúng 1 tiêu đề, tâm giữa trang                       ✅ ĐÃ CÓ
                 (cy >= 0.30, cx trong [0.35, 0.65])
exercise         tiêu đề mở đầu bằng "Bài tập"                       ✅ ĐÃ CÓ
content          còn lại                                              ✅ ĐÃ CÓ
                 -> ParsedPage.slide_type (src/models/parsed_document.py)
```

**Luật nằm ở MỘT chỗ: `ParsedPage.slide_type`** (property — tính lại mỗi lần nạp, KHÔNG ghi
ra JSON: deck mới gần như toàn `content`, ghi ra là nhiễu). `flags.py`, `chunker/`, S4 đều
chỉ đọc property đó.

- `flags.py` không bắn `empty_page` ở trang phân mục.
- KB coi trang bài tập là `content` (có nội dung thật, phải tìm được).
- S4 viết lại trang khi `slide_type` đổi. Trang `exercise`: 2–4 câu, ĐỌC yêu cầu, không giảng.
- `title` và `agenda` chỉ 2 trang trong 40, lợi ích nhỏ — chưa làm.

`exercise` chỉ xét TIÊU ĐỀ: dò "yêu cầu" trong cả trang là bắt nhầm deck nhân sự
(Onboarding p26 "Chấm công: Quản lý yêu cầu của CBNV").

> Nhược điểm phải nhận: luật fit trên MỘT deck, deck khác bố cục khác là gãy. Nhưng gãy
> thì nhìn bảng phân loại là thấy ngay, còn LLM gán sai thì im lặng. §2 NT2 ưu tiên
> deterministic.

- `polygon` thay cho `relations`: "sơ đồ bên trái" → so `block.center[0]` của các mẩu trên
  trang. Đo được ở p19: code `cx=0.28` (trái), chart `cx=0.71` (phải).
- `pronunciation.json` **KHÔNG sinh ở đây** (bản cũ sinh từ `entities`/chunk). Nó sinh TỪ
  KỊCH BẢN S4 — chỉ từ robot thật sự nói mới cần cách đọc. Đo trên 3_datavisualization:
  bảng quét từ chunk có 10/35 mục kịch bản không bao giờ nói, và thiếu 16 từ kịch bản có
  nói. Xem [docs/spec/pronunciation.md](docs/spec/pronunciation.md).

### S2 — co lại, phần lớn đã deterministic

- **`sections` KHÔNG cần LLM.** Dựng từ **trang mục lục** ngay ở S0 (`sections.py`): đọc tên
  chương, trang ĐẦU TIÊN có tiêu đề khớp một mục là trang mở chương, kéo tới trước trang mở
  chương kế tiếp. Đo: Onboarding 5/5, Thời gian làm việc 5/5 mục khớp. Không có mục lục → 0
  chương, KHÔNG đoán.
- **`time_budget`**: chia theo số trang `content` của từng section. Không cần model.
- **`concept_map` / `dependencies` / `arc`: BỎ.** Chúng sinh ra để bơm ngữ cảnh toàn cục
  vào prompt. Thiết kế này không làm thế — S4 đọc nội dung CHÍNH TRANG ĐÓ.
- Validate `sections` bằng code, không tin LLM: phủ kín · không chồng · liên tục.
  Vi phạm thường là lỗi thật của bộ slide → báo S7, KHÔNG tự sửa.

### S3 — BỎ ở v0

§3.0: KB đến từ chính file slide, nên align slide vào nguồn là align nó vào chính nó.
Luật đầy đủ của S3 giữ lại ở đây cho v1, khi có `source/*.pdf` riêng — xem lịch sử git.

### S4

Spec đầy đủ: [docs/spec/scenario.md](docs/spec/scenario.md).

- **Chạy song song theo SECTION, tuần tự TRONG section** — trang sau đọc kịch bản trang
  trước cùng section để không lặp ý (7 trang liền cùng tên "Đồ thị dạng đường"). Đây là
  thứ thay cho `message` đã bỏ.
- **Không có `time_budget`, KHÔNG có trần độ dài trang** — độ dài đi theo nội dung, nhưng
  **bắt buộc ĐỦ Ý**: người nghe phải hình dung được hết thông tin trên trang. Code kiểm:
  mỗi khối chữ có câu trỏ vào (`block_not_covered`), mọi con số trên slide được nói
  (`number_missing`). Không đọc thành tiếng: link, email, số điện thoại, mã tài liệu.
  Trần 2 câu chỉ còn cho trang chuyển chương / lời kết.
  Pass 2 thành **pass sửa lỗi validate**, chỉ chạy cho trang trượt.
- **Bộ kiểm code v0 (chốt 2026-10-06) chỉ còn:** 🔴 `bad_grounding` — câu `content` trỏ ĐÚNG một
  khối của trang, câu `delivery` không mang số / thuật ngữ (không thì thông tin lách qua câu
  không cần nguồn) · 🟠 đủ ý `block_not_covered`, `number_missing` · 🟡 `unknown_pronunciation` ·
  🔴 `empty_script`. Các luật "nói tự nhiên" bên dưới (nhịp, tỉ lệ delivery, văn viết, ký hiệu,
  ≤ 30 âm tiết…) **chỉ còn là hướng dẫn trong prompt, code KHÔNG kiểm**.
- **`syllables` do CODE tính** theo `pronunciation.json`, không để LLM tự khai.
- **Input mỗi trang: nội dung CHÍNH TRANG ĐÓ** (`KBChunk` của trang) + `title` trang
  trước/sau + `slide_type`. **KHÔNG nhồi cả deck vào prompt.**
- Mọi câu `content` phải có `grounding`; `null` → flag đỏ (NT4 §2)
- `slide_type` quyết định độ dài: `section_divider` thì một câu chuyển là xong,
  `title`/`agenda` cũng ngắn. Chỉ `content` mới viết dài. `section_divider` ở **trang cuối
  deck** ("THANK YOU!") là **lời kết**, không phải chương mới (`is_closing`).
- Tiếng Việt: **190–210 âm tiết/phút**. Đếm âm tiết, KHÔNG đếm từ,
  và đếm **theo `pronunciation.json`** (viết tắt đọc thế nào thì đếm thế ấy)
- **Thuật ngữ tiếng Anh / viết tắt: CHỈ dùng từ có trên chính trang đó** — code kiểm
  (`term_not_on_page`). Kho phát âm KHÔNG vào prompt (nó là của cả kho, không phải của trang).
- **Phát âm đi SAU kịch bản:** S4 → `scripts/extract_terms.py` gom từ lạ trong kịch bản vào
  kho chung → người chốt `say` → chạy lại S4 = chỉ đếm lại âm tiết, không gọi LLM.
  **Máy đề xuất, NGƯỜI chốt** — không tách nổi tiếng Việt không dấu khỏi tiếng Anh bằng luật.
- **Tự nhiên — 7 đòn bẩy, xếp theo tác động:**
  1. **Văn nói ≠ văn viết** (nguyên nhân số một). Cấm `việc…`, `sự…`, `được thực hiện bởi`,
     danh từ hoá. Câu chủ động, mệnh đề ngắn.
  2. **Nhịp** — trộn câu 6–12 / 15–20 / 25–30 âm tiết. Đo bằng **độ lệch chuẩn ≥ 6**.
  3. **`prosody` mỗi câu**: `emphasis[]`, `pause_before_ms`, `speed`
  4. **Từ diễn ngôn đặt ĐÚNG ranh giới cấu trúc** — ranh giới lấy từ `sections` (đã có
     từ S0) và `slide_type`. Rải ngẫu nhiên tệ hơn không có.
  5. Câu hỏi tu từ ở trang `section_divider` — đó là ranh giới chương
  6. Không đọc bullet, không đọc bảng theo hàng, **không đọc mã nguồn thành tiếng** —
     chỉ nói TÊN HÀM và nó làm gì. Người thật không đọc "pi-eo-ti chấm ép-rờ-bo mở
     ngoặc", họ nói "gọi errorbar, truyền thêm sai số trục y".
     Hệ quả: bảng phát âm chỉ cần ~25 mục, không phải 335.
     Cùng lý do: **số, ngày, ký hiệu viết theo cách NÓI** — "44 giờ một tuần", "ngày 25
     tháng 10", không "44h/tuần", "25/10". Code cấm `/ % & + < > @` và số dính chữ
     (`written_symbol`); deck nhân sự có 271 dấu `/`.
  7. `max_syllables` mỗi câu = **30** (không phải 40) — ranh giới ngắt của R7 là ranh
     giới câu, câu 40 âm tiết ≈ 12s không ngắt được
- **Pass 2 cắt theo thứ tự: trùng lặp ở câu `content` TRƯỚC, câu `delivery` SAU CÙNG.**
  Sàn 10% `delivery` là cứng. Không có luật này thì cân giờ vài vòng là kịch bản khô lại.

### S6a — dùng lại index của S5, không dựng index riêng

- **Một vector cho mỗi chunk**, đã có ở `src/offline/embedder/cli.py`. Multi-field **BỎ**: nó cần
  `message` / `desc` / `relations` tách riêng, mà §5 S1 đã bỏ hết.
- Tìm là **hybrid dense + BM25, gộp bằng RRF** — xem
  [docs/spec/search.md](docs/spec/search.md). Đo được: hybrid hơn từng nhánh riêng.
- **Self-retrieval / quét chunk trùng: BỎ (2026-10-13, `audit.py` đã xoá).** Câu hỏi lấy từ chính
  văn bản nên gần như luôn 100% — đề bài là đáp án; chunk trùng trong một trang thì `search` đã gộp
  theo trang. Đo bằng bộ câu hỏi có nhãn `data/eval/<doc_id>.queries.json` (`src/evaluation/eval.py`)
  và quét tham số RRF (`scripts/tune.sh`).
- **`deck_map.txt` BỎ (2026-10-13)** — nó chỉ viết lại `doc.sections` (dò ở S0, `parser/sections.py`)
  thành chữ cho prompt runtime. Chương / khoảng trang vẫn có trong bộ nhớ sau `enrich()`. Prompt runtime
  không còn danh sách chương: câu hỏi cấu trúc ("bài gồm những phần nào") phải tìm ra trang mục lục —
  đo được: hỏi "nội dung chính gồm những phần nào" ra p2 hạng 1 và trả lời đủ; hỏi chung chung "bài này
  gồm những phần nào" KHÔNG ra p2 → từ chối. Hỏi khoảng trang của chương / tổng số trang → từ chối.
- Tên file vector phải nhúng `model_id` — đổi embedding model mà không sinh lại thì
  runtime truy vấn index cũ bằng vector mới và trả rác **mà không báo lỗi**

### S6b — Precompute

- TTS cache key = **hash từng câu** (phần tốn tiền nhất, phải reuse)
- `qa_cache` match ngưỡng > 0.88 mới dùng
- **Một `voice_id` duy nhất** cho cả precomputed lẫn streaming runtime. Lệch giọng giữa
  câu kịch bản và câu trả lời là artifact chói tai nhất của cả hệ thống.
- `pronunciation.json`: xem [docs/spec/pronunciation.md](docs/spec/pronunciation.md).
- Hash `pronunciation.json` ghi vào `Scenario`; S6b **so hash trước khi synth**.
  Lệch bảng = S4 đếm một đằng, TTS đọc một nẻo, timing sai mà không ai thấy.
  Hash CHỈ tính trên các mục kịch bản đó dùng — kho chung, sửa từ deck khác nói không được
  làm dừng deck này.
- Xuất **bản nghe thử** (3 trang + mọi câu bị flag) cho S7

---

## 6. Runtime (R1–R7)

|              | Lớp                    |              | Lớp                    |
| ------------ | ----------------------- | ------------ | ----------------------- |
| **R1** | Intake & fast-path      | **R5** | Streaming speech        |
| **R2** | Slide navigation        | **R6** | State machine & sync    |
| **R3** | Context-aware rewriting | **R7** | Interrupt & turn-taking |
| **R4** | Grounded answering      |              |                         |

**R2, R3, R4 KHÔNG phải ba lần gọi** — chúng là ba mặt của MỘT lần gọi LLM.
Escalation không phải lớp riêng, nó là nhánh kết thúc của R4.

```
input → regex fast-path ──(khớp)──→ goto_slide()          [~5ms]
      └─(không khớp)─→ phát filler prerecorded (song song)
                     └→ 1× LLM function-calling
                        (state slide + slide_index + lịch sử)
                          ├→ goto_slide / find_slide → confidence gate
                          ├→ search_kb(query đã rewrite) → generate streaming → TTS theo câu
                          └→ meta_action
```

> **HIỆN TRẠNG v0 (2026-10-14): runtime CHỈ hỏi đáp** — `src/chat_engine.py`: tìm
> (`vector_search` + `keyword_search` → `ensemble_search`) → 1 lần LLM (`r_qa.md`, chỉ `answer` | `escalate`) → code kiểm nguồn. Điều hướng
> (goto / hỏi lại / cổng biên RRF), vòng thuyết trình (`session.py`), fast-path, R3a đã BỎ khỏi code —
> bản cũ xem git. Phần dưới là ĐÍCH; luật về điều hướng / cổng chưa có code nào áp dụng.

**Luật cứng:**

- **Đúng 1 lần gọi LLM** cho routing + rewrite + chọn tool. KHÔNG tách router riêng.
  (R3a là bước deterministic, KHÔNG phải lần gọi model thứ hai.)
- **v0: mỗi câu hỏi trả lời ĐỘC LẬP** (chốt 2026-10-06) — không lịch sử hỏi đáp trong prompt,
  không xử lý câu nối tiếp. Câu cụt ("vậy hạn chót là ngày nào") tìm bằng chính nó là lạc đề;
  làm cho đúng phải thêm một lần gọi LLM viết lại câu hỏi trước khi tìm (đã thử: đúng nhưng chậm
  thêm 2–4s) — để sau.
- **KHÔNG nhồi index hay bảng toàn bộ vào prompt.** Ranh giới phân loại:

  ```
  TRẠNG THÁI  -> inline, bounded   "tôi đang nhìn gì" — không index nào trả lời được
                 KBChunk trang hiện tại   (v0 không có history, không có deck_map)
  TRI THỨC    -> TRUY XUẤT         slide_index · concept · section · KB chunk
  ```

  Prompt mục tiêu **~1.3k token**, không phải 3.6k.
- **R2 = retrieve → LLM chọn.** KHÔNG có reranker.

  - hybrid dense + BM25 gộp bằng RRF (`src/utils/ensemble_search.py`) → **top-k = 5, CỐ ĐỊNH** →
    **cả top-5** vào prompt → LLM chỉ VERIFY và CHỌN trong 5, kèm lý do (không reranker
    lọc xuống 3 nữa: top-3 trượt 7/200 câu Onboarding mà top-5 có)
  - **Nhánh điều hướng phải TẮT lọc trang phân mục**: hỏi "quay lại phần đồ thị ba chiều"
    thì trang MỞ CHƯƠNG mới là đáp án đúng (`--no-filter`)
  - **Reranker: BỎ (chốt 2026-10-01).** Không chạy local (cùng lý do đã bỏ `bge-m3`), và
    không chờ quyền `/rerank` trên API key nữa (cổng trả 403 `capability_not_allowed`).
    Muốn xếp hạng tốt hơn thì làm ở tầng gộp (RRF K, trọng số — `scripts/tune.sh`), ở dữ
    liệu (chunk, mô tả ảnh) hoặc viết lại câu hỏi.
  - ⚠️ **Hệ quả: KHÔNG có confidence gate calibrate được.** Phải chấp nhận và ghi rõ,
    đừng lờ đi:

    ```
    §11 harmful_jump < 2%    KHÔNG ĐO ĐƯỢC chính xác — không có điểm calibrate được
    §10 cấm dùng điểm LLM tự khai làm gate   — vẫn cấm, không nới
    ```

    Cổng dùng **biên RRF** (`rrf(top1) − rrf(top2)`) làm phanh, đặt ngưỡng **rộng tay về
    phía hỏi lại**: thà hỏi nhiều còn hơn nhảy sai. Config khai rõ `calibrated: false`.
    Biên RRF **không phải** xác suất, chỉ là thứ tự — xem [search.md §4](docs/spec/search.md).
  - Trượt ở tầng truy xuất thì tầng LLM KHÔNG cứu được → theo dõi `recall@5`. **KHÔNG nới
    `k`** (chốt 2026-10-01) — trượt thì sửa ở RRF / dữ liệu / viết lại câu hỏi.
- **R3 tách đôi để gỡ vòng tròn** (muốn truy xuất cần query đã rewrite, muốn rewrite
  cần LLM, LLM chạy sau truy xuất):

  - **R3a — mở rộng query, DETERMINISTIC, ~5ms, không gọi model.** Khớp chuỗi từ chỉ trỏ
    vào `relations[].visual` / `visual_elements`, nối thêm `entities` trang hiện tại
    → `query_expanded` dùng để TRUY XUẤT.
  - **R3b — rewrite thật, trong lần gọi LLM duy nhất.** `query_rewritten` là tham số tool;
    `text` GỐC mới là thứ dùng để sinh câu trả lời.
- **Confidence gate:** `margin = rrf(top1) − rrf(top2) < threshold` → KHÔNG nhảy,
  hỏi lại + thumbnail.

  - Score lấy từ **biên RRF** (không có reranker — xem trên). KHÔNG dùng điểm LLM tự khai
    (tự tin thái quá có hệ thống).
  - Ngưỡng **fit trên bộ eval 50 câu có nhãn**, KHÔNG đoán. Điểm vận hành:
    `harmful_jump < 2%`, chấp nhận `ask_rate ~15%`. Để trong config, không hardcode.
  - Chỉ có 1 ứng viên trên ngưỡng sàn → coi như `margin = 0` → hỏi lại.
- **QA_CURRENT trả lời thẳng trong lần gọi routing**, không gọi tool rồi gọi LLM lần nữa
  (tiết kiệm 400–700ms). Bắt buộc kèm `grounding` trỏ vào `chunk_id` của trang, validate ref bằng code.
  Nội dung trang đã qua S7 duyệt nên nó LÀ nguồn đã kiểm, không phải trí nhớ của model.
- **Ma trận ngắt (R7):** điều hướng → ngắt ngay ở ranh giới câu · hỏi nội dung → đợi hết
  trang (trừ khi hàng đợi > 3) · meta → áp dụng ngay, KHÔNG ngắt lời · đang `navigating`
  → đợi ack xong đã.
- **Kiểm tra ngắt sau MỖI CÂU**, không liên tục và không sau mỗi trang.
- **`at_slide` chụp lúc NHẬN câu hỏi**, không phải lúc xử lý — nếu không R3 giải sai tiền ngữ.
- **Orchestrator là nguồn chân lý duy nhất về state.** Renderer phải ngu, chỉ nhận lệnh + ack.
  Không ack trong 500ms → retry rồi mới cập nhật state.
- **TTS phải streaming theo câu**, cắt theo dấu chấm từ token stream. Đợi generate xong là +2–3s.
- **Resume phải nói ra miệng.** Nhảy lặng lẽ về trang cũ thì khán giả mất dấu.
- Câu trả lời **2–4 câu**, không phải 3 đoạn văn. Ép cả prompt lẫn `max_tokens`.
- Retrieval score thấp → **escalate người thật**, KHÔNG bịa.

---

## 7. Cấu trúc lưu trữ

### 7.0 HIỆN TRẠNG v0 — file JSON trên đĩa + kho vector (inmem | chroma) ★

Chưa có tầng shared/per-deck. Nguồn là file JSON + `.npy` để mở ra xem; kho vector chỉ là
BẢN SAO để tìm, xoá đi thì tự dựng lại từ `.npy`, không tốn API.

**Code** — `src/`, khuôn theo `minhbtrc/chatbot-template` (đổi 2026-10-16): `chat_engine.py` ở ngoài
cùng, linh kiện thay được ở `components/`, các bước chuẩn bị dữ liệu ở `offline/`:

```
src/chat_engine.py   HỎI ĐÁP (online) — ChatEngine: vector_search · keyword_search -> ensemble_search
                     (RRF) -> _prepare_context -> 1 lần LLM -> _check_answer (kiểm nguồn).
                     API nhúng lỗi -> câu đó chỉ BM25. Chỉ online, KHÔNG cắt chunk / nhúng
src/models/          schema Pydantic dùng chung — parsed_document.py (S0) · kb_chunk.py (S5) ·
                     scenario.py (S4) · runtime.py (RuntimeConfig · Reply · Answer)
src/components/      LINH KIỆN: làm một việc chung, không biết gì về slide, thay được cách làm
  embedders/           nhúng chữ -> vector — base.py · cache.py · factory.py · variants/openai.py
  vectordb/            kho vector (CHỈ dense) — base.py · factory.py · variants/{inmem,chroma}.py
  keyword_search.py    BM25Index — bảng BM25 trong RAM, 1 cách làm nên 1 file (không base/factory)
src/offline/         CHUẨN BỊ DỮ LIỆU trước, chạy 1 lần, lưu ra file — mỗi bước có cli.py
  parser/              S0 — cli.py · base.py · variants/docling.py (biến thể DUY NHẤT, cli gọi thẳng) · vlm.py ·
                       enrich.py (chương → cờ) · sections · flags · pictures (②) · tables (②b) · show.py
  chunker/             S5 chunk — cli.py · factory.py · base.py · variants/page.py
  embedder/cli.py      S5 nhúng cả bộ chunk -> .npy + nạp kho (gọi components/embedders + vectordb)
  scenario/            S4 — cli.py · generate.py · validate.py
src/utils/           dụng cụ nhiều nơi dùng — config.py (gốc repo, .env, MODEL_ID, PROMPTS_DIR,
                     OUTPUT_DIR) · llm.py · vectors.py · filters.py · tokens.py · syllables.py ·
                     ensemble_search.py (RRF_K · W_DENSE · W_SPARSE · POOL · TOP_K · rrf_fuse · group_by_page)
src/evaluation/      đo tìm kiếm — eval.py (bộ câu hỏi có nhãn; SearchSetup = cùng 3 bước như ChatEngine,
                     không kéo LLM) · tune.py (quét K + trọng số RRF)
src/prompts/         MỌI prompt (*.md)
src/output/          MỌI artifact pipeline sinh ra (gitignore)
```

Khuôn linh kiện có NHIỀU biến thể (chunker, embedders, vectordb): `base.py` (ABC + phần dùng chung) + `variants/` + `factory.py` (dict
`"module:Class"` — đường dẫn ĐẦY ĐỦ `components.vectordb.variants.chroma:ChromaStore`, import lười). **Các thư mục stage KHÔNG import nhau qua lại tuỳ tiện** — phần
chung ở `src/utils/`, schema ở `src/models/`. Cache embedding là lớp bọc `components/embedders/cache.py`.
Đường dẫn gốc / prompt / output CHỈ lấy từ `utils.config` — không tự tính `Path(__file__).parents[...]`.
CLI mở đầu bằng `utils.config.console_setup()` (utf-8 + log INFO), không tự chép khối `reconfigure`.

**Vị trí trong cây LƯU THẲNG, không dùng con trỏ ngược** (back-reference bỏ 2026-10-12): block có
`page_no`, chunk có `page_no`, kịch bản trang có `page_no`. Cần chương thì `doc.section_of(page_no)`.

**Dữ liệu:**

```
data/raw/<file>.pdf                       file gốc — VỪA là deck VỪA là KB (.pptx cần .pdf cùng tên)
data/eval/<doc_id>.queries.json           câu hỏi có nhãn để đo retrieval
data/pronunciation.json                   KHO PHÁT ÂM CHUNG mọi deck — máy đề xuất từ kịch bản, người chốt
.env  VLM_MODEL · LLM_MODEL · EMBED_MODEL  tên model dùng — sửa ở đây, KHÔNG sửa trong code
.env  VLM_BASE_URL · LLM_BASE_URL       cổng riêng (vLLM local) cho VLM / LLM chữ — bỏ trống = OPENAI_BASE_URL
.env  EMBEDDER · VECTOR_DB=inmem|chroma · CHROMA_PATH   chọn biến thể — không sửa code

logs/vlm_pipeline/<doc_id>/pNNN.txt       phản hồi thô của VLM từng trang (xem vì sao trang hỏng)
```

**Output** — `src/output/` (gitignore). Mỗi thư mục con mang TÊN thư mục code sinh ra nó (`parser/`,
`chunker/`, `embedders/`…, KHÔNG kèm `offline/` / `components/`), đường dẫn là hằng số trong `utils.config`
(`PARSER_OUTPUT`, `CHUNKER_OUTPUT`, …) — dời code không làm đổi chỗ output:

```
src/output/
├── parser/<doc_id>/              S0 — python src/offline/parser/cli.py <file>  (① + ②b + ②)
│   ├── docling_vlmpipeline.json  ① DoclingDocument VlmPipeline (cache — có rồi thì không gọi VLM)
│   ├── tables.json · pictures.json   ②b chép bảng · ② xét ảnh (cache theo bảng / trang)
│   ├── document.json             ParsedDocument — link / chương / cờ KHÔNG có trong file:
│   │                             KB, S4, runtime gọi `offline.parser.enrich.enrich()` lúc nạp
│   │                             xem: python src/offline/parser/show.py <doc_id> [--page 7]
│   └── boxes/                    ảnh khung từng trang (scripts/try_docling_gemma.py), chỉ để soi
├── chunker/<doc_id>/chunks.json  S5 — KBChunk[]  (src/offline/chunker/cli.py)
├── embedders/
│   ├── <doc_id>/vectors__<model_id>.npy    ma trận vector — tên model PHẢI nằm trong tên file
│   ├── <doc_id>/vectors__<model_id>.json   rows[i] = chunk_id, keys[i] = hash chữ của hàng i
│   │                             (keys lệch chunks.json -> báo nhúng lại, không nạp vector cũ)
│   └── cache/<model>/<sha1>.npy  DÙNG CHUNG mọi deck — cache theo hash nội dung
│                                 (cả thư mục do python src/offline/embedder/cli.py <chunks.json> ghi)
├── vectordb/chroma/              DÙNG CHUNG — kho Chroma: MỘT collection kb__<model_id> cho mọi bài,
│                                 tách bằng metadata doc_id (chỉ khi VECTOR_DB=chroma)
├── evaluation/<doc_id>/          đo tìm kiếm — eval.json (src/evaluation/eval.py) ·
│                                 <tên chunks>_tune.{md,json} · _misses.json (scripts/tune.sh)
└── scenario/<doc_id>/            S4 — python src/offline/scenario/cli.py <document.json>
    ├── scenario.json             kịch bản CẢ deck, mỗi entry kèm page_hash cho incremental
    └── scenario.md               bản đọc cho người, ghi cùng lúc với .json
```

TTS cache (S6b, chưa code) sẽ có thư mục riêng theo tên thư mục code của nó, key = hash TỪNG CÂU.
Bảng phát âm KHÔNG ở output — một kho chung `data/pronunciation.json`.

**Kịch bản KHÔNG để chung với `ParsedDocument`** (parse lại là mất, đúng bẫy đã dính với
trang 15) **và KHÔNG để chung với `KBChunk`** (sửa một câu thoại không được làm bẩn index).
Không còn cờ "người đã sửa tay" (`edited_by` bỏ 2026-10-12): sửa tay `scenario.json` chỉ sống tới
lần trang / prompt / model đổi hoặc chạy `--force`. S7 phải có cơ chế riêng.

Kho vector KHÔNG mua tốc độ: 52 vector, quét vét cạn hết **0.01ms**; ngay cả 100.000 vector
cũng chỉ 25ms, trong khi gọi API nhúng câu hỏi đã mất ~700ms. Chroma mua về **lưu đĩa +
metadata filter + nhiều tài liệu chung một chỗ**. Kho chỉ làm nhánh dense — BM25 ở
`src/components/keyword_search.py` (cùng bộ hàm với `vectordb/`), RRF ở `utils/ensemble_search.py`.
`ChatEngine` (và `evaluation.eval.SearchSetup`) khởi tạo gọi `sync` cả hai: lệch `chunks.json` thì nạp lại từ
`.npy` / dựng lại BM25. Hai kho cùng bộ hàm `index_documents` · `retrieve_context` · `sync`
(so bộ chunk → xoá deck → nạp lại). BM25 `index_documents` nhận NGUYÊN bộ chunk một deck
(IDF tính trên cả deck) — gọi qua `sync`. Khung inmem/chroma/factory lấy từ `minhbtrc/chatbot-template` (MIT) — chỗ
khác bản gốc ghi ở `src/components/vectordb/base.py`. Sơ đồ §7.1 là đích, chưa áp dụng.

### 7.1 Đích (nhiều deck)

```
SHARED LAYER              ← dùng chung mọi deck
├── KB chunks + embeddings (metadata: source_doc, deck_ids[], content_type)
│   └── Qdrant: kb_chunks__{model_id}
└── pronunciation.json     ← MỘT kho cho mọi deck. Không có bản per-deck: viết tắt đọc
                              khác nhau tùy ngữ cảnh (T7) thì chấp nhận một cách đọc

PER-DECK LAYER
└── deck_{id}/
    ├── Scenario
    ├── Qdrant: slide_index__{deck_id}__{model_id}
    └── precomputed/

SESSION LAYER
└── active_deck_id → nạp đúng 1 bundle
```

- KB **shared** (nhiều deck dùng chung nguồn), Alignment **per-deck**
- Query KB luôn kèm filter `deck_ids ∋ active_deck_id`
- **Một phiên = một deck active.** Không cho nhảy slide sang deck khác.
  Hỏi sang deck khác → trả lời bằng lời + câu mềm, KHÔNG điều hướng.

---

## 8. Incremental build

```
page_hash không đổi → reuse Scenario, TTS, vector (cache đã lo)
page_hash đổi       → chunk + nhúng lại RIÊNG trang đó, chạy lại S4 cho nó
luôn chạy lại S2     → slide_type/sections là luật, rẻ
```

Sửa 3/20 trang → ~40s thay vì ~6 phút.

Lan `dirty`: page_hash đổi → **cộng thêm** trang kề sau, vì câu chuyển của nó nhắc tới
trang vừa đổi.

Người duyệt sửa `pronunciation.json` ở S7 → **đếm lại âm tiết** (rẻ, không gọi LLM) +
synth lại câu chứa từ đó; lệch timing > 15% thì chạy lại pass 2 của S4.

---

## 9. Quy ước code

- Python 3.11+, type hints bắt buộc
- **Pydantic** cho mọi artifact schema. Không dùng dict trần giữa các stage.
- Mỗi stage là một module độc lập, nhận/trả Pydantic model, **có thể chạy riêng qua CLI**
- Artifact ghi ra JSON, đọc lại được — pipeline phải resume được từ bất kỳ stage nào
- Gọi LLM/VLM: retry với exponential backoff, log full prompt + response vào `logs/`
- Mọi prompt nằm trong `src/prompts/*.md`, KHÔNG hardcode trong file .py
- Async cho mọi I/O; S1 và S4 phải song song hoá được (mặc định 5 luồng)
- Không `print()`, dùng `logging` hoặc `rich`

### Tổ chức code

Mỗi thứ một chỗ, mỗi chỗ một việc. Đọc tên thư mục là phải đoán được bên trong làm gì.

| Thứ           | Là gì                                                            | Luật                                                                                                                    |
| -------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| `models/`    | hình dạng dữ liệu (Pydantic) — "hợp đồng" giữa các stage | stage nói chuyện bằng model, không dict trần                                                                        |
| `components/` | linh kiện: làm một việc chung, không biết gì về slide, thay được cách làm (nhúng, kho vector, BM25) | phép thử: đem sang dự án chatbot khác vẫn dùng nguyên xi. Biết "trang" / "chương" thì KHÔNG phải linh kiện |
| `offline/`   | các bước chuẩn bị dữ liệu, chạy một lần, lưu ra file (parse, chunk, nhúng, kịch bản) | mỗi bước một thư mục + `cli.py`; không giới hạn thời gian |
| `chat_engine.py` | cửa hỏi đáp online, ở ngoài cùng `src/` | chỉ NỐI linh kiện + `utils/` lại, không tự cài đặt thuật toán tìm |
| `utils/`     | dụng cụ NHIỀU stage cùng dùng                                 | phép thử: xoá stage X mà file vẫn cần → `utils/`; chỉ X dùng → để trong X. Không biến thành thùng rác |
| `cli.py`     | cửa trước chạy MỘT LẦN từ terminal                          | chỉ đọc tham số → gọi lớp làm việc → in kết quả. KHÔNG chứa logic                                          |
| `app.py`     | cửa trước cho server chạy liên tục (chưa có ở v0)         | mỏng như `cli.py`, gọi cùng lớp logic bên dưới                                                                 |
| `base.py`    | lớp cha (ABC): "ai làm việc này phải có những hàm này"    | chỉ hứa hẹn (`@abstractmethod`) + phần dùng chung, không làm thay lớp con                                      |
| `variants/`  | các CÁCH khác nhau làm cùng một việc, mỗi cách một file  | thêm cách mới = thêm file mới, không sửa file cũ                                                                 |
| `factory.py` | đưa tên (từ `.env`), trả đúng biến thể                  | một bảng `"tên": "module:Class"`, import lười                                                                     |

- **Chỉ dựng `base + variants + factory` khi ĐÃ CÓ ≥ 2 cách làm.** Một cách thì cli gọi thẳng
  (như `parser/`). Dựng khung sớm là code thừa — có cách thứ hai rồi tách cũng chưa muộn.
- **Phụ thuộc chỉ đi XUỐNG:** `cli.py`/`app.py`/`chat_engine.py` → `offline/` → `components/` → `models/` + `utils/`. `utils/` không import
  ngược lên stage; các stage không import lẫn nhau tuỳ tiện — cần chung thì đẩy xuống `utils/`/`models/`.
- **Đặt tên — tên đầy đủ, KHÔNG viết tắt** (`output` không `out`, `chunk_list` không `cl`):
  - Class = **danh từ**, `PascalCase`. Biến thể: `<Cách làm><Vai trò>` (`ChromaStore`, `PageChunker`,
    `OpenAIEmbedder`); lớp khung: `Base<Vai trò>`.
  - Hàm = **động từ**, `snake_case`: `load_chunks`, `build_index`, `create_vectordb`,
    `normalize_polygon`. Trả đúng/sai thì `is_` / `has_` (`is_closing`).
  - Hằng số `CHỮ_HOA` (`OUTPUT_DIR`). File `snake_case`, tên nói rõ việc (`sections.py`).
  - Tên phải dùng chữ "và" (`parse_and_save`) = hàm làm quá nhiều việc → tách.
- Docstring mọi hàm bằng tiếng Việt đời thường, không viết ký hiệu nội bộ ("NT3", "§10").
- **Thêm stage mới:** ① model vào/ra trong `models/` → ② có nhiều cách làm không (không: vài file;
  có: base/variants/factory) → ③ lớp làm việc thật → ④ `cli.py` bọc ngoài → ⑤ prompt ở
  `src/prompts/`, đường dẫn lấy từ `utils.config`.

### Xem code: dùng `build-graph` trước khi đọc file

Repo có skill `build-graph` (từ `tirth8205/code-review-graph`) — dựng đồ thị code (file, class,
hàm, ai gọi ai). Cài một lần:

```
npx skills add tirth8205/code-review-graph --skill build-graph --agent claude-code
```

- **Trước khi đọc / sửa / review code**: chạy `build-graph` để dựng hoặc cập nhật đồ thị, rồi dùng
  nó tìm chỗ định nghĩa, nơi gọi, phụ thuộc giữa các stage — thay vì mở lần lượt từng file.
- Sửa một hàm → xem đồ thị ai gọi nó trước, để biết sửa thì lan tới đâu.
- Dùng đồ thị để kiểm luật "phụ thuộc chỉ đi xuống" ở trên: thấy `utils/` import stage, hoặc
  stage import chéo nhau → báo ra.

### Thư viện

| Việc            | Dùng                                                                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Parse PDF (v0)   | `docling` VlmPipeline (preset `dots_ocr`) + VLM đọc cả trang qua API                                                                            |
| Render PNG (v0)  | `pypdfium2`                                                                                                                                          |
| Parse pptx (v1)  | `python-pptx` + đọc XML thô cho animation timing                                                                                                  |
| Render PNG (v1)  | LibreOffice headless → PDF →`PyMuPDF` rasterize                                                                                                    |
| Parse PDF nguồn | `PyMuPDF`, `unstructured` hoặc `docling` cho cây heading                                                                                       |
| Embedding        | **v0: `text-embedding-3-small` qua API** (dense-only, 1536 chiều) · đích: `bge-m3` (dense + sparse 1 forward)                            |
| Rerank           | **KHÔNG dùng** (chốt 2026-10-01) — `bge-reranker-v2-m3` local đã loại (2.2 GB), không chờ quyền `/rerank` trên API key — xem §6 |
| Vector DB        | **v0: `chromadb` hoặc RAM** — chọn bằng `VECTOR_DB` (`src/components/vectordb/`). Tắt embedding có sẵn, cosine, tắt telemetry                 |
| Schema           | Pydantic v2                                                                                                                                            |

**Cảnh báo:** LibreOffice render pptx không khớp 100% với PowerPoint (font thay thế,
SmartArt đôi khi vỡ). Phải cài font Việt vào container.

---

## 10. CẤM

- ❌ Để LLM sinh lại `title`, `chart_data`, `tables` — đã có bản đúng từ S0
  (v0: không còn nguồn deterministic nào — chữ, bảng, số đều do VLM đọc)
- ❌ Coi mô tả ảnh do VLM sinh là dữ liệu chắc đúng — nó là `vlm`, đo được là có sai
  (chép `0x1675e5550` thành `0x1675e550`). **Nhưng S4 VẪN ĐƯỢC NÓI số từ block `vlm`**
  (người dùng chốt 2026-09-30, chấp nhận rủi ro sai: bảng hệ số làm thêm giờ là ảnh, cấm
  số thì trang mất nội dung). Từ 2026-10-08 mọi chữ là `vlm`; riêng số lấy từ MÔ TẢ ẢNH mới
  đáng soi (`vlm_number` — chưa có code).
- ❌ Fixed-size chunking ở S5
- ❌ Embed `text_raw` thay vì `text_enriched`
- ❌ Vứt chunk vì tưởng nó vô dụng (trang phân mục, chunk trùng) — ĐÁNH DẤU rồi lọc
  lúc truy vấn, đừng xoá. Xoá sai thì phải nhúng lại; lọc sai thì sửa một dòng luật.
- ❌ **Nhồi index hoặc nội dung cả deck vào prompt** — chỉ TRẠNG THÁI mới được inline,
  TRI THỨC phải truy xuất. Áp cho cả S4: viết kịch bản trang nào thì đọc trang đó.
- ❌ Dùng điểm LLM tự khai làm confidence gate
- ❌ Tách router riêng ở runtime (thêm 300–400ms vô ích)
- ❌ Đợi generate xong mới TTS
- ❌ Nhảy slide khi confidence gate không đạt
- ❌ Để renderer tự quyết trang
- ❌ Dùng LLM tự phình kiến thức từ slide để làm KB khi thiếu tài liệu nguồn
- ❌ Bắt người duyệt cả 20 trang ở S7 — chỉ duyệt phần bị flag
- ❌ Hardcode prompt trong file .py
- ❌ Fast-path (R1) bắt câu hỏi nội dung — chỉ điều hướng tường minh
- ❌ Đoán ngưỡng confidence gate thay vì fit trên bộ eval
- ❌ Trích dẫn bằng ký hiệu `[slide 7]` — TTS đọc thành "ngoặc vuông slide bảy"
- ❌ Trả lời sâu về slide có `answer_depth: "describe_only"` (S7 đã đánh dấu là không có nguồn)
- ❌ Ngắt giữa câu (trừ điều hướng, và phải fade ~80ms)
- ❌ Đọc to câu hỏi troll rồi mới từ chối — đọc lên là troll thành công
- ❌ Nói trước khi nhận ack của renderer
- ❌ `description` (mô tả ảnh của VLM) chứa đại từ trỏ ra ngoài trang hoặc từ chỉ vị trí
  (`nó`, `cái này`, `như trên`, `ở phần trước`) — nó bị embed và đứng một mình trong index
- ❌ Pass 2 của S4 cắt câu `delivery` xuống dưới sàn 10%
- ❌ Giọng TTS của precomputed khác giọng của streaming runtime
- ❌ Đặt tên file vector / collection không nhúng `model_id`

---

## 11. Quality gate (chạy được trong CI)

| Chỉ số                          | Ngưỡng                                      |
| --------------------------------- | --------------------------------------------- |
| Ungrounded rate (câu`content`) | < 10%                                         |
| Timing deviation                  | < 15% so với budget                          |
| Flag precision (S7)               | ≥ 60%                                        |
| P95 latency tới byte audio đầu | < 2.5s                                        |
| R2 recall@5                       | ≥ 95% — KHÔNG nới k, sửa RRF / dữ liệu |
| R2 Top-1 accuracy                 | báo cáo                                     |
| R2 harmful jump rate              | báo cáo — nhảy sai mà không hỏi lại   |

Gate self-retrieval (≥ 90%) **BỎ 2026-10-13** — câu hỏi lấy từ chính văn bản nên gần như luôn
100%, không nói lên gì. Số đo tìm kiếm lấy từ bộ câu hỏi có nhãn ở `data/eval/<doc_id>.queries.json`
(`src/evaluation/eval.py`) — đo 2026-10-13, top-1 dense · sparse · hybrid: Onboarding 200 câu
132 · 132 · 153 (top-5 hybrid 192); Thời gian làm việc 30 câu 25 · 29 · 27 (top-5 30/30).
Câu hiện đều do AI viết → số thiên vị, vẫn cần bộ câu **người viết**.
Xem [docs/spec/search.md §10](docs/spec/search.md).

**Proxy tự nhiên — chạy được trong CI:** (v0 KHÔNG kiểm — bỏ khỏi bộ kiểm S4 2026-10-06, xem §5 S4)

| Chỉ số                         | Ngưỡng |
| -------------------------------- | -------- |
| Độ lệch chuẩn âm tiết/câu | ≥ 6     |
| Từ văn viết bị cấm          | = 0      |
| Tỉ lệ câu`delivery`         | 10–25%  |

**Naturalness MOS ≥ 3.8/5 KHÔNG phải gate CI** — nó cần 3 người ngồi nghe.
Đó là **release gate**, chạy trước buổi thuyết trình thật.

---

## 12. Phạm vi

### v0 — ĐANG LÀM ★

**Trong phạm vi:** **một file `.pdf` duy nhất, vừa làm deck vừa làm KB** · parse bằng
docling, VLM mô tả ảnh gọi **qua API** · nhúng vector cũng **qua API**
(`text-embedding-3-small`) — **không chạy model local nào** · lưu **file JSON trên đĩa**,
kho vector Chroma hoặc RAM (`VECTOR_DB`) · tiếng Việt, thuật ngữ giữ gốc tiếng Anh

**Chấp nhận đánh đổi** (xem §3.0): không có `chart_data` / `tables` / `build_steps` từ
XML · S3 Alignment suy biến nên bỏ qua · độ sâu trả lời giới hạn ở mức **mô tả slide** ·
embedding qua API **chỉ có dense, mất sparse** của `bge-m3` → phải bù bằng BM25 riêng,
và tốn 719ms/câu hỏi thay vì 182ms (đo thật, xem [embedding.md §2](docs/spec/embedding.md))

**Chưa có:** S6b TTS · S7 duyệt · S2 `time_budget` · runtime R1–R7 thật (mới có bản chữ
chạy thử: `src/chat_engine.py`, `scripts/try_ask.py`)

**Reranker: BỎ** (chốt 2026-10-01) → R2 chạy được nhưng **không có confidence gate
calibrate được**, cổng dùng biên RRF, `calibrated: false` — xem §6.

### v1 — đích

**Trong phạm vi:** `.pptx` làm deck + `source/*.pdf` **riêng** làm KB · kênh hỏi bằng
**text** (QR → form web, không ASR) · app tự render slide · deck cố định lúc build ·
1 deck active mỗi phiên

**Ngoài phạm vi v1:** voice/ASR · robot vật lý · điều khiển PowerPoint ngoài ·
upload deck lúc runtime · cross-deck navigation

<!-- code-review-graph MCP tools -->

## MCP Tools: code-review-graph

**This project has a knowledge graph. Start with the code-review-graph
MCP tools to narrow scope, then read the source.** The graph is cheaper than scanning files and
gives you structural context (callers, dependents, test coverage) that file search cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of reading entire files
- **Finding relationships**: `query_graph_tool` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` + `list_communities_tool`

### Verify in the source

- Narrow scope with the graph, then read the source. Do not change code from graph output alone.
- For any non-trivial change, read the implementation and the relevant tests before concluding.
- Verify the exact source when touching behavior, database logic, migrations, retries, fallbacks,
  recovery, or compatibility code.
- When the graph and the source disagree, the source wins. The graph may be stale or may not
  model that relationship.
- An empty graph result can mean "not indexed" or "not statically visible", not "does not exist".

### Key Tools

| Tool                               | Use when                                               |
| ---------------------------------- | ------------------------------------------------------ |
| `detect_changes_tool`            | Reviewing code changes — gives risk-scored analysis   |
| `get_review_context_tool`        | Need source snippets for review — token-efficient     |
| `get_impact_radius_tool`         | Understanding blast radius of a change                 |
| `get_affected_flows_tool`        | Finding which execution paths are impacted             |
| `query_graph_tool`               | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes_tool`     | Finding functions/classes by name or keyword           |
| `get_architecture_overview_tool` | Understanding high-level codebase structure            |
| `refactor_tool`                  | Planning renames, finding dead code                    |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes_tool` for code review.
3. Use `get_affected_flows_tool` to understand impact.
4. Use `query_graph_tool` pattern="tests_for" to check coverage.

<!-- /code-review-graph MCP tools -->
