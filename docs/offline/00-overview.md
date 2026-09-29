# Nhánh offline — tổng quan

> **File này là THIẾT KẾ ĐÍCH.** Hiện trạng v0 khác ở ba điểm:
> **một file PDF duy nhất vừa làm deck vừa làm KB** (không phải `.pptx` + `source/*.pdf`
> tách biệt) · lưu **file JSON trên đĩa**, chưa có Qdrant · mới code xong đúng
> **S0 phiên bản PDF** ([`src/parsing/`](../../src/parsing/)), S1–S7 chưa có dòng nào.
>
> Hệ quả của việc gộp hai nguồn: [CLAUDE.md §3.0](../../CLAUDE.md). Tóm tắt — mất bản
> đúng của `chart_data`/`tables`/`build_steps`, S3 Alignment suy biến, hệ thống co lại
> thành "robot mô tả slide".

## 1. Vì sao có nhánh offline

> Slide là bản nén mất mát của kiến thức. Người thuyết trình là nơi chứa phần bị mất.
> **Toàn bộ nhánh offline tồn tại để tái tạo phần đó.**

Deck mục tiêu: ~20 trang, ít chữ nhiều hình, trung bình **dưới 20 từ mỗi trang**.
Con số này không phải chi tiết phụ — nó là lý do RAG text thuần chết ở đây và là lý do
phải có tầng VLM. Nếu deck 80 từ/trang thì phần lớn thiết kế dưới đây là thừa.

Nhánh offline trả trước toàn bộ chi phí suy luận để runtime chỉ còn việc **tra cứu**.

---

## 2. Bốn nguyên tắc bất di bất dịch

### NT1 — Offline không có ràng buộc thời gian, online thì có

Mọi thứ tính trước được phải đẩy vào offline. 6 phút build một deck là rẻ; 300ms thêm
ở runtime là đắt. Runtime budget: **< 2.5s tới byte audio đầu tiên**.

Hệ quả: gặp lựa chọn "làm kỹ, chậm" vs "làm nhanh, ẩu" ở offline → luôn chọn kỹ.
2 pass VLM, LLM verify từng cặp alignment, enrich từng chunk — đều đáng.

### NT2 — Dữ liệu chính xác KHÔNG BAO GIỜ để model sinh lại

`title`, `chart_data`, `tables`, `build_steps` đã có bản **đúng 100%** từ parsing pptx.
Đưa cho LLM là tự tạo cơ hội bịa lại cái đang đúng.

Hệ quả cứng: mọi artifact phải khai `provenance`, mỗi field thuộc đúng một trong hai nhóm:

```json
"provenance": {
  "text_layer": ["title", "text", "bảng chữ trong ô", "bbox", "page_label"],
  "vlm":        ["description (mô tả ảnh)", "số đọc từ biểu đồ"],
  "derived":    ["sections", "slide_type", "reading_order"],
  "manual":     ["nội dung gõ tay ở data/patches/"]
}
```

Không có field nào ở giữa. Nếu không biết xếp vào đâu → thiết kế sai, tách field ra.

### NT3 — Ảo giác offline nguy hiểm gấp nhiều lần online

Bịa ở runtime → sai một lần, trước một câu hỏi.
Bịa trong `Scenario` → robot nói sai ở **mọi buổi thuyết trình**, và không ai kiểm lại.

Hệ quả: mọi câu **nội dung** phải có `grounding`. `grounding: null` là **cờ đỏ**, bắt người
duyệt ở S7, không được im lặng cho qua. Sai ở S1 lan xuống S3, thành kịch bản sai ở S4,
rồi thành lời nói trước khán giả — sai ở đây là **sai có hệ số nhân**.

### NT4 — Tự nhiên là YÊU CẦU, không phải điểm cộng

Robot nghe như đọc bản tin thì hệ thống **thất bại**, dù alignment coverage 95% và
ungrounded rate 2%. Không ai ngồi nghe hết 30 phút giọng máy đọc.

