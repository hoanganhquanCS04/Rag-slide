"""Hoàn thiện ParsedDocument sau khi nạp `document.json`: dò chương, gắn cờ. Không gọi API.

Để làm gì: KB, kịch bản, runtime đều gọi `enrich(ParsedDocument.load(...))`; kết quả chỉ nằm trong bộ nhớ.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path

from offline.parser.flags import apply_flags
from models.parsed_document import ParsedDocument
from offline.parser.sections import apply_sections

log = logging.getLogger(__name__)


def page_pdf(src: Path) -> Path | None:
    """File PDF dùng để vẽ ảnh trang: file `.pdf` thì là chính nó, `.pptx` thì tìm `.pdf` cùng tên."""
    if src.suffix.lower() == ".pdf":
        return src
    pdf = src.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def enrich(doc: ParsedDocument) -> ParsedDocument:
    """Dò chương -> gắn cờ -> bỏ khối rỗng không có cờ (sửa doc tại chỗ, trả lại doc)."""
    apply_sections(doc)
    apply_flags(doc)
    if dropped := drop_empty_blocks(doc):
        log.info("  bo %d block rong khong co co: %s", sum(dropped.values()), dict(dropped))
        apply_flags(doc)
    return doc


def drop_empty_blocks(doc: ParsedDocument) -> Counter[str]:
    """Bỏ các khối không có nội dung mà cũng không bị cờ -> đếm số khối bỏ theo lý do.

    Cách làm: khối rỗng có cờ thì giữ lại để người duyệt xem; không đánh lại id; tính lại page_hash.
    """
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
