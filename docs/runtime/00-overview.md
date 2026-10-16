# Nhánh runtime — tổng quan

## 1. Runtime là gì trong hệ thống này

Nhánh offline đã tái tạo phần kiến thức bị slide lược bỏ và **trả trước toàn bộ chi phí
suy luận**. Runtime không suy luận lại — nó **tra cứu** những gì offline đã dựng sẵn,
trong ngân sách **2.5 giây tới byte audio đầu tiên**.

Nói cách khác: mỗi khi runtime phải "nghĩ", đó là dấu hiệu offline làm thiếu việc.

Đây là lý do toàn bộ thiết kế dưới đây nghiêng về **tra bảng** thay vì **tính toán**:
`slide_index` đã index sẵn, `concept_map` đã embed sẵn, TTS đã synth sẵn,
`qa_cache` đã trả lời sẵn.

---

## 2. Thứ quan trọng nhất: đây là HAI vòng lặp

Đa số tài liệu về agent hỏi đáp chỉ mô tả **đường ngắt**. Nhưng ~90% thời lượng một buổi
thuyết trình là **vòng thuyết trình**, và phần khó nằm ở chỗ hai vòng giao nhau.

```
VÒNG THUYẾT TRÌNH (mặc định, latency = 0)
  for slide in deck:
      for step in slide.steps:
          renderer.render(slide, step) --ack--> phát audio đã synth sẵn, TỪNG CÂU
                                                  │
                                                  └── sau MỖI câu: hỏi R7 "có được ngắt không?"
                                                          │
                                                          └─(có)─> VÒNG TƯƠNG TÁC
                                                                      │
                                                          <───────────┘ resume
```

**Ranh giới ngắt là ranh giới câu.** Đây chính là lý do S4 chia kịch bản theo câu và S6
cache TTS theo câu — không phải để tiết kiệm tiền, mà để có **điểm dừng sạch**. Ngắt giữa
câu thì robot nói cụt, nghe như mất điện.

Hệ quả kéo ngược lên offline: độ trễ khán giả **cảm nhận** =
`thời gian còn lại của câu đang nói` + `ngân sách 2.5s`.
Một câu 40 âm tiết ≈ 12 giây. Nên luật "câu ngắn" ở S4 **là ràng buộc của runtime**,
không phải của TTS. Khuyến nghị siết `max_syllables` từ 40 xuống **30**.

---

## 3. R2, R3, R4 không phải ba lần gọi

```
R1 ──> R3a ──> TRUY XUẤT ──> [ MỘT lần gọi LLM ] ──> R5 ──> R6
       ~5ms    retrieve         │  R3b rewrite  (tham số tool)
       không   + rerank          │  R2 chọn trong top-3
       gọi     30–60ms           │  R4 chọn phạm vi + trả lời
       model                     └──────────────────────────────┘
                     R7 là chính sách cắt ngang, không nằm trên đường đi
```

**R3a nằm TRƯỚC truy xuất và không gọi model** — nó gỡ một vòng tròn: muốn truy xuất cần
query đã rewrite, muốn rewrite cần LLM, mà LLM chạy sau truy xuất.
Chi tiết ở [R3 §5](./r3-context-rewriting.md).

> **Luật cứng: đúng 1 lần gọi LLM cho routing + rewrite + chọn tool. KHÔNG tách router riêng.**

Vì sao luật này tồn tại: cả ba việc đó cần **chính xác cùng một ngữ cảnh** (state trình
chiếu + ứng viên đã truy xuất + `SlideRepr` trang hiện tại). Tách ra thì phải gửi cùng
khối ngữ cảnh đó hai lần, tốn thêm **300–400ms** cho đúng một việc mà lần gọi thứ nhất
đã làm miễn phí.

**R1, R3a và tầng truy xuất KHÔNG vi phạm luật này** — chúng không gọi model nào:

```
R1   regex                      ~5ms
R3a  khớp chuỗi + nối entity    ~5ms
     hybrid retrieve + rerank    30–60ms   <- reranker là model, nhưng KHÔNG phải LLM
                                              và nó không quyết định gì, chỉ xếp hạng
```