**NT4 va thẳng vào NT3.** Nói tự nhiên nghĩa là có "thì", "nhé", "đúng không ạ", câu hỏi
tu từ, câu chuyển mạch. **Không câu nào trong đó có nguồn.** Theo NT3 nguyên bản, mỗi câu
như vậy là một cờ đỏ → hoặc ungrounded rate nổ tung, hoặc robot vẫn cứng.

Gỡ bằng cách tách hai loại câu, **không nới NT3**:

```
kind: "content"    grounding ∈ {slide_repr, kb_chunk, speaker_notes}
                   null = CỜ ĐỎ                      <- luật cũ giữ nguyên, không nới

kind: "delivery"   grounding ∈ {structure, style}
                   KHÔNG mang thông tin sự thật mới
                   validate BẰNG CODE: không chứa số
                                       không chứa entity chưa xuất hiện
                                       không chứa mệnh đề khẳng định về sự thật
```

- `ungrounded_rate` từ nay tính **trên câu `content`**
- Câu `delivery`: **10–25% tổng âm tiết**. 25% là trần chống lan man;
  **10% là SÀN CỨNG** — pass 2 của S4 không được cắt xuống dưới (xem [S4](./s4-scenario.md))

Nới NT3 thay vì tách câu là mở cửa cho ảo giác đi vào đúng chỗ nguy hiểm nhất. Đừng làm.

**NT1 và NT4 không mâu thuẫn:** mọi thứ làm cho tự nhiên (prosody, nhịp, từ điển phát âm)
đều tính trước được ở offline. Không cái nào tốn thêm một millisecond nào ở runtime.

---

## 3. Kiến trúc v0 — MỘT file, MỘT index ★

Bản trước giả định hai nguồn tách biệt (`deck.pptx` + `source/*.pdf`) hội tụ ở S3.
**v0 không như vậy:** một file PDF vừa là deck vừa là KB, nên S3 align nó vào chính nó —
vô nghĩa, đã bỏ. Xem [CLAUDE.md §3.0](../../CLAUDE.md).

```
data/raw/<ten>.pdf   ← VỪA là deck VỪA là KB
      |
   S0 Ingest  (docling + VLM mô tả ảnh qua API)
      |  ├─ sections   từ trang mục lục    ← luật, không gọi model
      |  ├─ slide_type từ bbox + tiêu đề   ← luật, 40/40 đúng
      |  └─ entities                        → pronunciation.json
      v
 ParsedDocument
      |
   S5 chunk → embed qua API → hybrid dense+BM25 (RRF)
      |
 [KBChunk + vector]   ← MỘT index duy nhất, vừa để trả lời vừa để điều hướng
      |
      +──────────────┬──────────────────┐
      v              v                  v
  S2 time_budget  S6a deck_map     S4 Scenario   ← đọc TỪNG TRANG, không nhồi cả deck
      |            + audit              |
      +──────────────┴──────────────────+
                     |                  v
                     |            S6b TTS + qa_cache
                     +──────────────────+
                                        v
                                 S7 HITL  (đọc + NGHE phần bị flag)
                                        |
                                 DeckBundle → Runtime
```

**Ba stage đã bỏ so với bản trước:**

| stage | vì sao bỏ |
| ----- | --------- |
| **S1 Slide Understanding** | `description` đã có từ VLM ở S0 · `slide_type` làm được bằng luật · `message` và `relations` sinh ra để nhồi ngữ cảnh toàn cục vào prompt, mà thiết kế này không nhồi |
| **S3 Alignment** | nguồn chính là deck → align vào chính mình |
| **multi-field embed** | cần `message`/`desc`/`relations` tách riêng, đã bỏ |

**Thứ tự chạy thật:**

```
S0 -> S5 -+-> S2 (luật, rẻ)
          +-> S6a deck_map + audit
          +-> S4 -> S6b ----------------+-> S7
```

