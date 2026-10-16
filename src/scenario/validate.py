"""Kiểm kịch bản một trang bằng CODE (không tin LLM tự khai).

Để làm gì: tìm lỗi để pass 2 sửa, hoặc gắn cờ cho người duyệt.
Các lỗi:
    empty_script           đỏ    kịch bản rỗng
    bad_grounding          đỏ    trang nội dung mà không có `refs`, hoặc có ref không phải khối của trang
    block_not_covered      sửa   có khối mang thông tin mà không nằm trong `refs`
    number_missing         sửa   có con số trên slide mà đoạn văn không nói
    unknown_pronunciation  vàng  từ tiếng Anh / viết tắt chưa có cách đọc trong kho
Cách xử lý: đỏ và "sửa" -> chạy pass 2; vàng -> chỉ gắn cờ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from models.scenario import SlideScript

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
    """Phần có nghĩa của một con số để so: bỏ dấu phân cách, bỏ số 0 ở đầu và cuối.

    Để làm gì: "08:00" khớp "8 giờ", "4.400.000" khớp "4,4 triệu" — đổi đơn vị được, bỏ số thì không.
    """
    s = re.sub(r"[.,]", "", tok).lstrip("0").rstrip("0")
    return s or None


def source_numbers(text: str) -> set[str]:
    """Các con số trên slide mà kịch bản phải nói.

    Cách làm: bỏ link, email, số điện thoại và số thứ tự đầu dòng trước khi lấy số.
    """
    t = _PHONE.sub(" ", _URL.sub(" ", text))
    lines = []
    for line in t.splitlines():                        # bỏ số thứ tự đầu dòng
        m = re.match(r"\s*\d{1,2}[.)]?\s+(\w)", line)
        lines.append(line[m.start(1):] if m and m.group(1).isupper() else line)
    return {s for s in map(_sig, _NUM.findall("\n".join(lines))) if s}


def spoken_numbers(text: str) -> set[str]:
    """Các con số trong lời nói: chữ số, và số 1–10 viết bằng chữ ("ba" -> 3)."""
    got = {s for s in map(_sig, _NUM.findall(text)) if s}
    return got | {_NUM_WORD[w] for w in re.findall(r"\w+", text.lower()) if w in _NUM_WORD}


@dataclass
class Issue:
    """Một lỗi tìm được: mã lỗi, mức (red / fix / warn), câu mô tả."""
    code: str
    level: Literal["red", "fix", "warn"]
    msg: str

    def __str__(self) -> str:
        """In lỗi ra dạng câu mô tả."""
        return self.msg


@dataclass
class BlockInfo:
    """Thông tin một khối cho bộ kiểm: chữ của khối, và khối có bắt buộc phải nói tới không."""
    text: str                # chữ của khối — lấy số phải nói
    must_cover: bool = False # khối mang thông tin (đoạn văn, danh sách, bảng) — phải được nói


def check(ss: SlideScript, blocks: dict[str, BlockInfo]) -> list[Issue]:
    """Kiểm kịch bản một trang -> danh sách lỗi.

    Cách làm: rỗng thì báo empty_script; mọi ref phải là id khối của trang; trang content / exercise
    thì phải có ref, đủ khối mang thông tin trong `refs` và đủ con số trong đoạn văn; có từ lạ thì
    báo unknown_pronunciation.
    """
    if not ss.text.strip():
        return [Issue("empty_script", "red", "kịch bản rỗng")]

    out: list[Issue] = []
    bad = [r for r in ss.refs if r not in blocks]
    if bad:
        out.append(Issue("bad_grounding", "red",
                         f"refs có {bad} không phải khối nào của trang này "
                         f"(hợp lệ: {', '.join(sorted(blocks))})"))

    if ss.slide_type in ("content", "exercise"):
        if not ss.refs:
            out.append(Issue("bad_grounding", "red", "trang nội dung mà `refs` rỗng — phải ghi id các khối đã nói"))
        # ĐỦ Ý, kiểm bằng code. Chỉ bắt được hai thứ: bỏ nguyên một khối, và bỏ con số.
        # Bỏ một ý CHỮ bên trong khối đã nhắc tới thì code không thấy -> việc của S7.
        skipped = [k for k, b in blocks.items() if b.must_cover and k not in ss.refs]
        if skipped:
            out.append(Issue("block_not_covered", "fix",
                             f"chưa nói tới khối {skipped} — phải nói ĐỦ ý trên trang và ghi id khối vào `refs`"))
        need = set().union(*(source_numbers(b.text) for b in blocks.values() if b.must_cover))
        said = spoken_numbers(ss.text)
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
    """Có lỗi đỏ hoặc lỗi "sửa" không (tức là cần chạy pass 2)."""
    return any(i.level in ("red", "fix") for i in issues)


def to_flags(issues: list[Issue]) -> list[str]:
    """Danh sách lỗi -> danh sách mã cờ (không trùng, xếp theo tên)."""
    return sorted({i.code for i in issues})