Kiến trúc "router → agent" nhìn sạch hơn trên sơ đồ, và đó chính là cái bẫy.

R2/R3/R4 tách nhau ở **tài liệu và đánh giá**, không tách ở **thực thi**.

---

## 4. Bảy lớp 

|              | Lớp                    | Một câu                                                                                      | File                              |
| ------------ | ----------------------- | ---------------------------------------------------------------------------------------------- | --------------------------------- |
| **R1** | Intake & fast-path      | Nhận vào hàng đợi, khử trùng · regex điều hướng ~5ms                               | [r1](./r1-intake-fastpath.md)      |
| **R2** | Slide navigation        | retrieve → rerank → LLM chọn top-3 +**confidence gate**                               | r2           |
| **R3** | Context-aware rewriting | Giải tiền ngữ bám**trạng thái phi ngôn ngữ** · R3a mở rộng query, R3b rewrite | [r3](./r3-context-rewriting.md)    |
| **R4** | Grounded answering      | 3 phạm vi · trích dẫn trang · biết nói không biết                                     | [r4](./r4-grounded-answering.md)   |
| **R5** | Streaming speech        | Cắt câu từ token stream → TTS từng câu                                                   | [r5](./r5-streaming-speech.md)     |
| **R6** | State machine & sync    | Nguồn chân lý duy nhất · hợp đồng ack · resume                                        | [r6](./r6-state-sync.md)           |
| **R7** | Interrupt & turn-taking | Khi nào được ngắt · hàng đợi · barge-in                                              | [r7](./r7-interrupt-turntaking.md) |

Escalation **không** là lớp riêng — nó là nhánh kết thúc của R4. "Biết nói không biết"
là một thuộc tính của việc trả lời có grounding, không phải một chặng xử lý.

### 4.1 Dòng thời gian một lượt

```
t=0     5ms    10ms    15ms         75ms              600ms    750ms    1.1s
│        │       │       │            │                 │        │        │
│ R1 regex       │       │            │                 │        │        │
├─ khớp ─>│ R6 render    │            │                 │        │        │  XONG
│         └────────────> │            │                 │        │        │  (~5ms)
│
└─ không khớp
     │
     ├──> filler prerecorded ══════════════════════════════════════════════> che ~2s
     │
     ├──> R3a mở rộng query   (~5ms, KHÔNG gọi model)
     │       khớp relations[].visual + nối entities trang hiện tại
     │            │
     ├──> TRUY XUẤT ═══════>│  hybrid multi-field top-5 -> rerank -> top-3
     │       30–60ms         │  ĐIỂM RERANKER -> confidence gate
     │                       │
     └──> 1x LLM  ═══════════╧══════════>│   prompt ~1.3k (không còn nhồi index)
                                          │
              ┌───────────────────────────┴──────────────────────────────┐
              │                                                          │
        find_slide            answer_current         search_kb       meta_action
              │                     │                    │                │
     R2 gate (điểm thật)       (R4, +0ms)          R4 KB retrieval    tra bảng
              │                     │               50–150ms       structure
        ├ nhảy ─> R6 render         │                    │                │
        │          └─> rồi mới NÓI  │               [generate]            │
        ├ hỏi lại ─> 2 thumbnail    │                ═══════>│            │
        └ từ chối                   │                        │            │
              │                     v                        v            v
              └────────────────> R5: cắt câu ─> TTS ─> phát  🔊
```

Ba đường dưới cùng đều đổ về R5. Đó là lý do R5 phải là **một hàng phát duy nhất** —
audio precomputed và audio streaming không được đi hai đường riêng.

### 4.2 Bên trong từng lớp

#### R1 · Intake & fast-path

