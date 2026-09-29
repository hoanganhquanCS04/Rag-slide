# Scenario — kịch bản robot nói cho từng trang (S4)

**Vào:** `out/kb/<doc_id>.chunks.json` + `out/parsed/<doc_id>/document.json` + `out/deck/<doc_id>/pronunciation.json`
· **Ra:** `out/deck/<doc_id>/scenario.json` · **Code:** `src/scenario/` · **LLM:** `gpt-5-mini`
· **Prompt:** `prompts/s4_scenario.md`

---

## 1. Để làm gì

Mỗi trang trong deck cần một đoạn lời để robot **nói ra khi chiếu trang đó**. Đoạn lời
này sinh **một lần ở offline**, người duyệt ở S7, rồi TTS thành file âm thanh ở S6b.

```
trang 11  →  "Vẽ xong rồi thì lưu lại kiểu gì?
              Gọi savefig kèm tên file, matplotlib ghi biểu đồ ra ảnh PNG.
              Muốn ảnh nét hơn thì thêm tham số dpi."
```

Vì sinh một lần rồi phát mãi, **sai ở đây là sai ở MỌI buổi thuyết trình** (NT3). Toàn bộ
spec này xoay quanh một câu hỏi: *làm sao để LLM viết lời tự nhiên mà KHÔNG bịa.*

---

## 2. Lưu ở đâu — file riêng, không nhét vào metadata

```
out/deck/<doc_id>/scenario.json      ← kịch bản, một file cho cả deck
```

**Không** để trong `ParsedDocument`, **không** để trong `KBChunk`. Lý do:

```
ParsedDocument   SUY RA được từ PDF        parse lại là có
scenario.json    LLM sinh + NGƯỜI duyệt    mất là phải trả tiền + duyệt lại
```

Trộn hai loại vào một file thì mỗi lần parse lại là đe doạ kịch bản — đúng cái bẫy đã
dính với trang 15, phải đẻ ra `data/patches/` để nội dung gõ tay sống sót.

**Lưu tách, xem chung:** `src/scenario/cli.py <parsed> --show --page 11` in kịch bản của trang,
mỗi câu content kèm `block_id` nó trỏ tới — mở `parsing/cli.py --page 11` cạnh bên là đối chiếu được.

---

## 3. Input mỗi trang — CHỈ trang đó, không nhồi cả deck

```
nội dung trang       các khối của ParsedPage, kèm id + provenance ← nguồn DUY NHẤT của sự thật
                     (lấy khối chứ không lấy KBChunk: câu content phải trỏ `ref` vào đúng block_id)
loại trang           slide_type                                   ← quyết định độ dài
trang trước / sau    CHỈ title                                    ← để viết câu chuyển
đã nói gì trước đó   kịch bản các trang TRƯỚC trong cùng section  ← chống lặp (xem §5)
thuật ngữ            danh sách key của pronunciation.json         ← chỉ dùng từ đã có cách đọc
vlm_ratio            bao nhiêu phần nội dung do VLM sinh          ← quyết định độ dè dặt (§7)
```

**Không** có `message`, `concept_map`, `time_budget` — đã bỏ (xem CLAUDE.md §5).

Prompt ước ~1.5–2.5k token mỗi trang. Chunk dài nhất 478 token.

---

## 4. `slide_type` quyết định độ dài

| loại | trang (deck này) | viết | trần |
|---|---|---|---|
| `section_divider` | p9 16 22 25 29 32 35 | **nêu tên chương** + có thể một câu hỏi tu từ, **cấm câu `content`** | **2 câu** |
| `content` | 33 trang còn lại | giảng nội dung trang | 6 câu |

**Chỉ hai loại — đã chốt.** `title` / `agenda` / `exercise` **không làm**: 5 trang (p1, p2,
p38–40) bị viết như trang nội dung. Hệ quả chấp nhận: 3 trang bài tập có thể bị giảng thay
vì chỉ đọc yêu cầu → người duyệt S7 sửa tay. Không đáng thêm luật cho 5/40 trang.

