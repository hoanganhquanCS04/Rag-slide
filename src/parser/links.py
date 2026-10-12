"""Link ẩn sau chữ trong PDF (annotation) -> gắn vào block chồng lên nó.

Slide xuất từ PowerPoint giữ hyperlink thành annotation: chữ "Cẩm nang" trên trang, URL nằm
ở vùng link đè lên chữ đó. Ảnh trang KHÔNG chứa URL -> VLM không thấy, phải đọc từ PDF (đo trên
Onboarding Kit: 52 URL khác nhau / 17 trang).

Chỉ đọc lớp ANNOTATION (vùng + URL), KHÔNG đọc text layer (bỏ 2026-10-08: S0 chỉ dùng chữ VLM).
Nhãn của link = DÒNG chữ Gemma nằm ở độ cao của vùng link trong khối, CHỈ KHI vùng link phủ phần
lớn diện tích dòng đó — nhãn quyết định dòng nào là "dòng link" (`_is_anchor`, models.py) và khối
>= nửa dòng là link thì robot KHÔNG đọc (`role = links`). Link chỉ phủ một cụm trong câu dài (p18
"mẫu 20/ĐK-TCT" trong quy định thuế) mà lấy cả dòng làm nhãn thì khối nội dung bị câm -> nhãn để
trống, URL vẫn gắn. Deterministic, không gọi model.
"""

from __future__ import annotations

import ctypes
import logging
import re
from pathlib import Path

from models.parsed_document import Block, Box, Href, ParsedDocument, ParsedParagraph

log = logging.getLogger(__name__)

_BULLET = re.compile(r"^[•✓➢➤►▪⚬\-\s]+")


def read_pdf_links(pdf_path: str | Path) -> dict[int, list[tuple[Box, str]]]:
    """-> {page_no: [(vùng link, url)]}. Chỉ lấy link URI, bỏ link nhảy trang."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    pdf = pdfium.PdfDocument(str(pdf_path))
    out: dict[int, list[tuple[Box, str]]] = {}
    try:
        for no, page in enumerate(pdf, 1):
            w, h = page.get_size()
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
                # PDF: gốc DƯỚI-trái -> lật về gốc trên-trái như polygon
                box = (r.left / w, (h - top) / h, r.right / w, (h - bottom) / h)
                out.setdefault(no, []).append((box, url))
    finally:
        pdf.close()
    return out


def _overlap(a: Box, b: Box) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0.0) * max(h, 0.0)


# Vùng link phủ >= LINK_COVER diện tích ước tính của dòng -> dòng đó là "dòng link". Đo trên
# Onboarding (2026-10-08): dòng KHÔNG phải link <= 0.35, dòng toàn link của 4 khối `links` >= 0.50.
LINK_COVER = 0.45


def _lines(block: Block) -> list[str]:
    return [ln for ln in (block.content or "").split("\n") if ln.strip()]


def _line_at(block: Block, box: Box) -> int:
    """Dòng Gemma nào nằm ở độ cao tâm vùng link. Mỗi dòng chiếm một dải dọc TỈ LỆ SỐ KÝ TỰ: dòng
    dài xuống nhiều hàng hiển thị (p32: câu có email xuống 2 hàng), Gemma gộp cả cột thành 1 dòng
    (p39) — chia đều theo số dòng là lệch."""
    lines = _lines(block)
    _, top, _, bottom = block.box
    total = sum(len(ln) for ln in lines)
    if len(lines) <= 1 or bottom <= top or not total:
        return 0
    cy, cum = (box[1] + box[3]) / 2, 0
    for i, ln in enumerate(lines):
        cum += len(ln)
        if cy <= top + (bottom - top) * cum / total:
            return i
    return len(lines) - 1


def _covered(block: Block, k: int, link_area: float) -> bool:
    """Diện tích vùng link (cộng mọi mẩu cùng URL ở dòng k) / diện tích ước tính của dòng k =
    diện tích khối × số ký tự dòng k / tổng ký tự."""
    lines = _lines(block)
    total = sum(len(ln) for ln in lines)
    l, t, r, b = block.box
    area = (r - l) * (b - t) * len(lines[k]) / total if total else 0.0
    return area > 0 and link_area >= LINK_COVER * area


def attach_links(doc: ParsedDocument, pdf_path: str | Path) -> int:
    """Gắn link vào `hrefs` của block chồng nhiều nhất lên vùng link. -> số vùng KHÔNG gắn được.

    PowerPoint xuất MỖI TỪ thành một vùng link riêng ("•", "Cẩm", "nang"...) -> gộp theo
    (block, url) và theo dòng; nhãn = các dòng chữ Gemma mà link PHỦ gần hết, đúng thứ tự, bỏ gạch
    đầu dòng. Link chỉ phủ một cụm trong dòng -> nhãn rỗng (URL vẫn gắn, dòng không tính là link).
    """
    links = read_pdf_links(pdf_path)
    lost = 0
    for page in doc.pages:
        blocks = [b for b in page.blocks if b.content]
        rows: dict[tuple[str, str], dict[int, float]] = {}
        for box, url in links.get(page.page_no, []):
            best = max(blocks, key=lambda b: _overlap(b.box, box), default=None)
            if best is None or _overlap(best.box, box) == 0:
                lost += 1
                continue
            areas = rows.setdefault((best.id, url), {})
            k = _line_at(best, box)
            areas[k] = areas.get(k, 0.0) + (box[2] - box[0]) * (box[3] - box[1])
        by_id = {b.id: b for b in blocks}
        for (bid, url), areas in rows.items():
            block = by_id[bid]
            lines = _lines(block)
            text = " ".join(_BULLET.sub("", lines[k]).strip() for k in sorted(areas)
                            if lines and _covered(block, k, areas[k]))
            block.hrefs.append(Href(text=text, url=url))
        for bid in {bid for bid, _ in rows}:
            if isinstance(by_id[bid], ParsedParagraph):
                by_id[bid].refresh_role()          # >= nửa số dòng là link -> role "links"
    n = sum(len(b.hrefs) for p in doc.pages for b in p.blocks)
    log.info("  link an: %d link gan vao block, %d vung link khong trung block nao", n, lost)
    return lost