- **S0 làm hết phần hiểu trang.** Không còn vòng gọi LLM thứ hai để "hiểu" lại.
- **S2 không gọi model.** `sections` đã có từ S0; `time_budget` chia theo số trang `content`.
- **S4 đọc từng trang một** — input là `KBChunk` của chính trang đó + `title` trang kề.
- **Một index duy nhất**, vì KB đến từ chính file slide.

### 3.1 Offline dựng MỘT index, không phải hai

Bản trước tách `kb_chunks` (shared) và `slide_index_{deck}` (per-deck). v0 gộp làm một:
cùng một file thì cùng một tập chunk.

Khác biệt giữa hai kiểu truy vấn nằm ở **bộ lọc**, không phải ở index:

```
R4  hỏi nội dung     →  LỌC trang phân mục   (cần trang có nội dung)
R2  hỏi điều hướng   →  KHÔNG lọc            (trang mở chương là đích hợp lệ)
```

Chưa dựng Qdrant và **chưa cần**: 52 vector quét vét cạn hết **0.01ms**, 100.000 vector
cũng chỉ 25ms — trong khi gọi API nhúng câu hỏi đã mất ~700ms. Qdrant mua về metadata
filter và nhiều deck, **không phải tốc độ**.

### 3.2 Dòng thời gian thật (deck 20 trang, build lần đầu)

```
t=0                                                            t ~ 6 phút
│
├── S5 KB ════════════════════════╗   chạy SONG SONG, không cần deck
│   (~2 phút, 1 lần / corpus)     ║   deck thứ 2 dùng chung nguồn --> SKIP
│                                 ║
├── S0 ═══╗                       ║
│  (~30s) ╚═ S1 ═══════╗          ║
│            (~90s)    ╚═ S2 ═╗   ║
│                      (~15s) ║   ║
│                             ╠═══╡ S6a ═╗                  <- song song với S3
│                             ║  (~25s)  ╚═ self-retr (~5s) ═╗
│                             ╚═══╩═ S3 ═══╗                 ║
│                                  (~105s) ╚═ S4 ═══╗        ║
│                                            (~2ph) ╚═ S6b ══╗║
│                                                    (~90s)  ╚╩═ S7
│                                                             (người, 10–20ph)
```

Ba thứ đọc được từ hình này: **S5 không nằm trên đường găng**; **S3 là nút thắt**
(không có nó thì S4 không chạy được); và **S6a nằm gọn trong bóng của S3** nên việc dựng
SlideIndex **không tốn thêm một giây nào**.

Hệ quả thứ ba mới là cái đáng giá: `self-retrieval check` xong **trước khi S4 chạy**, nên
nó bắt được slide có biểu diễn lẫn nhau **trước khi tiêu tiền viết kịch bản** cho chúng.

### 3.3 Bên trong từng stage

#### S0 · Ingest — `deck.pdf` → `ParsedDocument`  ✅ ĐÃ CÓ

`src/parsing/` · xem [spec đầy đủ](../spec/parsed-document.md)