`section_divider` là chỗ nguy hiểm nhất: nội dung trang chỉ có đúng tên chương. Bảo LLM
*"viết lời thuyết trình"* mà không ràng buộc thì nó **tự giảng** từ kiến thức nền — nghe
trơn tru nhưng không một chữ nào có trên slide. Trần 2 câu + cấm câu `content` là cách
chặn bằng code. Nhãn lấy từ `is_section_divider()` ở `src/kb/chunk.py`, đã có sẵn.

**Ước thời lượng** (không có `time_budget` — trần câu là cái phanh duy nhất):

```
28 content × ≤6 câu × ~18 âm tiết  ≈ 3.000 âm tiết
12 trang còn lại                    ≈   300 âm tiết
                                    ─────────────
                                    ≈ 3.300 âm tiết ÷ 200/phút ≈ 16–17 phút (trần trên)
```

Trần là **hằng số trong config**, sửa một dòng là đổi. Không có nó thì LLM viết 15 câu một
trang, 40 trang thành hơn một tiếng.

---

## 5. Chạy song song theo SECTION, tuần tự TRONG section

§9 đòi S4 song song hoá được. Nhưng deck này có 7 trang liền nhau cùng tên
*"Đồ thị dạng đường"* — viết độc lập từng trang thì robot mở đầu y hệt nhau bảy lần.

Cách gỡ:

```
sec_00  p9 → p10 → p11 → ... → p15     ┐
sec_01  p16 → p17 → ... → p21          │  8 nhóm chạy SONG SONG
...                                     │  (mặc định 5 luồng)
mở đầu  p1 → p2 → ... → p8             ┘
         ─────────────────►
         trong nhóm: TUẦN TỰ, trang sau thấy kịch bản trang trước
```

Trang p11 được xem kịch bản đã viết của p9, p10 → biết p10 đã giới thiệu đồ thị đường là
gì, nên p11 đi thẳng vào chuyện lưu file. Đây là thứ thay cho `message` đã bỏ — và rẻ hơn,
vì dùng lại chữ vừa sinh chứ không gọi thêm model.

Nhóm dài nhất 8 trang. Ước ~8 × 3s ≈ 25 giây cho cả deck.

---

## 6. Hai loại câu — cốt lõi của S4

CLAUDE.md §2 NT4. Mỗi câu khai `kind`:

```
content    mang THÔNG TIN (sự thật)     grounding BẮT BUỘC trỏ vào block/chunk
                                         grounding null → CỜ ĐỎ
delivery   dẫn dắt, chuyển ý, hỏi tu từ  KHÔNG được mang sự thật mới
                                         validate BẰNG CODE
```

```
[delivery]  "Vẽ xong rồi thì lưu lại kiểu gì?"                     ← không có sự thật nào
[content]   "Gọi savefig kèm tên file, matplotlib ghi ra ảnh PNG."  → p011.b01
[content]   "Muốn ảnh nét hơn thì thêm tham số dpi."                 → p011.b01
```

**Tỉ lệ `delivery`: 10–25% tổng âm tiết**, 10% là sàn cứng. Dưới sàn thì robot nghe như
đọc bản tin — NT4 coi đó là **thất bại**, không phải điểm trừ.

**Kiểm câu `delivery` không lén mang sự thật** — bằng code, không tin LLM:
- không chứa số (trừ số thứ tự trang/chương)
- không chứa thuật ngữ nào trong `pronunciation.json`
- không chứa tên hàm

---

## 7. Chống bịa — bốn tầng

**Tầng 1 — nguồn duy nhất là trang đó.** Prompt cấm kiến thức ngoài trang. CLAUDE.md §3.0:
*"Tuyệt đối không để LLM tự phình kiến thức từ slide ra để bù."*

**Tầng 2 — `grounding` trỏ vào `block_id` có thật.** Code kiểm: id phải tồn tại trong
`ParsedDocument`, và phải thuộc **đúng trang đang viết**. Trỏ sang trang khác → cờ.

**Tầng 3 — dè dặt theo `vlm_ratio`.** Deck này:

```
vlm_ratio = 0     12 chunk   toàn text layer — đúng 100%, nói chắc được
0 < x < 1         22 chunk   lẫn
vlm_ratio = 1     11 chunk   TOÀN mô tả do VLM sinh
```

§10: VLM **đo được là có sai** (chép `0x1675e5550` thành `0x1675e550`). Nên với câu
`content` trỏ vào block `provenance: vlm`:

- **cấm khẳng định con số đọc từ biểu đồ** (*"lương cao nhất 120 nghìn đô"*)
- được nói **hình dạng, xu hướng** (*"lương tăng dần theo tuổi"*)
- block `provenance: manual` (trang 15 vá tay) tin như `text_layer`

**Tầng 4 — người duyệt S7**, chỉ phần bị cờ (§10).

---

## 8. Viết cho TAI, không cho mắt

Bảy đòn bẩy ở CLAUDE.md §5 S4, rút gọn cho deck này:

| # | luật | kiểm bằng code? |
|---|---|---|
| 1 | Văn nói: cấm `việc…`, `sự…`, `được thực hiện bởi`, danh từ hoá | ✅ danh sách từ cấm |
| 2 | Nhịp: trộn câu ngắn/vừa/dài, **độ lệch chuẩn âm tiết ≥ 6** | ✅ |
| 3 | `prosody` mỗi câu: `emphasis[]`, `pause_before_ms`, `speed` | ✅ có field |
| 4 | Từ diễn ngôn đặt ở ranh giới (đầu trang, trước tương phản) | ⚠️ một phần |
| 5 | Câu hỏi tu từ ở trang `section_divider` | ✅ |
| 6 | **Không đọc code thành tiếng** — chỉ nói tên hàm và nó làm gì | ✅ cấm `(` `=` `.` trong câu |
| 7 | **Tối đa 30 âm tiết/câu** — R7 chỉ ngắt được ở ranh giới câu | ✅ |

Luật 6 quan trọng với deck này: trích được 41 tên hàm khác nhau. Người thật không đứng lớp
đọc *"pi-eo-ti chấm ép-rờ-bo mở ngoặc ích phẩy i"* — họ nói *"gọi errorbar, truyền thêm sai
số trục y"*.

---

## 9. Đếm âm tiết theo `pronunciation.json`

Tiếng Việt 190–210 âm tiết/phút. **Đếm âm tiết, không đếm từ**, và thuật ngữ đếm **theo
cách đọc**:

```
"Gọi savefig kèm tên file"
  Gọi(1) savefig→"sếp phích"(2) kèm(1) tên(1) file(1)   = 6 âm tiết, không phải 5
```

Từ tiếng Anh xuất hiện trong câu mà **không có** trong bảng → cờ `unknown_pronunciation`.
Không được đếm bừa là 1 — đó là cách timing lệch mà không ai thấy.

Ghi `pronunciation_hash` vào `scenario.json`. S6b so hash trước khi synth; lệch là
**DỪNG** — S4 đếm một đằng, TTS đọc một nẻo.

---

## 10. Hai pass

```
pass 1   LLM viết kịch bản cho trang               ← mọi trang
            │
         [validate bằng code — §11]
            │
         đạt ───────────────────────────► ghi
            │
         không đạt
            │
pass 2   LLM sửa, kèm DANH SÁCH LỖI cụ thể         ← chỉ trang trượt
         "câu 3 có 34 âm tiết, tối đa 30"
         "delivery chiếm 6%, cần >= 10%"
            │
         [validate lại]
            │
         vẫn trượt ─► ghi kèm CỜ cho S7, không thử lần 3
```

Pass 2 ở bản cũ dùng để **cân giờ** theo `time_budget`. Đã bỏ `time_budget`, nên pass 2
thành **pass sửa lỗi**, chỉ chạy cho trang trượt.

**Thứ tự cắt khi quá dài:** trùng lặp ở câu `content` TRƯỚC, câu `delivery` SAU CÙNG. Sàn
10% delivery là cứng — không có luật này thì sửa vài vòng là kịch bản khô lại.

---

## 11. Validate — code quyết, không tin LLM

