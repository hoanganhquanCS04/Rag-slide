# S2 — Deck Structure

> ⚠️ **BẢN CŨ — đã co lại nhiều.** `sections` giờ dựng bằng **luật** từ **trang mục lục**
> ngay ở S0 (`src/parsing/sections.py`): Onboarding 5 chương, Thời gian làm việc 5 chương,
> không cần LLM. `concept_map`, `dependencies`, `arc` đã **BỎ** — chúng sinh ra để bơm ngữ
> cảnh toàn cục vào prompt, mà thiết kế hiện tại không nhồi. S2 nay chỉ còn
> **`time_budget`**: chia theo số trang `content` mỗi section — **chưa code**, và S4 hiện
> cũng chưa cần (độ dài trang đi theo nội dung). Phần dưới giữ lại cho v1.
> Xem [CLAUDE.md §5](../../CLAUDE.md).

**Input:** 20 dòng nén từ `SlideRepr[]` + `section_native` (nếu có) + `time_budget_min`
**Output:** `DeckStructure`
**Model:** 1× LLM, gọi toàn cục

---

## 1. Ranh giới phải nhớ

> **S2 KHÔNG viết một câu tiếng Việt nào.**
> S2 sinh ra **cấu trúc** — dữ liệu khô, dạng quan hệ và nhóm.
> **S4 mới viết câu** — và nó viết được câu chuyển hay là nhờ đọc cấu trúc của S2.

Tách bạch chỗ này quan trọng: để S2 viết văn thì nó vừa phải suy luận cấu trúc vừa phải
hành văn, **cả hai đều tệ đi**.

---

## 2. S2 tồn tại để làm gì

Lý do gốc, một câu:

> S1 nhìn **từng trang một cách cô lập**. Không có stage nào nhìn thấy cả deck.
> **S2 là stage duy nhất nhìn toàn cục.**

Hệ quả nếu bỏ S2:

| Thiếu                                               | Hậu quả cụ thể                                                                              |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Không biết trang nào thuộc phần nào            | Khán giả nói "quay lại phần đánh giá" → không hiểu "phần" là gì                   |
| Không biết khái niệm nào định nghĩa ở đâu | S4 giải thích lại "embedding" ở trang 5, 8, 12 — lặp, thừa, khán giả chán             |
| Không biết trang 12 phụ thuộc trang 5            | Khán giả hỏi ở trang 12 về "vector", robot trả lời mà không biết đã nói ở trang 5 |
| Không biết mạch trình bày                       | Câu chuyển trang thành "Tiếp theo chúng ta sang trang 8" — vô hồn                       |
| Không có phân bổ thời lượng                   | S4 viết 42 phút cho budget 30 phút, không biết cắt chỗ nào                              |

Nói ngắn: **S1 cho bạn 20 mảnh rời. S2 dán chúng thành một bài.**

---

## 3. Input — phải nén, không đưa toàn bộ SlideRepr

Đây là chỗ hay làm sai. Đưa cả 20 `SlideRepr` đầy đủ → ~25k token, LLM ngợp, kết quả tệ hơn.

Nén mỗi trang thành **1 dòng**:

```
[1]  title: "Giới thiệu RAG"   | type: title   | msg: (trang bìa)
[5]  title: "Embedding là gì"  | type: concept | msg: Embedding biến văn bản thành vector, khoảng cách vector phản ánh tương đồng ngữ nghĩa | ent: [embedding, vector, cosine]
[7]  title: "Kiến trúc RAG"    | type: diagram | msg: RAG tách truy xuất khỏi sinh, cập nhật tri thức không cần train lại | ent: [retriever, reranker, generator]
```

Mỗi dòng ~60–90 token → 20 dòng ≈ **1.5k token**. Cộng prompt hướng dẫn ≈ **2.5k**. Gọn.

**Trường cần:** `slide_id`, `title`, `slide_type`, `message`, `entities`.
**Bỏ hết:** `description`, `relations`, `visual_elements`, `chart_data`.
S2 không quan tâm trang đó **vẽ gì**, chỉ quan tâm trang đó **nói gì** và **nói về khái niệm nào**.

