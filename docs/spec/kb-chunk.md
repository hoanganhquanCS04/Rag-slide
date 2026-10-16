# KBChunk — cắt tài liệu thành mẩu để tìm

**Vào:** `src/output/parser/<doc_id>/document.json` (`ParsedDocument`) — file DUY NHẤT, không đọc
`docling_vlmpipeline.json` / `tables.json` / `pictures.json` (đã gộp vào đó ở S0) · **Ra:**
`src/output/chunker/<doc_id>/chunks.json` · **Code:** [src/offline/chunker/](../../src/offline/chunker/) — `BaseChunker`
([base.py](../../src/offline/chunker/base.py)) + biến thể `page` ([variants/page.py](../../src/offline/chunker/variants/page.py))

```
python src/offline/chunker/cli.py src/output/parser/<doc_id>/document.json   # -> src/output/chunker/<doc_id>/chunks.json
python src/offline/embedder/cli.py src/output/chunker/<doc_id>/chunks.json            # -> src/output/embedders/<doc_id>/vectors__<model>.*
```

---

## 1. Chunk để làm gì

Khán giả hỏi một câu. Có **hai loại**, đi hai đường khác nhau:

```
"sơ đồ bên trái là gì?"            → hỏi về TRANG ĐANG CHIẾU
                                     → trang đó đã nằm SẴN trong prompt
                                     → lọc bbox → trả lời
                                     → KHÔNG đụng tới chunk

"Cartopy khác Basemap thế nào?"    → thứ không có trên màn hình
                                     → tìm trong 40 chunk → ra trang 36
                                     → ĐÂY mới là việc của chunk
```

Đây là ranh giới §6 CLAUDE.md đặt ra:

```
TRẠNG THÁI  -> inline, bounded   "tôi đang nhìn gì" — không index nào trả lời được
TRI THỨC    -> TRUY XUẤT         slide khác, chunk KB
```

Không thể nhét cả 40 trang vào prompt (§10 cấm). Nên cắt nhỏ, nhúng vector, rồi tìm.

**Chunk chỉ cần đủ tốt để tìm ra ĐÚNG TRANG.** Chỉ vào đúng phần tử nào trên trang là
việc của `bbox`, không phải của chunk.

---

## 2. Đơn vị: một trang = một chunk

Đo trên `3_DataVisualization` (40 trang):

```
token mỗi chunk:  min 18 · trung vị 144 · max 353
KHÔNG chunk nào vượt 500  ->  không phải cắt nhỏ
```

Ba lý do, xếp theo sức nặng:

1. **Trang là đơn vị điều hướng của runtime.** R2 nhảy tới *một trang*. Chunk vắt qua
   hai trang thì trả về không biết nhảy đâu. `KBChunk` trong
   [s5](../offline/s5-kb-construction.md) cũng chỉ có field `page` số ít.
2. **Trang đã đủ nhỏ.** Luật 300–500 token của §5 S5 nhắm vào tài liệu văn xuôi dày.
   Slide trung vị 144 — cắt nhỏ nữa thành vô nghĩa.
3. **Không cắt giữa câu** — tự động thoả, ranh giới chunk là ranh giới trang.

---

## 3. Hình dạng một chunk

```
[Nhân sự · Thuế và đăng ký giảm trừ gia cảnh · trang 18/51]   ← tiền tố (text_enriched)
Thuế và đăng ký giảm trừ gia cảnh             ← block đầu trang (tiêu đề)
...                                           ← mọi block có `content`, đúng thứ tự `page.blocks`
```

Thân chunk lấy từ `page.blocks[].content` — loại nào cũng vậy:

| block | `content` là gì | tin được không |
|---|---|---|
| `paragraph` | chữ VLM đọc từ ảnh trang | có thể sai |
| `table` | markdown SINH từ `cells` mỗi lần nạp | lưới hàng/cột có thể sai |
| `image` | mô tả của VLM | dễ bịa bối cảnh hơn chữ. `content: null` (`why_empty`) thì tự rơi |

