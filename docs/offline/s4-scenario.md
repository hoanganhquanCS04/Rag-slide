# S4 — Scenario

> ⚠️ **Đọc trước: input đã đổi.** S4 (đã code, `src/scenario/`) nhận **block của CHÍNH
> TRANG ĐÓ** (kèm `id` + `provenance` để câu trỏ `ref` vào) + `title` trang trước/sau +
> `slide_type` + kịch bản các trang trước cùng chương. **Không** còn `SlideRepr`, `message`,
> `concept_map`, `AlignmentMap` — S1 và S3 đã bỏ — và **chưa có `time_budget`**: pass 2 hiện
> là pass **sửa lỗi bộ kiểm**, chỉ chạy cho trang trượt, không phải cân giờ. Luật về
> `content` / `delivery`, 7 đòn bẩy tự nhiên, đếm âm tiết **vẫn nguyên giá trị**.
> Cái đang chạy: [spec/scenario.md](../spec/scenario.md) · [CLAUDE.md §5](../../CLAUDE.md).
>
> Kịch bản lưu ở `out/deck/<doc_id>/scenario.json` — **không** để chung với
> `ParsedDocument` (parse lại là mất) hay `KBChunk` (sửa lời thoại không được làm bẩn index).

**Input:** `SlideRepr[]` + `DeckStructure` + `AlignmentMap[]` + `KBChunk[]` + `pronunciation.json` (+ `speaker_notes`)
**Output:** `Scenario[]`
**Model:** LLM, 2 pass (pass 1 viết, pass 2 cân thời lượng)

S4 là stage duy nhất sinh ra tiếng Việt thành phẩm. Mọi stage trước chỉ sinh dữ liệu.
Nó cũng là nơi **NT3 và NT4 gặp nhau và cãi nhau** — phần lớn file này nói về chỗ đó.

---

## 1. Hai nguyên tắc cùng đổ vào đây

**NT3 — ảo giác offline nguy hiểm gấp nhiều lần online.** Một câu bịa ở đây được robot nói
ở **mọi buổi thuyết trình**, và không ai kiểm lại.

**NT4 — tự nhiên là yêu cầu, không phải điểm cộng.** Robot nghe như đọc bản tin thì hệ
thống thất bại dù mọi chỉ số khác đạt. Không ai ngồi nghe hết 30 phút giọng máy đọc.

Hai cái này va nhau trực diện: nói tự nhiên nghĩa là có "thì", "nhé", "đúng không ạ", câu
hỏi tu từ, câu chuyển mạch — **không câu nào trong đó có nguồn**.

---

## 2. Gỡ va chạm: tách hai loại câu ★

```
kind: "content"    grounding ∈ {slide_repr, kb_chunk, speaker_notes}
                   null = CỜ ĐỎ                    <- luật NT3 giữ nguyên, KHÔNG nới

kind: "delivery"   grounding ∈ {structure, style}
                   KHÔNG mang thông tin sự thật mới
```

Câu `delivery` được validate **bằng code**, không bằng niềm tin:

```
không chứa CON SỐ
không chứa entity chưa xuất hiện ở trang này hoặc trang trước
không chứa mệnh đề khẳng định về sự thật
```

- `ungrounded_rate` từ nay tính **trên câu `content`**
- Câu `delivery`: **10–25% tổng âm tiết**
  - **25% là trần** — chống robot nói nhiều mà không nói gì
  - **10% là SÀN CỨNG** — xem §6, đây là chỗ dễ mất nhất

Nới NT3 thay vì tách câu là mở cửa cho ảo giác đi vào đúng chỗ nguy hiểm nhất. Đừng làm.

### Ví dụ phân loại

```json
{"kind": "delivery", "text": "Phần này thì hơi kỹ thuật một chút nhé.",
 "grounding": {"type": "structure", "ref": "sec3"}}

{"kind": "content",  "text": "Điểm mấu chốt là truy xuất tách hẳn khỏi sinh.",
 "grounding": {"type": "kb_chunk", "ref": "doc2#c118"}}

{"kind": "delivery", "text": "Vậy tại sao lại cần tách ra như vậy?",
 "grounding": {"type": "structure", "ref": "sec2->sec3"}}

{"kind": "delivery", "text": "Cách này giảm được khoảng 60% chi phí đấy."}
                              ^ SAI — chứa con số => phải là content, phải có nguồn
```