**Hai input ngoài:**

- `section_native` từ pptx (nếu tác giả đã chia section sẵn)
- `time_budget_min` — người nhập, ví dụ 30

---

## 4. Deck ví dụ để bám theo

Bài *"Giới thiệu RAG cho hệ thống hỏi đáp nội bộ"*, 20 trang:

```
 1 Trang bìa              11 Chunking strategies
 2 Bối cảnh doanh nghiệp  12 Hybrid search BM25 + dense
 3 Vấn đề: LLM bịa        13 Reranking nâng cao
 4 Fine-tuning là gì      14 Các metric đánh giá
 5 Embedding là gì        15 Kết quả thực nghiệm
 6 Hạn chế fine-tuning    16 So sánh RAG vs fine-tuning
 7 Kiến trúc RAG          17 Case study nội bộ
 8 Retriever              18 Hạn chế của RAG
 9 Reranker               19 Hướng phát triển
10 Generator              20 Kết luận
```

Điểm mấu chốt: **S1 chỉ biết trang 12 nói về hybrid search. Nó không đời nào biết được
trang 12 đang dựa vào khái niệm của trang 5.** Đó là thứ S2 sinh ra.

---

## 5. Output — 5 thành phần

### 5.1 `sections` — chia phần

```json
"sections": [
  {"id": "sec1", "title": "Mở đầu", "slides": [1, 2, 3],
   "summary": "Bối cảnh doanh nghiệp và vấn đề LLM trả lời bịa",
   "role": "problem"},
  {"id": "sec3", "title": "Kiến trúc", "slides": [7, 8, 9, 10],
   "summary": "Ba khối retriever, reranker, generator và cách chúng phối hợp",
   "role": "solution"}
]
```

**Dùng làm gì:** `summary` được S6a embed thành **`v_section`**.
Khán giả nói *"quay lại chỗ nói về ba khối"* → truy xuất khớp `v_section` của `sec3`
→ nhảy trang 7. Đây là đường điều hướng **mức section** của
R2, tách riêng khỏi đường mức trang.

Vì bị embed nên `summary` phải **tự đứng được** — xem §5.2.

### 5.2 `concept_map` — khái niệm được giới thiệu ở đâu ★

Đây là output giá trị nhất và hầu như không ai làm.

```json
"concept_map": {
  "embedding": {
    "introduced_at": 5, "used_at": [8, 11, 12], "depth": "defined",
    "gloss": "Embedding là cách biến văn bản thành vector số, sao cho khoảng cách giữa hai vector phản ánh mức tương đồng ngữ nghĩa của hai đoạn văn bản."
  },
  "fine-tuning": {
    "introduced_at": 4, "used_at": [6, 16, 18], "depth": "defined",
    "gloss": "Fine-tuning là việc huấn luyện tiếp một model đã có trên dữ liệu riêng để nó nắm được tri thức của miền đó."
  }
}
```

**`gloss` là trường mới, và nó tồn tại vì một lý do runtime cụ thể:** S6a
**embed nó thành `v_concept`**, thay cho việc dump cả bảng `concept_map` vào prompt runtime.

```
CŨ:  nhồi cả concept_map (~600 token) vào mọi prompt
MỚI: index từng khái niệm -> R4 truy xuất đúng 1–3 khái niệm liên quan tới câu hỏi
```

Nên `gloss` phải **tự đứng được**, đúng luật của
`message` ở S1 §3.1: gọi tên khái niệm ra, không đại từ,
không "như đã nói ở trên". Một câu, 20–40 từ.

`sections[].summary` cũng bị embed thành `v_section` → **cùng luật**.

Ba công dụng, đều rất cụ thể:

1. **S4 không giải thích lại.** Viết kịch bản trang 12, thấy `embedding.introduced_at = 5`
   → chỉ cần *"như đã nói ở phần nền tảng, embedding…"* thay vì định nghĩa lại từ đầu.
   Đây là khác biệt giữa kịch bản nghe như người thật và kịch bản nghe như robot đọc wiki.
