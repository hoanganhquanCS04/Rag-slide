"""③ docling.json + layout.json -> ParsedDocument chuẩn (`out/parsed/<doc_id>/document.json`).

Không gọi API — chạy lại bao nhiêu lần cũng được, luôn dựng MỚI từ docling.json:

    from_docling   block thô, toạ độ (header/footer lặp bị bỏ)
    layout         trang có bố cục VLM hợp lệ -> thay block (trượt kiểm tra -> giữ docling + cờ)
    links          link ẩn trong PDF -> `hrefs`, block >= nửa dòng là link -> role "links"
    sections       chương từ thanh header chạy
    patch          vá tay (data/patches/)
    flags          cờ cho người duyệt; block rỗng không cờ bị bỏ
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from parsing.flags import apply_flags
from parsing.from_docling import from_docling_json, slugify_doc_id
from parsing.layout import apply_layout, load_layout, page_pdf
from parsing.links import attach_links
from parsing.models import ParsedDocument
from parsing.patch import apply_patch, find_patch, load_patch
from parsing.sections import apply_sections

log = logging.getLogger(__name__)


def build_document(src: Path, docling_json: Path, layout_json: Path) -> ParsedDocument:
    raw = json.loads(docling_json.read_text(encoding="utf-8"))
    layout = load_layout(layout_json)
    model = next((e["model"] for e in layout["pages"].values()), None)

    log.info("③ dung document.json")
    doc = from_docling_json(docling_json, doc_id=slugify_doc_id(src.stem), source_pdf=str(src),
                            vlm_model=model)
    apply_layout(doc, raw, layout)
    if pdf := page_pdf(src):
        attach_links(doc, pdf)
    apply_sections(doc)
    if pf := find_patch(doc.doc_id):
        log.info("  va tay tu %s", pf.name)
        apply_patch(doc, load_patch(pf))
    apply_flags(doc)
    if dropped := drop_empty_blocks(doc):
        log.info("  bo %d block rong khong co co: %s", sum(dropped.values()), dict(dropped))
        apply_flags(doc)
    return doc


def drop_empty_blocks(doc: ParsedDocument) -> Counter[str]:
    """Bỏ block `content: null` mà KHÔNG có cờ -> {lý do: số block bỏ}.

    Ảnh nhỏ, ảnh trang trí: không vào KB, không vào kịch bản, chỉ là nhiễu khi đọc file. Block
    CÓ cờ (ảnh to không tả được, bảng rỗng — thường là ảnh chụp bảng) giữ lại cho người duyệt.
    Id các block còn lại KHÔNG đánh lại -> id trong file vá vẫn trỏ đúng.
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
