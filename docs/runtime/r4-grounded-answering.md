# R4 — Grounded answering

**Vào:** tool call từ lần gọi LLM duy nhất + state
**Ra:** 2–4 câu có trích dẫn trang, **hoặc** escalate người thật
**Model:** lần gọi LLM duy nhất (QA_CURRENT) hoặc thêm một lần generate (QA_KB)

---

## 1. Vì sao có lớp này

### 1.1 Kinh tế học của lòng tin là bất đối xứng

```
Robot nói "mình chưa chắc"     ->  khán giả tin hơn
Robot bịa một câu trôi chảy    ->  phá sạch độ tin cậy của cả buổi
                                   VÀ thường không ai phát hiện tại chỗ
```

Vế thứ hai mới là vấn đề thật. Một câu bịa không tự lộ ra — nó nghe hay hơn câu thật, vì
model không bị ràng buộc bởi sự thật nên viết trôi hơn. Không ai trong hội trường có tài
liệu gốc để đối chiếu. Sai sót **không có cơ chế tự sửa**.

Vì vậy R4 không được thiết kế quanh câu hỏi *"làm sao trả lời hay nhất"* mà quanh câu hỏi
*"làm sao không bao giờ nói thứ mình không có nguồn"*.

### 1.2 Chi phí ba phạm vi chênh nhau một bậc

Không phải câu hỏi nào cũng cần truy xuất. Gộp hết vào một đường là **tự trả giá 400–700ms
cho 60% câu hỏi không cần**.

| Phạm vi | Ví dụ | Nguồn | Retrieval | Thêm bao nhiêu |
|---|---|---|---|---|
| **QA_CURRENT** | "mũi tên đỏ kia là gì?" | `SlideRepr[at_slide]` — **đã nằm trong prompt rồi** | không | **~0** |
| **QA_DECK** | "embedding là gì?" (đang ở trang 12) | `v_concept` / `v_section` trong `slide_index` | **có** — nhưng cùng lần truy xuất với R2 | **~0 thêm** |
| **QA_KB** | "có số liệu nào chứng minh không?" | `kb_chunks` hybrid | có | +50–150ms + 1 lần generate |

**QA_DECK đổi cơ chế** so với bản trước: `concept_map` không còn được dump cả bảng vào
prompt (~600 token mỗi lượt). Mỗi khái niệm giờ là một document trong `slide_index` với
`v_concept` embed từ `gloss` ([S2](../offline/s2-deck-structure.md)).

Nó **không tốn thêm latency** vì chạy chung lần truy xuất với R2:
slide, section và concept nằm chung index, khác `content_type`. Một lần gọi Qdrant, lọc
kết quả theo loại.

---

## 2. Công dụng cụ thể

| Không có R4 có kỷ luật | Có |
|---|---|
| Model trả lời từ trí nhớ của nó | Trả lời từ nguồn đã qua S7 duyệt |
| Không biết câu trả lời đến từ đâu | Trích dẫn trang, mở được nước đi tiếp |
| Hết nguồn thì bịa | Hết nguồn thì escalate |
| Mọi câu hỏi tốn như nhau | Câu về trang hiện tại gần như miễn phí |

---

## 3. QA_CURRENT — trả lời thẳng trong lần gọi routing ★

> **Quyết định đã chốt:** cho phép, **bắt buộc kèm `grounding`**.

```json
answer_current(
  answer: "Mũi tên đỏ đó là vòng cập nhật tri thức. Nó cho phép nạp tài liệu mới vào knowledge base mà không phải train lại model.",
  grounding: {"type": "slide_repr", "ref": "slide7.relations[2]"}
)
```

### Vì sao được phép

Model **đã cầm `SlideRepr[7]` trong prompt** rồi. Bắt nó gọi thêm một tool để lấy về đúng
thứ nó đang cầm, rồi gọi LLM lần nữa để diễn đạt — là phí trắng **400–700ms**.

### Vì sao không phá kỷ luật grounding

`SlideRepr` **đã qua S7 duyệt**. Nó không phải trí nhớ của model, nó là **nguồn đã kiểm**.
Trả lời từ nó chính xác như trả lời từ một chunk KB đã verify.

Điều kiện: `grounding.ref` phải trỏ vào một đường dẫn **có thật** trong `SlideRepr` —
validate bằng code. Trỏ vào `slide7.relations[9]` khi trang chỉ có 3 relations → coi như
không có grounding → hạ xuống đường escalate.

### Ranh giới: khi nào KHÔNG được dùng QA_CURRENT

```
Câu hỏi đi ra ngoài thứ có trên trang  ->  phải search_kb
"tại sao lại thiết kế như vậy?"        ->  slide không trả lời được -> KB
"cái này dùng ở đâu trong thực tế?"    ->  KB
"mũi tên đỏ kia là gì?"                ->  QA_CURRENT
```

Ghi rõ trong prompt: *"Chỉ dùng `answer_current` khi câu trả lời nằm trọn trong dữ liệu
trang hiện tại đã cho. Cần thêm bất cứ gì → `search_kb`."*

---

## 4. QA_DECK — truy xuất khái niệm, tra bảng phần còn lại