---

## 3. Đơn vị: câu, không phải đoạn

```
Scenario[slide] -> steps[] (theo build_step) -> sentences[]
```

| Lý do | Hệ quả |
|---|---|
| TTS cache key = hash từng câu | Sửa 1 câu chỉ synth lại 1 câu |
| `grounding` gắn ở mức câu | Cờ đỏ chỉ ra đúng câu nào bịa |
| **R7 kiểm tra ngắt sau mỗi câu** | Khán giả hỏi giữa chừng thì dừng ở ranh giới câu, không cụt |
| Đếm âm tiết để cân giờ | Cắt/thêm ở mức câu |

**`max_syllables` = 30, không phải 40.** Đây là ràng buộc của **runtime**, không phải của
TTS: ranh giới ngắt của [R7](../runtime/r7-interrupt-turntaking.md) là ranh giới câu, mà
câu 40 âm tiết ≈ **12 giây** — khán giả gửi câu hỏi rồi phải chờ hết 12 giây đó.

---

## 4. Bảy đòn bẩy tự nhiên, xếp theo tác động thật

### ① Văn nói ≠ văn viết — nguyên nhân số một

Tiếng Việt viết đọc lên nghe như đọc bản tin. Đây là thứ phải chặn trước mọi thứ khác.

```
CẤM:   "Việc tách truy xuất khỏi sinh được thực hiện nhằm mục đích cho phép
        sự cập nhật tri thức mà không cần tiến hành huấn luyện lại."

ĐÚNG:  "Mình tách truy xuất ra khỏi sinh. Nhờ vậy, muốn cập nhật tri thức
        thì chỉ cần thay tài liệu, không phải train lại."
```

Danh sách chặn (kiểm bằng code, ngưỡng = 0):

```
việc… (danh từ hoá)   sự…    được thực hiện bởi    nhằm mục đích
tiến hành…            thông qua việc…               đối với…
```

Luật viết: **câu chủ động · mệnh đề ngắn · không danh từ hoá động từ**.

### ② Nhịp — biến thiên độ dài câu

20 câu đều 28 âm tiết = máy đếm nhịp. Người thật nói dài ngắn xen kẽ.

```
trộn:  6–12 âm tiết   (câu chốt, câu chuyển)
       15–20          (câu chính)
       25–30          (câu giải thích)
```

Đo bằng **độ lệch chuẩn số âm tiết mỗi câu ≥ 6**. Rẻ, tự động, và bắt được đúng cái tai
người nghe thấy.

### ③ `prosody` mỗi câu

```json
"prosody": {"emphasis": ["tách hẳn"], "pause_before_ms": 250, "speed": 1.0}
```

S4 sinh, S6b áp vào TTS. Không có nó thì mọi câu cùng một đường
ngữ điệu phẳng.

### ④ Từ diễn ngôn đặt ĐÚNG ranh giới cấu trúc

"thì", "nhé", "à", "đúng không ạ" — đặt ở:

```
đầu build_step mới           -> "Tiếp theo nhé."
sau một con số               -> "Sáu mươi phần trăm đấy, không nhỏ đâu."
trước một tương phản          -> "Nhưng mà có một chỗ đánh đổi."
ranh giới section (từ arc)   -> câu hỏi tu từ
```

**Rải ngẫu nhiên thì TỆ HƠN không có** — nghe như model bị tic. Vị trí lấy từ
`arc` và `dependencies` của [S2](./s2-deck-structure.md), đó là lý do S2 sinh hai thứ đó.

### ⑤ Câu hỏi tu từ ở ranh giới section

Từ `arc`. Đây là thứ người thật làm để kéo lại sự chú ý sau mỗi 5–6 phút.

```
sec2 (problem) -> sec3 (solution)
   "Vậy làm sao khắc phục được? Câu trả lời nằm ở kiến trúc sau đây."
```

Một câu hỏi tu từ mỗi section là đủ. Nhiều hơn thành sáo.

### ⑥ Không đọc bullet, không đọc bảng theo hàng

Deck ít chữ nên ít rủi ro, nhưng **bảng và chart dụ model liệt kê**:

```
CẤM:   "Cột một là phương pháp, cột hai là chi phí, cột ba là độ trễ.
        Hàng một: RAG, thấp, trung bình. Hàng hai: fine-tuning, cao, thấp."

ĐÚNG:  "Bảng này so ba tiêu chí. Điểm đáng chú ý là RAG rẻ hơn hẳn,
        đổi lại độ trễ nhỉnh hơn một chút."
```

### ⑦ Từ điển phát âm

`pronunciation.json` (shared layer, S1 §3.2 bổ sung).

Hai lý do nó không phải chi tiết vặt của TTS:

1. Đọc sai "RAG" suốt 30 phút là thứ khán giả nhận ra ngay từ phút đầu
2. **S4 phải đếm âm tiết THEO bảng này** — "BM25" đọc rời từng chữ là 5 âm tiết, đọc gộp
   là 2. Đếm sai thì `time_budget` lệch theo

S4 ghi `pron_hash` vào `Scenario`; S6b **so hash trước khi synth**. Lệch = S4 đếm một
đằng, TTS đọc một nẻo, **timing sai mà không ai thấy**.

---

## 5. Chia theo `build_step` — luật cứng

> **Không tiết lộ nội dung chưa hiện lên màn hình.**

Câu thuộc `step k` chỉ được nói về phần tử đã hiện ở step ≤ k. Vi phạm thì robot "spoil"
trước khi khán giả nhìn thấy, và làm hỏng luôn tác dụng của animation tác giả cố ý dựng.

Validate bằng code (kiểm lỏng, chỉ để sinh flag): entity xuất hiện trong câu ở step k phải
nằm trong tập phần tử của step ≤ k.

`build_steps: null` (deck PDF suy biến) → toàn trang là 1 step.

---

## 6. Hai pass

### Pass 1 — viết

Song song 5 luồng. Input cho mỗi slide:

```
- SlideRepr[i] đầy đủ (trừ bbox, confidence)
- AlignmentMap[i].links + text_raw của các chunk được link
- DeckStructure: section chứa slide, role trong arc, time_budget của section
- concept_map: khái niệm ĐÃ định nghĩa ở trang trước  <- để KHÔNG giải thích lại
- dependencies[i]: trang nào là nền của trang này
- Câu cuối của kịch bản trang trước                   <- để viết câu chuyển
- pronunciation.json
- speaker_notes nếu có
```

Chế độ:

```
notes_word_count_avg > 30  ->  REFINE   (notes là bản nháp, S4 biên tập)
notes_word_count_avg = 0   ->  GENERATE (viết từ đầu)
```

**Dùng `concept_map` để không giải thích lại.** Thấy `embedding.introduced_at = 5` khi đang
viết trang 12 → *"như đã nói ở phần nền tảng, embedding…"*. Đây là khác biệt giữa kịch bản
nghe như người thật và kịch bản nghe như robot đọc wiki.

**Đổi giọng theo `arc`:** `problem` nhấn hậu quả · `evidence` dẫn số liệu ·
`closing` tổng kết.

### Pass 2 — cân thời lượng, và thứ tự cắt là LUẬT

```
actual > budget * 1.15  ->  NÉN theo ĐÚNG thứ tự:
     (1) trùng lặp giữa các câu content
     (2) câu content phụ, không mang ý chính
     (3) câu delivery — CUỐI CÙNG, và KHÔNG xuống dưới SÀN 10%

actual < budget * 0.85  ->  GIÃN bằng nội dung TỪ KB (có grounding)
     cấm giãn bằng câu content grounding=null
     không có gì để nói thêm -> để section ngắn, flag timing_underflow
```

**Vì sao thứ tự này là luật, không phải gợi ý:**

> Câu `delivery` là thứ **dễ cắt nhất** — bỏ đi không mất thông tin nào. Nên nếu không ghi
> thứ tự, mọi vòng cân giờ sẽ ăn vào đúng chỗ đó trước, và sau vài lần build kịch bản quay
> về đúng trạng thái cũ: **đủ giờ, đúng nguồn, và khô như đọc báo cáo.**

Toàn bộ §4 bốc hơi ở đây nếu không có sàn 10%.

---

## 7. `grounding` — 5 loại