**Tiền tố là phần contextual enrichment** mà §5 S5 gọi là "bước quan trọng nhất":
`[<chương> · <tiêu đề trang> · trang N/M]`, thiếu phần nào bỏ phần đó, tiêu đề trùng tên
chương (trang phân mục) thì ghi một lần. Lấy từ `sections` + `page.title` + `page_no` nên
**không phải gọi LLM**.

- **Chương** — 7 trang `p9–p15` của 3_datavisualization có tiêu đề **giống hệt nhau**,
  `· trang 11/40` mới tách được.
- **Tiêu đề trang** — trang dài bị cắt thì mảnh sau vẫn mang nó. Onboarding `p9.2` mở bằng
  *"Chỉ gửi email cho đúng người…"* — thiếu *"Trao đổi thông tin qua email"* là không biết
  đang nói chuyện gì. Mảnh đầu có tiêu đề hai lần (tiền tố + block tiêu đề) — cố ý giữ
  `text_enriched = tiền tố + text_raw`, `text_raw` = đúng nội dung trang, không đặc cách.
- `tetnguyendan` không có mục lục lẫn tiêu đề → tiền tố chỉ còn `[trang 4/10]`.

Header/footer lặp không có trong `ParsedDocument` — S0 đã bỏ.

---

## 4. Nhiều ảnh trên một trang → vẫn MỘT chunk trang (bỏ vector phụ 2026-10-10)

Mô tả ảnh là một block có chữ như mọi block khác → nằm trong chunk trang. Không tách vector
riêng cho từng ảnh nữa.