```
VÀO  text từ form web (QR)
  │
  ├─> [normalize]  lowercase · bỏ dấu câu · BỎ DẤU THANH ──> text_norm
  │
  ├─> [rate limit]  1 câu / 30s / session_token ─> vượt: bỏ IM LẶNG
  │
  ├─> [fast-path regex]  neo đầu chuỗi + giới hạn độ dài
  │       khớp ───────────────────────────────> action ─> R6      [~5ms]
  │       <- CHỈ điều hướng tường minh          KHÔNG qua hàng đợi
  │       <- "tiếp" giữa trang = build_step tiếp, KHÔNG phải trang tiếp
  │       <- tối ưu cho PRECISION, không phải recall
  │
  └─ không khớp
       ├─> [khử trùng]  so chuỗi ─> embed bge-m3, cosine > 0.9
       │       trùng ─> dup_count++ trên câu đã có, DỪNG   (không vứt)
       │
       └─> [push queue]  Question{qid, text, at_slide, at_step, dup_count}
                                              ^ chụp LÚC NHẬN, không lúc xử lý
RA   action ─> R6    |    Question ─> R7 quyết định khi nào lấy ra
```

#### R2 · Slide navigation

```
VÀO  query_expanded (từ R3a) + slide_index (Qdrant) + state
  │
  ├─> [hybrid retrieve]  multi-field, KHÔNG gộp một vector      ~20–35ms
  │       v_message  <- "chỗ nói về việc không cần train lại"
  │       v_desc     <- "cái sơ đồ ba khối"
  │       sparse     <- "BM25"
  │       v_section  <- "quay lại phần đánh giá"   (đường mức section)
  │       -> top-k = 5
  │
  ├─> [rerank]  bge-reranker-v2-m3 -> top-3 kèm ĐIỂM THẬT       ~10–25ms
  │
  ├─> [1x LLM]  find_slide(slide_id, from_candidates:[7,16,11], why:"...")
  │                              ^ chỉ CHỌN trong 3, không tự khai điểm
  v
[confidence gate — điểm RERANKER]
   margin = rerank(top1) − rerank(top2)       <- số thật, calibrate được
   floor  = rerank(top1)
     │
     ├─ margin>=T ∧ floor>=T ∧ LLM chọn top1
     │     └─> NHẢY ─> push resume_stack ─> R6 render(slide, step CUỐI)
     │                                          └─> rồi MỚI nói
     ├─ margin nhỏ, hoặc LLM chọn top2, hoặc cặp nằm trong known_confusable_pairs
     │     └─> HỎI LẠI: 2 thumbnail + why      <- không đổi state
     └─ floor thấp ─> TỪ CHỐI: "bạn mô tả rõ hơn được không"

   recall@5 >= 95% TRƯỚC, rồi mới fit T trên bộ eval 50 câu
   <- trượt ở tầng truy xuất thì tầng LLM KHÔNG cứu được
RA   slide_id ─> R6    |    câu hỏi lại ─> R5
```

#### R3 · Context-aware rewriting

```
VÀO  câu hỏi thô + state
  │
  ├─> R3a  MỞ RỘNG QUERY — deterministic, ~5ms, KHÔNG gọi model
  │     khớp CHUỖI từ chỉ trỏ vào relations[].visual / visual_elements
  │        "mũi tên đỏ" -> relations[2]{Generator -> Knowledge Base,
  │                                      vòng cập nhật tri thức}
  │     + nối entities trang hiện tại (tối đa 8)
  │        │
  │        └─> query_expanded ──────────> dùng để TRUY XUẤT (R2, R4)
  │                                       <- xấu là bình thường, máy đọc
  │
  │     ^^^ R3a gỡ VÒNG TRÒN: muốn truy xuất cần query rewrite,
  │         muốn rewrite cần LLM, LLM chạy SAU truy xuất
  │
  └─> R3b  REWRITE THẬT — trong lần gọi LLM duy nhất
        [prompt đã có] SlideRepr[at_slide]: relations (TRIPLE) ·
                       visual_elements · chart_data · tables
                       + visited[] + history 3 lượt
        "chỗ vừa nãy"  ─> visited[] + history
        "nó / cái này" ─> lượt trước THẮNG, trừ khi có từ chỉ trỏ thị giác
            │
            ├─> query_rewritten ───────> LÀ THAM SỐ của tool call
            └─> text GỐC ──────────────> dùng để SINH CÂU TRẢ LỜI
                                         <- KHÔNG trả lời câu đã rewrite
RA   query_expanded ─> truy xuất   |   query_rewritten ─> tham số tool
```