2. **Runtime kéo ngữ cảnh.** Khán giả ở trang 12 hỏi *"embedding là gì?"* → hệ thống biết
   đáp án nằm ở trang 5, trả lời xong gợi ý luôn: *"khái niệm này mình đã trình bày ở
   trang 5, quay lại nhanh nhé?"*
3. **Bắt lỗi chính bộ slide.** Nếu `used_at` có trang nhỏ hơn `introduced_at` → tác giả
   dùng khái niệm trước khi định nghĩa. Đây là **lỗi thiết kế slide thật**, đáng báo cho
   người duyệt.

`depth` ∈ `defined` (trang đó định nghĩa hẳn) | `mentioned` (chỉ nhắc qua).

### 5.3 `dependencies` — phụ thuộc giữa trang

```json
"dependencies": [
  {"slide": 7,  "requires": [6],    "via": "hạn chế fine-tuning là động lực của RAG"},
  {"slide": 8,  "requires": [5],    "via": "embedding"},
  {"slide": 12, "requires": [5, 8], "via": "vector search"},
  {"slide": 16, "requires": [4, 7], "via": "so sánh hai phương pháp"}
]
```

Khác `concept_map` ở chỗ: `concept_map` theo **khái niệm**, `dependencies` theo **trang**.
Cái sau dùng để runtime tự động nạp thêm `SlideRepr` của trang phụ thuộc vào ngữ cảnh
trả lời.

### 5.4 `arc` — vai trò tường thuật

```json
"arc": {
  "hook": [2], "problem": [3, 6], "solution": [7, 10, 11, 13],
  "evidence": [14, 17], "closing": [18, 20]
}
```

**Dùng làm gì:** S4 đổi giọng theo vai trò. Trang `problem` nói giọng nhấn mạnh hậu quả,
trang `evidence` nói giọng dẫn số liệu, trang `closing` nói giọng tổng kết. Không có `arc`
thì cả 20 trang cùng một giọng đều đều.

### 5.5 `time_budget` — phân bổ thời lượng

```json
"time_budget": {
  "total_min": 30,
  "allocation": {"sec1": 3, "sec2": 4, "sec3": 9, "sec4": 5, "sec5": 6, "sec6": 3},
  "rationale": "sec3 là phần cốt lõi nên được nhiều nhất; sec1 chỉ dẫn nhập"
}
```

**Dùng làm gì:** input bắt buộc cho pass 2 của S4. Không có nó thì S4 viết tự do rồi tổng
ra 42 phút, và không biết cắt ở đâu cho hợp lý.

---

## 6. Năm tình huống runtime — chỗ để nó "click"

| Tình huống                              | Không có S2                                                   | Có S2                                                     |
| ----------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------- |
| "Quay lại phần đánh giá"             | Tìm ngữ nghĩa trên 20 trang, có thể rơi vào 15 hoặc 16 | `sec5.slides[0]` → nhảy đúng trang 14                |
| Ở trang 12, hỏi "dense vector là gì?" | Trả lời từ KB, hết                                          | `concept_map` → "đã nói ở trang 5, quay lại nhé?" |
| Trả lời câu hỏi ở trang 16           | Chỉ có ngữ cảnh trang 16                                    | `dependencies` → nạp thêm trang 4 và 7 làm nền     |
| Robot báo tiến độ                     | Không biết gì                                                | "Ta đang ở phần 3/6, còn khoảng 18 phút"             |
| Sau khi lạc đề, quay về               | "Về trang 7"                                                   | "Quay lại phần Kiến trúc mình đang dở, trang 7"     |

Dòng 2 và 3 là **tra cứu một bảng đã dựng sẵn**, không phải retrieval → tốn **0ms**.

---

## 7. Vì sao đúng 1 lần gọi LLM

N = 20, tất cả tóm tắt gói trong 1.5k token. Nên gọi một lần, cho nó nhìn cả deck.

**Đừng làm tuần tự từng cặp** (so trang 1 với 2, 2 với 3…). Cách đó cho ranh giới cục bộ
nhưng **không bao giờ ra được `arc` và `concept_map`**, vì hai cái đó đòi nhìn cả deck một
lúc. Muốn biết "embedding được giới thiệu lần đầu ở đâu" thì phải thấy hết 20 trang cùng lúc.