| kiểm | ngưỡng | trượt thì |
|---|---|---|
| câu `content` có `grounding` | 100% | 🔴 `ungrounded_content_sentence` |
| `grounding` trỏ vào block có thật, đúng trang | 100% | 🔴 `bad_grounding_ref` |
| âm tiết mỗi câu | ≤ 30 | pass 2 |
| tỉ lệ âm tiết `delivery` | 10–25% | pass 2 |
| độ lệch chuẩn âm tiết/câu (trang ≥ 4 câu) | ≥ 6 | 🟡 `monotone_rhythm` |
| từ văn viết bị cấm | = 0 | pass 2 → 🟡 `written_register_hit` |
| câu `delivery` mang số / thuật ngữ / tên hàm | = 0 | pass 2 |
| câu có ký hiệu code `(` `=` `[` | = 0 | pass 2 |
| từ Anh không có trong `pronunciation.json` | = 0 | 🟡 `unknown_pronunciation` |
| số câu | ≤ trần theo `slide_type` | pass 2 |
| `section_divider` có câu `content` | = 0 | 🔴 |
| `section_divider` không nêu tên chương | = 0 | pass 2 |
| câu content trỏ vào **khối tiêu đề** mà dài hơn tiêu đề > 6 âm tiết | = 0 | 🔴 `title_grounded_claim` |
| câu content trỏ vào khối `vlm` mà có số / khoảng "từ … đến" | = 0 | pass 2 `vlm_number` |
| nói VỀ SLIDE: "trang này dạy", "mô tả cho biết", "trong ảnh"… | = 0 | pass 2 `meta_talk` |
| câu delivery đệm: "hãy chú ý", "lắng nghe", "tiếp theo thôi"… | = 0 | pass 2 `filler_delivery` |

🔴 = cờ đỏ, **không đóng gói được** cho tới khi người duyệt xử lý.

---

## 12. Schema

```python
class Prosody(BaseModel):
    emphasis: list[str] = []          # từ cần nhấn
    pause_before_ms: int = 0
    speed: float = 1.0                # 0.9 chậm lại, 1.1 nhanh lên

class Grounding(BaseModel):
    type: Literal["kb_chunk", "structure", "style"]
    ref: str | None = None            # block_id, vd "p011.b01"; None với structure/style

class Sentence(BaseModel):
    kind: Literal["content", "delivery"]
    text: str
    grounding: Grounding | None       # content mà None → CỜ ĐỎ
    syllables: int                    # CODE tính theo pronunciation.json, KHÔNG để LLM tự khai
    prosody: Prosody = Prosody()

class SlideScript(BaseModel):
    page_no: int
    page_hash: str                    # khớp ParsedPage.page_hash -> incremental
    slide_type: str
    sentences: list[Sentence]
    flags: list[str] = []
    passes: int = 1                   # 1 hay 2 — trang cần sửa là trang đáng soi

    @property
    def syllables(self) -> int: ...
    @property
    def seconds(self) -> float: ...   # syllables / 200 * 60

class Scenario(BaseModel):
    doc_id: str
    model: str                        # LLM đã dùng
    prompt_hash: str                  # đổi prompt -> kịch bản cũ lạc hậu
    pronunciation_hash: str           # S6b so trước khi synth
    slides: list[SlideScript]
```

**`syllables` do code tính, không để LLM khai.** LLM đếm âm tiết tiếng Việt sai có hệ
thống, và nó không biết `savefig` đọc thành mấy âm tiết.

---

## 13. Incremental

```
page_hash không đổi + prompt_hash không đổi   → giữ nguyên, không gọi LLM
page_hash đổi                                  → viết lại trang đó
                                                 + trang KỀ SAU (câu chuyển nhắc tới nó)
                                                 + các trang sau trong CÙNG section
                                                   (chúng đã đọc kịch bản trang này ở §5)
prompt_hash đổi                                → viết lại tất cả
pronunciation_hash đổi                         → KHÔNG gọi LLM, chỉ đếm lại âm tiết
```

Dòng thứ ba là hệ quả của §5: trang sau đọc kịch bản trang trước, nên sửa trang trước là
trang sau có thể lặp ý. Lan tới hết section là an toàn.

---

## 14. Chạy

```bash
python src/scenario/cli.py out/kb/3_datavisualization.chunks.json
python src/scenario/cli.py out/kb/3_datavisualization.chunks.json --page 11     # một trang
python src/scenario/cli.py out/kb/3_datavisualization.chunks.json --dry-run     # in prompt, không gọi API
```