Bản trước có tách: trang >= 2 ảnh có mô tả thì mỗi ảnh thêm 1 vector phụ cùng trỏ về
`page_no`. Lý do lúc đó đo trên `3_datavisualization` (5/40 trang dạng "code + biểu đồ nó vẽ
ra" — gộp một vector thì bình quân hai chủ đề, loãng). Bỏ vì vector phụ chỉ **nhân bản chữ đã
có** trong chunk trang, và deck nhân sự không có kiểu trang đó. Đo trên Onboarding, 200 câu
(hybrid):

```
                          chunk   top-1     top-5
có vector ảnh phụ          152    156/200   191/200
chỉ chunk trang             89    149/200   193/200
```

top-5 (gate §11, R2 đưa cả top-5 cho LLM chọn) không tụt; top-1 tụt 7 câu — chấp nhận. Câu hỏi
đều do AI viết, số thiên vị. Deck có nhiều trang "code + kết quả" thì đo lại.

---

## 5. Trang phân mục — đánh dấu, KHÔNG vứt

7 trang `p9, p16, p22, p25, p29, p32, p35` chỉ có dải tiêu đề, **18 token**. Chúng đúng
là trang mở đầu của 7 chương. Embed là rác lẫn vào index.

Nhưng **không xoá** — đánh `content_type: "section_divider"` rồi lọc khỏi truy vấn mặc
định. Hai lý do: §10 cấm vứt chunk, và nếu luật nhận diện sai thì còn dữ liệu để sửa,
khỏi parse lại (parse lại **tốn tiền API**).

---

## 6. Metadata

```python
chunk_id       "3_datavisualization#p011"    ← "#p011.2" mảnh cắt
page_no        11
page_hash      "ed0af3583bd451dc"            ← copy từ ParsedPage: trang đổi -> chỉ nạp lại trang đó (§8)
section_id     "sec_00"
section_title  "Đồ thị dạng đường"
block_ids      ["p011.b00", "p011.b01"]      ← truy ngược về đúng mẩu
n_images       1                             ← bao nhiêu mẩu là MÔ TẢ ẢNH -> image_ratio
content_type   "content" | "section_divider"
token_count    178
vector_role    "page"                        ← "image" không còn sinh, xem §4
```

**`n_images` / `image_ratio`**: chunk **trộn** chữ trên slide với mô tả ảnh do VLM sinh (dễ bịa bối
cảnh hơn). Không ghi tỉ lệ thì lúc R4 trả lời không biết phần nào đáng dè dặt. Trường
`provenance` cũ (đếm `{"vlm": 3}`) đã bỏ 2026-10-12 — mọi khối đều `vlm`, nó không phân biệt gì.


`block_ids` cho phép truy ngược về `ParsedDocument`, nên `grounding` của NT3 chỉ được
đúng vào ảnh `p019.b02` chứ không phải chung chung cả trang.

---

## 7. Khi nào cắt thêm

Chỉ khi chunk **vượt 500 token** (đếm bằng `tiktoken` `cl100k_base` — tokenizer thật của
`text-embedding-3-*`, kể cả tiền tố). Mỗi mảnh giữ nguyên tiền tố, thêm hậu tố `#p011.1`,
`#p011.2`. Cắt ở ranh giới **to nhất còn vừa**:

```
trang   -> ranh giới block
block   -> bảng: theo hàng, mảnh nào cũng LẶP hàng tiêu đề + |---|
           chữ : theo dòng (list = mỗi gạch đầu dòng một dòng)
dòng    -> theo câu
câu     -> KHÔNG cắt — một câu dài hơn 500 thì để nguyên
```

3_datavisualization không cần (max 478). **Onboarding thì cần** — deck nhân sự nhiều chữ,
ngược hẳn giả định "ít chữ nhiều hình":

```
7 block MỘT MÌNH đã > 500 token     bảng chấm công p21: 1570 · list thuế p19: 848
cắt theo block thôi                 -> 9 chunk > 500, max 1583 — một vector bình quân
                                       cả bảng 13 hàng, hỏi một dòng quy định là loãng
cắt thêm trong block                -> 0 chunk > 500, max 498 · 25/51 trang bị cắt
                                       "xin phép vắng mặt trên ILVG" -> trúng p21 (BM25)
```

Hàng tiêu đề phải lặp: không có nó mảnh `p021.3` chỉ còn các ô chữ, không biết cột nào là
"cấp T3 trở lên", cột nào là "còn lại".

### Chọn CHỖ cắt — không cắt thêm khúc nào (2026-10-02)

Số khúc của trang giữ ít nhất có thể (đúng bằng cách nhồi đầy), nhưng không nhồi cho đầy 500
rồi mới cắt — chọn chỗ cắt theo thứ tự ưu tiên:

```
trước một ĐỀ MỤC   0   block title không ở đầu trang ("LƯU Ý:", "Quy tắc phản hồi email:"),
                       hoặc dòng ngắn (<= 12 chữ) kết bằng ':' ("Bảo hiểm sức khỏe Vingroup:")
giữa hai block     1
giữa dòng / hàng   2
giữa câu           4
NGAY SAU đề mục    8   đề mục trơ trọi cuối khúc, nội dung sang khúc sau — gần như cấm
khúc < 100 token  +5   rồi vẫn còn thì gộp vào khúc bên cạnh (được vượt 500, tối đa 600)
hoà                    chọn cách chia đều nhất
```

Vì sao: nhồi đầy cắt ngang chủ đề — Onboarding p29 bảng gói bảo hiểm rơi sang khúc sau, đề mục
"Bảo hiểm sức khỏe Vingroup:" ở lại khúc trước; p9, p11 đề mục nằm trơ cuối khúc. Đo trên 200 câu
(kiểm chéo, chọn cấu hình trên 100 câu, chấm trên 100 câu còn lại): top-1/3/5 153.7/185.3/191.6
-> 155.1/187.2/192.3; số cấu hình RRF đạt top-5 >= 194 tăng 9 -> 30 (ít nhạy với tham số hơn).

---

## 8. Nhúng vector

Đo thật trên endpoint đang dùng:

| model | chiều | độ trễ 1 câu | MIRACL đa ngữ | giá |
|---|---|---|---|---|
| **`text-embedding-3-small`** ✅ | 1536 | **719ms** | 44.0% | 1× |
| `text-embedding-3-large` | 3072 | 1352ms | 54.9% | 6.5× |
| `text-embedding-ada-002` | 1536 | 749ms | 31.4% | 1.3× |

**Chọn `3-small`.**

- **Loại `ada-002`**: cùng chiều, cùng độ trễ với `3-small` nhưng kém 13 điểm đa ngữ.
  Model 2022, đã bị thay thế. Thua mọi mặt.
- **Không chọn `3-large`** vì độ trễ. Ngân sách runtime là **< 2.5s tới byte audio đầu**
  (§2 NT1). Mỗi câu hỏi phải nhúng trước khi truy xuất được — nó nằm **trên đường găng**.
  `3-large` ăn 54% ngân sách chỉ để nhúng một câu. Đổi 11 điểm chất lượng lấy 633ms là
  không đáng, nhất là khi nút thắt hiện tại là **chất lượng parse**, không phải model nhúng.

Đổi model sau chỉ là một dòng config + nhúng lại cả bộ vector, mất vài giây.

**Hai điều phải nhớ:**

1. **API cho dense-only, không có sparse.** §5 S5 bắt buộc hybrid → phải thêm BM25 riêng
   (`rank-bm25`). Với tiếng Việt lẫn thuật ngữ Anh (`matplotlib`, `plt.savefig`,
   `Cartopy`) thì BM25 không phải tuỳ chọn — nó bắt đúng mấy từ khoá dense hay trượt.
2. **719ms là giá của việc gọi mạng.** Đã đo `bge-m3` local trên chính máy này:
   **182ms/câu hỏi**, nhanh gấp 4, không cần mạng, và cho cả dense lẫn sparse trong một
   lần forward — đúng thứ §9 chỉ định. Vẫn chọn API vì nó ngốn 4.3 GB đĩa và lần nạp
   nguội đầu mất ~10 phút. Khi siết ngân sách 2.5s thì đây là chỗ quay đầu đầu tiên.
   Lý do đầy đủ ở [embedding.md §2](./embedding.md).

> Lưu ý kỹ thuật: endpoint chặn User-Agent của `urllib` (Cloudflare error 1010).
> Phải gọi bằng `httpx` hoặc `requests`.

---

## 9. Luật chốt

```
vào           src/output/parser/<doc_id>/document.json — file duy nhất
đơn vị        1 trang = 1 chunk (mô tả ảnh nằm trong đó, KHÔNG có vector ảnh phụ)
tiền tố       [<chương> · <tiêu đề trang> · trang N/M], thiếu phần nào bỏ phần đó
nội dung      mọi block có content, đúng thứ tự page.blocks
đánh dấu      trang phân mục (slide_type) -> section_divider, lọc lúc tìm, KHÔNG xoá
cắt thêm      > 500 token: trang -> block -> hàng bảng (lặp tiêu đề) / dòng -> câu
chỗ cắt       ít khúc nhất; ưu tiên trước đề mục > giữa block > giữa dòng > giữa câu;
              cấm ngay sau đề mục; khúc < 100 token gộp sang bên cạnh (tối đa 600)
nhúng         text_enriched · EMBED_MODEL trong .env (text-embedding-3-small) + BM25
```

---

## 10. Phải đo, chưa biết

Hai câu chỉ có số mới trả lời được — làm xong phần tìm (`src/utils/ensemble_search.py`) thì chạy ngay:

**Đo tìm kiếm bằng bộ câu hỏi có nhãn** (`src/evaluation/eval.py`, `data/eval/<doc_id>.queries.json`):
trang đúng có ở top-1 / top-5 không. Đây là **vòng phản hồi đầu tiên** của cả dự án — nó đo xem
parse có đủ tốt để tìm được không, bằng số thật. (Bản đầu dùng self-retrieval — lấy chính trang
làm câu hỏi — nhưng nó gần như luôn 100% vì đề bài là đáp án; bỏ 2026-10-13.)

Nghi ngờ cụ thể cần kiểm:

- **7 trang `p9–p15` cùng tiêu đề** — tiền tố `· trang N/40` tách được bao nhiêu?
- **`p15` mất nội dung** (docling bỏ sót code + bảng màu, xem
  [parsed-document.md §0](./parsed-document.md)) — chunk 18 token, chắc chắn trượt
- **7 trang phân mục** — lọc đúng chưa, hay lọc nhầm trang có nội dung

Kết quả đo quyết định việc tiếp theo: cứu `p15`, hay thêm `slide_type` (sinh `message`
ở S1 từng là một lựa chọn — S1 đã bỏ). **Đừng đoán trước khi có số.**
