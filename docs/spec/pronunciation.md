# pronunciation.json — robot đọc thuật ngữ thế nào

**Vào:** `out/deck/*/scenario.json` (kịch bản S4) · **Ra:** `data/pronunciation.json` — **MỘT kho
cho mọi deck** · **Code:** `scripts/extract_terms.py` (gom) · `src/scenario/syllables.py` (tra + đếm)

---

## 1. Để làm gì — hai việc, không phải một

```
1. TTS đọc đúng          "CBNV" -> "xê bê en vê", không để TTS tự đoán        (S6b — chưa có)
2. S4 ĐẾM ÂM TIẾT        cùng một chữ, đọc khác nhau thì SỐ ÂM TIẾT khác nhau  (đang chạy)
```

CLAUDE.md §5 S4: đếm âm tiết **theo `pronunciation.json`** (viết tắt đọc thế nào thì đếm thế ấy).

```
"plt.savefig"   đọc "pi-eo-ti chấm sếp-phích"   ->  6 âm tiết
                đọc "sếp-phích"                  ->  2 âm tiết
"CBLĐ"          đếm như một chữ Việt             ->  1 âm tiết   ← bản cũ đếm thế này
                đọc "xê bê e lờ đê"              ->  5 âm tiết
```

`CBLĐ` xuất hiện 60 lần trên slide Onboarding. Mỗi lần robot nhắc là hụt 4 âm tiết; nhắc
chừng ấy lần là kịch bản dài hơn ước tính ~1 phút **mà không ai phát hiện cho tới lúc nghe
thật**.

---

## 2. Sinh TỪ KỊCH BẢN, không từ chunk

Chỉ từ robot **thật sự nói** mới cần cách đọc — và thứ robot nói là kịch bản, không phải
slide. Bản cũ quét `chunks.json` trước S4; đo trên 3_datavisualization:

```
bảng quét từ chunk, người duyệt xong      35 mục
kịch bản thật sự nói                      25     -> 10 mục duyệt uổng công
kịch bản CÓ nói mà bảng KHÔNG có          16     -> CSV, Google Maps, John Hunter, Isomap...
```

Quét chunk còn vớ rác: chữ Việt viết hoa trong tiêu đề (`NỘI DUNG` -> `DUNG`,
`KHÔNG MAY` -> `MAY`) bị coi là viết tắt, rồi khớp nhầm "nội dung", "may mắn" trong lời
nói -> đếm 4 âm tiết thay vì 1, và bắn oan `delivery_has_fact`.

Vậy S4 chạy TRƯỚC khi có cách đọc thì đếm sao? **Ước lượng + báo** — từ lạ đếm theo cách
đánh vần (viết tắt) hoặc cụm nguyên âm (từ Anh), và gắn cờ `unknown_pronunciation`. Người
chốt xong, chạy lại S4 **chỉ đếm lại**, không gọi LLM (§5).

---

## 3. Một kho chung, không có bản per-deck

`CBNV` xuất hiện ở cả hai deck nhân sự (41 + 66 lần). Duyệt một lần là xong cho mọi deck.

Chữ đọc khác nhau tùy ngữ cảnh (`T7` = "thứ bảy" trong "lịch ON/OFF T7", nhưng `T3` là cấp
bậc) — **chấp nhận MỘT cách đọc**. Viết tắt vốn đã không nhằm cho người ngoài hiểu; đọc
chưa khớp ngữ cảnh ở vài chỗ rẻ hơn nhiều so với duy trì hai tầng kho + luật ghi đè.

---

## 4. Schema

```json
{
  "_huong_dan": ["..."],
  "terms": {
    "CBNV":       {"say": "xê bê en vê",  "mode": "spell",       "by": "auto"},
    "matplotlib": {"say": "mát plót líp", "mode": "phonetic_vi", "by": "nguoi"},
    "Google":     {"say": "Google",       "mode": "as_english",  "by": "auto"}
  }
}
```

| field | nghĩa |
|---|---|
| key | từ như trong kịch bản. Viết tắt (toàn hoa) khớp **đúng hoa-thường**; từ thường khớp không phân biệt (`Python` = `python`) |
| `say` | cách đọc, viết bằng chữ Việt. **Dùng cho CẢ đếm âm tiết LẪN đưa vào TTS** |
| `mode` | `spell` đánh vần từng chữ · `phonetic_vi` phiên âm Việt · `as_english` để nguyên chữ Anh. Chỉ để người và S6b đọc — code đếm không dùng |
| `by` | `auto` máy đoán, chưa ai duyệt · `nguoi` đã chốt — chạy lại script **không bao giờ đè** |

**Số âm tiết KHÔNG lưu** — tính từ `say` mỗi lần nạp. Lưu là mở cửa cho sửa `say` mà quên
sửa số (đã dính: `LED` có `say: "led"` mà `syllables: 4`).

**Không có hash trong file.** Hash ghi vào `Scenario` chỉ tính trên **các mục kịch bản đó
dùng** (`Pronunciation.hash_for`): kho chung, sửa một từ deck khác nói thì kịch bản deck này
không bị coi là lệch.

---

## 5. Quy trình

```
1. S4 viết kịch bản        src/scenario/cli.py <document.json>
                           từ lạ đếm ước lượng + cờ unknown_pronunciation

2. gom từ lạ vào kho       scripts/extract_terms.py out/deck/<doc_id>/scenario.json [...]
                           viết tắt -> đánh vần · từ Anh -> để nguyên · by:"auto"

3. NGƯỜI mở kho            sửa "say" chỗ máy đoán sai, đổi by "auto" -> "nguoi"

4. chạy lại S4             trang không đổi: CHỈ đếm lại âm tiết, không gọi LLM
                           đếm lại mà trượt luật (câu > 30 âm tiết...) thì viết lại trang đó
```

Từ lạ nằm trong kịch bản mà **không nên nói ra miệng** (`plt`, tên biến) thì sửa **kịch
bản**, không thêm vào kho.

---

## 6. Máy nhận ra từ nào cần cách đọc

Luật ký tự, trong `syllables.py`:

```
có trong kho                                  -> đọc theo kho
toàn chữ hoa, >= 2 ký tự                      -> VIẾT TẮT (kể cả có Đ: CBLĐ, HĐLĐ, VNĐ)
   trừ: có dấu + đúng dạng âm tiết Việt       -> chữ Việt viết hoa (THỜI, LƯƠNG)
   trừ: nằm trong dải chữ hoa có dấu          -> chữ Việt viết hoa ("THỜI GIAN LÀM VIỆC"
                                                 -> GIAN không bị đánh vần)
có dấu, hoặc đúng dạng âm tiết Việt không dấu -> tiếng Việt, 1 âm tiết
còn lại                                       -> từ Anh
```

**Giới hạn phải nhận:** từ Anh trông như âm tiết Việt (`sin`, `map`, `tin`) lọt thành
tiếng Việt, trừ khi đã có trong kho. Không tách được bằng luật ký tự — nhưng giờ chỉ phải
xét vài chục từ robot thật sự nói, không phải 335 từ trong chunk.

---

## 7. Đo gì

```
số mục by:"nguoi" / tổng số mục                   tỉ lệ đã duyệt
từ trong kịch bản KHÔNG có trong kho              -> cờ unknown_pronunciation (vàng)
hash trong Scenario != hash_for(kịch bản) hiện tại -> S6b DỪNG, không synth
```
