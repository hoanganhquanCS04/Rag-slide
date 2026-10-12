"""Hoàn thiện ParsedDocument sau khi nạp `document.json`: link ẩn · chương · vá tay · cờ. Không gọi API.

KB, kịch bản, runtime gọi `enrich(ParsedDocument.load(...))` — kết quả chỉ nằm trong bộ nhớ.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from parsing.flags import apply_flags
from parsing.links import attach_links
from parsing.models import ParsedDocument
from parsing.patch import apply_patch, find_patch, load_patch
from parsing.sections import apply_sections

log = logging.getLogger(__name__)


def page_pdf(src: Path) -> Path | None:
    """PDF để vẽ trang và đọc link ẩn. `.pptx`: PDF cùng tên (`scripts/pptx2pdf.ps1`)."""
    if src.suffix.lower() == ".pdf":
        return src
    pdf = src.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def enrich(doc: ParsedDocument) -> ParsedDocument:
    """Link ẩn -> chương -> vá tay -> cờ -> bỏ khối rỗng. Thứ tự có nghĩa, sửa tại chỗ."""
    if (pdf := page_pdf(Path(doc.source.path))) and pdf.exists():
        attach_links(doc, pdf)
    apply_sections(doc)                   # đọc role SAU link: dòng mục lục là link thì không tính
    if pf := find_patch(doc.doc_id):
        log.info("  va tay tu %s", pf.name)
        apply_patch(doc, load_patch(pf))
    apply_flags(doc)                      # SAU vá tay: khối đã vá không còn bị cờ
    if dropped := drop_empty_blocks(doc):
        log.info("  bo %d block rong khong co co: %s", sum(dropped.values()), dict(dropped))
        apply_flags(doc)
    return doc


def drop_empty_blocks(doc: ParsedDocument) -> Counter[str]:
    """Bỏ block `content: null` KHÔNG có cờ -> {lý do: số block}. Không đánh lại id (patch vẫn trỏ đúng)."""
    flagged = {f.block_id for f in doc.flags if f.block_id}
    out: Counter[str] = Counter()
    for page in doc.pages:
        empty = [b for b in page.blocks if not b.content and b.id not in flagged]
        if not empty:
            continue
        out.update(getattr(b, "why_empty", None) or b.kind for b in empty)
        page.blocks = [b for b in page.blocks if b.content or b.id in flagged]
        page.page_hash = page.compute_hash()
    return out
