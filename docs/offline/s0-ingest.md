# S0 — Ingest

**Input:** `deck.pptx`
**Output:** `RawSlide[]` + `render/s{n}.png`
**Model:** không có. Thuần parsing.

> **v0 KHÔNG chạy theo file này** — file này là thiết kế đích cho `.pptx`. v0 nhận **PDF**
> (hoặc `.pptx` kèm `.pdf` cùng tên), parse bằng `docling` (tắt OCR), rồi VLM **qua API**
> nhìn ảnh CẢ trang để sắp mẩu chữ thành khối (chỉ trỏ id, chữ vẫn là text layer) và chép
> bảng từ ảnh bảng. Output là `ParsedDocument`, không phải `RawSlide[]`.
> Luồng v0: [00-overview §3.3](./00-overview.md) · [01-hien-trang](./01-hien-trang.md) ·
> code: [`src/parsing/`](../../src/parsing/) · spec: [parsed-document.md](../spec/parsed-document.md)
>
> Mất so với bản pptx dưới đây: `chart_data`, `tables`, `build_steps` — chúng đến từ
> XML mà PDF không có. Hệ quả cho NT2 xem [CLAUDE.md §3.0](../../CLAUDE.md).

---

## 1. S0 làm gì

Bú XML của pptx ra text run, toạ độ shape, grouping, thứ tự animation, speaker notes;
đồng thời render mỗi slide thành PNG. Gom thành `RawSlide[]`.

**Giữ hai biểu diễn song song, cố ý:**

| Biểu diễn | Chính xác về | Mù về |
|---|---|---|
| Structured (XML) | text, số liệu, toạ độ, cấu trúc | bố cục thị giác thật, cái mắt người thấy |
| Visual (PNG render) | đúng thứ khán giả nhìn | không đọc được số, không biết đâu là title |

S1 cần cả hai: PNG để hiểu ý nghĩa thị giác, XML để không phải đoán chữ và số.

**Chưa OCR, chưa model gì cả.** Nếu slide có ảnh chụp chứa chữ (screenshot, ảnh scan
bảng số) thì S0 **không đọc chữ trong đó** — chỉ lôi ảnh ra và ghi nhận "đây là một ảnh
raster". Đọc nội dung ảnh là việc của S1 (VLM). Phải ý thức rằng ở S0, **text trong ảnh
coi như chưa tồn tại**.

---

## 2. Bắt buộc phải có

### 2.1 Reading order ≠ shape order

Thứ tự shape trong XML là thứ tự **tác giả tạo ra**, không phải thứ tự đọc. Người ta hay
tạo textbox rồi kéo lung tung. Phải **sort lại theo bbox** (trên→dưới, trái→phải, có gom
cột) rồi mới lưu, và ghi `reading_order` vào từng run.

Không làm là câu chữ đảo lộn, kịch bản ở S4 sai mạch.

### 2.2 Hidden slides

pptx cho phép ẩn slide (`show="0"`). Ẩn nghĩa là **không trình chiếu**. Phải lọc bỏ hoặc
ít nhất đánh dấu `visible: false` và không đánh số vào chuỗi trình chiếu.

Không lọc thì đánh số trang lệch — khán giả nói "trang 15", hệ thống nhảy sang trang khác.

### 2.3 Bảng (table)

Trong pptx bảng là `graphicFrame`, lấy ra được **cấu trúc ô thật** — hàng, cột, merge.
Đừng để nó rơi vào `text_runs` thành đống chữ rời.

Deck "ít chữ nhiều hình" rất hay có bảng so sánh, và đó thường là trang khán giả hỏi
nhiều nhất.

### 2.4 Chart data lấy từ XML, không lấy từ pixel

Chart native trong pptx chứa **đúng series data**. Lấy thẳng từ đó. Tuyệt đối đừng để
VLM đọc số từ ảnh biểu đồ khi số nằm sẵn trong file.

Đây là khác biệt giữa chính xác 100% và chính xác 70%.

Nếu chart thực ra là **ảnh dán vào** (không phải chart native) → `charts[].from_xml: false`,
`chart_data: null`, và **sinh flag** `chart_not_from_xml`. S1 sẽ phải đoán số từ ảnh, và
S7 phải duyệt.

### 2.5 Chuẩn hoá toạ độ

XML dùng **EMU** (914400 EMU = 1 inch). PNG dùng pixel. Hai hệ khác nhau → VLM không đối
chiếu được bbox với ảnh.

Normalize mọi bbox về `[0,1]` theo chiều slide, lưu kèm `slide_size` (EMU) để quy đổi ngược.

```
bbox_norm = [x/W, y/H, (x+w)/W, (y+h)/H]
```

### 2.6 Placeholder type

Từ layout: `title` / `ctrTitle` / `subTitle` / `body`. Phân biệt được **tiêu đề thật** với
một textbox chữ to trông giống tiêu đề.