Ba query, đừng lẫn: `text` cho **người nghe**, `query_expanded` cho **truy xuất**,
`query_rewritten` cho **tham số tool**.

#### R4 · Grounded answering

```
VÀO  tool call + state
  │
  ├─> QA_CURRENT   answer_current(answer, grounding)              +0ms
  │       nguồn: SlideRepr[at_slide] ĐÃ nằm trong prompt
  │       <- trả lời NGAY trong lần gọi routing (tiết kiệm 400–700ms)
  │       <- bắt buộc grounding trỏ slide_repr, VALIDATE ref bằng code
  │
  ├─> QA_DECK                                                 +0ms THÊM
  │    ├ TRUY XUẤT: v_concept (gloss của S2) — CÙNG lần gọi Qdrant với R2
  │    │     "embedding là gì?" -> gloss + introduced_at=5
  │    │     -> "đã trình bày ở trang 5, quay lại nhé?"
  │    └ TRA BẢNG:  dependencies -> nạp SlideRepr trang nền
  │                 time_budget + elapsed_sec -> "còn khoảng 18 phút"
  │       ^ hỏi bằng NGÔN NGỮ thì truy xuất, tra bằng KHOÁ thì tra bảng
  │
  └─> QA_KB        search_kb trên kb_chunks                   +50–150ms
         [filter]  deck_ids ∋ active_deck_id          <- LUÔN LUÔN
                   content_type theo intent
                   answer_depth == "describe_only" ─> CHẶN, escalate luôn
              │
              ├─> hybrid retrieve ─> [generate] 2–4 câu ─> R5
              │       ép prompt + max_tokens (CẢ HAI)
              │       trích dẫn bằng LỜI: "như ở trang 7 mình có nói..."
              │                           <- KHÔNG dùng [slide 7]
              └─ score thấp ─> ESCALATE người thật, KHÔNG bịa
                    │
              [post-hoc, chỉ LOG]  số trong câu trả lời có trong chunk không?
RA   câu trả lời ─> R5    |    escalate ─> R6 (phase = escalated)
```

#### R5 · Streaming speech

```
VÀO  token stream    |    câu precomputed (kịch bản chính / qa_cache hit)
  │
  └─> [buffer] ─> [cắt câu] ─> [hàng TTS, trần 2 câu] ─> [phát]
                      │
         cắt ở . ? !  nhưng:
           bảng abbrev (TS. PGS. Fig. v.v.)
           dấu chấm giữa 2 CHỮ SỐ ≠ kết câu
           <- ép ở NGUỒN: prompt cấm "0.88", viết "không phẩy tám tám"
           <- ép câu ĐẦU < 12 âm tiết ─> ăn thẳng 200–300ms TTFB

         câu 1 ĐANG PHÁT  ║  câu 2 ĐANG SYNTH  ║  câu 3 ĐANG SINH
                      │
         [cancel token xuyên MỌI tầng]  generate · tts · play
              play phải huỷ được GIỮA FILE, không chỉ giữa các câu
              huỷ ─> fade out ~80ms, KHÔNG cắt cụp

         nghỉ: 150ms giữa câu · 400ms giữa ý / sang trang · 600ms sau câu hỏi lại
RA   audio ─> loa        (MỘT hàng phát duy nhất cho cả precomputed lẫn streaming)
```

#### R6 · State machine & sync

