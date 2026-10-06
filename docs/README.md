# Tài liệu hệ thống

> **HIỆN TRẠNG v0 — đọc trước.** Bài toán: robot thuyết trình và trả lời câu hỏi từ
> **một file PDF duy nhất**, file đó vừa là deck vừa là KB. Lưu file JSON trên đĩa + kho
> vector Chroma (hoặc RAM), **không chạy model local nào** (VLM, LLM, embedding đều qua API).
>
> So với bản thiết kế cũ: **S1 đã bỏ** (gộp vào S0), **S3 đã bỏ** (align vào chính mình),
> **multi-field embed đã bỏ**. Lý do ở [CLAUDE.md §5](../CLAUDE.md).
> `runtime/` mới có bản chữ chạy thử (`src/runtime/`, `scripts/try_ask.py`) — chưa giọng
> nói, chưa ngắt lời.

| Nhóm | Nội dung |
|---|---|
| [`spec/`](./spec/) | **Cái đã code thật.** Schema dữ liệu đang chạy |
| [`offline/`](./offline/) | *(thiết kế đích)* Đặc tả nhánh offline S0–S7 |
| [`runtime/`](./runtime/) | *(thiết kế đích)* Đặc tả nhánh runtime R1–R7 |
| `eval/` | *(chưa viết)* bộ test có nhãn, ablation, báo cáo |

## Đã code đến đâu

```
✅ S0  file raw → ParsedDocument   src/parsing/          docling + VLM bố cục trang + chép bảng
✅ S5  chunk + vector + kho        src/kb/               1536 chiều · cache · chroma | inmem
✅     tìm hybrid + đo             src/kb/{search,eval,tune,audit}.py
✅ S6a deck_map                    src/kb/deck_map.py
   ─── bốn dòng trên + S4: MỘT lệnh  bash scripts/run_deck.sh "<file>"
🟡 S4  kịch bản                    src/scenario/         bước 4 của run_deck.sh, nhịp chưa đạt gate
🟡     runtime bản chữ             src/runtime/          hỏi đáp + thuyết trình thử trong terminal
🚫 S1, S3                          BỎ — xem bảng dưới
⬜ S2 time_budget · S6b TTS · S7 duyệt · R5 giọng nói · R7 ngắt lời
```

| Spec | Nội dung |
|---|---|
| [parsed-document.md](./spec/parsed-document.md) | Cấu trúc sau khi parse PDF — trang, mẩu, chương, cờ |
| [kb-chunk.md](./spec/kb-chunk.md) | Cắt tài liệu thành mẩu để tìm |
| [embedding.md](./spec/embedding.md) | Biến chunk thành vector — model, cách gộp, cache |
| [search.md](./spec/search.md) | Từ câu hỏi ra số trang — hybrid dense + BM25, gộp bằng RRF |
| [pronunciation.md](./spec/pronunciation.md) | Robot đọc thuật ngữ thế nào — và S4 đếm âm tiết theo đó |
| [scenario.md](./spec/scenario.md) | S4 — kịch bản mỗi trang: input, hai loại câu, chống bịa, validate |

## Nhánh offline

**Muốn biết cái đang chạy thật:** đọc [`offline/01-hien-trang.md`](./offline/01-hien-trang.md) —
luồng, lệnh, cấu trúc dữ liệu từng tầng, gọn trong một trang.
**Cần lệnh để chạy / đọc kết quả:** [`offline/02-lenh.md`](./offline/02-lenh.md).

Muốn biết thiết kế đích: đọc [`offline/00-overview.md`](./offline/00-overview.md), rồi theo **thứ tự chạy thật**
(số stage là lớp khái niệm, không phải thứ tự thực thi):

