# Search — từ câu hỏi ra số trang

**Vào:** `out/kb/*/chunks.json` + `out/kb/*/vectors__<model>.npy` · **Ra:** `SearchHit[]` · **Code:** `src/kb/search.py` + `src/kb/sparse/` (BM25) + `src/kb/audit.py`

---

## 1. Để làm gì

Đang có 52 chunk và 52 vector nằm trong file. **Chưa có gì dùng chúng.**

`search.py` là tầng biến đống số đó thành thứ trả lời được:

```
"làm sao lưu biểu đồ ra file ảnh"   →   [p11, p10, p13]
```

Runtime cần nó ở hai chỗ, cùng một hàm:

```
R4  hỏi nội dung      → tìm trang chứa câu trả lời, đưa cho LLM viết câu
R2  "quay lại chỗ..." → tìm trang cần nhảy tới
```

Đây **không phải** tầng trả lời. Nó chỉ trả về *trang nào liên quan*. Viết câu là việc của R4.

---

## 2. Vì sao phải hai nhánh, không phải một

Đo được trên chính deck này — dense một mình **trượt** thuật ngữ:

```
HỎI: "savefig"
CẦN: p11   (chứa plt.savefig('line_graph.png'))
dense: hạng 5, cos=0.254    ❌   lôi p8, p4 lên trước
BM25 : hạng 1, điểm 5.07    ✅
hybrid RRF: p11 TOP-1
```

Vector hiểu **Ý**, không nhìn **CHỮ**. `savefig` là thuật ngữ hiếm — không phải tiếng
Việt, không phải tiếng Anh thường — vector chỉ hiểu mang máng nên xếp nó ngang hàng với
mấy trang nói chung chung về matplotlib.

BM25 làm ngược lại: **không hiểu gì, chỉ đếm chữ**. Chữ nào hiếm thì tính điểm cao. Trang
duy nhất chứa `savefig` nhảy lên đầu ngay.

> **Điều kiện để BM25 cứu được: câu hỏi phải CHỨA cái từ hiếm đó.** Bản đầu của spec này
> lấy ví dụ *"làm sao lưu biểu đồ ra file ảnh"* và bảo BM25 sẽ kéo `p11` lên — **sai**.
> Câu đó không có chữ `savefig` nào, nên BM25 chẳng có gì để bắt, và đo thật thì cả
> hybrid lẫn dense đều trượt. Xem §10 để biết ca đó phải chữa bằng cách khác.

**Hai thằng hỏng theo hai kiểu khác nhau — đó chính là lý do ghép:**

| | mạnh khi | trượt khi |
|---|---|---|
| **Dense** (vector) | người hỏi dùng **từ khác** với slide | thuật ngữ hiếm, tên hàm, viết tắt |
| **Sparse** (BM25) | từ khoá **trùng khít** | người hỏi diễn đạt kiểu khác hẳn |

CLAUDE.md §5 S5 ghi thẳng:

> **Hybrid là bắt buộc, không phải tuỳ chọn.** Tiếng Việt lẫn thuật ngữ tiếng Anh là ca
> điển hình. Khán giả hỏi "BM25" mà chỉ có dense search thì rất dễ trượt.

Deck này đầy `matplotlib`, `plt.savefig`, `np.arange`, `ax.plot3D`, `cnames`, `Cartopy`.
Bỏ sparse là bỏ đúng loại từ mà người ta hay hỏi nhất.

> **Vì sao phải tự dựng BM25:** `bge-m3` cho sẵn sparse trong cùng một lần forward. v0 đã
> bỏ `bge-m3` để nhúng qua API, mà API **chỉ trả dense**. Đây là món nợ đã ghi ở
> [embedding.md §9](./embedding.md) — chỗ này là chỗ trả nợ.

---

## 3. Nhánh sparse: BM25

### Tách từ

Tiếng Việt viết rời từng âm tiết, nên tách theo khoảng trắng là đủ dùng:

