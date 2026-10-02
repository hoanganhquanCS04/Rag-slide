# S7 — HITL Review

**Input:** mọi artifact + `flags.json` + **bản nghe thử** (`precomputed/listen_sample/`)
**Output:** `DeckBundle` verified + `review.json`
**Model:** không có. Người — **đọc và NGHE**.

> **⬜ Chưa có code.** Cờ v0 đang sinh ra để chờ S7: cờ S0 trong `document.json`
> (`flags[]`), cờ S4 trong từng trang của `scenario.json`. `self_retrieval_fail` chưa bao
> giờ bắn — self-retrieval gần như luôn 100% (xem [00-overview §8](./00-overview.md)).
>
> Vòng NGHE (§5b) và xử lý `self_retrieval_fail` (§4b) đã qua vòng chốt. Phần còn lại
> (UI, thang hành động) vẫn là suy ra từ luật + tập flag — sửa thoải mái khi code thật.

---

## 1. Nguyên tắc trung tâm

> **Chỉ duyệt phần bị flag. KHÔNG bắt người duyệt cả 20 trang.**

Bắt duyệt hết thì hai chuyện xảy ra, cả hai đều tệ: hoặc người bỏ cuộc, hoặc người bấm
"đồng ý" hàng loạt mà không đọc. Lúc đó HITL thành nghi lễ, không phải cơ chế an toàn.

Mục tiêu: **10–20 phút cho một deck 20 trang.**

Điều này chỉ khả thi nếu flag **chính xác**. Nên có một chỉ số riêng cho chính S7:

> **Flag precision ≥ 60%** — trong số chỗ bị flag, ít nhất 60% thực sự cần sửa.

Dưới ngưỡng đó nghĩa là hệ thống đang kêu oan quá nhiều, và người duyệt sẽ mất tin vào flag.

---

## 2. Tập flag — gộp từ mọi stage

| Stage | Flag | Mức | Người duyệt phải quyết |
|---|---|---|---|
| S0 | `chart_not_from_xml` | ⚠ | Số trong chart do VLM đoán — đúng không? |
| S0 | `parse_quality != full` | ⚠ | Chấp nhận chạy tiếp hay đổi file nguồn? |
| S0 | `no_speaker_notes` | i | Chỉ để biết, không cần quyết |
| S1 | `low_confidence_message` | ⚠ | `message` này có đúng ý trang không? |
| S1 | `two_pass_disagree` | ⚠ | Chọn bản nào trong hai bản |
| S1 | `has_image_text` | ⚠ | VLM OCR — đọc đúng chữ trong ảnh không? |
| S1 | `empty_relations_on_diagram` | ⚠ | Sơ đồ có quan hệ mà VLM không đọc ra? |
| S1 | `message_not_standalone` | ⚠ | `message` dính đại từ / từ chỉ vị trí — sửa để embed được |
| S1 | `unknown_pronunciation` | ⚠ | **"BM25" đọc thế nào?** Ảnh hưởng cả giọng lẫn timing |
| S2 | `dependency_forward` | ⚠ | **Lỗi của bộ slide, không phải của hệ thống** |
| S2 | `concept_used_before_introduced` | ⚠ | Như trên |
| S2 | `no_structure` | ⚠ | Deck thật sự không có mạch, hay S2 chạy hỏng? |
| S2 | `gloss_not_standalone` | ⚠ | `gloss` không embed được |
| S3 | `zero_coverage` | ⚠ | Ba khả năng ở mục 4 — chọn một |
| S3 | `weak_coverage` | ⚠ | Link yếu này có dùng được không? |
| S3 | `over_linking` | ⚠ | Corpus quá hẹp? |
| S4 | **`ungrounded_content_sentence`** | **🔴** | **Câu này lấy từ đâu ra? Giữ hay bỏ?** |
| S4 | `delivery_below_floor` | ⚠ | Pass 2 đã ăn vào sàn 10% → kịch bản đang khô lại |
| S4 | `written_register_hit` | ⚠ | Dính danh sách văn viết bị cấm |
| S4 | `monotone_rhythm` | ⚠ | `syllable_std < 6` — nhịp đều như máy |
| S4 | `timing_overflow` | ⚠ | Cắt phần nào? |
| S4 | `spoiler_before_build_step` | ⚠ | Câu nói trước nội dung chưa hiện |
| **S6a** | **`self_retrieval_fail`** | ⚠ | **Hai khả năng ở mục 4b — chọn một** |
| S6b | `duration_mismatch` | ⚠ | Thời lượng thật lệch budget |
| S6b | `pronunciation_hash_mismatch` | 🔴 | Chặn cứng, không phải việc của người duyệt — build sai |