| Trạng thái | File | Một câu |
|---|---|---|
| — | [00-overview](./offline/00-overview.md) | Bốn nguyên tắc · kiến trúc v0 **một file một index** · sơ đồ từng stage · incremental |
| ✅ | [S0 Ingest](./offline/s0-ingest.md) | PDF qua docling + VLM nhìn cả trang sắp bố cục, chép bảng · `sections` và `slide_type` bằng **luật** |
| ✅ | [S5 KB Construction](./offline/s5-kb-construction.md) | Chunk theo trang + embed qua API + hybrid dense/BM25 gộp RRF |
| ✅ | S6a deck_map | Bản đồ chương ~180 token cho prompt runtime — luật, không gọi model |
| ⬜ | [S2 Deck Structure](./offline/s2-deck-structure.md) | Chỉ còn `time_budget` — `sections` đã có từ S0 |
| 🟡 | [S4 Scenario](./offline/s4-scenario.md) | Kịch bản nói · `content` vs `delivery` · 7 đòn bẩy tự nhiên — đã code, bước 4 của `run_deck.sh` |
| ⬜ | S6b Precompute | TTS theo câu, một giọng duy nhất, qa_cache, bản nghe thử |
| ⬜ | [S7 HITL Review](./offline/s7-hitl-review.md) | Duyệt **chỉ phần bị flag** · vòng ĐỌC + vòng **NGHE** (MOS) |
| 🚫 | ~~S1 Slide Understanding~~ | **BỎ** — gộp vào S0. `message`/`relations` sinh ra để nhồi prompt, không dùng |
| 🚫 | ~~S3 Alignment~~ | **BỎ ở v0** — KB đến từ chính file slide, align vào chính mình thì vô nghĩa |

Ba thay đổi lớn đã áp vào toàn bộ bộ docs:

- **NT4 — tự nhiên là yêu cầu.** Gỡ va chạm với NT3 bằng cách tách câu `content` / `delivery`
- **Bỏ nhồi context, làm RAG truy xuất chuẩn.** Prompt runtime ~1.3k token, không phải 3.6k.
  S4 viết kịch bản trang nào thì đọc trang đó, không nhồi cả deck.
- **Bỏ tầng "hiểu slide" riêng (S1).** Mô tả ảnh do VLM sinh ngay ở S0; `slide_type` và
  `sections` làm bằng **luật**, đo được 40/40 đúng, không tốn một lần gọi model nào.

## Nhánh runtime

Đọc [`runtime/00-overview.md`](./runtime/00-overview.md) trước — nó giải thích **vì sao
R2/R3/R4 không phải ba lần gọi** mà là ba mặt của một lần gọi. Không nắm chỗ đó thì đọc
từng file riêng sẽ hiểu sai thành pipeline tuần tự.

| | File | Một câu |
|---|---|---|
| — | [00-overview](./runtime/00-overview.md) | Hai vòng lặp, một lần gọi LLM, state, ngân sách, 3 quyết định đã chốt |
| **R1** | [Intake & fast-path](./runtime/r1-intake-fastpath.md) | Hàng đợi + khử trùng · regex điều hướng ~5ms |
| **R2** | Slide navigation | LLM chọn trang toàn cục + **confidence gate** |
| **R3** | [Context-aware rewriting](./runtime/r3-context-rewriting.md) | Giải tiền ngữ bám **trạng thái phi ngôn ngữ** |
| **R4** | [Grounded answering](./runtime/r4-grounded-answering.md) | 3 phạm vi · trích dẫn trang · biết nói không biết |
| **R5** | [Streaming speech](./runtime/r5-streaming-speech.md) | Cắt câu từ token stream → TTS từng câu |
| **R6** | [State machine & sync](./runtime/r6-state-sync.md) | Nguồn chân lý duy nhất · hợp đồng ack · resume |
| **R7** | [Interrupt & turn-taking](./runtime/r7-interrupt-turntaking.md) | Khi nào được ngắt · hàng đợi · barge-in |

Escalation **không** là lớp riêng — nó là nhánh kết thúc của R4.

Context ngắn gọn cho AI coding agent nằm ở [`../CLAUDE.md`](../CLAUDE.md) — đó là bản rút gọn
để nhét vào context window. Bộ docs này là bản đầy đủ có lý do đằng sau mỗi luật.
Khi hai bên mâu thuẫn, `CLAUDE.md` là bản chuẩn về *luật*, docs là bản chuẩn về *lý do*.