| `type` | `ref` trỏ vào | Dùng cho |
|---|---|---|
| `slide_repr` | `slide7.message`, `slide7.relations[2]`, `slide7.chart_data` | câu `content` mô tả thứ có trên trang |
| `kb_chunk` | `chunk_id` từ `AlignmentMap` | câu `content` giải thích sâu hơn slide |
| `speaker_notes` | `slide7.notes` | câu `content` refine từ ghi chú tác giả |
| `structure` | `sec3`, `sec2->sec3`, `concept_map.embedding` | câu `delivery`: chuyển mạch, tham chiếu ngược, hỏi tu từ |
| `style` | — | câu `delivery` thuần diễn đạt ("Phần này hơi kỹ một chút nhé") |

Câu `content` với `grounding: null` là **cờ đỏ**, bắt duyệt ở S7.
Câu `delivery` **không bao giờ** được mang `slide_repr` / `kb_chunk` — nếu nó cần nguồn thì
nó là câu `content`, phân loại sai.

---

## 8. Đếm ÂM TIẾT, không đếm từ

> Tiếng Việt: **190–210 âm tiết/phút**.

Tiếng Việt đơn âm tiết — "công nghệ thông tin" là **1 từ ghép, 4 âm tiết**. Đếm từ thì lệch
2–3 lần và toàn bộ `time_budget` thành vô nghĩa.

```
token tiếng Việt   -> 1 âm tiết
token tiếng Anh    -> TRA pronunciation.json, không đoán
target_sec = syllables / (200 / 60)
```

---

## 9. Scenario — schema

```json
{
  "slide_id": 7,
  "section_id": "sec3",
  "arc_role": "solution",
  "target_sec": 78,
  "actual_sec": 74,
  "pron_hash": "7c1f9a...",

  "transition_in": {
    "kind": "delivery",
    "text": "Vậy làm sao khắc phục được? Câu trả lời nằm ở kiến trúc sau đây.",
    "syllables": 22,
    "prosody": {"emphasis": ["kiến trúc"], "pause_before_ms": 400, "speed": 0.97},
    "grounding": {"type": "structure", "ref": "sec2->sec3"}
  },

  "steps": [
    {"build_step": 1,
     "sentences": [
       {"id": "s7.1.1", "kind": "content",
        "text": "Kiến trúc RAG gồm ba khối nối tiếp nhau.",
        "syllables": 11,
        "prosody": {"emphasis": ["ba khối"], "pause_before_ms": 0, "speed": 1.0},
        "grounding": {"type": "slide_repr", "ref": "slide7.relations"},
        "tts_hash": "9f2a1c..."},

       {"id": "s7.1.2", "kind": "delivery",
        "text": "Nhìn thì đơn giản, nhưng chỗ hay nằm ở cách chúng nối với nhau.",
        "syllables": 17,
        "prosody": {"emphasis": ["cách chúng nối"], "pause_before_ms": 200, "speed": 1.0},
        "grounding": {"type": "structure", "ref": "sec3"},
        "tts_hash": "3b77e0..."},

       {"id": "s7.1.3", "kind": "content",
        "text": "Truy xuất tách hẳn khỏi sinh, nên cập nhật tri thức không cần train lại model.",
        "syllables": 24,
        "prosody": {"emphasis": ["tách hẳn"], "pause_before_ms": 250, "speed": 0.96},
        "grounding": {"type": "kb_chunk", "ref": "doc2#c118", "conf": 0.94},
        "tts_hash": "c41d82..."}
     ]}
  ],

  "stats": {"syllables_total": 248, "delivery_ratio": 0.17, "syllable_std": 7.2},
  "flags": [],
  "provenance": {"deterministic": ["syllables", "target_sec", "tts_hash", "stats"],
                 "llm": ["text", "kind", "prosody", "grounding"]}
}
```

`tts_hash` = SHA của `text` đã normalize + `voice_id` + `speed` + format.

---

## 10. Validate bằng code

1. Mọi câu có `kind` và `grounding` (giá trị `null` được, thiếu field thì không)
2. `grounding.ref` **tồn tại thật**: `chunk_id` có trong KB, path `slide_repr` hợp lệ,
   `section_id` có trong S2