```
"làm sao lưu biểu đồ ra file ảnh"
→ [làm, sao, lưu, biểu, đồ, ra, file, ảnh]
```

Chuẩn hoá trước khi tách:

```
hạ chữ thường                  Matplotlib → matplotlib
giữ nguyên dấu tiếng Việt      biểu ≠ bieu  (xem cảnh báo dưới)
tách dính liền của code        plt.savefig() → [plt, savefig]
                               np.arange     → [np, arange]
bỏ chữ quá ngắn (1 ký tự)      trừ chữ số
```

**Tách tên hàm ra là điểm mấu chốt.** Người hỏi gõ *"savefig"* chứ không gõ
*"plt.savefig('line_graph.png')"*. Không tách thì cả cụm là một token và không bao giờ khớp.

### Hạn chế phải biết trước

1. **Tách theo âm tiết, không phải theo từ.** "biểu đồ" là một từ hai âm tiết, BM25 coi
   là hai token rời. Hỏi *"biểu diễn"* sẽ khớp một phần với *"biểu đồ"* — nhiễu nhẹ.
   Có thư viện tách từ tiếng Việt (`underthesea`, `pyvi`) nhưng thêm phụ thuộc; để sau,
   khi đo được là nhiễu này gây hại thật.
2. **Gõ thiếu dấu là BM25 trượt SẠCH.** `bieu` ≠ `biểu`, không khớp gì cả. Dense còn vớt
   được mờ mờ, BM25 thì bằng không. Đã đo: gõ không dấu, cả 4 câu thử đều trượt.
   → Nếu kênh hỏi là form web (kế hoạch v1) thì phải tính tới chuyện người gõ không dấu.
   Cách xử: index **hai bản** — có dấu và bỏ dấu — chưa làm ở v0, ghi lại ở §9.
3. **Chunk 0 điểm KHÔNG được xếp hạng** (đã sửa, `src/kb/sparse/rankbm25.py`). Bản đầu lấy
   top-50 bất kể điểm: câu hỏi toàn từ hiếm thì gần hết pool là chunk 0 điểm, hoà nhau nên
   giữ thứ tự file — p1 hạng 2, p2 hạng 3… — và trang đầu deck ăn điểm RRF không vì lý do
   gì. Đo trên onboarding_kit: `VGRFOC`, `VGRDBIZ`, `kscv` trước ra **p1 (trang bìa) top-1**,
   sau khi sửa ra đúng trang. Câu hỏi thường không bị: luôn khớp vài chữ chung ("được",
   "bao nhiêu") nên không có chunk 0 điểm — bộ eval 93 câu không đổi một dòng.

---

## 4. Gộp hai nhánh: RRF, không phải cộng điểm

### Vì sao không cộng điểm

Hai thang đo không so được với nhau:

```
dense cho p11:   0.42     luôn nằm trong 0 → 1
BM25  cho p11:   8.31     to bao nhiêu tuỳ độ hiếm của chữ, không có trần
```

Cộng thẳng thì BM25 nuốt sạch dense. Nhân hệ số cho cân thì phải **tự đoán hệ số**, mà
deck khác là lệch lại — đúng kiểu §10 cấm: *"Đoán ngưỡng thay vì fit trên bộ eval"*.

### RRF — chỉ nhìn thứ hạng

Vứt điểm đi, chỉ hỏi *"mày xếp nó thứ mấy"*:

```
RRF(chunk) = Σ  1 / (K + hạng của chunk trong bảng đó)
           mọi bảng
K = 60
```

Số thật từ câu `"savefig"` ở §2:

```
                 dense xếp    BM25 xếp
p8                 2            9
p11                5            1

p8   = 1/(60+2) + 1/(60+9) = 0.0161 + 0.0145 = 0.0306
p11  = 1/(60+5) + 1/(60+1) = 0.0154 + 0.0164 = 0.0318   ← lên đầu
```

