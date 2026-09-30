# Scenario — kịch bản robot nói cho từng trang (S4)

**Vào:** `out/parsed/<doc_id>/document.json` + kho phát âm chung `data/pronunciation.json`
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
                     3 trang gần nhất NGUYÊN VĂN, xa hơn chỉ câu mở đầu (PREV_FULL)
thuật ngữ            KHÔNG đưa kho phát âm vào prompt             ← chỉ dùng từ Anh CÓ TRÊN TRANG
                     (code kiểm: term_not_on_page)
provenance           ghi theo TỪNG khối (text_layer / vlm / manual) ← quyết định độ dè dặt (§7)
```

Không giới hạn "đã nói gì" thì section dài phình prompt: `sec_01` của Onboarding 23 trang,
trang cuối mang kịch bản 22 trang (~2k token) — nhồi cả chương, trái §10.

**Không** có `message`, `concept_map`, `time_budget` — đã bỏ (xem CLAUDE.md §5).

Prompt ước ~1.5–2.5k token mỗi trang. Chunk dài nhất 478 token.

---

## 4. Độ dài đi theo NỘI DUNG — bắt buộc ĐỦ Ý

**Không có trần độ dài cho trang có nội dung.** Trang ít chữ thì nói ngắn, trang nhiều chữ
thì nói dài, nhưng **không được bỏ ý nào** — người nghe phải hình dung được hết thông tin
trên trang. (Bản trước có trần 6 câu / trang nội dung, 4 câu / trang bài tập, và luật "chọn
vài ý chính" — đã bỏ: trang nhân sự 100–200 chữ nói trong 6 câu là mất ý.)

Nhãn lấy từ `ParsedPage.slide_type` (luật, CLAUDE.md §5 S1). Luật viết theo nhãn ở
`TYPE_RULES` (`generate.py`), trần số câu ở `MAX_SENTENCES` (`validate.py`):

| loại | viết | trần |
|---|---|---|
| `section_divider` | **nêu tên chương** + có thể một câu hỏi tu từ, **cấm câu `content`** | **2 câu** |
| lời kết | `section_divider` ở **trang cuối deck** ("THANK YOU!"): cảm ơn, mời hỏi, cấm tóm tắt, không kiểm tên chương | 2 câu |
| `exercise` | đọc lại ĐỦ yêu cầu / hạn nộp, cấm giảng, cấm gợi ý cách làm | không trần — đủ ý |
| `content` | nói **ĐỦ Ý**, gộp mục cùng loại thành câu nói, không đọc từng dòng | không trần — đủ ý |

**Đủ ý kiểm bằng code** (`validate.py`), không tin LLM tự khai:

```
block_not_covered   mỗi khối chữ mang thông tin (đoạn văn, danh sách, bảng, >= 3 chữ)
                    phải có >= 1 câu content trỏ `ref` vào
number_missing      MỌI con số trong khối chữ phải xuất hiện trong lời nói
                    so theo phần CÓ NGHĨA (bỏ số 0 đầu/cuối, dấu phân cách):
                    "08:00" khớp "8 giờ", "4.400.000" khớp "4,4 triệu" — đổi đơn vị được,
                    BỎ số thì không