```
VÀO  data/raw/<ten>.pdf
  │
  ├─> [docling]  layout + TableFormer chạy local
  │              thứ tự đọc theo body.children   <- KHÔNG đọc tuần tự texts[]
  │              bbox gốc DƯỚI-TRÁI ──> TRÊN-TRÁI, [0,1]
  │              bỏ header / footer / số trang lặp (không phải nội dung)
  │              OCR TẮT: đo được bật chậm 8.4×, markdown GIỐNG HỆT
  │
  ├─> [VLM qua API]  mỗi TRANG một request, nhìn ảnh CẢ trang, prompt s0_page_layout.md
  │                  VLM chỉ TRỎ ID mẩu chữ docling -> chữ vẫn là text_layer
  │                  ảnh trang trí ─> VLM xếp vào decorative, không thành block
  │                  chỉ ô bảng / mô tả hình đọc từ ảnh là vlm   <- CÓ THỂ BỊA (NT2)
  │                  code kiểm: sót id / id bịa / bỏ quên vùng ─> dùng docling + cờ
  │
  ├─> [luật] sections   từ trang mục lục: trang đầu tiên có tiêu đề khớp mục = mở chương
  │          slide_type từ bbox tiêu đề + tiêu đề "Bài tập…"
  │          KHÔNG gọi model cho hai thứ này
  │
  ├─> [patch tay]  data/patches/<ten>.json ─> provenance = manual
  │                cho trang docling bỏ sót (p15: ảnh chụp màn hình)
  │
  └─> [hash]  page_hash = SHA(nội dung + bbox từng mẩu)   <- incremental
                    │
                    v
              ParsedDocument ─> [cờ]  empty_page · image_not_described · table_empty
                                      layout_failed · no_sections
RA   out/parsed/<doc_id>/document.json  ──> S5
```

> **S1 cũ nằm ở đây.** Bản trước có một stage riêng gọi LLM sinh `message` / `relations`.
> Đã bỏ: `description` do VLM sinh ngay tại S0, `slide_type` làm bằng luật, còn `message`
> sinh ra chỉ để nén cả deck nhồi vào prompt — thiết kế này không nhồi.

#### S5 · KB Construction — `ParsedDocument` → `KBChunk[]` + vector  ✅ ĐÃ CÓ

`src/kb/` · xem [kb-chunk.md](../spec/kb-chunk.md), [embedding.md](../spec/embedding.md),
[search.md](../spec/search.md)

```
VÀO  out/parsed/<doc_id>/document.json
  │
  ├─> [chunk]  1 trang = 1 chunk chính            <- vì R2 nhảy tới TRANG
  │            mỗi mô tả ảnh = 1 vector phụ
  │            tiền tố "[<chương> · trang N/M]"   <- KHÔNG gọi LLM
  │            > 500 token thì cắt theo ranh giới block
  │            KHÔNG overlap: ranh giới là ranh giới TRANG, không câu nào bị cắt đôi
  │            trang phân mục ─> đánh dấu section_divider, KHÔNG xoá
  │                    │
  │                    v  52 chunk (45 tìm được, 7 phân mục)
  │
  ├─> [embed]  text_enriched qua API text-embedding-3-small   <- KHÔNG dùng text_raw
  │            1536 chiều · chuẩn hoá L2 · cache theo sha1
  │            nhúng CẢ 52, kể cả phân mục — lọc là việc của lúc TRUY VẤN
  │
  └─> [index]  dense: ma trận .npy, quét vét cạn (0.01ms, chính xác tuyệt đối)
               sparse: BM25 dựng trong RAM lúc khởi động (~10ms)
               gộp bằng RRF K=60   <- KHÔNG cộng điểm: hai thang đo không so được
RA   out/kb/<doc_id>.chunks.json + <doc_id>__<model_id>.vectors.npy
```

Đo được (7 câu hỏi có nhãn): **hybrid 4/7 top-1 · dense 3/7 · sparse 3/7** — hybrid hơn
từng nhánh riêng.

#### S2 · Deck Structure — `ParsedDocument` → `time_budget`  ⬜ CHƯA CÓ

```
VÀO  ParsedDocument (đã có sections + slide_type)
  │
  ├─> sections      ĐÃ CÓ TỪ S0, không làm lại
  ├─> time_budget   chia theo số trang content mỗi section
  └─> validate      phủ kín · không chồng · liên tục
                    vi phạm ─> báo S7, KHÔNG tự sửa
RA   DeckStructure  ──> S4
```

> **Không gọi model.** Bản trước cho LLM sinh `concept_map` / `dependencies` / `arc`.
> Bỏ hết — chúng phục vụ việc bơm ngữ cảnh toàn cục vào prompt.

#### S4 · Scenario — từng trang → `Scenario[]`  ⬜ CHƯA CÓ

