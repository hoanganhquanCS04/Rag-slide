"""Tìm các chỗ cần người xem (cờ) trong ParsedDocument.

Để làm gì: người duyệt chỉ xem phần bị cờ, không phải mở cả deck — nên luật cờ phải chọn kỹ,
không cờ bừa.
"""

from __future__ import annotations

import logging

from models.parsed_document import Flag, ParsedDocument

log = logging.getLogger(__name__)


def _empty_page(doc: ParsedDocument) -> list[Flag]:
    """Cờ `empty_page`: trang không có chữ (ngoài tiêu đề) và không có ảnh / bảng có nội dung.

    Cách làm: bỏ qua trang chuyển chương (rỗng là đúng); bắt cả trang VLM trả JSON hỏng ở bước ① —
    lý do xem trong `logs/vlm_pipeline/<doc_id>/pNNN.txt`.
    """
    out: list[Flag] = []
    for page in doc.pages:
        if not page.is_text_starved or page.slide_type == "section_divider":
            continue
        if any(im.content for im in page.images) or any(t.content for t in page.tables):
            continue
        out.append(
            Flag(
                kind="empty_page",
                page_no=page.page_no,
                detail=(
                    f"{len(page.paragraphs)} block chu, "
                    f"{len(page.images)} anh nhung 0 co mo ta — "
                    f"xem logs/vlm_pipeline/{doc.doc_id}/p{page.page_no:03d}.txt"
                ),
                severity="error",
            )
        )
    return out


def _image_not_described(doc: ParsedDocument) -> list[Flag]:
    """Cờ `image_not_described`: ảnh đủ to mà không có mô tả (hoặc gọi VLM bị lỗi)."""
    out: list[Flag] = []
    for page in doc.pages:
        for im in page.images:
            if im.needs_review:
                out.append(
                    Flag(
                        kind="image_not_described",
                        page_no=page.page_no,
                        block_id=im.id,
                        detail=f"chiem {im.area:.1%} trang, ly do={im.why_empty}",
                        severity="error",
                    )
                )
    return out


def _table_empty(doc: ParsedDocument) -> list[Flag]:
    """Cờ `table_empty`: có vùng bảng mà không đọc ra chữ nào (thường là ảnh chụp bảng)."""
    return [
        Flag(
            kind="table_empty",
            page_no=page.page_no,
            block_id=t.id,
            detail=f"chiem {t.area:.1%} trang, 0 o co chu — co the la anh chup bang",
            severity="error",
        )
        for page in doc.pages
        for t in page.tables
        if not t.content
    ]


def _no_sections(doc: ParsedDocument) -> list[Flag]:
    """Cờ `no_sections`: không dò được chương nào."""
    if doc.sections:
        return []
    return [
        Flag(
            kind="no_sections",
            detail="khong tim thay trang muc luc khop -> moi trang la mot don vi doc lap",
            severity="info",
        )
    ]


def build_flags(doc: ParsedDocument) -> list[Flag]:
    """Chạy mọi luật cờ -> danh sách cờ, xếp theo mức độ (error trước) rồi theo trang."""
    flags = (
        _empty_page(doc)
        + _image_not_described(doc)
        + _table_empty(doc)
        + _no_sections(doc)
    )
    order = {"error": 0, "warn": 1, "info": 2}
    flags.sort(key=lambda f: (order[f.severity], f.page_no or 0))
    return flags


def apply_flags(doc: ParsedDocument) -> ParsedDocument:
    """Gán danh sách cờ vào `doc.flags` và in số cờ theo loại."""
    doc.flags = build_flags(doc)
    by_kind: dict[str, int] = {}
    for f in doc.flags:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    log.info("  co: %s", by_kind or "(khong co)")
    return doc