```

Không bắt (thứ KHÔNG đọc thành tiếng — khán giả tự nhìn trên slide): link, email, số điện
thoại (0… / +84… / 1900…), mã tài liệu dạng chữ + số (`VSF_IT03`), số thứ tự đầu dòng.
Số trong khối `vlm` (bảng / danh sách máy đọc từ ảnh) **CŨNG phải nói** — xem §7 tầng 3.

**Giới hạn:** code chỉ thấy bỏ NGUYÊN khối và bỏ CON SỐ. Bỏ một ý chữ bên trong khối đã
nhắc tới thì code không thấy → việc của người duyệt S7.

`title` / `agenda` **không làm**: trang bìa bị viết như trang nội dung — chấp nhận, S7 sửa tay.

**Lời kết** không phải nhãn riêng mà suy ra lúc viết (`is_closing`): luật chuyển chương áp
lên trang "THANK YOU!" bắt robot nói *"sang chương THANK YOU"*.

`section_divider` là chỗ nguy hiểm nhất: nội dung trang chỉ có đúng tên chương. Bảo LLM
*"viết lời thuyết trình"* mà không ràng buộc thì nó **tự giảng** từ kiến thức nền — nghe
trơn tru nhưng không một chữ nào có trên slide. Trần 2 câu + cấm câu `content` là cách
chặn bằng code.

**Trang dày chữ** (deck nhân sự 100–200 chữ/trang, gấp 10 lần deck mục tiêu): LLM nhồi hết
vào vài câu 45–58 âm tiết — đo được ở p9 Thời gian làm việc. Đủ ý không có nghĩa là câu dài:
trần **30 âm tiết / câu vẫn giữ** (R7 chỉ ngắt ở ranh giới câu) — nhiều ý thì NHIỀU CÂU.
Lời nhắc pass 2 "TÁCH thành hai câu" là để chặn chỗ này.

**Ước thời lượng** — không có trần, nên tính theo số chữ trên slide (không tính tiêu đề,
mô tả ảnh), nói đủ ý xấp xỉ bằng số chữ:

```
Thời gian làm việc   17 trang   1.556 chữ   ≈ 8 phút
Onboarding           51 trang   9.112 chữ   ≈ 46 phút   (một trang 736 chữ ≈ 3–4 phút)
```

Đã chấp nhận (người dùng chốt): đủ ý quan trọng hơn ngắn.

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

Nhóm dài nhất quyết định thời gian: `sec_01` của Onboarding 23 trang × ~30–45s/lần gọi
(đo được ở deck nhân sự, gồm cả pass 2) ≈ 15–25 phút. Offline không có ràng buộc thời gian
(NT1) — chấp nhận, đổi lại trang sau thấy kịch bản trang trước.

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
- không chứa từ tiếng Anh / viết tắt nào, cũng không chứa từ có trong kho phát âm
  (trừ chữ trong tên chương — trang chuyển chương bắt buộc nêu tên chương)
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

§10: VLM **đo được là có sai** (chép `0x1675e5550` thành `0x1675e550`). Bản trước cấm
nói mọi con số từ block `provenance: vlm`.

**Đã đổi — người dùng chốt 2026-09-30: số đọc từ ảnh ĐƯỢC NÓI, chấp nhận rủi ro sai.**
Lý do: ở deck nhân sự, bảng số liệu hay là ẢNH — bảng hệ số làm thêm giờ (p7 Thời gian làm
việc: 1.5 / 2.1 / 2 / 2,7 / 3 / 3.9), tỉ lệ đóng bảo hiểm (p13), giờ check-in (Onboarding
p20). Cấm số thì các trang đó mất hết nội dung, trái luật ĐỦ Ý (§4).

- số từ block `vlm` được nói và **phải nói đủ** (`number_missing` tính cả block `vlm`)
- câu nêu số từ block `vlm` mang cờ **vàng** `vlm_number` — chỉ đánh dấu để ai muốn đối
  chiếu với ảnh thì biết câu nào, KHÔNG bắt pass 2
- vẫn cấm nêu tên riêng / dịp lễ mà chữ trên slide không nhắc — đó là VLM đoán bối cảnh ảnh
- block `provenance: manual` (vá tay) tin như `text_layer`, không mang cờ

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

Từ tiếng Anh / viết tắt xuất hiện trong câu mà **không có** trong kho → đếm ước lượng
(viết tắt theo cách đánh vần, từ Anh theo cụm nguyên âm) + cờ `unknown_pronunciation`.
Không được đếm bừa là 1 — đó là cách timing lệch mà không ai thấy.

Kho sinh **SAU** kịch bản: `scripts/extract_terms.py` gom các từ lạ này vào kho chung, người
chốt cách đọc, chạy lại S4 thì chỉ đếm lại. Xem [pronunciation.md](./pronunciation.md).

Ghi `pronunciation_hash` vào `scenario.json` — hash CHỈ các mục kịch bản này dùng. S6b so
hash trước khi synth; lệch là **DỪNG** — S4 đếm một đằng, TTS đọc một nẻo.

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

**Pass 2 không mặc nhiên tốt hơn.** Sửa lỗi này có thể đẻ lỗi khác, kể cả cờ đỏ → giữ bản
**ít lỗi hơn** (so cờ đỏ trước, lỗi thường sau; bằng nhau thì lấy pass 2).

**Lời nhắc pass 2 phải nói CÁCH sửa, không chỉ nói sai gì.** Đo ở p9 Thời gian làm việc:
nhắc "câu 2 có 45 âm tiết, tối đa 30" thì pass 2 vẫn ra 39; nhắc "delivery 7%, cần 10%" thì
LLM lờ đi. Nay nhắc kèm cách: "TÁCH thành hai câu", "thêm một câu delivery giữa trang", và
nhịp thì đưa SỐ ÂM TIẾT từng câu — LLM không tự đếm được.

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
| độ lệch chuẩn âm tiết/câu (trang ≥ 4 câu) | ≥ 6 | pass 2 → 🟡 `monotone_rhythm` (trước là chỉ cờ vàng: 23/28 và 8/8 trang trượt mà không ai sửa) |
| từ văn viết bị cấm (`việc`/`sự` danh từ hoá — trừ từ ghép: làm/thử/công việc, nhân sự, sự cố…) | = 0 | pass 2 → 🟡 `written_register` |
| câu `delivery` mang số / thuật ngữ / tên hàm | = 0 | pass 2 |
| câu có ký hiệu code `(` `=` `[` | = 0 | pass 2 |
| câu viết kiểu slide: `/` `%` `&` `+` `<` `>` `@`, số dính chữ `44h` `75tr` | = 0 | pass 2 → 🟡 `written_symbol` |
| từ Anh / viết tắt không có trên trang | = 0 | pass 2 → 🟡 `term_not_on_page` |
| từ Anh / viết tắt không có trong kho phát âm | = 0 | 🟡 `unknown_pronunciation` |
| số câu — CHỈ trang chuyển chương / lời kết | ≤ 2 | pass 2 |
| mỗi khối chữ mang thông tin có câu content trỏ vào | 100% | pass 2 → 🟡 `block_not_covered` |
| mọi con số trong khối chữ (trừ link, sđt, mã, khối vlm) được nói | 100% | pass 2 → 🟡 `number_missing` |
| `section_divider` có câu `content` | = 0 | 🔴 |
| `section_divider` không nêu tên chương | = 0 | pass 2 |
| câu content trỏ vào **khối tiêu đề** mà dài hơn tiêu đề > 6 âm tiết | = 0 | 🔴 `title_grounded_claim` |
| câu content trỏ vào khối `vlm` mà có số / khoảng "từ … đến" | báo cáo | 🟡 `vlm_number` (được nói — §7 tầng 3) |
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
python src/scenario/cli.py out/parsed/3_datavisualization/document.json
python src/scenario/cli.py out/parsed/3_datavisualization/document.json --page 11     # một trang
python src/scenario/cli.py out/parsed/3_datavisualization/document.json --dry-run     # in prompt, không gọi API
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

**Chạy hỏng giữa chừng không mất tiền đã trả:**

- `scenario.json` ghi lại **sau MỖI trang** (ghi file tạm rồi thay — dừng giữa lúc ghi
  không hỏng file cũ). Chạy lại là đi tiếp: trang đã viết khớp prompt/model thì dùng lại.
- API sập (đo được: 503 *"temporarily unavailable"* cả loạt) → trang đã có kịch bản **giữ
  bản cũ** + cờ `llm_failed`, KHÔNG ghi đè bằng bản rỗng. Chờ giữa các lần thử 5+10+20s.
- LLM trả giá trị hỏng (`"pause_before_ms": "300ms"`, `sentences` không phải mảng) → lấy
  mặc định / bỏ câu đó, không làm sập cả deck. `speed` kẹp về 0.8–1.2, `pause` về 0–1500ms,
  `emphasis` không nằm trong câu thì bỏ.

---

## 15. Đo gì

```
ungrounded_rate (câu content)   < 10%       gate §11
tỉ lệ delivery                  10–25%      gate §11
độ lệch chuẩn âm tiết/câu       >= 6        gate §11
từ văn viết bị cấm              = 0         gate §11
tổng thời lượng                 báo cáo     (không có trần — độ dài theo nội dung, §4)
số trang phải chạy pass 2       báo cáo     cao = prompt tệ
số cờ đỏ                        báo cáo     phải = 0 mới đóng gói
```

**Naturalness MOS ≥ 3.8** không đo ở đây — cần người ngồi nghe, là việc của S7 sau S6b.

---

## 16. Đã quyết

- **LLM: `gpt-5-mini`** — gọi qua endpoint đang dùng, JSON mode chạy. ~15–25s mỗi trang
  deck ít chữ; ~30–45s mỗi lần gọi ở trang deck nhân sự dày chữ
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