```
VÀO  KBChunk của CHÍNH TRANG ĐÓ + title trang trước/sau + slide_type + time_budget
     <- KHÔNG nhồi cả deck vào prompt
  │
  ├─> [pass 1]  viết lời, mỗi câu khai kind + grounding
  │             kind=content   grounding null ─> CỜ ĐỎ (NT3)
  │             kind=delivery  không mang sự thật mới, validate BẰNG CODE
  │             slide_type quyết định độ dài:
  │                 section_divider ─> một câu chuyển
  │                 content         ─> viết đủ
  │
  ├─> [pass 2]  cân giờ theo time_budget
  │             cắt trùng lặp ở câu content TRƯỚC, câu delivery SAU CÙNG
  │             sàn 10% delivery là CỨNG
  │
  └─> [đếm âm tiết]  theo pronunciation.json, 190–210 âm tiết/phút
                     KHÔNG đếm từ
RA   out/deck/<doc_id>/scenario.json  ──> S6b
```

#### S6a · deck_map + audit — `KBChunk[]` → `deck_map.txt`  ⬜ CHƯA CÓ

```
VÀO  KBChunk[] + sections
  │
  ├─> deck_map.txt (~150 token: danh sách section)  ─> prompt runtime
  │
  └─> [audit]  src/kb/audit.py — ĐÃ CÓ
               quét trùng lặp: cosine cặp >= 0.94   ─> bắt được 2 cặp thật
               self-retrieval: gate §11 >= 90%
               ⚠️ self-retrieval hiện gần như luôn 100% vì câu hỏi lấy TỪ CHÍNH
                  văn bản chunk — đề bài là đáp án. Số đo thật lấy từ
                  data/eval/queries.json (src/kb/eval.py)
RA   deck_map.txt + out/kb/audit/*.json
```

#### S6b · Precompute — `Scenario[]` → `precomputed/`

```
VÀO  Scenario[] + pronunciation.json
  │
  ├─> [so hash pronunciation]   Scenario.pron_hash == hash(pronunciation.json) ?
  │       lệch ─> DỪNG. S4 đếm một đằng, TTS đọc một nẻo, timing sai mà không ai thấy
  │
  ├─> [TTS]  cache key = hash TỪNG CÂU    <- phần tốn tiền nhất, reuse triệt để
  │       tts_hash = SHA(normalize(text) + voice_id + speed + format)
  │       MỘT voice_id duy nhất, dùng chung với streaming TTS ở runtime
  │           <- lệch giọng giữa kịch bản và câu trả lời = artifact CHÓI TAI nhất
  │       áp prosody của S4: emphasis · pause_before_ms · speed
  │       ─> precomputed/tts/{hash}.mp3  + duration_ms THẬT
  │             └─> đối chiếu ước lượng âm tiết của S4; lệch >20% ─> chỉnh hằng số
  │
  ├─> [filler]  bộ câu đệm dùng chung mọi deck ─> data/kb/fillers/
  │
  ├─> [qa_cache]  LLM sinh 3–5 câu hỏi/slide ─> trả lời + TTS sẵn
  │       runtime match cosine > 0.88 mới dùng
  │
  ├─> [thumbs]  s{n}.jpg ~480px   <- cho confidence gate của R2 khi hỏi lại
  │
  └─> [BẢN NGHE THỬ]  3 trang + mọi câu bị flag ─> cho S7 NGHE, không chỉ đọc
RA   precomputed/ + manifest.json  ──> S7 ──> runtime
```

#### S7 · HITL Review — flags → `DeckBundle`