```
VÀO  action từ R1 / R2 / R4 / R7
  │
  ├─> [máy trạng thái]
  │      presenting ──hỏi nội dung──> answering ──xong──> presenting
  │           ├──điều hướng──> navigating ──ack──> presenting
  │           ├──pause──> paused                └──> answering
  │           └──escalate──> escalated
  │
  ├─> [resume_stack]  CHỈ push khi rời presenting để đi navigating
  │       trần độ sâu 2                <- hỏi đáp tại chỗ KHÔNG push
  │       pop ─> (1) NÓI RA MIỆNG "quay lại phần Hybrid search đang dở nhé"
  │                   ^ tên SECTION từ S2, KHÔNG nói số trang
  │              (2) về đúng build_step
  │              (3) về đúng sentence_id
  │
  └─> [hợp đồng renderer]   THỨ TỰ BẤT BIẾN: lệnh hình ─> ack ─> lời nói
         render(slide_id, build_step) ───────────────>│  renderer (NGU)
                        │<── ack{slide_id, build_step, ts} ──┘
                        │       ^ ack phải MANG NỘI DUNG, không phải {ok:true}
         không ack 500ms ─> retry 1 lần ─> vẫn không: CHẾ ĐỘ SUY BIẾN
              nói tiếp bằng lời · KHÔNG cập nhật slide_id · báo màn hình vận hành
RA   state cập nhật  (nguồn chân lý DUY NHẤT)
```

#### R7 · Interrupt & turn-taking

```
VÀO  hàng đợi + phase hiện tại      <- KHÔNG nằm trên đường đi của một lượt
  │
  ├─> [điểm kiểm tra]  sau MỖI CÂU
  │       không liên tục      ─> sẽ phải cắt giữa câu
  │       không sau mỗi trang ─> chờ quá lâu (trang dài 90s)
  │
  ├─> [ma trận ngắt]
  │       presenting + điều hướng    ─> NGẮT NGAY ở ranh giới câu
  │       presenting + hỏi nội dung  ─> ĐỢI HẾT TRANG (trừ khi queue > 3)
  │       presenting + meta          ─> áp dụng ngay, KHÔNG ngắt lời
  │       answering  + điều hướng    ─> ngắt ngay (fade 80ms)
  │       answering  + câu hỏi mới   ─> vào hàng đợi
  │       navigating + bất cứ gì     ─> đợi ack xong đã
  │
  ├─> [xếp ưu tiên]  dup_count*w1 + độ_cũ*w2 + (về trang hiện tại ? w3 : 0)
  │       trần hàng đợi 20 · trần chờ 90s ─> quá hạn: trả lời ngay / xin lỗi
  │
  └─> [phản hồi thị giác]  <- bù cho kênh text KHÔNG có backchannel
         điện thoại người gửi : "đã nhận · đang có 3 câu trước bạn"
         màn hình trình chiếu : "3 câu hỏi đang chờ"
         khi bắt đầu trả lời  : "đang trả lời: cái mũi tên đỏ kia..."
RA   quyết định lấy câu nào ra, khi nào ─> R2 / R3 / R4
```

---

## 5. Lần gọi LLM duy nhất — prompt gồm gì

Vì R2/R3/R4 sống trong đây, phải thấy nó trước mọi thứ khác.

### Ranh giới: TRẠNG THÁI inline, TRI THỨC truy xuất ★

```
TRẠNG THÁI  -> inline, bounded   "tôi đang nhìn gì" — KHÔNG index nào trả lời được
TRI THỨC    -> TRUY XUẤT         slide · section · concept · KB chunk
```

Truy xuất được cái bạn **có thể không cần**. Không truy xuất được cái bạn **đang đứng trên đó**.

```
[SYSTEM]   vai trò · luật 2–4 câu · luật không bịa · luật escalate

[STATE]    Đang ở trang 7, bước 2/3, phần "Kiến trúc" (sec3)
           Đã trình bày: trang 1–7 · Còn khoảng 18 phút

[TRANG HIỆN TẠI]   SlideRepr[7] rút gọn:                 ~400 tok
           message · description · relations (triple) · entities
           · chart_data · tables                       <- nhiên liệu của R3

[DECK_MAP]         BỎ 2026-10-13 — không còn danh sách chương trong prompt;
                   câu hỏi cấu trúc phải tìm ra trang mục lục

[ỨNG VIÊN ĐÃ TRUY XUẤT]   top-3 slide sau rerank        ~300 tok
                   + concept/section khớp, nếu có
                   <- THAY CHO slide_index (1.4k) và concept_map (600) của bản cũ

[LỊCH SỬ]          3 lượt gần nhất, CẮT CỨNG            ~400 tok

[CÂU HỎI]          "cái mũi tên đỏ kia để làm gì"

[TOOLS]            goto_slide · find_slide · answer_current
                   search_kb · meta_action · escalate
```