**🔴 = cờ đỏ, không được bỏ qua.** Các mức khác cho phép "chấp nhận có điều kiện".

Bốn flag S4 mới (`delivery_below_floor`, `written_register_hit`, `monotone_rhythm`, và
`unknown_pronunciation` của S1) đều phục vụ **NT4**. Chúng không nói câu nào SAI — chúng
nói kịch bản đang **nghe như máy**, và đó là lý do phải có thêm vòng nghe ở mục 5b.

---

## 3. Thứ tự duyệt

Sắp theo **mức độ lan toả của sai sót**, không theo số trang:

```
VÒNG ĐỌC
1. Cờ đỏ S4 (ungrounded content)  -> robot sẽ nói ra miệng, sai trước khán giả
2. Flag S1 relations / image_text -> sai ở đây lan xuống S3 và S4
3. Flag S6a self_retrieval_fail   -> R2 sẽ trượt ở runtime
4. Flag S3 zero_coverage          -> quyết định trang nào được trả lời sâu
5. Flag S2 dependency             -> lỗi bộ slide, thường là "ghi nhận" chứ không sửa
6. Flag S0 / S6b                  -> kỹ thuật, ít khi cần người

VÒNG NGHE                          <- MỚI, xem mục 5b
7. Flag NT4 (delivery/register/rhythm/pronunciation) + chấm MOS
```

Vì sao `self_retrieval_fail` xếp trên `zero_coverage`: trang không tìm được ở runtime thì
**mọi thứ khác về trang đó thành vô nghĩa**, kể cả kịch bản hay và nguồn đầy đủ.

Sửa ở tầng trên thì phải chạy lại tầng dưới. UI phải nói rõ cái giá: *"Sửa `message` của
trang 7 → chạy lại S6a, S3, S4, S6b cho trang 7 và 8 (~50 giây)"*.

---

## 4. Ba quyết định cho `zero_coverage`

Slide không có nguồn nào. Người phải chọn một trong ba, và lựa chọn này **ghi vào bundle**
vì runtime cần biết:

| Quyết định | Ghi vào | Hệ quả runtime |
|---|---|---|
| **Ý riêng của tác giả**, không có trong tài liệu | `answer_depth: "describe_only"` | Trang này chỉ mô tả được, hỏi sâu thì escalate người thật |
| **Corpus thiếu tài liệu** | — | Bổ sung nguồn → chạy lại S5 + S3 |
| **S3 chạy sai** | — | Sửa query/ngưỡng → chạy lại S3 |

Đây là lý do S3 không tự quyết được và bắt buộc phải đẩy lên đây.

---

## 4b. Hai quyết định cho `self_retrieval_fail`

Slide không tự truy xuất được chính nó — `message[i]` không ra top-1 là slide `i`.

| Quyết định | Khi nào | Làm gì |
|---|---|---|
| **`message` viết tệ** | Hai trang thật ra khác hẳn nhau | Sửa `message` → chạy lại S6a |
| **Trùng chủ đề THẬT** | Trang 8 "Retriever" và 11 "Chunking" đều xoay quanh truy xuất | Đánh dấu **cặp đã biết** vào `review.json` → lần sau không flag nữa |

```json
// review.json
{"known_confusable_pairs": [[8, 11]]}
```

**Lối thứ hai bắt buộc phải có.** Không có nó thì check kêu oan mãi ở cùng một cặp, người
duyệt học cách bỏ qua flag, và **cả cơ chế flag mất giá trị** — đúng thứ mà chỉ số
`flag precision` ở mục 1 tồn tại để chống.

Khi chấp nhận một cặp, runtime vẫn có lối thoát: confidence gate của
R2 sẽ thấy margin nhỏ ở đúng cặp đó và **hỏi lại kèm
thumbnail** thay vì nhảy bừa. Chấp nhận cặp không có nghĩa là chấp nhận nhảy sai.

