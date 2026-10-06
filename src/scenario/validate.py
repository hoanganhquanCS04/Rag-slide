"""Kiểm kịch bản bằng CODE — không tin LLM. Xem docs/spec/scenario.md §11.

Chỉ còn ba loại (chốt 2026-10-06 — luật "nói tự nhiên" đã bỏ, chỉ còn là hướng dẫn trong prompt):

    bad_grounding          🔴 red   câu content phải trỏ ĐÚNG một khối của trang; câu delivery (không
                                    cần nguồn) không được mang số / thuật ngữ — không thì thông tin
                                    lách qua câu delivery mà không ai kiểm
    block_not_covered      🟠 fix   ĐỦ Ý: mỗi khối mang thông tin có ít nhất một câu content trỏ vào
    number_missing         🟠 fix   ĐỦ Ý: mọi con số trên slide được nói
    unknown_pronunciation  🟡 warn  từ Anh / viết tắt chưa có trong kho phát âm (đếm âm tiết đoán)
    empty_script           🔴 red   kịch bản rỗng

    red   KHÔNG đóng gói được       -> thử pass 2, còn thì bắt người duyệt
    fix   vi phạm luật              -> pass 2 sửa, còn thì thành cờ vàng
    warn  đáng soi, không bắt sửa   -> cờ vàng thẳng
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from scenario.models import SlideScript
from scenario.syllables import Pronunciation

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
    text: str                # chữ của khối — lấy số phải nói
    must_cover: bool = False # khối mang thông tin (đoạn văn, danh sách, bảng) — phải được nói


def check(ss: SlideScript, blocks: dict[str, BlockInfo], pron: Pronunciation,
          page_title: str | None = None) -> list[Issue]:
    """`blocks`: id -> thông tin của các khối CÓ nội dung trên trang đang viết."""
    S = ss.sentences
    if not S:
        return [Issue("empty_script", "red", "kịch bản rỗng")]

    out: list[Issue] = []
    # chữ trong tên chương không tính: câu chuyển chương được nêu tên chương
    title_words = {w.lower() for w in re.findall(r"\w+", page_title or "")}
    for i, s in enumerate(S, 1):
        t = s.text
        if s.kind == "content":
            ref = s.grounding.ref if s.grounding else None
            if not ref:
                out.append(Issue("bad_grounding", "red", f"câu {i} là content nhưng không có ref"))
            elif ref not in blocks:
                out.append(Issue("bad_grounding", "red",
                                 f"câu {i} trỏ vào \"{ref}\", không phải khối nào của trang này "
                                 f"(hợp lệ: {', '.join(sorted(blocks))})"))
        else:
            terms = [w for w in pron.terms_in(t) if w.lower() not in title_words]
            what = "con số" if re.search(r"\d", t) else f"thuật ngữ {terms}" if terms else None
            if what:
                out.append(Issue("bad_grounding", "red",
                                 f"câu {i} là delivery nhưng mang thông tin ({what}) — "
                                 f"chuyển thành câu content có ref: \"{t}\""))

    if ss.slide_type in ("content", "exercise"):
        # ĐỦ Ý, kiểm bằng code. Chỉ bắt được hai thứ: bỏ nguyên một khối, và bỏ con số.
        # Bỏ một ý CHỮ bên trong khối đã nhắc tới thì code không thấy -> việc của S7.
        cited = {s.grounding.ref for s in S if s.kind == "content" and s.grounding}
        skipped = [k for k, b in blocks.items() if b.must_cover and k not in cited]
        if skipped:
            out.append(Issue("block_not_covered", "fix",
                             f"chưa nói tới khối {skipped} — phải nói ĐỦ ý trên trang, mỗi khối "
                             f"ít nhất một câu content trỏ vào"))
        need = set().union(*(source_numbers(b.text) for b in blocks.values() if b.must_cover))
        said = spoken_numbers(" ".join(s.text for s in S))
        if need - said:
            out.append(Issue("number_missing", "fix",
                             f"chưa nói các con số trên trang: {sorted(need - said, key=int)} — "
                             f"phải nói ĐỦ, viết bằng chữ số (đổi đơn vị cho dễ nghe thì được)"))

    if ss.unknown_terms:
        out.append(Issue("unknown_pronunciation", "warn",
                         f"từ tiếng Anh/viết tắt chưa có cách đọc: {sorted(set(ss.unknown_terms))}"
                         f" — chạy scripts/extract_terms.py"))
    return out


def needs_fix(issues: list[Issue]) -> bool:
    return any(i.level in ("red", "fix") for i in issues)


def to_flags(issues: list[Issue]) -> list[str]:
    return sorted({i.code for i in issues})