`p11` thắng vì **khá ở cả hai bên**, còn `p8` chỉ giỏi một bên. Đó đúng là hành vi mong muốn.

**K = 60** là hằng số gốc trong paper RRF (Cormack et al. 2009), không phải số bịa. Tác
dụng: làm khoảng cách hạng 1 ↔ hạng 2 không quá lớn, để một nhánh không tự tiện quyết hết.
Để trong config, không hardcode.

> Con số RRF **không phải xác suất, không phải độ tương đồng**. Nó chỉ dùng để XẾP THỨ TỰ.
> Cấm lấy nó làm confidence gate — gate phải lấy điểm reranker (§6, và §9 dưới đây).

---

## 5. Lọc trang phân mục

7 trang trong deck chỉ có mỗi tên chương đặt giữa trang, không nội dung: p9, 16, 22, 25,
29, 32, 35.

Đo được, chúng **cướp kết quả**:

```
HỎI: "đồ thị dạng đường là gì"
p9   0.753   ← trang trắng, chỉ có tiêu đề
p10  0.626   ← trang có 322 token nội dung thật
```

Trang trắng thắng **0.127 điểm**. Vì điểm là *độ giống trung bình*: `p9` không có chữ nào
làm loãng nên trùng khít câu hỏi; `p10` có đúng cụm đó nhưng chìm giữa 322 token.

Bật lọc, đo trên 4 câu thử: **1/4 → 3/4 top-1 đúng**.

**Mặc định LỌC**, cờ `--no-filter` để tắt mà so. Lọc chứ **không xoá vector** — lý do đầy
đủ ở [embedding.md §4](./embedding.md), tóm tắt: luật nhận diện trang phân mục là phỏng
đoán theo vị trí tiêu đề, sai thì lọc sửa trong vài giây còn xoá thì phải nhúng lại.

**Nhánh điều hướng (R2) phải TẮT lọc.** Hỏi *"quay lại phần đồ thị ba chiều"* thì `p32`
— trang mở chương — mới là đáp án đúng. Hai kiểu hỏi cần hai tập ứng viên khác nhau:

```
R4  hỏi nội dung      →  lọc      (cần trang có nội dung)
R2  hỏi điều hướng    →  KHÔNG lọc (trang mở chương là đích hợp lệ)
```

---

## 6. Trả về cái gì

Pydantic, không dùng dict trần (§9):

```python
class SearchHit(BaseModel):
    chunk_id: str
    page_no: int              # R2 nhảy tới đây
    section_id: str | None
    section_title: str | None
    score: float              # điểm RRF — CHỈ để xếp thứ tự
    rank_dense: int | None    # hạng ở nhánh vector, None = không lọt top
    rank_sparse: int | None   # hạng ở nhánh BM25
    score_dense: float        # cosine thô, để debug
    score_sparse: float       # BM25 thô, để debug
    text_enriched: str        # đưa thẳng cho LLM ở R4
    vlm_ratio: float          # bao nhiêu phần do model bịa ra (NT2)
```

**Giữ cả hạng lẫn điểm thô của từng nhánh.** Nhìn `rank_dense=11, rank_sparse=1` là biết
ngay kết quả này do BM25 kéo lên — chẩn đoán được. Chỉ trả mỗi điểm RRF thì mù.

`vlm_ratio` đi kèm vì NT2: R4 phải biết phần nội dung nó đang dựa vào là text layer (đúng
100%) hay mô tả do VLM sinh (có thể bịa).

**Nhiều chunk cùng một trang thì gộp.** Trang 34 có 3 chunk; trả về 3 dòng cùng trỏ p34 là
lãng phí top-k. Gộp theo `page_no`, giữ chunk **điểm cao nhất**.