```
VÀO  mọi artifact + flags.json + BẢN NGHE THỬ    <- CHỈ duyệt phần bị flag
  │
  ├─> [VÒNG ĐỌC]  sắp theo độ LAN TOẢ, không theo số trang
  │     1  S4 ungrounded (câu content)  <- CỜ ĐỎ, robot sẽ NÓI RA MIỆNG
  │     2  S0 image_not_described       <- mất nội dung thật của trang
  │     3  S0 empty_page                <- trang rỗng mà KHÔNG phải section_divider
  │     4  S6a chunk trùng / self_retrieval_fail
  │     5  S0 no_sections / layout_failed  <- dựng chương / bố cục trượt, chỉ ghi nhận
  │     6  S6b                          <- kỹ thuật
  │           │
  │           ├─ accept / whitelist ─> review.json   (BỀN qua các lần build)
  │           ├─ edit               ─> chạy lại stage dưới của riêng slide đó
  │           └─ rerun + hint       ─> chạy lại stage đó
  │
  ├─> [VÒNG NGHE]  * MỚI — nghe bản nghe thử, chấm MOS 1–5, 3 người
  │       gate: MOS >= 3.8    <- RELEASE gate, KHÔNG phải CI gate
  │       sửa pronunciation.json ─> đếm lại âm tiết + synth lại câu liên quan
  │
  ├─ image_not_described ─> người chọn 1 trong 2:
  │       ảnh có nội dung thật ─> gõ tay vào data/patches/ ─> provenance=manual
  │       ảnh trang trí        ─> đánh dấu is_decorative, hết flag
  │
  └─ chunk trùng ─> người chọn 1 trong 2:
          mô tả ảnh nuốt gần hết trang ─> bỏ chunk phụ
          hai trang TRÙNG CHỦ ĐỀ THẬT ─> đánh dấu "cặp đã biết, chấp nhận"
                vào review.json ─> LẦN SAU KHÔNG FLAG NỮA
                ^ không có lối này thì nó kêu mãi và người duyệt học cách bỏ qua flag
RA   DeckBundle verified ──> runtime     (còn cờ đỏ ─> KHÔNG đóng gói được)
```

---

## 4. Bảng stage

| Stage                                          | Input                                          | Output                                     | Model                      | Thời gian (deck 20 trang)    |
| ---------------------------------------------- | ---------------------------------------------- | ------------------------------------------ | -------------------------- | ----------------------------- |
| [S0](./s0-ingest.md) Ingest                | `deck.pdf`                             | `ParsedDocument`                         | docling + VLM qua API      | ~9s + VLM 11 ảnh  ✅ |
| [S5](./s5-kb-construction.md) KB           | `ParsedDocument`                       | `KBChunk[]` + vector                     | embedding qua API          | ~4s (52 chunk)     ✅ |
| S6a deck_map + audit                        | `KBChunk[]` + `sections`               | `deck_map.txt` + audit                   | embedding                  | vài giây          ⬜ |
| [S2](./s2-deck-structure.md) Structure     | `ParsedDocument`                       | `time_budget`                            | — (luật)                  | tức thì           ⬜ |
| [S4](./s4-scenario.md) Scenario            | `KBChunk` từng trang + `pronunciation` | `Scenario[]`                             | LLM, 2 pass                | ~2 phút           ⬜ |
| S6b Precompute                              | `Scenario[]` + `pronunciation.json`    | `precomputed/`                           | TTS                        | ~90s              ⬜ |
| [S7](./s7-hitl-review.md) HITL             | mọi artifact + flags + bản nghe thử    | `DeckBundle` verified                    | —                         | 10–20 phút người ⬜ |

Phần đã có (S0 + S5) chạy hết **~15s** cho deck 40 trang, chưa tính lần gọi VLM đầu tiên.
Cache vector làm lần chạy lại gần như miễn phí: đo được **52/52 trúng cache, 0 lần gọi API**.

---

## 5. Hợp đồng dữ liệu

Mọi artifact là **Pydantic v2 model**, ghi ra JSON, đọc lại được. Không dict trần giữa
các stage. Pipeline phải resume được từ bất kỳ stage nào — nghĩa là mỗi stage đọc file
của stage trước chứ không nhận object trong bộ nhớ.