Câu hỏi liên quan tới **quan hệ giữa các trang**, giải bằng `DeckStructure` đã dựng sẵn ở
[S2](../offline/s2-deck-structure.md) — nhưng theo hai đường khác nhau:

### 4.1 Đường TRUY XUẤT — khái niệm

```
"embedding là gì?" (đang ở trang 12)
   -> R3a query_expanded -> truy xuất slide_index
   -> khớp v_concept của "embedding" (gloss do S2 viết)
   -> kèm theo metadata: introduced_at = 5
   -> trả lời từ gloss + "mình đã trình bày ở trang 5, quay lại nhé?"
```

Vì sao truy xuất thay vì dump cả `concept_map`: deck 20 trang có thể có 40–60 khái niệm.
Dump hết là ~600 token **mỗi lượt hỏi**, để dùng đúng một dòng.

### 4.2 Đường TRA BẢNG — quan hệ và tiến độ

Những thứ **không truy vấn được bằng ngôn ngữ**, chỉ tra theo khoá:

| Khán giả hỏi | Tra cái gì | Robot nói |
|---|---|---|
| "cái này khác gì fine-tuning?" | `dependencies[16].requires = [4,7]` | nạp thêm `SlideRepr[4]`, `SlideRepr[7]` làm nền |
| "còn bao lâu nữa?" | `time_budget` + `elapsed_sec` | *"Ta đang ở phần 3/6, còn khoảng 18 phút"* |
| "đang ở phần nào rồi?" | `doc.sections` + `section_id` (S0, trong bộ nhớ) | *"Phần Kiến trúc, trang 7 trên 20"* |

**Tốn 0ms** — tra khoá, không phải truy vấn ngữ nghĩa. `dependencies` chỉ mục theo
`slide_id` nên nó nằm inline trong bundle, không cần index.

Ranh giới: **cái gì hỏi bằng ngôn ngữ thì truy xuất, cái gì tra bằng khoá thì tra bảng.**

---

## 5. QA_KB — truy xuất, và các filter bắt buộc

Dùng `query_expanded` của [R3a](./r3-context-rewriting.md) để retrieve,
`query_rewritten` của R3b làm tham số tool. **Hai index tách biệt:**

```
slide_index__{deck}__{model}   <- QA_DECK: slide · section · concept
kb_chunks__{model}             <- QA_KB:   chunk từ tài liệu nguồn
```

Filter bắt buộc trên `kb_chunks`:

```
deck_ids ∋ active_deck_id                      <- LUÔN LUÔN, không ngoại lệ
content_type theo intent                       <- "X là gì"  -> definition
                                                  "bao nhiêu" -> evidence
                                                  "làm thế nào" -> method
slide có answer_depth == "describe_only"       <- KHÔNG đi KB, escalate luôn
```

### Vì sao filter `deck_ids`

KB là **shared layer** giữa nhiều deck. Không filter thì câu trả lời có thể lấy từ tài liệu
của deck khác — đúng về nội dung nhưng khán giả không có ngữ cảnh đó, và robot không trích
dẫn được trang nào.

### Vì sao filter `content_type`

Gần như miễn phí (metadata filter trong Qdrant) và tăng precision rõ rệt. Câu "embedding là
gì" mà retrieval trả về một đoạn dùng từ "embedding" trong ngữ cảnh so sánh hiệu năng thì
câu trả lời sẽ lệch.

### Vì sao `answer_depth: describe_only` phải chặn

Trường này do **người duyệt ở S7** đặt cho slide `zero_coverage` được xác nhận là ý riêng
của tác giả, không có trong tài liệu.

> Trang không có nguồn mà vẫn trả lời sâu chính là **ca bịa nguy hiểm nhất**, vì model nói
> rất trôi chảy — nó đang **nhớ**, không phải đang **đọc**.

Chặn ở đây là chỗ duy nhất chặn được: retrieval sẽ vẫn trả về chunk gì đó (không bao giờ
trả về rỗng), và LLM sẽ vẫn viết được một đoạn hay.

---

## 6. Trích dẫn — nói bằng lời, không bằng ký hiệu

```
SAI:   "RAG tách truy xuất khỏi sinh [slide 7]."
ĐÚNG:  "Như ở trang 7 mình có nói, RAG tách truy xuất khỏi sinh."
```

TTS đọc `[slide 7]` thành *"ngoặc vuông slide bảy"*. Citation phải là **cấu trúc câu tiếng
Việt**, không phải markup. Ghi luật này vào prompt, không xử lý bằng hậu kỳ regex.

Công dụng thứ hai của citation: nó **mở được nước đi tiếp**.

```
"Khái niệm này mình đã trình bày ở trang 5, quay lại nhanh nhé?"
   -> khán giả gật -> goto_slide(5) -> resume sau đó
```

Không có citation thì `concept_map` của S2 mất một nửa công dụng.

---

## 7. Thang từ chối

```
conf cao          -> trả lời thẳng
conf trung bình   -> trả lời + rào: "theo tài liệu mình có thì..."
conf thấp         -> KHÔNG trả lời -> escalate người thật
ngoài phạm vi     -> từ chối ngắn, không giảng giải
hỏi sang deck khác-> trả lời bằng LỜI + câu mềm, KHÔNG điều hướng
```

