# S5 — KB Construction

**Input:** `source/*.pdf` (paper, giáo trình, báo cáo nội bộ)
**Output:** `KBChunk[]` + `KBIndex`
**Model:** LLM (enrich + classify) + VLM (caption hình) + embedding

> **v0:** chưa có tài liệu nguồn riêng — **chính file deck PDF đang làm luôn KB**.
> Nghĩa là ca mô tả ở [§8](#8-nếu-không-có-tài-liệu-nguồn) dưới đây **đang là hiện
> thực**, không phải tình huống giả định: hệ thống co lại thành "robot mô tả slide".
> Input của S5 ở v0 là `ParsedDocument` ([spec](../spec/parsed-document.md)), không
> phải `source/*.pdf` riêng.
>
> Cái đang chạy (`src/kb/`): chunk theo TRANG ([kb-chunk.md](../spec/kb-chunk.md)) · nhúng
> qua API ([embedding.md](../spec/embedding.md)) · hybrid dense + BM25 gộp RRF
> ([search.md](../spec/search.md)). **Không có** bước enrich bằng LLM (§4) và phân loại
> `content_type` theo ý (§5): tiền tố `[chương · tiêu đề · trang N/M]` dựng bằng luật,
> `content_type` chỉ phân `content` / `section_divider`. Tóm tắt ở
> [00-overview §3.3](./00-overview.md).

---

## 1. Vị trí trong pipeline

Chỗ hay nhầm nhất:

```
NHÁNH DECK          NHÁNH NGUỒN
deck.pptx           source/*.pdf
   |                    |
  S0                   S5   <- độc lập hoàn toàn, không biết gì về slide
   |                    |
  S1                  KBIndex
   |                    |
   +--------+-----------+
            |
           S3   <- chỗ hai nhánh gặp nhau
```

S5 chạy **trước** S3, và S5 **không đụng tới slide**. Nó chỉ xử lý tài liệu nguồn.

Hệ quả thực tế: có 5 deck cùng dùng chung một bộ paper thì **S5 chạy đúng 1 lần**, còn
**S3 chạy 5 lần**. Đây là lý do KB nằm ở shared layer, còn alignment nằm ở per-deck layer.

S5 = chunking + indexing. Nhưng có vài thứ ở giữa quan trọng hơn cả hai cái đó.

---

## 2. S5.1 — Parse & phát hiện cấu trúc

Khác hẳn S0: **S0 xử lý slide (đơn vị là trang), S5 xử lý văn bản dài (đơn vị là mục)**.

Cần lôi ra:

- **Cây heading** (`1. Introduction` → `3.1 Overview` → …)
- Đoạn văn kèm **số trang**
- **Bảng** (giữ nguyên cấu trúc ô)
- **Hình + caption**
- **Công thức**
- Tài liệu tham khảo — để **bỏ**, đừng index

**Công cụ:** `PyMuPDF` cho text + toạ độ; `unstructured` hoặc `docling` cho cây heading.
PDF scan thì phải OCR trước.

**Lọc rác ngay từ đây:** header/footer lặp, số trang, mục lục, phần references.
Không lọc thì retrieval trả về toàn *"Trang 14 | Hội thảo XYZ 2024"*.

Cách phát hiện header/footer rẻ tiền: chuỗi text xuất hiện ở cùng vùng bbox trên **≥ 60%
số trang** → là header/footer, bỏ.

---

## 3. S5.2 — Chunking

**Quyết định: structure-aware, KHÔNG fixed-size.**

```
đi theo cây heading
  -> mục nào <= 500 token: giữ nguyên làm 1 chunk
  -> mục nào > 500 token: cắt theo ranh giới đoạn văn, overlap 50 token
  -> không bao giờ cắt giữa câu, giữa bảng, giữa công thức
```

Kích thước mục tiêu: **300–500 token**, overlap **50**.

**Lý do bỏ fixed-size:** cắt 512 token cứng sẽ chặt đôi một định nghĩa, nửa đầu vào chunk A
nửa sau vào chunk B, và **cả hai đều vô dụng**.

### Bảng xử lý riêng

| Loại | Cách làm |
|---|---|
| Bảng nhỏ (< 15 hàng) | Giữ nguyên cả bảng làm 1 chunk, serialize dạng markdown |
| Bảng lớn | **Mỗi hàng 1 chunk, lặp lại header ở mỗi hàng** |

Không lặp header thì hàng đứng một mình không hiểu nổi cột nào là cột nào.

### Hình trong tài liệu nguồn

VLM sinh caption → index caption như một chunk, `content_type: "figure"`, giữ đường dẫn
ảnh để runtime hiển thị được nếu cần.

---

## 4. S5.3 — Contextual enrichment ★

**Đây là bước quan trọng nhất của S5 và hay bị bỏ qua.**

**Vấn đề:** một chunk tách khỏi ngữ cảnh thì mơ hồ.

```
Chunk thô:
"Cách tiếp cận này giảm chi phí khoảng 60% so với phương án trước,
 nhưng đánh đổi bằng độ trễ tăng thêm 80ms."
```

"Cách tiếp cận này" là cách nào? Embed câu này xong thì nó gần như không match được gì
có ý nghĩa.

**Cách sửa:** prepend một câu ngữ cảnh do LLM sinh, **trước khi embed**.

```
Chunk đã enrich:
"[Bối cảnh: mục 4.2 của paper Lewis 2020, đánh giá chi phí của RAG
  so với fine-tuning toàn phần]
 Cách tiếp cận này giảm chi phí khoảng 60% so với phương án trước,
 nhưng đánh đổi bằng độ trễ tăng thêm 80ms."
```

Bây giờ chunk **tự đứng được**. Và điều này ảnh hưởng trực tiếp tới S3: alignment chỉ tốt
khi chunk tự mang đủ ngữ cảnh.

**Chi phí:** 1 lần gọi LLM rẻ cho mỗi chunk, hoặc gộp **batch 10 chunk** một lần. Offline
nên không tiếc. Đây là thứ **mua được nhiều chất lượng nhất trên mỗi đồng bỏ ra** trong
cả pipeline.

**Luật cứng: embed `text_enriched`, KHÔNG phải `text_raw`.** Giữ cả hai trong chunk —
`text_raw` để hiển thị và trích dẫn cho người đọc, `text_enriched` để embed và để LLM verify ở S3.

Input cho lần gọi enrich: `doc_title` + `section_path` + 1 chunk trước + chunk hiện tại.
Không cần cả tài liệu.

---

## 5. S5.4 — Phân loại `content_type`

```
definition  - "X là ..."            -> ưu tiên khi hỏi "X là gì"
method      - quy trình, cách làm   -> ưu tiên khi hỏi "làm thế nào"
evidence    - số liệu, kết quả      -> ưu tiên khi hỏi "bao nhiêu", "kết quả ra sao"
comparison  - so sánh A vs B
example     - ví dụ minh hoạ
figure      - caption hình
limitation  - hạn chế, caveat
```

**Dùng làm gì:** runtime lọc theo intent. Khán giả hỏi *"embedding là gì"* →
filter `content_type: definition` trước khi rank. Tăng precision rõ rệt, gần như miễn phí.

Phân loại bằng LLM **cùng lượt với bước enrich**, đừng gọi riêng.

---

## 6. S5.5 — Embed & index

| Hạng mục | Chọn | Lý do |
|---|---|---|
| Model | **v0: `text-embedding-3-small` qua API** | Không tốn đĩa, không phải nạp model. Đích vẫn là `bge-m3` (đa ngữ, dense + sparse 1 forward) |
| Dense | vector 1536 chiều | Ngữ nghĩa. `bge-m3` thì 1024 |
| Sparse | BM25 hoặc SPLADE | **Bắt buộc có.** Thuật ngữ kỹ thuật, tên riêng, viết tắt — dense hay trượt |
| Vector DB | Qdrant / Milvus | Cần hỗ trợ **metadata filter** tốt |
| Embed cái gì | **`text_enriched`** | Không phải `text_raw` |

**Hybrid là bắt buộc, không phải tuỳ chọn.** Tiếng Việt lẫn thuật ngữ tiếng Anh
(*retriever*, *embedding*) là ca điển hình. Khán giả hỏi "BM25" mà chỉ có dense search
thì rất dễ trượt.

Metadata phải index được để filter: `source_doc`, `content_type`, `deck_ids`, `align_role`.

---

## 7. KBChunk — schema đầy đủ

```json
{
  "chunk_id": "doc2#c118",
  "text_raw": "Việc tách retriever khỏi generator cho phép cập nhật kho tri thức độc lập với tham số mô hình...",
  "text_enriched": "[Bối cảnh: mục 3.1 paper Lewis 2020, mô tả kiến trúc tổng quan của RAG] Việc tách retriever...",

  "source_doc": "doc2",
  "doc_title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP",
  "section_path": "3. Architecture > 3.1 Overview",
  "page": 14,

  "content_type": "method",
  "token_count": 380,

  "dense_vec": [],
  "sparse_terms": {},

  "deck_ids": [],
  "related_slides": [],
  "align_role": null
}
```

**Ba trường cuối đang rỗng. Toàn bộ nhiệm vụ của S3 là đi điền chúng.**

---

## 8. Nếu không có tài liệu nguồn

Phải nói thẳng với người dùng: hệ thống **co lại thành "robot mô tả slide"**, không trả
lời sâu hơn được.

Lựa chọn duy nhất là dùng chính slide làm KB, nhưng khi đó:

- `depth` của mọi câu trả lời bị giới hạn ở mức **mô tả**
- Ngưỡng từ chối phải **nâng lên cao**
- **Tuyệt đối không để LLM tự phình kiến thức ra làm KB** — đó là nướng ảo giác vào hệ
  thống vĩnh viễn, ở dạng không ai kiểm được

---

## 9. Chi phí

| Bước | Số lần gọi | Thời gian |
|---|---|---|
| Enrich + classify (300 chunk, batch 10) | 30 LLM | ~45s |
| Caption hình (20 hình) | 20 VLM | ~40s |
| Embed | 1 batch | ~20s |

S5 chỉ chạy **một lần cho cả corpus**, và gần như không bao giờ chạy lại.

---

## 10. Failure mode

| Tình huống | Dấu hiệu | Xử lý |
|---|---|---|
| PDF scan không OCR | `text_extraction_rate` ~0 | OCR trước, hoặc loại tài liệu đó |
| Cây heading không dò được | Mọi chunk có `section_path` rỗng | Fallback: chunk theo đoạn + overlap, nhưng enrich phải bù bằng `doc_title` + số trang |
| Chunk quá dài sống sót | `token_count > 800` | Cắt lại; chunk dài làm S3 verify nhiễu (xem failure mode của S3) |
| Enrich sinh câu bịa | Câu bối cảnh nhắc nội dung không có trong tài liệu | Siết prompt: câu bối cảnh chỉ được dùng `doc_title`, `section_path`, và chunk liền trước |
| References lọt vào index | Retrieval trả về toàn danh mục tài liệu | Lọc theo heading tên `References` / `Tài liệu tham khảo` trở đi |

---

## 11. Quality gate ra khỏi S5

| Chỉ số | Ngưỡng |
|---|---|
| `text_extraction_rate` mỗi tài liệu | ≥ 90%, dưới thì cảnh báo |
| Tỉ lệ chunk có `section_path` | ≥ 80% |
| Phân bố `token_count` | p95 < 600, không có chunk > 800 |
| Tỉ lệ chunk có `text_enriched != text_raw` | = 100% |
| Tỉ lệ chunk `content_type == null` | < 5% |

---

## 12. Cấm

- **Fixed-size chunking**
- **Embed `text_raw` thay vì `text_enriched`**
- Bảng lớn thành một chunk khổng lồ, hoặc mỗi hàng một chunk **mà không lặp header**
- Chỉ dense, bỏ sparse
- Index cả phần references
- Dùng LLM tự phình kiến thức từ slide để làm KB khi thiếu tài liệu nguồn
