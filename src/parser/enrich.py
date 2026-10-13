"""Hoàn thiện ParsedDocument sau khi nạp `document.json`: chương · cờ. Không gọi API.

KB, kịch bản, runtime gọi `enrich(ParsedDocument.load(...))` — kết quả chỉ nằm trong bộ nhớ.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from parser.flags import apply_flags
from models.parsed_document import ParsedDocument
from parser.sections import apply_sections

log = logging.getLogger(__name__)


def page_pdf(src: Path) -> Path | None:
    """PDF để vẽ ảnh trang. `.pptx`: PDF cùng tên (`scripts/pptx2pdf.ps1`)."""
    if src.suffix.lower() == ".pdf":
        return src
    pdf = src.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def enrich(doc: ParsedDocument) -> ParsedDocument:
    """Chương -> cờ -> bỏ khối rỗng. Thứ tự có nghĩa, sửa tại chỗ."""
    apply_sections(doc)
    apply_flags(doc)
    if dropped := drop_empty_blocks(doc):
        log.info("  bo %d block rong khong co co: %s", sum(dropped.values()), dict(dropped))
        apply_flags(doc)
    return doc


def drop_empty_blocks(doc: ParsedDocument) -> Counter[str]:
    """Bỏ block `content: null` KHÔNG có cờ -> {lý do: số block}. Không đánh lại id (chunk / kịch bản vẫn trỏ đúng)."""
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