| cờ | nghĩa |
|---|---|
| `--page N` | chỉ viết trang N (vẫn nạp kịch bản các trang trước cùng section) |
| `--model` | LLM dùng, mặc định trong config |
| `--dry-run` | in prompt ra xem, không tốn tiền |
| `--force` | bỏ qua incremental, viết lại hết |
| `--workers` | số luồng, mặc định 5 |

Log **full prompt + response** vào `logs/s4/` (§9) — kịch bản sai thì phải truy được LLM đã
thấy gì.

---

## 15. Đo gì

```
ungrounded_rate (câu content)   < 10%       gate §11
tỉ lệ delivery                  10–25%      gate §11
độ lệch chuẩn âm tiết/câu       >= 6        gate §11
từ văn viết bị cấm              = 0         gate §11
tổng thời lượng                 báo cáo     (không còn time_budget để so)
số trang phải chạy pass 2       báo cáo     cao = prompt tệ
số cờ đỏ                        báo cáo     phải = 0 mới đóng gói
```

**Naturalness MOS ≥ 3.8** không đo ở đây — cần người ngồi nghe, là việc của S7 sau S6b.

---

## 16. Đã quyết

- **LLM: `gpt-5-mini`** — gọi qua endpoint đang dùng, JSON mode chạy. ~15–25s mỗi trang
  (model có bước suy luận), không đặt `temperature` (gpt-5 chỉ nhận mặc định).
- **Có cho LLM xem ảnh trang không.** Hiện chỉ đưa chữ. Đưa ảnh thì viết sống hơn nhưng mở
  cửa cho VLM đọc lại số từ pixel — đúng thứ §10 cấm. Mặc định: **không**.

---

## 17. Đo được khi chạy thử — 3 vòng trên p9, p10, p11, p20

| vòng | chuyện gì xảy ra | sửa |
|---|---|---|
| 1 | Gate đạt hết, nhưng đọc lên toàn **nói về slide**: *"Trang dạy rằng…"*, *"Thuật ngữ ở đây là…"*. Nhịp đều đều (5.2) | prompt: nói về chủ đề, không nói về slide · bộ kiểm `meta_talk` |
| 2 | Hết "trang này dạy" — nhưng **câu ví dụ "ĐÚNG" trong prompt bị LLM chép nguyên vào p20**, trỏ `ref` vào khối tiêu đề. Kiểm `ref` vẫn qua vì khối có thật | bỏ ví dụ có nội dung khỏi prompt · kiểm `title_grounded_claim` (🔴) |
| 2 | Cấm delivery chứa thuật ngữ → LLM lùi về câu đệm: *"Hãy chú ý và lắng nghe…"* | ví dụ delivery tốt + kiểm `filler_delivery` |
| 2 | Luật "phải có câu ngắn" → LLM **bịa câu content rỗng** cho đủ nhịp: *"Các điểm nối với nhau."* | bỏ luật cứng, chỉ khuyên câu ngắn nên là delivery |
| 2 | Đọc khoảng giá trị trục từ mô tả VLM: *"từ 0 đến gần 10"* | kiểm `vlm_number` |
| 3 | Đạt 4 gate, 0 cờ đỏ. Còn *"Mô tả cho biết…"*, *"Trong ảnh…"* | thêm vào `meta_talk` |

**Hai bài học phải nhớ:**

1. **Ví dụ trong prompt là nội dung.** LLM chép ví dụ "đúng" vào output như thể đó là sự
   thật của trang. Ví dụ trong prompt S4 chỉ được dùng chỗ giữ chỗ (`<chủ đề>`), hoặc câu
   KHÔNG mang thông tin (câu delivery).
2. **Ép chỉ số thì model lách chỉ số.** Luật "phải có câu < 10 âm tiết" sinh ra câu rỗng
   đúng 5 âm tiết. Luật "delivery không chứa thuật ngữ" sinh ra câu đệm. Mỗi luật cứng
   phải đi kèm một bộ kiểm cái lách của nó.

**Giới hạn còn lại:** kiểm `ref` chỉ biết khối có tồn tại và có đúng loại không — **không**
biết câu có thật sự được khối đó chứng minh. Bắt được ca trỏ vào tiêu đề, không bắt được ca
trỏ vào khối nội dung mà nói lệch ý. Chỗ đó là việc của người duyệt S7.
