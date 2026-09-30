# KBChunk — cắt tài liệu thành mẩu để tìm

**Vào:** `out/parsed/<doc_id>/document.json` (`ParsedDocument`) — file DUY NHẤT, không đọc
`docling.json` / `layout.json` / `data/patches/` (đã gộp vào đó ở S0) · **Ra:**
`out/kb/<doc_id>/chunks.json` · **Code:** [src/kb/chunk.py](../../src/kb/chunk.py)

```
python src/kb/cli.py out/parsed/<doc_id>/document.json -o out/kb/<doc_id>/chunks.json [--embed]
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

| block | `content` là gì | `provenance` |
|---|---|---|
| `paragraph` | chữ từ text layer | `text_layer` — đúng 100% |
| `table` | markdown SINH từ `cells` mỗi lần nạp | chữ ô đúng, lưới hàng/cột có thể sai |
| `image` | mô tả của VLM | `vlm` — có thể sai. `content: null` (`why_empty`) thì tự rơi |

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

## 4. Nhiều ảnh trên một trang → nhiều vector, cùng một trang

Đo được **5/40 trang** có từ 2 ảnh được mô tả trở lên, và mẫu rất rõ:

```
p19  code Python vẽ bubble chart  +  biểu đồ tán xạ kết quả
p24  code Python                  +  biểu đồ có thanh sai số
p34  code matplotlib + numpy      +  biểu đồ 3D scatter
p37  code cartopy                 +  quả địa cầu bán cầu Bắc
p20  3 ảnh hoa iris (setosa · versicolor · virginica)
```

Bốn trang đầu là **cùng một thứ**: đây là code, đây là cái nó vẽ ra. Tách đôi thì hỏng —
hỏi *"làm sao vẽ scatter 3D"* cần cả hai.

Nhưng gộp một vector thì **loãng**: chunk 338 token của p19 trộn từ ngữ của code lẫn mô
tả hình, vector ra là bình quân hai chủ đề.

Gỡ bằng đúng nguyên tắc §5 S6a đã dùng cho slide (*multi-field embed, KHÔNG gộp một
vector*):

```
trang 19  ──┬── vector A : cả trang           338 tok
            ├── vector B : mô tả ảnh code     212 tok
            └── vector C : mô tả ảnh biểu đồ  108 tok

            cả ba trỏ về  page_no = 19
```

Khớp vector nào cũng ra trang 19 — điều hướng không đổi, mà không loãng.

Chi phí: 40 chunk trang + ~33 mô tả ảnh ≈ **73 vector**. Chưa tới một xu.

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
chunk_id       "3_datavisualization#p011"    ← "#p011.2" mảnh cắt · "#p019.b02" ảnh
page_no        11
page_hash      "ed0af3583bd451dc"            ← copy từ ParsedPage: trang đổi -> chỉ nạp lại trang đó (§8)
section_id     "sec_00"
section_title  "Đồ thị dạng đường"
block_ids      ["p011.b00", "p011.b01"]      ← truy ngược về đúng mẩu
provenance     {"text_layer": 1, "vlm": 1}   ← phần nào chắc đúng, phần nào model sinh
content_type   "content" | "section_divider"
token_count    178
vector_role    "page" | "image"              ← multi-vector, xem §4
```

**`provenance` đếm theo mẩu** là chỗ cố ý khác luật gốc. NT2 đòi mọi field khai nguồn,
nhưng chunk **trộn** hai loại: tiêu đề từ text layer (đúng 100%), mô tả ảnh do VLM sinh
(có thể bịa). Không ghi tỉ lệ thì lúc R4 trả lời không biết phần nào tin được.

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

Đổi model sau chỉ là một dòng config + nhúng lại 73 vector, mất vài giây.

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
vào           out/parsed/<doc_id>/document.json — file duy nhất
đơn vị        1 trang = 1 chunk chính
+ phụ         trang >= 2 ảnh có mô tả -> mỗi ảnh 1 vector phụ, cùng trỏ về page_no
tiền tố       [<chương> · <tiêu đề trang> · trang N/M], thiếu phần nào bỏ phần đó
nội dung      mọi block có content, đúng thứ tự page.blocks
đánh dấu      trang phân mục (slide_type) -> section_divider, lọc lúc tìm, KHÔNG xoá
cắt thêm      > 500 token: trang -> block -> hàng bảng (lặp tiêu đề) / dòng -> câu
nhúng         text_enriched · EMBED_MODEL trong .env (text-embedding-3-small) + BM25
```

---

## 10. Phải đo, chưa biết

Hai câu chỉ có số mới trả lời được — làm xong `src/kb/` thì chạy ngay:

**Self-retrieval** (§5 S6a · gate §11 ≥ 90%): lấy nội dung trang `i` làm truy vấn, top-1
phải ra đúng trang `i`. Đây là **vòng phản hồi đầu tiên** của cả dự án — nó đo xem parse
có đủ tốt để tìm được không, bằng số thật.

Nghi ngờ cụ thể cần kiểm:

- **7 trang `p9–p15` cùng tiêu đề** — tiền tố `· trang N/40` tách được bao nhiêu?
- **`p15` mất nội dung** (docling bỏ sót code + bảng màu, xem
  [parsed-document.md §0](./parsed-document.md)) — chunk 18 token, chắc chắn trượt
- **7 trang phân mục** — lọc đúng chưa, hay lọc nhầm trang có nội dung

Kết quả audit quyết định việc tiếp theo: sinh `message` (S1), cứu `p15`, hay thêm
`slide_type`. **Đừng đoán trước khi có số.**