Miễn phí, và giúp S1 chính xác hơn hẳn. `title` trong `SlideRepr` lấy từ đây, không để VLM sinh.

---

## 3. Nên có

| # | Thứ | Vì sao |
|---|---|---|
| 7 | **Layout name / slide master** | "Title Slide", "Section Header", "Two Content", "Comparison" — nhãn `slide_type` miễn phí. S1 không cần đoán, chỉ cần verify |
| 8 | **Native sections của pptx** | PowerPoint có section thật. Tác giả đã chia rồi thì S2 gần như xong sẵn, chỉ verify thay vì suy luận từ đầu |
| 9 | **SmartArt** | Là `graphicFrame` có drawing fallback. Text lấy được nhưng quan hệ phân cấp thì nát. Rất phổ biến trong slide Việt Nam. Đánh `has_smartart: true` để S1 nhìn ảnh kỹ hơn |
| 10 | **Group lồng nhau** | Group có thể lồng nhiều tầng → duyệt **đệ quy**, không thì mất shape con. Giữ `group_path` vì **nhóm = quan hệ ngữ nghĩa**: 3 khối trong một group thường là một cụm khái niệm |
| 11 | **Ảnh bị crop / transform** | Ảnh trong `ppt/media/` là bản **gốc**; cái hiển thị có thể đã crop, xoay, lật. Đưa file gốc cho VLM là nó nhìn khác cái khán giả thấy → **cắt vùng ảnh từ PNG render theo bbox** |
| 12 | **Hyperlink** | Link trong slide thường chính là tài liệu nguồn → gợi ý luôn cho S5 nên nạp gì |
| 13 | **Media nhúng (video/audio)** | Robot phải biết để dừng nói, phát video, chờ hết rồi nói tiếp. Không biết thì nó nói đè lên video |

---

## 4. Speaker notes — mỏ vàng nếu có

Notes chính là **thứ S4 đang phải sinh lại**. Có notes thật thì S4 chuyển từ *generate*
sang *refine*: chất lượng nhảy vọt, tỉ lệ ungrounded giảm mạnh.

Tính luôn `notes_word_count` mỗi slide và `notes_richness` (trung bình toàn deck) như một
**chỉ số chất lượng deck**, đưa vào báo cáo S0.

```
notes_word_count_avg = 0     -> deck không có notes, S4 phải generate từ đầu
notes_word_count_avg > 30    -> S4 chuyển sang chế độ refine
```

---

## 5. Hash cho incremental build

```
hash = SHA256( normalize(XML shape tree của slide) + bytes của mọi ảnh trên slide )
```

- **Không hash cả file pptx** — sửa metadata (tác giả, thời gian) là hash đổi, build lại vô ích.
- `normalize` phải bỏ: id ngẫu nhiên do PowerPoint sinh, thứ tự thuộc tính, whitespace,
  revision id. Không bỏ thì mở file ra rồi đóng lại cũng đổi hash.
- Hash ở mức **slide**, không phải mức deck.

---

## 6. RawSlide — schema đầy đủ

```json
{
  "slide_id": 7,
  "visible": true,
  "layout_name": "Two Content",
  "section_native": "Kiến trúc",
  "slide_size": {"w": 12192000, "h": 6858000},

  "text_runs": [
    {"text": "Kiến trúc RAG", "placeholder": "title",
     "bbox_norm": [0.08, 0.06, 0.92, 0.16],
     "font_size": 32, "bold": true, "color": "#1A1A1A",
     "reading_order": 0}
  ],

  "tables": [
    {"bbox_norm": [0.10, 0.30, 0.90, 0.70],
     "n_rows": 4, "n_cols": 3,
     "cells": [["Phương pháp", "Chi phí", "Độ trễ"],
               ["RAG", "Thấp", "Trung bình"]],
     "merges": []}
  ],

  "charts": [
    {"chart_type": "bar", "bbox_norm": [0.10, 0.25, 0.88, 0.75],
     "series": [{"name": "Accuracy",
                 "categories": ["A", "B"],
                 "values": [0.82, 0.91]}],
     "from_xml": true}
  ],

  "shapes": [
    {"type": "arrow", "color": "#E53935", "style": "dashed",
     "from_norm": [0.65, 0.53], "to_norm": [0.10, 0.53],
     "z": 3, "group_path": ["grp_flow"]}
  ],

  "images": [
    {"crop_from_render": [0.12, 0.30, 0.88, 0.72],
     "media_ref": "ppt/media/image4.png",
     "is_cropped": true, "alt_text": ""}
  ],

  "has_smartart": false,
  "media": [],
  "hyperlinks": ["https://arxiv.org/abs/2005.11401"],

  "animation_order": [[1, 2], [3], [4]],
  "build_steps": 3,

  "png_render": "render/s7.png",
  "png_steps": null,

  "speaker_notes": "",
  "notes_word_count": 0,

  "parse_quality": "full",
  "hash": "a3f9c2..."
}
```

