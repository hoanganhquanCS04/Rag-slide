# Embedding — biến chunk thành vector để tìm

**Vào:** `out/kb/*/chunks.json` · **Ra:** `out/kb/*/vectors__<model_id>.{npy,json}` · **Code:** `src/kb/embed.py`

---

## 1. Để làm gì

Máy không hiểu chữ. Khán giả hỏi *"làm sao lưu biểu đồ ra file ảnh"*, slide viết
`plt.savefig('line_graph.png')` — **không chung một chữ nào**. So chữ là trượt.

Embedding biến mỗi đoạn chữ thành một dãy số. Hai đoạn cùng ý nghĩa thì hai dãy số nằm
gần nhau, dù không chung chữ. Đo thật trên dữ liệu này:

```
CÂU HỎI:  "làm sao lưu biểu đồ ra file ảnh"

   0.526   Đoạn mã Python plt.plot(x,y) rồi plt.savefig('line_graph.png')
   0.294   Biểu đồ đường của hai hàm sin(x) và cos(x)
   0.088   Deadline: 25/10/2025, mỗi nhóm nộp báo cáo và code
```

Nó phục vụ đúng hai việc ở runtime:

```
R4  hỏi nội dung      → tìm trang chứa câu trả lời
R2  "quay lại chỗ..." → tìm trang cần nhảy tới
```

Cả hai đều là *"trong 40 trang, trang nào liên quan nhất"* — biến từ bài toán đọc hiểu
thành phép **tính khoảng cách**, chạy trong mili giây.

---

## 2. Model: `text-embedding-3-small` gọi qua API

[CLAUDE.md §9](../../CLAUDE.md) ghi `bge-m3`. **v0 KHÔNG dùng bge-m3** — chạy qua API.
Quyết định này đi ngược lại số đo, nên phải ghi rõ cả hai mặt.

### Số đo thật, cùng một máy

| | nạp model | nhúng 1 câu hỏi | tốn đĩa |
|---|---|---|---|
| `bge-m3` local, CPU | 1.9s (lần nguội: **~10 phút**) | **182ms** | 4.3 GB |
| API `text-embedding-3-small` | không phải nạp | **719ms** | 0 |
| API `text-embedding-3-large` | không phải nạp | 1352ms | 0 |

**bge-m3 nhanh gấp 4 lần ở runtime.** Câu hỏi phải nhúng xong mới truy xuất được, nên
nó nằm **trên đường găng** của ngân sách 2.5s (NT1):

```
bge-m3 local   182ms  →   7% ngân sách
API 3-small    719ms  →  29% ngân sách
```

### Vì sao vẫn chọn API

1. **Máy dev không chịu nổi.** Lần nạp nguội đầu mất ~10 phút — Windows Defender quét
   file pickle 2.2 GB lúc đọc. Nạp lần hai chỉ 1.9s vì đã nằm trong page cache, nhưng
   cái giá đó phải trả lại mỗi lần khởi động lạnh.
2. **4.3 GB đĩa + ~2.5 GB RAM** trên máy còn 5.6 GB trống.
3. **Offline thì API NHANH HƠN** — gửi cả lô 64 chunk trong một request, còn CPU local
   chạy tuần tự 819ms/chunk. Toàn bộ S5/S6a nằm ở offline.

### Cái mất, phải nhớ

- **Phụ thuộc mạng ở runtime.** Đang thuyết trình mà API timeout thì hỏng buổi. Phải có
  đường lui: `qa_cache` cho câu hay gặp (§5 S6b), và một câu xin lỗi prerecorded.
- **Mất sparse vector** — xem §9. Đây là cái mất nặng nhất về mặt kiến trúc.
- **719ms ăn 29% ngân sách.** Nếu P95 latency (§11) vượt 2.5s thì đây là chỗ đầu tiên
  phải xem lại, và đường lui là quay về `bge-m3`.

Đổi lại, đếm token giờ **đúng hơn trước**: `chunk.py` dùng `cl100k_base`, đúng bộ
tokenizer của `text-embedding-3-*`. Trước đếm bằng `bge-m3` và chính code cũ đã ghi đó
chỉ là *proxy*.

---

## 3. Embed cái gì

`text_enriched`, **không phải** `text_raw`. §10 cấm thẳng:

> ❌ Embed `text_raw` thay vì `text_enriched`

```
text_raw       "Đồ thị dạng đường\nĐoạn mã Python và biểu đồ..."
text_enriched  "[Đồ thị dạng đường · trang 11/40] Đồ thị dạng đường\nĐoạn mã..."
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ tiền tố PHẢI nằm trong vector
```

Với deck này nó không phải trang trí: 7 trang `p9–p15` có tiêu đề **giống hệt nhau**,
tiền tố `· trang 11/40` là thứ duy nhất tách chúng.

---

