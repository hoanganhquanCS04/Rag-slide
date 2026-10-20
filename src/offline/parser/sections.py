"""Dò chương từ TRANG MỤC LỤC. Không tìm thấy mục lục khớp -> 0 chương, không đoán.

Để làm gì: biết mỗi trang thuộc chương nào (dùng cho tiền tố chunk, câu chuyển chương).
Cách làm: đọc tên các mục trên trang mục lục; trang ĐẦU TIÊN phía sau có tiêu đề khớp một mục là
trang mở chương; chương kéo dài tới trước trang mở chương kế tiếp.
"""

from __future__ import annotations

import logging
import re

from models.parsed_document import ParsedDocument, SectionSpan

log = logging.getLogger(__name__)

MIN_TOC_MATCH = 0.60         # >= 60% mục tìm được trang mở chương thì mới tin là mục lục
TOC_MAX_PAGE = 5             # mục lục nằm ở đầu deck; tìm xa hơn là vớ phải mục lục CON (p14)


def _strip(s: str) -> str:
    """Bỏ số thứ tự đầu dòng ("01 ", "4. ") và dấu cách thừa trước dấu câu."""
    s = re.sub(r"\s+([,.;:)!?])", r"\1", s)
    return " ".join(re.sub(r"^\W*\d+\s*[.):\-]?\s+", "", s.strip()).split())


_NUMBERED = re.compile(r"^\W*\d+\s*[.):\-]?(\s|$)")


def _toc_items(lines: list[str]) -> list[str]:
    """Tách các dòng của một khối thành các mục mục lục.

    Cách làm: dòng bắt đầu bằng số thứ tự mở mục mới; dòng không có số nối vào mục đang dở
    (tên mục bị ngắt dòng: "03" / "LÀM THÊM" / "GIỜ" -> một mục).
    """
    items: list[list[str]] = []
    for ln in lines:
        if not items or _NUMBERED.match(ln):
            items.append([ln])
        else:
            items[-1].append(ln)
    return [" ".join(it) for it in items]


def _key(s: str) -> str:
    """Khoá để so tên mục với tiêu đề trang: bỏ số thứ tự, viết thường, bỏ hết dấu cách."""
    return re.sub(r"\s+", "", _strip(s).lower())


def detect_sections(doc: ParsedDocument) -> tuple[list[SectionSpan], list[str]]:
    """Tìm trang mục lục trong 5 trang đầu và dựng danh sách chương -> (chương, dòng log).

    Cách làm: với mỗi trang trong 5 trang đầu, lấy các mục (cần >= 3 mục); tìm trang mở chương cho
    từng mục (tiêu đề bằng hoặc chứa tên mục, đúng thứ tự); >= 2 mục và >= 60% mục tìm được thì đó là
    mục lục; không trang nào đạt -> 0 chương.
    """
    titled = [(p.page_no, _key(p.title)) for p in doc.pages if p.title]
    for toc in doc.pages[:TOC_MAX_PAGE]:
        items = [it for b in toc.paragraphs if b.role in ("list", "body") for it in _toc_items(b.lines)]
        items = [ln for ln in items if len(_key(ln)) >= 3]
        if len(items) < 3:
            continue

        starts: list[tuple[str, int]] = []
        after = toc.page_no
        for it in items:
            k = _key(it)
            hit = next((no for no, t in titled if no > after and (t == k or k in t)), None)
            if hit is not None:
                starts.append((it, hit))
                after = hit
        ratio = len(starts) / len(items)
        if len(starts) < 2 or ratio < MIN_TOC_MATCH:
            continue

        notes = [f"muc luc p{toc.page_no}: {len(starts)}/{len(items)} muc tim duoc trang mo chuong "
                 f"-> {' · '.join(f'p{no}' for _, no in starts)}"]
        if miss := [it for it in items if it not in {i for i, _ in starts}]:
            notes.append(f"  muc khong khop tieu de nao: {miss}")
        last = doc.pages[-1].page_no
        sections = [
            SectionSpan(
                id=f"sec_{i:02d}",
                title=_strip(it),
                pages=(no, starts[i + 1][1] - 1 if i + 1 < len(starts) else last),
                source="outline_page",
            )
            for i, (it, no) in enumerate(starts)
        ]
        return sections, notes
    return [], [f"muc luc: khong tim thay trong {TOC_MAX_PAGE} trang dau (hoac khop < 60%) -> 0 chuong"]


def apply_sections(doc: ParsedDocument) -> ParsedDocument:
    """Dò chương rồi gán vào `doc.sections` và `section_id` của từng trang (sửa tại chỗ)."""
    sections, notes = detect_sections(doc)
    for n in notes:
        log.info("  %s", n)
    doc.sections = sections
    for page in doc.pages:
        sec = doc.section_of(page.page_no)
        page.section_id = sec.id if sec else None
    return doc