3. **Câu `delivery` không chứa con số, không chứa entity mới, không khẳng định sự thật**
4. **Câu `delivery` không mang `grounding.type` ∈ {slide_repr, kb_chunk, speaker_notes}**
5. `delivery_ratio` ∈ [0.10, 0.25]
6. `syllable_std` ≥ 6
7. Không có từ trong danh sách văn viết bị cấm
8. Câu ≤ **30** âm tiết
9. Số `steps` == `build_steps` của slide đó
10. `pron_hash` khớp `pronunciation.json` hiện tại
11. Không có câu trùng lặp giữa các slide

---

## 11. Flag đẩy sang S7

```
[!!] ungrounded_content_sentence     -> CỜ ĐỎ, duyệt bắt buộc
[!]  grounding.conf < 0.6
[!]  delivery_below_floor            -> pass 2 đã ăn vào sàn 10%
[!]  written_register_hit            -> dính danh sách văn viết bị cấm
[!]  monotone_rhythm                 -> syllable_std < 6
[!]  timing deviation của section > 15%
[!]  spoiler_before_build_step
[!]  câu > 30 âm tiết
[!]  slide coverage = none nhưng kịch bản vẫn giải thích sâu
```

Dòng cuối là bẫy nguy hiểm nhất của S4: slide không có nguồn nhưng LLM vẫn viết được một
đoạn rất trôi chảy — vì nó **nhớ** chứ không phải nó **đọc**. Phải bắt bằng rule, không
trông vào `grounding` tự khai.

---

## 12. Quality gate ra khỏi S4

### Chạy trong CI

| Chỉ số | Ngưỡng |
|---|---|
| **Ungrounded rate (câu `content`)** | **< 10%** |
| **Timing deviation** | **< 15%** so với `time_budget` |
| `delivery_ratio` | 10–25% |
| `syllable_std` | ≥ 6 |
| Từ văn viết bị cấm | = 0 |
| Câu > 30 âm tiết | < 5% |
| `grounding.ref` trỏ vào thứ không tồn tại | = 0 (fail cứng) |

### Release gate — không chạy CI

**Naturalness MOS ≥ 3.8/5**, chấm ở [S7](./s7-hitl-review.md) trên bản nghe thử do S6b
xuất ra. Cần người ngồi nghe, nên đừng nhét vào CI rồi tự lừa mình bằng một con số giả.

Proxy tự động ở trên **tương quan** với MOS nhưng không thay được nó: một kịch bản có thể
đạt cả 6 proxy mà vẫn nghe như máy.

---

## 13. Failure mode

| Tình huống | Dấu hiệu | Xử lý |
|---|---|---|
| Pass 2 ăn hết câu `delivery` | `delivery_ratio` < 0.10 sau vài vòng build | Sàn cứng §6; nếu vẫn đụng sàn thì `time_budget` quá chặt, báo S7 |
| Từ diễn ngôn rải đều khắp nơi | Nghe như tic | Ép vị trí theo `arc`/`dependencies`, không để model tự rải |
| Model dùng `delivery` để lách grounding | Câu `delivery` mang thông tin thật | Validate 3 và 4 ở §10 |
| Nhịp đều | `syllable_std` < 6 | Ghi rõ vào prompt: yêu cầu trộn 3 nhóm độ dài |
| Đếm âm tiết lệch | `duration_ms` thật lệch >20% ước lượng | Thiếu từ trong `pronunciation.json`, hoặc `pron_hash` lệch |
| Refine mode nhưng notes viết kiểu văn viết | Dính danh sách cấm hàng loạt | Refine vẫn phải chuyển sang văn nói, không chép nguyên |

---

## 14. Cấm

- Câu không có `kind` hoặc không có field `grounding`
- **Câu `delivery` mang thông tin sự thật** (số, entity mới, khẳng định)
- **Pass 2 cắt `delivery` xuống dưới sàn 10%**, hoặc cắt `delivery` trước `content`
- Giãn thời lượng bằng câu bịa thay vì bằng nội dung từ KB
- Nói về phần tử chưa hiện ở `build_step` đó
- Đếm **từ** thay vì **âm tiết**, hoặc đếm không tra `pronunciation.json`
- Giải thích lại khái niệm đã `introduced_at` ở trang trước
- Viết cả trang thành một khối văn rồi mới cắt câu ở S6
- Danh từ hoá kiểu văn viết (`việc…`, `sự…`, `được thực hiện bởi`)
- Đọc bảng theo hàng, đọc bullet theo thứ tự