## 4. Embed bao nhiêu: TẤT CẢ 51

Kể cả 7 trang phân mục và `p15` (trang docling bỏ sót nội dung, nay đã vá tay).

**Lọc ở bước TRUY VẤN, không phải ở bước nhúng.** Đây là hai việc khác nhau:

```
embed.py     nhúng cả 51 chunk
search.py    mặc định lọc content_type != "section_divider"
             có cờ --no-filter để bật lại mà so
audit.py     chạy CẢ HAI kiểu, in ra chênh lệch
```

Ba lý do không lọc sớm:

1. **Lọc sớm là giấu mất hiện tượng.** Audit sẽ không bao giờ thấy trang phân mục cướp
   kết quả, vì chúng không có trong index. Tự lọc rồi tự khen index sạch — không đo
   được gì.
2. **Luật `is_section_divider` có thể sai.** Nó dựa vào vị trí tiêu đề (giữa trang vs
   góc trên). Deck khác bố cục khác, có thể bắt nhầm trang có nội dung. Lúc đó không có
   vector, muốn kiểm phải nhúng lại.
3. **Không tiết kiệm gì.** 8 chunk thêm là chưa tới một giây, tiền thì không đáng kể.

Cũng đúng tinh thần §10: *"❌ Vứt chunk `background`"* — giữ, đánh dấu, chứ không loại
khỏi hệ thống.

**Vì sao phải lọc lúc truy vấn:** chunk trang phân mục toàn bộ là tên chương lặp hai lần.

```
p9   "[Đồ thị dạng đường · trang 9/40] Đồ thị dạng đường"
```

Hỏi *"đồ thị dạng đường là gì"* thì nó khớp **rất mạnh** vì không có gì làm loãng, trong
khi `p11` có 178 token nội dung thật nên điểm bị pha. Robot nhảy vào **trang trắng** —
đúng loại `harmful_jump` mà §11 đặt gate dưới 2%.

---

## 5. Gộp token: API làm sẵn, không phải chọn

API trả thẳng một vector cho mỗi đoạn chữ. Không có bước gộp token nào để làm sai.

Ghi lại cái bẫy này phòng khi quay về model local:

```
bge-m3                lấy vector ở token CLS        (hidden[:, 0])
MiniLM / nhiều model  lấy TRUNG BÌNH các token      (mean pooling)
```

Dùng sai cách gộp thì vector vẫn ra đủ chiều, code vẫn chạy, **không báo lỗi gì** — chỉ
là chất lượng truy xuất tụt mà không ai biết vì sao.

---

## 6. Chuẩn hoá L2

OpenAI trả sẵn vector đã chuẩn hoá, nhưng `embed.py` vẫn ép lại một lần — rẻ, và không
phải tin vào lời hứa của nhà cung cấp.

Chuẩn hoá rồi thì cosine similarity chỉ còn là phép nhân vô hướng — nhanh hơn, và ngưỡng
điểm ổn định giữa các model.

Ghi `normalized: true` vào file meta để bên tìm kiếm biết không phải chuẩn hoá lại.

---

## 7. Cache theo hash

```
khoá = sha1(model_id + "\0" + text_enriched)
đĩa  = out/kb/.embed_cache/<model_slug>/<sha1>.npy
```

Sửa mô tả ảnh của một trang → chỉ nhúng lại chunk đó, 50 cái kia lấy từ cache.

Chạy qua API thì cache **tiết kiệm tiền chứ không chỉ tiết kiệm thời gian** — cùng
nguyên tắc `tts_hash` ở §5 S6b: *"phần tốn tiền nhất, phải reuse"*.

`model_id` nằm trong khoá — đổi model là cache miss toàn bộ, đúng như phải thế.

---

## 8. File sinh ra

```
out/kb/3_datavisualization/vectors__text-embedding-3-small.npy   ma trận (51, 1536) float32
out/kb/3_datavisualization/vectors__text-embedding-3-small.json  {model, backend, dim, normalized, rows:[chunk_id...]}
```

**`model_id` nằm trong TÊN FILE.** Đây là luật §5 S6a áp cho file thay vì Qdrant
collection:

> Đổi embedding model mà không rebuild thì runtime truy vấn index cũ bằng vector mới và
> trả rác **mà không báo lỗi**

Tên file khác nhau thì không thể vô tình dùng nhầm. Chiều vector cũng khác nhau
(1024 của `bge-m3` vs 1536 của `3-small`) nên nạp nhầm là vỡ ngay — nhưng **đừng trông
vào đó**: `3-small` và `ada-002` cùng 1536 chiều mà vector hoàn toàn khác nhau, nạp nhầm
sẽ im lặng trả rác.

File `.json` giữ thứ tự hàng → `chunk_id`, vì `.npy` chỉ là ma trận số, không biết hàng
nào là chunk nào.

---

