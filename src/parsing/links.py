"""Link ẩn sau chữ trong PDF (annotation) -> gắn vào block chồng lên nó.

Slide xuất từ PowerPoint giữ hyperlink thành annotation: chữ "Cẩm nang" trên trang, URL nằm
ở vùng link đè lên chữ đó. Text layer KHÔNG chứa URL, docling chỉ bắt được một phần (đo trên
Onboarding Kit: 52 URL khác nhau / 17 trang, docling gắn `hyperlink` cho 16 đoạn chữ).

Đọc thẳng từ PDF -> deterministic, không gọi model. Kèm CHỮ nằm dưới vùng link: một block
list có thể mang nhiều link (p3: 7 cái), không có chữ thì không biết URL nào của dòng nào.
Link xuống dòng sinh nhiều vùng cùng URL -> gộp theo URL, nối chữ.
"""

from __future__ import annotations

import ctypes
import logging
import re
from pathlib import Path

from parsing.from_docling import Box
from parsing.models import Href, ParsedDocument, ParsedParagraph

log = logging.getLogger(__name__)

_BULLET = re.compile(r"^[•✓➢⚬\-\s]+")


def read_pdf_links(pdf_path: str | Path) -> dict[int, list[tuple[Box, str, str]]]:
    """-> {page_no: [(vùng link, url, chữ dưới link)]}. Chỉ lấy link URI, bỏ link nhảy trang."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument(str(pdf_path))
    out: dict[int, list[tuple[Box, str, str]]] = {}
    try:
        for no, page in enumerate(pdf, 1):
            w, h = page.get_size()
            tp = page.get_textpage()
            pos, link = ctypes.c_int(0), raw.FPDF_LINK()
            while raw.FPDFLink_Enumerate(page.raw, ctypes.byref(pos), ctypes.byref(link)):
                act = raw.FPDFLink_GetAction(link)
                n = raw.FPDFAction_GetURIPath(pdf.raw, act, None, 0) if act else 0
                r = raw.FS_RECTF()
                if not n or not raw.FPDFLink_GetAnnotRect(link, ctypes.byref(r)):
                    continue
                buf = ctypes.create_string_buffer(n)
                raw.FPDFAction_GetURIPath(pdf.raw, act, buf, n)
                url = buf.value.decode("utf-8", errors="replace").strip()
                top, bottom = max(r.top, r.bottom), min(r.top, r.bottom)
                text = tp.get_text_bounded(r.left, bottom, r.right, top)
                text = " ".join(re.sub(r"[\x00-\x1f]", "", text).split())   # \x02 = gạch nối mềm
                # PDF: gốc DƯỚI-trái -> lật về gốc trên-trái như polygon
                box = (r.left / w, (h - top) / h, r.right / w, (h - bottom) / h)
                out.setdefault(no, []).append((box, url, text))
    finally:
        pdf.close()
    return out


def _overlap(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0.0) * max(h, 0.0)


def attach_links(doc: ParsedDocument, pdf_path: str | Path) -> int:
    """Gắn link vào `hrefs` của block chồng nhiều nhất lên vùng link. -> số vùng KHÔNG gắn được.

    PowerPoint xuất MỖI TỪ thành một vùng link riêng ("•", "Cẩm", "nang"...) -> gom mẩu chữ
    theo (block, url) đúng thứ tự rồi mới nối, bỏ gạch đầu dòng sau khi nối.
    """
    links = read_pdf_links(pdf_path)
    lost = 0
    for page in doc.pages:
        blocks = [b for b in page.blocks if b.content]
        pieces: dict[tuple[str, str], list[str]] = {}
        for box, url, text in links.get(page.page_no, []):
            best = max(blocks, key=lambda b: _overlap(b.box, box), default=None)
            if best is None or _overlap(best.box, box) == 0:
                lost += 1
                continue
            pieces.setdefault((best.id, url), []).append(text)
        by_id = {b.id: b for b in blocks}
        for (bid, url), texts in pieces.items():
            text = _BULLET.sub("", " ".join(t for t in texts if t)).strip()
            by_id[bid].hrefs.append(Href(text=text, url=url))
        for bid in {bid for bid, _ in pieces}:
            if isinstance(by_id[bid], ParsedParagraph):
                by_id[bid].refresh_role()          # >= nửa số dòng là link -> role "links"
    n = sum(len(b.hrefs) for p in doc.pages for b in p.blocks)
    log.info("  link an: %d link gan vao block, %d vung link khong trung block nao", n, lost)
    return lost