**Tổng ~1.3k token**, thay cho ~3.6k của bản trước. Và ổn định — không phình theo thời gian
vì lịch sử cắt cứng ở 3 lượt. Không cắt thì cuối buổi prompt phình gấp ba và latency trôi
dần, **đúng lúc khán giả hỏi nhiều nhất**.

Hai khối bị bỏ và lý do:

| Bỏ                      | Cũ                     | Giờ ở đâu                                                               |
| ------------------------ | ----------------------- | --------------------------------------------------------------------------- |
| `slide_index` cả deck | ~1.4k token mỗi lượt | `slide_index` Qdrant, R2 truy xuất top-5            |
| `concept_map` cả deck | ~600 token mỗi lượt  | `v_concept` trong cùng index, [R4](./r4-grounded-answering.md) truy xuất |

### Tool schema — đây chính là bảng phân loại intent

Không có bước "phân loại intent" nào tách rời. Phân loại intent **là** việc LLM chọn tool.

| Intent                                | Tool                                                          |
| ------------------------------------- | ------------------------------------------------------------- |
| điều hướng                        | `goto_slide(slide_id)` · `find_slide(query, candidates)` |
| hỏi nội dung (trang hiện tại)     | `answer_current(answer, grounding)`                         |
| hỏi nội dung (cần nguồn)          | `search_kb(query, content_type_hint)`                       |
| meta                                  | `meta_action(kind)`                                         |
| ngoài phạm vi / không đủ tự tin | `escalate(reason)`                                          |

---

## 6. State — nguồn chân lý duy nhất

```json
{
  "deck_id": "rag-intro",
  "slide_id": 7, "build_step": 2, "sentence_id": "s7.2.1",
  "phase": "presenting",

  "resume_stack": [{"slide_id": 7, "build_step": 2, "sentence_id": "s7.2.1"}],
  "visited": [1,2,3,4,5,6,7],
  "elapsed_sec": 640, "section_id": "sec3",

  "queue": [{"qid": "q_0142", "text": "...", "at_slide": 7, "dup_count": 3}],
  "history": [{"role": "audience", "text": "...", "at_slide": 7}],

  "renderer_ack": {"slide_id": 7, "build_step": 2, "at": 1699000000}
}
```

**Orchestrator giữ state này. Renderer không giữ gì cả.** Chi tiết ở [R6](./r6-state-sync.md).

---

## 7. Ngân sách

| Bước                        | Ngân sách                             | Đổi so với bản cũ                 |
| ----------------------------- | --------------------------------------- | -------------------------------------- |
| R1 regex fast-path            | ~5 ms (bắt ~40%)                       | —                                     |
| **R3a mở rộng query** | **~5 ms**                         | **mới**                         |
| **Truy xuất + rerank** | **30–60 ms**                     | **mới**                         |
| 1x LLM function-calling       | **300–550 ms**                   | nhanh hơn (prompt 1.3k thay vì 3.6k) |
| R4 retrieval KB (chỉ QA_KB)  | 50–150 ms                              | —                                     |
| R5 generation, first token    | 300–500 ms                             | —                                     |
| R5 TTS chunk đầu            | 200–400 ms                             | —                                     |
| **Tổng**               | **1.2–1.8 s** · gate P95 < 2.5s | **ròng ≈ hoà**                |

Truy xuất thêm 35–65ms nhưng prompt ngắn đi ~2.3k token nên lần gọi LLM nhanh lại tương
đương. Đổi lại được **điểm thật cho confidence gate** và **từng thành phần ablate được**.

### Ba lớp che — đây mới là thứ khán giả cảm nhận

1. **Filler audio** phát ở t≈10ms, che ~2s — **dài hơn cả tổng ngân sách**
2. **Hành động thị giác đi trước lời nói** — nhảy trang/highlight thấy ngay, não khán giả
   tính đó là "đã phản hồi"