## 9. Sparse: API KHÔNG trả — đây là món nợ

§5 S5 bắt buộc hybrid dense + sparse. `bge-m3` cho cả hai trong một lần forward; **API
chỉ cho dense**.

Với deck này sparse không phải tuỳ chọn. Nội dung lẫn tiếng Việt và thuật ngữ tiếng Anh
(`matplotlib`, `plt.savefig`, `Cartopy`, `cnames`) — đúng loại từ khoá mà dense hay
trượt còn khớp chuỗi thì bắt chính xác.

Đường bù, làm ở `search.py`:

```
rank-bm25 trên text_enriched  →  nhánh từ khoá
vector API                    →  nhánh ngữ nghĩa
gộp bằng RRF (reciprocal rank fusion)
```

Thêm một thư viện, thêm một chỉ mục phải đồng bộ. Đó là cái giá của việc bỏ `bge-m3`.

`embed.py` ghi `"sparse": false` vào file meta và **bắn cảnh báo mỗi lần chạy** — để
không ai tưởng đã đủ hybrid trong khi mới có một nửa.

---

## 10. Đã đo — kết quả lần chạy đầu

### Tốc độ

```
nhúng 52 chunk    3.6s   (69ms/chunk) — MỘT lần gọi API cho cả lô 52
nhúng 1 câu hỏi   240ms  đo trên lô 4 câu; gọi lẻ 1 câu thì ~719ms
```

Gộp lô là chỗ API thắng `bge-m3` local ở offline: 3.6s so với ~43s chạy tuần tự trên CPU.

### Chất lượng: 4 câu hỏi có nhãn

| câu hỏi | cần | không lọc | lọc phân mục |
|---|---|---|---|
| liệt kê tên và mã màu trong matplotlib | p15 | **p15 ✅** 0.663 | **p15 ✅** |
| đồ thị dạng đường là gì | p10 | p9 ❌ *(phân mục)* | **p10 ✅** |
| vẽ đồ thị ba chiều như thế nào | p34 | p32 ❌ *(phân mục)* | **p34 ✅** |
| làm sao lưu biểu đồ ra file ảnh | p11 | p6 ❌ | p6 ❌ *(p11 hạng 11, 0.422)* |
| | | **1/4** | **3/4** |

**Ba điều chốt được:**

1. **Lọc trang phân mục là bắt buộc, không phải tối ưu.** 1/4 → 3/4. Hai lần trượt đều
   do đúng cơ chế §4 mô tả: `p9` được 0.753 còn `p10` (trang có nội dung thật) chỉ 0.626
   — trang trắng thắng trang có nội dung **0.127 điểm**.
2. **Vá tay `p15` có tác dụng thật.** Trước khi vá, trang này 18 token và không thể tìm
   ra. Giờ nó là top-1 với 0.663.
3. **Câu "lưu biểu đồ ra file ảnh" trượt — đây đúng là ca cần sparse.** Trang `p11` chứa
   `plt.savefig('line_graph.png')`, mà câu hỏi không có chữ `savefig`. Dense đẩy nó xuống
   hạng 11, nhường cho `p6`/`p3` — hai trang nói chung chung về "trực quan hoá dữ liệu".
   BM25 khớp chuỗi `savefig` sẽ kéo nó lên. **Đây là bằng chứng đo được cho món nợ ở §9**,
   không phải lo xa.

> Lưu ý khi tự đo: câu hỏi phải gõ **có dấu**. Thử gõ không dấu thì cả 4 câu đều trượt và
> điểm tụt xuống quanh 0.32 — vector không hiểu tiếng Việt mất dấu.

### Còn phải làm

Chạy **self-retrieval audit** đầy đủ (§5 S6a · gate §11 ≥ 90%): lấy nội dung trang `i`
làm truy vấn, top-1 phải ra đúng trang `i`. Bốn câu ở trên mới là thăm dò, chưa phải gate.

Nghi ngờ còn lại ở [kb-chunk.md §10](./kb-chunk.md): 7 trang `p9–p15` cùng tiêu đề —
tiền tố `· trang N/40` tách được bao nhiêu phần?

---

## 11. Chạy

```bash
# cần OPENAI_API_KEY (và OPENAI_BASE_URL nếu đi qua proxy)
.venv/Scripts/python.exe src/kb/cli.py out/parsed/3_datavisualization/document.json \
    -o out/kb/3_datavisualization/chunks.json --embed --stats
```

| cờ | nghĩa |
|---|---|
| `--embed` | nhúng qua API sau khi cắt chunk |
| `--embed-model` | mặc định `text-embedding-3-small` |
| `-o` | `out/kb/<ten>/chunks.json` — vector ghi cùng thư mục, bắt buộc khi `--embed` |
| `--no-cache` | bỏ qua cache trên đĩa, gọi API lại từ đầu |