**Ngưỡng escalate phải cao hơn trực giác.** Cảm giác tự nhiên của người code là "cố trả
lời được thì tốt". Ở đây thì ngược lại — chi phí của một câu bịa cao hơn nhiều chi phí của
một lần chuyển cho người thật.

Câu escalate phải **có lối ra**, không được cụt:

```
KHÔNG:  "Xin lỗi, tôi không biết."
CÓ:     "Câu này mình chưa có đủ dữ liệu để trả lời chắc chắn,
         xin phép chuyển cho anh/chị phụ trách."
```

**Over-refusal cũng là lỗi.** Robot câm thì vô dụng. Vì vậy phải đo **cả hai chiều** —
xem §9.

---

## 8. Kiểm faithfulness rẻ tiền

Post-hoc, chạy **sau khi đã phát audio**, chỉ để **log**:

```
mọi CON SỐ trong câu trả lời có xuất hiện trong chunk đã lấy không?
   -> không  ->  log cờ đỏ kèm qid, chunk_id, câu trả lời
```

Vì sao chỉ số: **số là thứ model bịa nhiều nhất và dễ kiểm nhất bằng code**. Thực thể tên
riêng cũng kiểm được nhưng nhiều false positive hơn (viết tắt, biến thể).

Vì sao không gate: đã nói ra miệng rồi, chặn cũng muộn. Nhưng log lại thì sau buổi có
**dữ liệu thật** để đo faithfulness, thay vì phải chấm tay toàn bộ.

---

## 9. Chỉ số

| Chỉ số | Vì sao |
|---|---|
| **Faithfulness** | Câu trả lời có nằm trong nguồn không — chấm tay + số liệu từ §8 |
| **Correct-refusal rate** | Từ chối đúng lúc, trên bộ câu hỏi out-of-scope |
| **Over-refusal rate** | **Từ chối oan** — cũng tệ, robot câm thì vô dụng |
| Tỉ lệ QA_CURRENT / QA_DECK / QA_KB | Kiểm định thiết kế: nếu QA_KB > 60% thì `SlideRepr` đang quá nghèo |
| Latency theo phạm vi | Kiểm chứng rằng QA_CURRENT thật sự ~0ms |

Bộ test cần **hai nửa**: câu hỏi in-scope (đo faithfulness + over-refusal) và out-of-scope
(đo correct-refusal). Chỉ có một nửa thì tối ưu được một chiều bằng cách hy sinh chiều kia.

---

## 10. Độ dài: 2–4 câu, ép ở CẢ HAI chỗ

```
prompt:      "Trả lời 2–4 câu. Không dùng gạch đầu dòng."
max_tokens:  chặn cứng
```

Chỉ ép prompt → model vẫn viết 3 đoạn khi hào hứng.
Chỉ ép `max_tokens` → câu cuối bị cắt cụt giữa chừng, TTS đọc ra một câu dở dang.

Ép cả hai: **prompt lo độ dài, `max_tokens` lo chặn thảm hoạ.**

Vì sao 2–4: khán giả đang nghe, không đọc. Không tua lại được. Quá 4 câu là mất dấu, và
làm chậm buổi thuyết trình.

---

## 11. Failure mode

| Tình huống | Dấu hiệu | Xử lý |
|---|---|---|
| Model dùng `answer_current` cho câu cần KB | Câu trả lời chung chung, không có chi tiết | Siết ranh giới ở prompt (§3) |
| `grounding.ref` trỏ vào chỗ không tồn tại | Validate bằng code | Hạ xuống escalate |
| Retrieval trả về chunk `background` không liên quan | Score thấp đều | Đúng đường escalate, đừng cố ép |
| Trả lời đúng nhưng không trích dẫn | — | Ghi luật vào prompt; log tỉ lệ có citation |
| Câu trả lời chứa số không có trong nguồn | §8 bắt được | Log; nếu tỉ lệ cao → xem lại prompt hoặc hạ nhiệt độ |
| Khán giả hỏi về deck khác | — | Trả lời bằng lời + câu mềm, **không** điều hướng |
| `qa_cache` hit nhưng câu hỏi thực ra khác ý | Ngưỡng quá thấp | Giữ ngưỡng **0.88**, không hạ để tăng hit rate |

---

## 12. Cấm

- Trả lời sâu về slide có `answer_depth: "describe_only"`
- Bỏ filter `deck_ids`
- **Dump cả `concept_map` vào prompt** — mỗi khái niệm là một document trong `slide_index`
- **Truy xuất `kb_chunks` cho câu hỏi khái niệm trong deck** — đó là việc của `v_concept`,
  rẻ hơn và trả lời đúng cái tác giả định nói, không phải cái paper nói
- Trích dẫn bằng ký hiệu `[slide 7]`
- Bịa khi retrieval score thấp thay vì escalate
- Chỉ ép độ dài bằng một trong hai cơ chế
- Hạ ngưỡng `qa_cache` xuống dưới 0.88
- `answer_current` mà không có `grounding` hợp lệ