3. **`qa_cache` hit** → ~200ms, audio đã synth sẵn từ S6

Cộng với vòng thuyết trình latency 0, **~90% thời lượng buổi nói là tức thời**.

---

## 8. Ba quyết định đã chốt

| # | Quyết định                                                                                                                                                                                                                                   | Ở đâu                              |
| - | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| 1 | **Confidence gate** lấy score từ **reranker** (`bge-reranker-v2-m3`) — số thật, calibrate được, không dùng điểm LLM tự khai. Ngưỡng **fit trên bộ 50 câu có nhãn**, sau khi `recall@5` đã đạt 95% | R2 §4–5        |
| 2 | **QA_CURRENT trả lời thẳng trong lần gọi routing**, không gọi tool rồi gọi lại. Bắt buộc kèm `grounding` trỏ vào `slide_repr`                                                                                          | [R4 §3](./r4-grounded-answering.md)   |
| 3 | **Ma trận ngắt:** điều hướng ngắt ngay ở ranh giới câu · hỏi nội dung đợi hết trang (trừ khi hàng đợi > 3) · meta áp dụng ngay không ngắt lời                                                                   | [R7 §3](./r7-interrupt-turntaking.md) |

---

## 9. Bảng chỉ số

| R  | Chỉ số chính                                                        | Ngưỡng                                     |
| -- | ---------------------------------------------------------------------- | -------------------------------------------- |
| R1 | fast-path hit rate ·**false positive rate**                     | ~40% · ≈ 0                                 |
| R2 | **harmful jump rate** · **recall@5** · Top-1 · ask rate | < 2% ·**≥ 95%** · báo cáo · ~15% |
| R3 | coreference resolution accuracy                                        | so với 2 baseline                           |
| R4 | faithfulness · correct-refusal ·**over-refusal**               | báo cáo cả ba                             |
| R5 | TTFB ·**underrun** (hụt tiếng)                                | P95 < 2.5s · 0                              |
| R6 | **desync events** · ack p95                                     | 0 · < 200ms                                 |
| R7 | thời gian chờ câu hỏi · số lần ngắt / buổi                    | báo cáo                                    |

Hai chỉ số in đậm đáng chú ý vì chúng **không phải chỉ số hiển nhiên**:
`harmful jump rate` quan trọng hơn Top-1 accuracy (R2), và
`underrun` quan trọng hơn TTFB ([R5](./r5-streaming-speech.md)).

---

## 10. Runtime đọc gì từ offline

Runtime **chỉ** đọc `DeckBundle` đã qua S7 duyệt:

```
slide_index (Qdrant)   -> R2 TRUY XUẤT, R4 QA_DECK    <- collection, KHÔNG phải file text
manifest.json          -> audio + duration + prosody, R5/R6
slide_repr_lite[]      -> inline: bảng chỉ trỏ cho R3, nguồn cho R4 QA_CURRENT
structure              -> sections, dependencies — R4/R6 (concept_map đã vào index)
answer_depth[]         -> R4 filter
known_confusable_pairs -> R2 luôn hỏi lại ở cặp này
voice_id + speed       -> R5 PHẢI dùng đúng giá trị này
qa_cache               -> R4 fast answer
thumbs/                -> R2 confidence gate
tts/                   -> R5
```

Runtime **không** đọc `raw/`, `alignment.json`, `flags.json`, `scenario.json` — đó là tài
sản của offline.

**Hai kiểm tra bắt buộc lúc khởi động:**

```
model_id trong tên collection == model đang nạp ?   không -> TỪ CHỐI CHẠY
voice_id trong bundle == voice_id của streaming TTS ? không -> TỪ CHỐI CHẠY
```

Cả hai đều là lớp lỗi **im lặng**: lệch `model_id` thì truy xuất trả rác mà không báo lỗi;
lệch `voice_id` thì câu trả lời phát ra bằng giọng khác giọng kịch bản.

Chạy với bundle **chưa duyệt** thì orchestrator phải **cảnh báo rõ ràng lúc khởi động**,
không im lặng chạy.
