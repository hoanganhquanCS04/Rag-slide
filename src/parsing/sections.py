"""Dò chương (section) từ TRANG MỤC LỤC. Không tìm thấy mục lục khớp -> [] (KHÔNG đoán).

Vì sao cần: docling dựng cây heading PHẲNG — mọi `section_header` đều `level=1`. Tức là
KHÔNG có phân cấp nào để dựa vào.

Luật: deck thường có mục lục ở trang 2. Đọc tên chương ở đó, dò tiêu đề các trang phía sau —
trang ĐẦU TIÊN có tiêu đề khớp một mục là trang bắt đầu chương, kéo tới trước trang bắt đầu
chương kế tiếp. Trang mở chương có thể chỉ có tên hoặc có cả nội dung — không quan trọng,
chỉ cần tiêu đề khớp. Đo:

    Onboarding Kit      mục lục p2, 5/5 mục -> p3 · p14 · p37 · p42 · p47
    Thời gian làm việc  mục lục p2, 5/5 mục -> p3 · p4 · p6 · p9 · p12  (không có trang phân
                        chương riêng — trang mở chương chính là trang nội dung)
"""

from __future__ import annotations

import logging
import re

from parsing.models import ParsedDocument, SectionSpan

log = logging.getLogger(__name__)

MIN_TOC_MATCH = 0.60         # >= 60% mục tìm được trang mở chương thì mới tin là mục lục
TOC_MAX_PAGE = 5             # mục lục nằm ở đầu deck; tìm xa hơn là vớ phải mục lục CON (p14)


def _strip(s: str) -> str:
    """Bỏ số thứ tự đầu dòng ("01 ", "4. ") + dấu cách docling chèn trước dấu câu."""
    s = re.sub(r"\s+([,.;:)!?])", r"\1", s)
    return " ".join(re.sub(r"^\W*\d+\s*[.):\-]?\s+", "", s.strip()).split())


def _key(s: str) -> str:
    """Khoá so tên mục với tiêu đề — không phân biệt hoa thường."""
    return _strip(s).lower()


def detect_sections(doc: ParsedDocument) -> tuple[list[SectionSpan], list[str]]:
    """-> (sections, các dòng giải thích để log).

    Trang mục lục = trang đầu deck mà >= 60% dòng của nó tìm được trang mở chương phía sau,
    theo đúng thứ tự — không dựa vào chữ "Nội dung", chính các trang sau xác nhận đó là mục lục.
    Khớp: tiêu đề == tên mục, hoặc tiêu đề CHỨA tên mục ("CHẾ ĐỘ LƯƠNG THƯỞNG" ∋ "LƯƠNG THƯỞNG").
    """
    titled = [(p.page_no, _key(p.title)) for p in doc.pages if p.title]
    for toc in doc.pages[:TOC_MAX_PAGE]:
        items = [ln for b in toc.paragraphs if b.role in ("list", "body") for ln in b.lines]
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
                confidence=round(ratio, 2),
            )
            for i, (it, no) in enumerate(starts)
        ]
        return sections, notes
    return [], [f"muc luc: khong tim thay trong {TOC_MAX_PAGE} trang dau (hoac khop < 60%) -> 0 chuong"]


def apply_sections(doc: ParsedDocument) -> ParsedDocument:
    """Dò section, gán vào doc.sections và page.section_id. Sửa tại chỗ."""
    sections, notes = detect_sections(doc)
    for n in notes:
        log.info("  %s", n)
    doc.sections = sections
    for page in doc.pages:
        sec = doc.section_of(page.page_no)
        page.section_id = sec.id if sec else None
    return doc