---

## 5. Hành động người duyệt có thể làm

| Hành động | Ghi vào `review.json` | Chạy lại |
|---|---|---|
| **Accept** | `{"flag_id": ..., "decision": "accept"}` | không |
| **Accept + whitelist** (câu xã giao `grounding: null`) | `"decision": "whitelist"` | không, và lần build sau không flag nữa |
| **Edit** (sửa trực tiếp câu / `message` / link) | `"decision": "edit", "before": ..., "after": ...` | stage dưới của riêng slide đó |
| **Reject + rerun** | `"decision": "rerun", "hint": "..."` | chạy lại stage đó với hint bơm vào prompt |
| **Drop** (bỏ câu / bỏ link) | `"decision": "drop"` | stage dưới |

**`review.json` phải bền qua các lần build.** Whitelist và edit là công sức của người;
build lại mà mất hết thì lần thứ hai không ai duyệt nữa.

Khớp lại quyết định cũ theo `hash` của slide + `tts_hash` của câu. Hash đổi → quyết định
cũ hết hiệu lực, flag lại.

Hai loại quyết định **không gắn với hash** nên bền hơn: `known_confusable_pairs` (mục 4b)
và sửa `pronunciation.json` — chúng thuộc về deck/corpus, không thuộc về một câu cụ thể.

---

## 5b. Vòng NGHE — bắt buộc từ khi có NT4 ★

> **Naturalness không đọc ra được. Phải nghe.**

Kịch bản có thể đạt **cả 6 proxy tự động** của S4 mà vẫn nghe như máy: ngữ điệu phẳng,
ngắt sai chỗ, viết tắt đọc sai, nhấn nhầm từ. Proxy **tương quan** với MOS nhưng không
thay được nó.

### Nghe cái gì

S6b xuất sẵn `precomputed/listen_sample/`:

```
slide_03.mp3  slide_12.mp3  slide_18.mp3   <- 3 trang đại diện: đầu / giữa / cuối deck
flagged/                                     <- MỌI câu bị flag NT4, mỗi câu 1 file
```

Không nghe cả 20 trang — cùng nguyên tắc với vòng đọc.

### Chấm thế nào

**MOS 1–5, ba người.** Thang tối thiểu:

| Điểm | Nghĩa |
|---|---|
| 5 | Không phân biệt được với người thật |
| 4 | Nghe như người, có chỗ hơi máy |
| **3.8** | **Ngưỡng chấp nhận** |
| 3 | Rõ là máy nhưng nghe được hết |
| 2 | Khó chịu, không ngồi nghe 30 phút được |
| 1 | Máy đọc |

### Sửa được gì ở đây

| Nghe thấy | Sửa | Chạy lại |
|---|---|---|
| Đọc sai viết tắt | `pronunciation.json` | đếm lại âm tiết + synth lại câu chứa từ đó; lệch timing >15% → pass 2 của S4 |
| Nhấn sai từ | `prosody.emphasis` của câu đó | synth lại 1 câu |
| Ngắt quá gấp / quá lê thê | `prosody.pause_before_ms` | synth lại 1 câu |
| Cả trang nghe khô | Đánh `rerun` cho S4 trang đó, kèm hint | S4 + S6b trang đó |

Ba dòng đầu đều **rẻ** — sửa metadata rồi synth lại một câu, không gọi LLM.

### MOS KHÔNG phải gate CI

Nó cần ba người ngồi nghe. Nhét vào CI là tự lừa mình bằng một con số giả.

```
CI              -> proxy tự động của S4 (syllable_std, từ cấm, delivery_ratio)
RELEASE GATE    -> MOS >= 3.8, chạy trước buổi thuyết trình thật
```

---

## 6. UI — cái tối thiểu phải có

Một trang web local, `python -m src.review.app --deck-id ...`.

Mỗi flag là một thẻ, và thẻ phải có đủ để quyết **mà không cần mở file khác**:

```
[🔴 ungrounded] Trang 12, câu s12.2.3

  "Thực tế nhiều doanh nghiệp đã giảm được 40% chi phí vận hành nhờ cách này."

  Ảnh trang 12:  [thumbnail]
  message:       Hybrid search kết hợp BM25 và dense để bắt cả thuật ngữ lẫn ngữ nghĩa
  Nguồn đã link: doc1#c088 (elaborates, 0.72)
                 "...kết hợp hai tín hiệu cho kết quả ổn định hơn..."

  Không tìm thấy nguồn nào nhắc tới con số 40%.

  [ Giữ ]  [ Sửa ]  [ Bỏ câu ]  [ Viết lại trang này ]
```