```
data/
├── kb/                                <- SHARED LAYER, dùng chung mọi deck
│   ├── chunks.jsonl                   KBChunk[]
│   ├── index/                         Qdrant: kb_chunks__{model_id} + BM25
│   ├── pronunciation.json             <- MỚI. Thuật ngữ trùng nhau giữa các deck
│   ├── fillers/                       câu đệm prerecorded
│   └── sources/                       PDF gốc
│
├── decks/{deck_id}/
│   ├── deck.pdf                       VỪA là deck VỪA là KB (v0)
│   ├── parsed.json                    ParsedDocument        <- S0
│   ├── chunks.json                    KBChunk[]             <- S5
│   ├── <doc_id>__<model_id>.vectors.npy                     <- S5
│   ├── structure.json                 time_budget           <- S2
│   ├── scenario.json                  Scenario[] + pron_hash <- S4
│   ├── deck_map.txt                   ~150 token, danh sách section  <- S6a
│   ├── audit/self_retrieval.json      <- S6a
│   ├── precomputed/
│   │   ├── tts/{sentence_hash}.mp3
│   │   ├── qa_cache.jsonl
│   │   ├── thumbs/s{n}.jpg
│   │   ├── listen_sample/             bản nghe thử cho S7   <- S6b
│   │   └── manifest.json
│   ├── flags.json                     gộp flag mọi stage    <- S0–S6
│   └── review.json                    quyết định của người  <- S7
│
└── eval/                              bộ test có nhãn
```

- **KB shared, Alignment per-deck, SlideIndex per-deck.**
- `pronunciation.json` ở **shared layer** vì thuật ngữ trùng nhau giữa các deck —
  "RAG", "BM25", "retriever" chỉ chốt cách đọc một lần.
- Query KB ở runtime **luôn kèm filter** `deck_ids ∋ active_deck_id`.
- Một phiên = một deck active. Không cross-deck navigation trong v1.
- **`model_id` trong tên collection là bắt buộc** — đổi embedding model mà quên rebuild
  thì runtime truy vấn index cũ bằng vector mới và trả rác, **không báo lỗi**.
  `kb_chunks` rebuild một lần, nhưng `slide_index` là per-deck nên phải rebuild **N lần**.

---

## 6. Incremental build

```
page_hash không đổi  -> reuse Scenario, TTS, vector (cache sha1 đã lo)
page_hash đổi        -> chunk + nhúng lại RIÊNG trang đó, chạy lại S4 cho nó
luôn chạy lại S2     -> luật, gần như miễn phí
luôn chạy lại S6a    -> deck_map + audit, rẻ
```

**Ca đặc biệt — người duyệt sửa `pronunciation.json` ở S7:**

```
đổi cách đọc "BM25"
  -> đếm LẠI âm tiết (rẻ, không gọi LLM)
  -> synth lại MỌI câu chứa từ đó
  -> lệch timing của section > 15% ? -> chạy lại PASS 2 của S4 (không phải pass 1)
```

Sửa 3/20 trang → **~40s** thay vì ~6 phút.

`page_hash` tính trên **nội dung + bbox từng mẩu** của trang, không hash cả file PDF —
đổi metadata là hash đổi, build lại vô ích. Chi tiết ở [S0](./s0-ingest.md).

Cache vector đi theo **hash của `text_enriched`**, không theo `page_hash`, nên đổi `doc_id`
hay đổi tên file cũng không mất cache. Đo được: sinh lại toàn bộ sau khi đổi `doc_id` →
**52/52 trúng cache, 0 lần gọi API**.

Điểm dễ sai: S4 của trang N phụ thuộc trang N−1 qua câu chuyển. Khi N−1 đổi, phải chạy
lại S4 cho cả N. Quy tắc an toàn:

```
dirty  = {slide có hash đổi}
dirty += {slide kề sau mỗi slide dirty}          (vì câu chuyển)
dirty += {slide có dependencies giao dirty}      (vì tham chiếu ngược)
```

