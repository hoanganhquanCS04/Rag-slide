"""Hoàn thiện ParsedDocument sau khi nạp `document.json`: dò chương, bỏ khối rỗng. Không gọi API.

Để làm gì: KB, kịch bản, runtime đều gọi `enrich(ParsedDocument.load(...))`; kết quả chỉ nằm trong bộ nhớ.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from models.parsed_document import ParsedDocument
from offline.parser.sections import apply_sections

log = logging.getLogger(__name__)


def page_pdf(src: Path) -> Path | None:
    """File PDF dùng để vẽ ảnh trang: file `.pdf` thì là chính nó, `.pptx` thì tìm `.pdf` cùng tên."""
    if src.suffix.lower() == ".pdf":
        return src
    pdf = src.with_suffix(".pdf")
    return pdf if pdf.exists() else None

def drop_empty_blocks(doc: ParsedDocument) -> Counter[str]:
    """Bỏ các khối không có nội dung -> đếm số khối bỏ theo lý do.

    Cách làm: không đánh lại id; trang có khối bị bỏ thì tính lại page_hash.
    """
    out: Counter[str] = Counter()
    for page in doc.pages:
        empty = [b for b in page.blocks if not b.content]
        if not empty:
            continue
        out.update(getattr(b, "why_empty", None) or b.kind for b in empty)
        page.blocks = [b for b in page.blocks if b.content]
        page.page_hash = page.compute_hash()
    return out

def enrich(doc: ParsedDocument) -> ParsedDocument:
    """Dò chương -> bỏ khối rỗng (sửa doc tại chỗ, trả lại doc)."""
    apply_sections(doc)
    if dropped := drop_empty_blocks(doc):
        log.info("  bo %d block rong: %s", sum(dropped.values()), dict(dropped))
    return doc