`animation_order`: mảng các **nhóm** shape id xuất hiện cùng một nhịp click.
`build_steps` = `len(animation_order)`.

---

## 7. Tooling

| Việc | Công cụ | Ghi chú |
|---|---|---|
| Parse pptx | `python-pptx` + đọc XML thô | `python-pptx` không expose animation timing, phải mở `p:timing` thủ công |
| Render PNG | LibreOffice headless → PDF → `PyMuPDF` rasterize | `soffice --headless --convert-to pdf`. Ổn định nhất trên Linux |
| Parse deck.pdf (đường suy biến) | `PyMuPDF` | |
| Cắt ảnh theo bbox | `Pillow` | cắt từ PNG render, không từ `ppt/media/` |

### Cảnh báo render

LibreOffice render pptx **không khớp 100%** với PowerPoint: font thay thế, sai lệch vị trí,
SmartArt đôi khi vỡ. Phải **cài font tiếng Việt vào container**, không thì vỡ dấu.
Với đồ án thì chấp nhận được, nhưng nên soi mắt vài trang trước khi tin.

### Về `png_steps`

Render riêng từng bước animation là khó với LibreOffice — nó chỉ xuất trạng thái cuối.
Hai lựa chọn:

| | Cách làm | Đánh giá |
|---|---|---|
| Dễ | Chỉ render trạng thái cuối, dùng `animation_order` để chia kịch bản theo bước | **Chọn cách này** |
| Khó | Tự ẩn shape theo `animation_order` rồi render lại từng bước | Làm được nhưng tốn công, lợi ích thấp |

Chia kịch bản theo bước là đủ để robot không tiết lộ trước nội dung. Không cần ảnh riêng
từng bước. `png_steps: null` là trạng thái bình thường.

---

## 8. Đường suy biến: chỉ có `deck.pdf`

Ngoài phạm vi v1 (v1 chỉ nhận `.pptx`), ghi lại để biết cái giá phải trả nếu mở rộng.

| Lấy được | Mất hẳn |
|---|---|
| Text + bbox (PyMuPDF) | **Animation order** → không chia được build step |
| Vector drawing (đường, mũi tên) | **Speaker notes** → mất luôn mỏ vàng |
| Ảnh nhúng | **Chart data gốc** → VLM phải đoán số từ ảnh |
| Render PNG (dễ hơn pptx) | **Placeholder role** → không biết đâu là title thật |
| | **Cấu trúc bảng** → thành text rời rạc |

Xử lý: `parse_quality: "degraded"`, `build_steps: null`, hạ confidence threshold ở S1,
chấp nhận số flag sang S7 tăng.

### Bẫy: PDF xuất theo bước animation

PowerPoint export PDF có tuỳ chọn xuất **mỗi bước animation thành một trang riêng**.
Deck 20 slide ra file PDF 45 trang, các trang gần giống hệt nhau.

Phải có bước **dedup**: so ảnh render liền kề, nếu trang sau chỉ là trang trước **cộng
thêm phần tử** → gộp lại thành 1 slide với `build_steps`.

Không làm bước này thì `slide_index` có 45 dòng rác và điều hướng loạn hết.

---

## 9. Quality gate ra khỏi S0

Dừng lại kiểm trước khi cho chạy S1:

```
n_slides_visible     = 20        (đã lọc hidden)
text_extraction_rate = 100%      (pptx) / 94% (pdf)
charts_from_xml      = 3/4   [!] 1 chart là ảnh, VLM phải đoán
tables_extracted     = 2
notes_word_count_avg = 0     [!] không có speaker notes
parse_quality        = full
avg_text_per_slide   = 14 từ  -> xác nhận đúng dạng "ít chữ nhiều hình"
```

**Dòng cuối có giá trị chẩn đoán.** Dưới 20 từ/trang nghĩa là deck cực thưa chữ, xác nhận
rằng RAG text thuần sẽ chết và toàn bộ gánh nặng dồn vào VLM ở S1. Đây chính là con số
đưa vào báo cáo để biện minh cho thiết kế.

| Điều kiện | Hành động |
|---|---|
| `n_slides_visible == 0` | Fail cứng |
| `text_extraction_rate < 80%` | Fail — nhiều khả năng deck toàn ảnh, hoặc parse hỏng |
| `charts_from_xml < 100%` | Flag mỗi chart thiếu |
| `parse_quality != "full"` | Flag toàn deck |
| `notes_word_count_avg == 0` | Flag thông tin (không chặn), ghi vào báo cáo |

---

## 10. Cấm

- Dùng thứ tự shape trong XML làm reading order
- Để VLM đọc số từ ảnh biểu đồ khi chart là native
- Lấy ảnh từ `ppt/media/` thay vì cắt từ PNG render
- Hash cả file pptx
- Bỏ qua hidden slide (hoặc tính nó vào số trang)
- Duyệt group không đệ quy
