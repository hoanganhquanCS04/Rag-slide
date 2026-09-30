"""Kiểm kịch bản bằng CODE — không tin LLM. Xem docs/spec/scenario.md §11.

Ba mức:
    red    cờ đỏ, KHÔNG đóng gói được       -> thử pass 2, còn thì bắt người duyệt
    fix    vi phạm luật                     -> pass 2 sửa, còn thì thành cờ vàng
    warn   đáng soi nhưng không bắt sửa     -> cờ vàng thẳng
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Literal

from scenario.models import SlideScript
from scenario.syllables import Pronunciation

MAX_SYLLABLES = 30                      # R7 chỉ ngắt được ở ranh giới câu
# Trần số câu CHỈ cho trang không có ý để nói (chuyển chương, lời kết). Trang nội dung / bài
# tập KHÔNG có trần: độ dài đi theo nội dung, bắt buộc ĐỦ Ý (`block_not_covered`,
# `number_missing`) — người nghe phải hình dung được hết thông tin trên trang.
MAX_SENTENCES = {"section_divider": 2}
DELIVERY_RANGE = (0.10, 0.25)           # NT4 — 10% là sàn cứng
STDEV_MIN = 6.0

# Văn viết = "việc"/"sự" DANH TỪ HOÁ động từ ("việc chấm công", "sự thay đổi"). Từ ghép thì
# không phải: đo trên 2 deck nhân sự — làm việc 64, công việc 24, thử việc 12, nhân sự 22…
# Bản cũ chỉ chừa "công việc"/"thực sự" -> LLM phải đổi "thử việc" thành "thời kỳ thử".
_VIEC_OK = ("công", "làm", "thử", "nghỉ", "nhận", "học", "thôi", "xin", "tìm", "bỏ", "mất",
            "giao", "vào", "sự")
_SU_OK_BEFORE = ("thực", "nhân", "lịch", "dân", "hình", "quân")
_SU_OK_AFTER = ("việc", "kiện", "nghiệp", "cố", "vụ")
BANNED = [
    (re.compile("".join(f"(?<!{w} )" for w in _VIEC_OK) + r"(?<!\w)việc(?!\w)", re.I), "việc"),
    (re.compile("".join(f"(?<!{w} )" for w in _SU_OK_BEFORE) + r"(?<!\w)sự(?!\w)"
                + rf"(?! (?:{'|'.join(_SU_OK_AFTER)})\b)", re.I), "sự"),
    (re.compile(r"được thực hiện bởi", re.I), "được thực hiện bởi"),
]
# Nói VỀ SLIDE thay vì nói VỀ CHỦ ĐỀ — đo được ở lần chạy đầu: "Trang này dạy...",
# "Thuật ngữ ở đây là...". Giảng viên thật không nói vậy.
META = re.compile(
    r"(?:trang|slide)(?: này)? (?:dạy|nói|cho thấy|trình bày|giới thiệu|minh hoạ|minh họa)"
    r"|thuật ngữ ở đây|như trên hình|ví dụ minh h[oọ]a này"
    r"|mô tả cho (?:biết|thấy)|trong (?:ảnh|hình|bức ảnh)|bức ảnh (?:cho|thể hiện)", re.I)
# Câu delivery rỗng, ra lệnh cho người nghe — đo được ở lần chạy thứ hai sau khi cấm
# delivery chứa thuật ngữ: LLM lùi về câu đệm vô nghĩa.
# "đã lắng nghe" không tính: đó là câu cảm ơn ở trang kết, không phải ra lệnh.
FILLER = re.compile(r"hãy chú ý|(?<!đã )lắng nghe|cùng quan sát|tiếp theo thôi|tập trung vào", re.I)
# Khoảng giá trị đọc từ biểu đồ, kể cả viết bằng chữ ("từ một đến bốn")
NUM_WORD = r"(?:\d+|không|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười)"
RANGE = re.compile(rf"từ {NUM_WORD}\b.{{0,20}}?đến", re.I)
# Đọc code thành tiếng: ngoặc, dấu bằng, gạch dưới, dạng a.b (nhưng cho phép 1.0)
CODE = re.compile(r"[()=\[\]{}_]|[A-Za-z]\.[A-Za-z]")
# Đọc URL thành tiếng — cả dạng viết lẫn dạng đã phiên âm ("nhatot chấm com").
# Dạng "abc.com" đã bị CODE bắt; đây bắt thêm phần CODE không thấy.
URL_SPOKEN = re.compile(
    r"https?\s*:|www\b|chấm\s+(?:com|vn|net|org|edu)\b|gạch chéo|\b(?:com|net|org)\s+chấm\s+vn\b",
    re.I)
# Viết theo kiểu SLIDE thay vì kiểu NÓI — TTS đọc nguyên văn, và code đếm âm tiết không thấy
# ký hiệu ("%" là 2 âm tiết "phần trăm", đếm ra 0). Deck nhân sự: "/" 271 lần, "%" 53, "&" 26,
# "44h", "75tr", "30p". Đã lọt vào kịch bản: "Hạn chót nộp là 25/10/2025."
SYMBOL = re.compile(r"[/%&+<>@]")
NUM_UNIT = re.compile(r"\b\d+(?:[.,]\d+)?[a-zđ]+\b")          # 44h, 75tr, 30p — không bắt 3D

# ĐỦ Ý — con số là thông tin cốt lõi của deck nhân sự (số giờ, hệ số, phần trăm, số tiền).
# Thứ KHÔNG đọc thành tiếng thì không bắt: link, email, số điện thoại, mã tài liệu (VSF_IT03
# — số dính sau chữ), số thứ tự đầu dòng ("1 Cấu phần lương").
_URL = re.compile(r"https?://\S+|www\.\S+|\S+@\S+|[\w.-]+\.(?:com|vn|net|org)\S*", re.I)
# số điện thoại VN mở đầu bằng 0 / +84 / 1900 / 1800 — KHÔNG bắt số tiền "4.400.000"
_PHONE = re.compile(r"(?<![\d.])(?:(?:\+84[ .]?|0)\d[\d .]{7,}\d|1[89]00[\d .]{3,}\d)(?!\d)")
_NUM = re.compile(r"(?<!\w)\d+(?:[.,]\d+)*")
_NUM_WORD = {"một": "1", "hai": "2", "ba": "3", "bốn": "4", "năm": "5", "sáu": "6",
             "bảy": "7", "tám": "8", "chín": "9", "mười": "1"}


def _sig(tok: str) -> str | None:
    """Phần CÓ NGHĨA của con số: bỏ dấu phân cách, số 0 đầu và cuối. So theo phần này thì
    "08:00" khớp "8 giờ", "30" khớp "30 phút", "4.400.000" khớp "4,4 triệu" — robot được đổi
    đơn vị cho dễ nghe, chỉ không được BỎ số."""
    s = re.sub(r"[.,]", "", tok).lstrip("0").rstrip("0")
    return s or None


def source_numbers(text: str) -> set[str]:
    """Số trên slide mà kịch bản phải nhắc tới."""
    t = _PHONE.sub(" ", _URL.sub(" ", text))
    lines = []
    for line in t.splitlines():                        # bỏ số thứ tự đầu dòng
        m = re.match(r"\s*\d{1,2}[.)]?\s+(\w)", line)
        lines.append(line[m.start(1):] if m and m.group(1).isupper() else line)
    return {s for s in map(_sig, _NUM.findall("\n".join(lines))) if s}


def spoken_numbers(text: str) -> set[str]:
    """Số trong lời nói — chữ số, và số 1–10 viết bằng chữ (dễ dãi: thà lọt còn hơn bắt oan)."""
    got = {s for s in map(_sig, _NUM.findall(text)) if s}
    return got | {_NUM_WORD[w] for w in re.findall(r"\w+", text.lower()) if w in _NUM_WORD}


@dataclass
class Issue:
    code: str
    level: Literal["red", "fix", "warn"]
    msg: str

    def __str__(self) -> str:
        return self.msg


@dataclass
class BlockInfo:
    provenance: str          # text_layer | vlm | manual | ...
    is_title: bool
    syllables: int           # độ dài khối tiêu đề — để phát hiện câu "bơm" thêm vào tiêu đề
    text: str = ""           # để kiểm thuật ngữ tiếng Anh trong câu có nằm trên trang không
    must_cover: bool = False # khối mang thông tin (đoạn văn, danh sách, bảng) — phải được nói


def check(ss: SlideScript, blocks: dict[str, BlockInfo], pron: Pronunciation,
          page_title: str | None = None) -> list[Issue]:
    """`blocks`: id -> thông tin của các khối CÓ nội dung trên trang đang viết."""
    out: list[Issue] = []
    S = ss.sentences
    valid_refs = set(blocks)
    # tách cả "plt.savefig" thành plt / savefig — kịch bản chỉ được nói tên hàm trần
    on_page = {w.lower() for b in blocks.values() for w in re.findall(r"\w+", b.text)}
    title_words = {w.lower() for w in re.findall(r"\w+", page_title or "")}

    if not S:
        return [Issue("empty_script", "red", "kịch bản rỗng")]

    cap = MAX_SENTENCES.get(ss.slide_type)
    if cap and len(S) > cap:
        out.append(Issue("too_many_sentences", "fix",
                         f"có {len(S)} câu, loại trang {ss.slide_type} tối đa {cap} câu"))

    for i, s in enumerate(S, 1):
        t = s.text
        if s.syllables > MAX_SYLLABLES:
            out.append(Issue("too_long", "fix",
                             f"câu {i} có {s.syllables} âm tiết, tối đa {MAX_SYLLABLES} — TÁCH thành "
                             f"hai câu, hoặc bỏ bớt chi tiết phụ: \"{t}\""))
        if CODE.search(t):
            out.append(Issue("code_read_aloud", "fix",
                             f"câu {i} đọc mã nguồn (có ngoặc/dấu bằng/dạng a.b): \"{t}\""))
        glued = sorted(set(SYMBOL.findall(t) + NUM_UNIT.findall(t)))
        if glued:
            out.append(Issue("written_symbol", "fix",
                             f"câu {i} viết kiểu slide {glued} — viết theo cách NÓI "
                             f"(\"giờ\", \"triệu\", \"phần trăm\", \"ngày … tháng …\", \"hoặc\", \"và\"): \"{t}\""))
        if URL_SPOKEN.search(t):
            out.append(Issue("url_read_aloud", "fix",
                             f"câu {i} đọc địa chỉ web thành tiếng — chỉ nói là trang gì: \"{t}\""))
        if META.search(t):
            out.append(Issue("meta_talk", "fix",
                             f"câu {i} nói VỀ SLIDE thay vì về chủ đề: \"{t}\""))
        for rx, word in BANNED:
            if rx.search(t):
                out.append(Issue("written_register", "fix",
                                 f"câu {i} dùng văn viết \"{word}\": \"{t}\""))
        # Thay cho danh sách "thuật ngữ được phép" cũ trong prompt: từ Anh / viết tắt phải
        # lấy từ CHÍNH TRANG. Tự thêm từ Anh = hoặc chêm tiếng Anh vô cớ, hoặc bơm kiến thức ngoài.
        alien = sorted({w for w in pron.terms_in(t) if w.lower() not in on_page})
        if alien:
            out.append(Issue("term_not_on_page", "fix",
                             f"câu {i} dùng từ tiếng Anh/viết tắt không có trên trang {alien} — "
                             f"nói bằng tiếng Việt: \"{t}\""))

        if s.kind == "content":
            if ss.slide_type == "section_divider":
                out.append(Issue("divider_has_content", "red",
                                 f"câu {i} là content nhưng trang chuyển chương chỉ được dẫn dắt"))
            ref = s.grounding.ref if s.grounding else None
            if not ref:
                out.append(Issue("ungrounded_content_sentence", "red",
                                 f"câu {i} là content nhưng không có ref"))
            elif ref not in valid_refs:
                out.append(Issue("bad_grounding_ref", "red",
                                 f"câu {i} trỏ vào \"{ref}\", không phải khối nào của trang này "
                                 f"(hợp lệ: {', '.join(sorted(valid_refs))})"))
            else:
                b = blocks[ref]
                # Khối tiêu đề chỉ chứng minh được TÊN chủ đề. Câu dài hơn tiêu đề nhiều mà
                # trỏ vào tiêu đề = đang bơm kiến thức ngoài trang (đo được ở p20).
                if b.is_title and s.syllables > b.syllables + 6:
                    out.append(Issue("title_grounded_claim", "red",
                                     f"câu {i} trỏ vào khối TIÊU ĐỀ \"{ref}\" nhưng nói nhiều hơn "
                                     f"tên chủ đề — tiêu đề không chứng minh được điều đó. Trỏ "
                                     f"vào khối có nội dung, hoặc bỏ câu: \"{t}\""))
                # Số đọc từ ẢNH (vlm) có thể sai — nhưng người dùng chốt (2026-09-30): ĐƯỢC
                # nói, chấp nhận rủi ro sai. Bảng hệ số làm thêm giờ (p7 Thời gian làm việc)
                # là ảnh; cấm số thì trang mất hết nội dung. Chỉ gắn cờ VÀNG để ai muốn đối
                # chiếu thì biết câu nào — không bắt pass 2.
                if b.provenance == "vlm" and (re.search(r"\d", t) or RANGE.search(t)):
                    out.append(Issue("vlm_number", "warn",
                                     f"câu {i} nêu số đọc từ ảnh ({ref}) — có thể sai, đối chiếu "
                                     f"với ảnh nếu cần: \"{t}\""))
        else:
            if FILLER.search(t):
                out.append(Issue("filler_delivery", "fix",
                                 f"câu {i} là câu đệm ra lệnh, không nói gì cả: \"{t}\" — "
                                 f"thay bằng câu gợi tò mò về chủ đề hoặc nối với trang trước"))
            if re.search(r"\d", t):
                out.append(Issue("delivery_has_fact", "fix",
                                 f"câu {i} là delivery nhưng chứa con số: \"{t}\""))
            # chữ trong tên chương không tính: trang chuyển chương BẮT BUỘC nêu tên chương
            terms = [w for w in pron.terms_in(t) if w.lower() not in title_words]
            if terms:
                out.append(Issue("delivery_has_fact", "fix",
                                 f"câu {i} là delivery nhưng chứa thuật ngữ {terms}: \"{t}\""))

    if ss.slide_type == "section_divider" and page_title:
        if page_title.strip().lower() not in " ".join(s.text for s in S).lower():
            out.append(Issue("divider_no_title", "fix",
                             f"trang chuyển chương phải nêu tên chương \"{page_title}\""))

    if ss.slide_type in ("content", "exercise"):
        # ĐỦ Ý, kiểm bằng code. Chỉ bắt được hai thứ: bỏ nguyên một khối, và bỏ con số.
        # Bỏ một ý CHỮ bên trong khối đã nhắc tới thì code không thấy -> việc của S7.
        cited = {s.grounding.ref for s in S if s.kind == "content" and s.grounding}
        skipped = [k for k, b in blocks.items() if b.must_cover and k not in cited]
        if skipped:
            out.append(Issue("block_not_covered", "fix",
                             f"chưa nói tới khối {skipped} — phải nói ĐỦ ý trên trang, mỗi khối "
                             f"ít nhất một câu content trỏ vào"))
        # kể cả khối vlm (bảng/danh sách đọc từ ảnh) — được nói, xem `vlm_number`
        need = set().union(*(source_numbers(b.text) for b in blocks.values()
                             if b.must_cover))
        said = spoken_numbers(" ".join(s.text for s in S))
        if need - said:
            out.append(Issue("number_missing", "fix",
                             f"chưa nói các con số trên trang: {sorted(need - said, key=int)} — "
                             f"phải nói ĐỦ, viết bằng chữ số (đổi đơn vị cho dễ nghe thì được)"))

    if ss.unknown_terms:
        out.append(Issue("unknown_pronunciation", "warn",
                         f"từ tiếng Anh/viết tắt chưa có cách đọc: {sorted(set(ss.unknown_terms))}"
                         f" — chạy scripts/extract_terms.py"))

    if ss.slide_type == "content" and len(S) >= 3:
        lo, hi = DELIVERY_RANGE
        r = ss.delivery_ratio
        if not lo <= r <= hi:
            out.append(Issue("delivery_ratio", "fix",
                             f"câu delivery chiếm {r:.0%} âm tiết, cần {lo:.0%}–{hi:.0%} — "
                             + ("thêm một câu delivery (câu hỏi, câu chuyển ý) giữa trang" if r < lo
                                else "bớt hoặc rút ngắn câu delivery")))

    if ss.slide_type == "content" and len(S) >= 4:
        sd = statistics.pstdev([s.syllables for s in S])
        if sd < STDEV_MIN:
            # "fix" chứ không "warn": warn thì pass 2 không bao giờ sửa — đo được 23/28 và 8/8
            # trang trượt. Đưa SỐ THẬT từng câu: LLM không tự đếm được âm tiết tiếng Việt.
            # Chỉ cách sửa không lách được (§17): câu ngắn phải là delivery, không phải
            # câu content rỗng thêm vào cho đủ nhịp.
            lens = ", ".join(f"câu {k}: {x.syllables}" for k, x in enumerate(S, 1))
            out.append(Issue("monotone_rhythm", "fix",
                             f"nhịp đều đều — số âm tiết {lens} (độ lệch chuẩn {sd:.1f}, cần "
                             f">= {STDEV_MIN:.0f}). Cần câu dài xen câu ngắn: gộp hai câu content "
                             f"liền ý thành MỘT câu 22–28 âm tiết; câu ngắn 6–9 âm tiết thì dùng "
                             f"câu delivery (câu hỏi, câu chuyển ý) — có thể thêm một câu như thế "
                             f"giữa trang. KHÔNG thêm câu content rỗng cho đủ nhịp"))
    return out


def needs_fix(issues: list[Issue]) -> bool:
    return any(i.level in ("red", "fix") for i in issues)


def to_flags(issues: list[Issue]) -> list[str]:
    return sorted({i.code for i in issues})