> **KHÔNG cộng dồn điểm.** Bản đầu viết là cộng dồn và sai ngay lần chạy thử: trang 20 có
> 5 chunk, mỗi cái góp một tí rồi leo lên hạng 1, trong khi không chunk nào của nó vào nổi
> top-3 của cả hai nhánh. Cộng dồn là thưởng cho trang **nhiều mẩu**, mà số mẩu chỉ phản
> ánh trang đó lắm ảnh — chẳng liên quan gì tới câu hỏi.

---

## 7. Ngân sách thời gian

```
nhúng câu hỏi qua API     ~700ms   ← chiếm gần hết
nhân 52 vector             <1ms
BM25 đếm chữ               <1ms
gộp RRF + xếp              <1ms
                          ───────
                          ~700ms
```

Ngân sách runtime là **< 2.5s tới byte audio đầu** (NT1). Search ăn **28%**, và gần như
toàn bộ là một lần gọi mạng.

Hai chỗ tối ưu khi cần, chưa làm ở v0:

- **Chạy song song với LLM routing.** R3a sinh `query_expanded` bằng code (~5ms) → gọi
  embed API **cùng lúc** với lần gọi LLM, thay vì nối đuôi.
- **Cache câu hỏi.** `qa_cache` ngưỡng 0.88 ở §5 S6b đã tính chuyện này.

Dựng bảng BM25 mất ~10ms cho 52 chunk, làm **một lần lúc khởi động**, không phải mỗi truy vấn.

---

## 8. Chạy

```bash
python src/kb/search.py out/kb/3_datavisualization/chunks.json "làm sao lưu biểu đồ ra file ảnh"
```

| cờ | nghĩa |
|---|---|
| `-k N` | trả về N kết quả, mặc định 5 (khớp `top-k = 5` của §6) |
| `--dense-only` | chỉ vector |
| `--sparse-only` | chỉ BM25 |
| `--no-filter` | giữ cả trang phân mục — dùng cho nhánh R2 |
| `--explain` | in hạng và điểm thô của từng nhánh |

Ba cờ đầu tồn tại để **so được ba kiểu trên cùng câu hỏi**. Không có chúng thì không chứng
minh được hybrid hơn dense ở chỗ nào — chỉ còn nước tin lời người viết code.

---

## 9. Chưa làm — ghi ra để khỏi tưởng đã đủ

**Reranker.** §6 đòi `retrieve → rerank → LLM chọn`, và confidence gate phải lấy điểm
**reranker** chứ không lấy điểm tự khai. Nhưng `bge-reranker-v2-m3` là **model local
2.2 GB** — đúng thứ vừa bị gỡ khỏi máy. Mâu thuẫn chưa gỡ, ba đường:

```
a. chạy reranker local        đi ngược quyết định "không model local nào"
b. tìm rerank API             phải khảo sát, chưa biết endpoint hiện có cho không
c. bỏ rerank, gate bằng RRF   RRF không calibrate được → §10 cấm thẳng
```

Chưa chọn. Nhưng **không có reranker thì chưa dựng được confidence gate**, mà không có
gate thì §11 `harmful_jump < 2%` không đo được. Phải quyết trước khi làm R2.

**Multi-field.** §5 S6a đòi `v_message`, `v_title`, `v_desc`... riêng, §10 cấm gộp một
vector cho cả slide. Hiện mới có **một vector cho cả chunk**, vì `SlideRepr` là sản phẩm
của S1 mà S1 chưa có dòng code nào. Làm được sau khi có S1.

**Bỏ dấu.** Chưa index bản không dấu. Người gõ thiếu dấu thì BM25 về 0.

**Tách từ tiếng Việt.** Đang tách theo âm tiết. Xem §3.

---

## 10. Đã đo — và gate self-retrieval hiện KHÔNG đo được gì

`src/kb/audit.py` chạy hai bài. Kết quả lần đầu:

### Self-retrieval — 100%, và đó là tin xấu