Ba thứ bắt buộc trên mỗi thẻ: **nội dung bị nghi**, **bằng chứng đối chiếu**, **ảnh trang**.
Thiếu ảnh trang thì người duyệt không có cách nào kiểm nhanh.

---

## 7. Output: `DeckBundle`

Sau khi duyệt xong, đóng gói đúng những gì runtime cần — **không phải toàn bộ artifact**:

```
DeckBundle {
  deck_id, version, reviewed_at, reviewed_by, mos_score

  slide_index (Qdrant collection)  <- TRUY XUẤT cho R2, không phải file text
  deck_map.txt                     <- ~150 token, inline vào prompt
  manifest.json                    <- audio + duration + prosody
  slide_repr_lite[]                <- message, description, relations, entities
                                      (bỏ bbox, conf) — R3 dùng làm bảng chỉ trỏ
  structure                        <- sections, dependencies (bỏ rationale)
                                      concept_map đã nằm trong index, không dump ở đây
  answer_depth[]                   <- per-slide, từ quyết định ở mục 4
  known_confusable_pairs           <- từ mục 4b, R2 dùng để nới gate ở đúng cặp đó
  voice_id + speed                 <- R5 PHẢI dùng đúng giá trị này
  pronunciation.json (ref)
  qa_cache · thumbs/ · tts/
}
```

Runtime **không đọc** `raw/`, `alignment.json`, `flags.json`, `scenario.json`.
Những file đó là của offline.

Ba trường mới so với bản trước, và mỗi cái chặn một lớp lỗi cụ thể:

| Trường | Chặn cái gì |
|---|---|
| `slide_index` là **collection**, không phải file text | R2 nhồi index vào prompt |
| `voice_id` + `speed` trong bundle | R5 dùng giọng khác giọng precomputed |
| `known_confusable_pairs` | R2 nhảy bừa ở cặp trang đã biết là dễ lẫn |

Bundle phải có `version` và `reviewed_at`. Chạy buổi thuyết trình với bundle chưa duyệt thì
orchestrator **cảnh báo rõ ràng lúc khởi động**, không im lặng chạy.

---

## 8. Quality gate ra khỏi S7

| Điều kiện | Hành động |
|---|---|
| Còn cờ đỏ chưa quyết | **Không đóng gói được** |
| **MOS < 3.8** | **Không release** (đóng gói được, nhưng không đem đi thuyết trình) |
| `self_retrieval_fail` chưa quyết (sửa hay chấp nhận) | Không đóng gói được |
| Flag precision < 60% | Không chặn build, nhưng phải xem lại ngưỡng sinh flag |
| Số phút duyệt / deck > 30 | Dấu hiệu flag quá nhiều hoặc UI thiếu ngữ cảnh |

Phân biệt hai mức: **đóng gói được** (bundle hợp lệ, chạy được) và **release được**
(đem đi thuyết trình thật). MOS chặn mức thứ hai, không chặn mức thứ nhất — vì còn phải
đóng gói được thì mới nghe thử rồi sửa được.

Hai dòng cuối là **chỉ số đánh giá chính hệ thống flag**, đo trong phiên duyệt thật và đưa
vào báo cáo.

---

## 9. Cấm

- Bắt người duyệt cả 20 trang
- **Bắt người NGHE cả 20 trang** — chỉ 3 trang đại diện + câu bị flag
- Đóng gói bundle khi còn cờ đỏ chưa quyết
- Làm mất `review.json` giữa các lần build
- Hiện flag mà không kèm bằng chứng đối chiếu + ảnh trang
- Để hệ thống tự sửa lỗi thuộc về bộ slide (`dependency_forward`, `concept_used_before_introduced`)
- **Nhét MOS vào CI** rồi coi như đã kiểm tự nhiên
- **Bỏ lối "chấp nhận cặp trùng chủ đề"** ở mục 4b — thiếu nó thì flag kêu oan mãi và
  người duyệt học cách bỏ qua