Chỉ chuyển sang phân cấp khi deck **> 150 trang**: pass 1 chia chương, pass 2 chạy S2
trong từng chương.

---

## 8. Validate bằng code — 5 luật cứng

LLM sẽ vi phạm mấy cái này. Check bằng code, **không tin nó**.

1. `sections` phủ kín và không chồng: hợp = `{1..20}`, giao = `∅`
2. `sections` liên tục: `[4,5,7]` là sai, section không được nhảy cóc
3. `dependencies` phải **lùi**: `requires < slide`
4. `dependencies` không có chu trình (DAG)
5. `concept_map`: `introduced_at ≤ min(used_at)`
6. **`gloss` và `summary` qua check tự đứng được** — danh sách từ cấm, và phải gọi tên
   chính khái niệm / chủ đề đó ra (cùng bộ kiểm với S1 §3.1)

**Vi phạm luật 3 hoặc 5 thường không phải lỗi của LLM mà là lỗi thật của bộ slide** —
tác giả xếp nhầm thứ tự. **Đừng tự sửa**, báo lên S7 cho người quyết.

Vi phạm luật 1, 2, 4, 6 thì là lỗi LLM → retry một lần với thông báo lỗi cụ thể trong
prompt, retry vẫn hỏng thì fail stage (luật 6: flag `gloss_not_standalone`).

---

## 9. Khi nào S2 gần như miễn phí

pptx có section gốc. Nếu tác giả đã chia rồi:

```
section_native có -> dùng làm prior
                  -> LLM chỉ verify + sinh summary/role
                  -> vẫn phải tự sinh concept_map, dependencies, arc
                     (pptx không bao giờ có mấy cái này)
```

Prompt lúc đó đổi từ *"hãy chia deck thành các phần"* sang *"tác giả đã chia thế này,
kiểm tra xem có hợp lý không"*. Chính xác hơn hẳn, và rẻ hơn.

---

## 10. Failure mode

| Tình huống                           | Dấu hiệu                                                  | Xử lý                                                                                          |
| -------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Deck rời rạc, không có mạch       | LLM trả về 1 section chứa cả 20 trang, hoặc 20 section | Flag`no_structure`: `arc` sẽ vô dụng, S4 phải viết phẳng                               |
| `concept_map` rỗng                  | `entities` từ S1 quá nghèo                             | Quay lại xem S1 có chạy đúng không, đừng vá ở S2                                       |
| Dependency dày đặc (>2 cạnh/trang) | LLM nối bừa vì "cùng chủ đề"                         | Siết prompt: chỉ nối khi**không hiểu được trang sau nếu chưa xem trang trước** |
| Section name chung chung ("Phần 2")   | Deck thiếu tiêu đề rõ                                  | Chấp nhận, dùng`summary` thay tên khi điều hướng                                       |

---

## 11. Quality gate ra khỏi S2

| Điều kiện                                            | Hành động                                                             |
| ------------------------------------------------------- | ------------------------------------------------------------------------ |
| 6 luật validate                                        | Luật 1/2/4/6 fail → retry rồi fail. Luật 3/5 fail → flag, đi tiếp |
| `len(sections)` == 1 hoặc == `n_slides`            | Flag`no_structure`                                                     |
| `concept_map` rỗng                                   | Fail — S4 mất khả năng tránh lặp                                   |
| **`gloss` thiếu ở bất kỳ khái niệm nào** | **Fail** — S6a không embed được, R4 mất đường QA_DECK     |
| `sum(time_budget.allocation) != total_min`            | Tự chuẩn hoá lại theo tỉ lệ, log warning                           |

---

## 12. Tóm một câu

> **S2 = bảng tra cứu toàn cục, dựng một lần lúc offline, để cả S4 lẫn runtime không bao
> giờ phải suy luận lại "trang này nằm ở đâu trong mạch bài và dựa trên cái gì".**

Rẻ (1 lần gọi LLM, 15 giây), nhưng thiếu thì kịch bản lặp lê thê và robot mất hẳn khả năng
nói *"như đã trình bày ở trang 5"*.