---

## 7. Quy ước chung cho mọi stage

- Python 3.11+, type hints bắt buộc, Pydantic v2 cho schema.
- Mỗi stage là một module độc lập, **chạy riêng được qua CLI**:
  `python -m src.offline.s1_understand --deck-id rag-intro`
- Gọi LLM/VLM: retry exponential backoff, log **full prompt + response** vào `logs/`.
  Không log thì không bao giờ debug được vì sao model bịa.
- Mọi prompt nằm trong `prompts/*.md`. **Không hardcode prompt trong file `.py`.**
- Async cho mọi I/O. S1 và S4 phải song song hoá được (mặc định 5 luồng).
- Không `print()`. Dùng `logging` hoặc `rich`.
- Stage sinh flag thì **append vào `flags.json`**, không tự sửa dữ liệu, không tự quyết.
  Quyết định là việc của người ở S7.

---

## 8. Quality gate ra khỏi nhánh offline

Không đạt thì không deploy. Chạy được trong CI.

### Gate chạy được trong CI

| Chỉ số                                     | Ngưỡng                               | Đo ở        |
| -------------------------------------------- | -------------------------------------- | ------------- |
| Alignment coverage                           | ≥ 80% slide có ≥1 nguồn conf > 0.6 | S3            |
| Ungrounded rate (**câu `content`**) | < 10%                                  | S4            |
| Timing deviation                             | < 15% so với`time_budget`           | S4            |
| **Self-retrieval top-1**               | **≥ 90% slide**                 | **S6a** |
| Flag precision                               | ≥ 60%                                 | S7            |
| P95 latency tới byte audio đầu            | < 2.5s                                 | runtime       |

### Proxy tự nhiên — cũng chạy được trong CI

| Chỉ số                                                                       | Ngưỡng                | Đo ở |
| ------------------------------------------------------------------------------ | ----------------------- | ------ |
| Độ lệch chuẩn số âm tiết / câu                                         | ≥ 6                    | S4     |
| Từ văn viết bị cấm (`việc…`, `sự…`, `được thực hiện bởi`) | = 0                     | S4     |
| Tỉ lệ câu`delivery`                                                       | 10–25% tổng âm tiết | S4     |
| Câu > 30 âm tiết                                                            | < 5%                    | S4     |

### Release gate — KHÔNG chạy trong CI

| Chỉ số                  | Ngưỡng             | Cách đo                           |
| ------------------------- | -------------------- | ----------------------------------- |
| **Naturalness MOS** | **≥ 3.8 / 5** | 3 người NGHE bản nghe thử ở S7 |

MOS cần người ngồi nghe, nên đừng nhét nó vào CI rồi tự lừa mình bằng một con số giả.
Proxy tự động ở trên chạy mỗi lần build; MOS chạy **trước buổi thuyết trình thật**.

Mỗi stage còn có gate riêng, xem cuối từng file.

---

## 9. Bản đồ flag

Mọi flag từ mọi stage đổ về một file, S7 đọc file đó. Chi tiết ở [S7](./s7-hitl-review.md).

| Stage | Flag tiêu biểu                                                                                                                                                                  |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| S0    | `empty_page`, `image_not_described`, `table_empty`, `layout_failed`, `no_sections` |
| S5    | chunk trùng nhau (cosine >= 0.94), chunk vượt 500 token                                              |
| S2    | `sections` không phủ kín / chồng nhau / đứt quãng                                                    |
| S4    | `ungrounded_content_sentence` ← **cờ đỏ**, `timing_overflow`, **`delivery_below_floor`**, **`written_register_hit`**, **`monotone_rhythm`** |
| S6a   | `self_retrieval_fail` — ⚠️ hiện gần như không bao giờ bắn, xem §3.3                                |
| S6b   | `duration_mismatch`, **`pronunciation_hash_mismatch`**, **`voice_id_mismatch`**                 |