| câu hỏi lấy từ | dense | BM25 | hybrid |
|---|---|---|---|
| `text_enriched` (nguyên văn) | 100% | 100% | 100% |
| `text_raw` (bỏ tiền tố) | 100% | 100% | 100% |
| `section_title` + 120 ký tự đầu | 97.8% | 100% | 97.8% |

Gate §11 đòi ≥ 90%. Đạt sạch. **Nhưng con số này gần như vô nghĩa.**

Lý do: cả ba biến thể đều lấy câu hỏi **từ chính chữ của tài liệu**. Hỏi bằng nguyên văn
trang 11 thì tất nhiên trang 11 khớp — nó so chính nó với chính nó. Bài thi mà đề bài là
đáp án.

§5 S6a viết gate này cho `message[i]` — **câu do S1 sinh ra**, tức một cách diễn đạt KHÁC
về cùng nội dung. Đó mới là phép thử thật: *"nói lại bằng lời khác thì có còn tìm ra không"*.
`message` chưa tồn tại vì S1 chưa có dòng code nào.

**Kết luận phải ghi rõ:** self-retrieval hiện tại chỉ chứng minh được **không có hai chunk
trùng nhau**. Nó KHÔNG chứng minh index tìm tốt. Đừng lấy con số 100% này báo cáo là đạt
gate §11 — gate đó chỉ có nghĩa sau khi có S1.

Thứ duy nhất nó bắt được ngay bây giờ là biến thể `title` rớt xuống 97.8%: một chunk mất
top-1 khi câu hỏi bị cắt ngắn. Dấu hiệu yếu, nhưng là dấu hiệu duy nhất có thật.

### Quét trùng lặp — bắt được lỗi thật

So từng cặp trong 52 vector (vector đã chuẩn hoá L2 nên tích vô hướng chính là cosine):

```
0.969   p024  ==  p024.b02     ← TRÙNG THẬT
0.950   p034  ==  p034.b02     ← TRÙNG THẬT
0.934   p027  ==  p028         ← hai trang KHÁC nhau, chỉ là nội dung gần nhau
```

Hai cặp đầu là lỗi trong `chunk.py`: chunk cả trang đã nuốt sẵn mô tả ảnh, rồi mô tả ảnh
đó **lại** được tách ra thành chunk phụ. Một nội dung nằm hai chỗ, chiếm hai suất trong
top-k, và người duyệt ở S7 phải đọc hai lần cùng một thứ.

Ngưỡng chốt **0.94**: 0.95 hụt mất `p034` (0.9498), 0.93 bắt oan `p027`/`p028` (0.9337).
Khe rất hẹp, và đây mới là deck **duy nhất** đo được — deck thứ hai vào là phải xem lại.

### Chạy

```bash
python src/kb/audit.py out/kb/3_datavisualization/chunks.json -o out/kb/3_datavisualization/audit/self_retrieval.json
python src/kb/audit.py out/kb/3_datavisualization/chunks.json --query-from title --show-fail
```

Thoát mã 1 khi top-1 dưới gate, để CI bắt được.

### Bộ câu hỏi có nhãn — `src/kb/eval.py`

`audit.py` tự sinh câu hỏi từ văn bản. `eval.py` chạy câu hỏi **người viết**, ở
`data/eval/queries.json`:

```bash
python src/kb/eval.py out/kb/3_datavisualization/chunks.json -o out/kb/3_datavisualization/audit/eval.json
python src/kb/eval.py out/kb/3_datavisualization/chunks.json --by nguoi
```

Hai bài **bổ sung** nhau, không thay được nhau:

```
audit  nhãn KHÔNG AI BỊA ĐƯỢC (chunk tự tìm chính nó)  ·  nhưng đề bài là đáp án
eval   câu hỏi SÁT THỰC TẾ                              ·  nhưng nhãn do người gán, dễ thiên vị
```

Mỗi câu khai `by: "ai" | "nguoi"`. 100% câu do AI viết thì `eval.py` in cảnh báo — số
đó là AI tự ra đề rồi tự chấm, không kết luận được gì.

**Kết quả đo được (7 câu, 2 câu do người thật viết):**

| | top-1 | top-5 |
|---|---|---|
| dense | 3/7 43% | 5/7 71% |
| sparse | 3/7 43% | 5/7 71% |
| **hybrid** | **4/7 57%** | 5/7 71% |

Hybrid hơn cả hai nhánh riêng — **bằng chứng thật đầu tiên** cho §2, không còn là lý thuyết.

Đáng lo: **top-5 bằng top-1 ở hai câu trượt** — trang đúng không có mặt trong cả 5 kết quả.
Nới `k` không cứu được.

### Ca trượt do câu hỏi quá nhiều chữ đệm

```
"cái dữ liệu hoa mà dùng để làm đồ thị nó nằm chỗ nào vậy"   →  TRƯỢT SẠCH
"dữ liệu hoa iris"                                          →  p20 TOP-1
"hoa"                                                       →  p20 TOP-1
```

Một chữ `hoa` là đủ. Thêm 14 chữ đệm vào thì hỏng: `dữ liệu`, `đồ thị` là từ khoá của
**cả deck** nên khớp 40/40 trang và kéo vector về mấy trang giới thiệu chung; `cái`, `mà`,
`nằm chỗ nào`, `vậy` thì không mang thông tin nào.

**Không phải lỗi của search.** §6 đã có sẵn tầng xử lý:

> **R3b — rewrite thật, trong lần gọi LLM duy nhất.** `query_rewritten` là tham số tool;
> `text` GỐC mới là thứ dùng để sinh câu trả lời.

LLM viết lại thành `"dữ liệu hoa Iris scatter plot"` rồi mới đem đi tìm. Ca này là bằng
chứng đo được rằng R3b **bắt buộc phải có**, không phải tính năng làm cho sang.

### File sinh ra

```
out/kb/3_datavisualization/audit/self_retrieval.json    duplicates[] + top-1/top-3 từng kiểu + caveat
out/kb/3_datavisualization/audit/eval.json              kết quả từng câu hỏi, từng kiểu
```

`search.py` **không ghi file** — nó là hàm chạy lúc runtime, trả `SearchHit[]` trong bộ
nhớ cho R4 dùng ngay, không phải stage offline sinh artifact.

Nghi ngờ cần soi riêng: 7 trang p9–p15 cùng tiêu đề *"Đồ thị dạng đường"*. Tiền tố
`· trang N/40` là thứ duy nhất tách chúng — có đủ không?

### Ca search KHÔNG chữa được

```
HỎI: "làm sao lưu biểu đồ ra file ảnh"     CẦN: p11
dense trượt · BM25 trượt · hybrid trượt
```

Câu hỏi nói tiếng Việt thuần (*lưu*, *file ảnh*), tài liệu chỉ có định danh tiếng Anh
(`savefig`). **Không có một chữ nào chung**, nên BM25 bằng 0; còn dense thì không bắc được
cầu từ khái niệm tiếng Việt sang tên hàm.

Đây **không phải lỗi của search**, và nới thêm `k` hay chỉnh RRF cũng vô ích. Chỗ hổng nằm
ở nội dung chunk: `p11` chỉ có mã nguồn và mô tả *hình vẽ trông thế nào*, không có câu nào
nói trang này **dùng để làm gì**.

Đó đúng là việc của **S1** (§5): sinh `message` — *"trang này muốn nói gì"*, tách bạch với
`description` — *"trang này vẽ gì"*. Một câu `message` kiểu *"lưu biểu đồ đã vẽ ra file ảnh
bằng plt.savefig"* là khớp ngay cả hai nhánh.

Ghi lại ở đây để sau khỏi đi sửa nhầm chỗ: **ca này chờ S1, không chờ search.**
