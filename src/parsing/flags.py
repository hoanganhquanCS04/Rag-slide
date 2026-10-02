"""Sinh danh sách chỗ cần người xem.

§10 cấm bắt người duyệt mở cả deck — chỉ duyệt phần bị flag. §11 lại có gate
`Flag precision (S7) >= 60%`, nên luật flag phải CHỌN KỸ, không flag bừa.

Ví dụ cụ thể về chọn kỹ: trên `3_DataVisualization` có 43 ảnh không được mô tả.
Flag hết 43 cái thì precision sụp, vì đã đo được cả 43 đều dưới 1% diện tích và
phần lớn là thanh điều khiển PowerPoint lọt vào lúc export — bỏ chúng là ĐÚNG.
Luật đúng phải là "ảnh TRÊN ngưỡng mà vẫn không mô tả được", và luật đó trên file
này bắn ra 0 flag.
"""

from __future__ import annotations

import logging

from parsing.models import Flag, ParsedDocument

log = logging.getLogger(__name__)


def _empty_page(doc: ParsedDocument) -> list[Flag]:
    """Không chữ (ngoài tiêu đề) mà cũng không mô tả ảnh nào -> vào KB gần như rỗng.

    Trang phân mục rỗng là ĐÚNG thiết kế — không bắn (trước đây bắn oan 7 cờ error,
    CLI thoát mã 1, CI đỏ giả). Trang chỉ có tiêu đề + MỘT BẢNG có chữ cũng không rỗng —
    Onboarding p21/p22/p49/p50 (bảng chấm công, danh mục văn bản ATTT) từng bị bắn oan.
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
                    f"{len(page.images)} anh nhung 0 co mo ta"
                ),
                severity="error",
            )
        )
    return out


def _image_not_described(doc: ParsedDocument) -> list[Flag]:
    """Ảnh TRÊN ngưỡng diện tích mà vẫn không có mô tả -> mất nội dung thật."""
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
    """Docling khoanh được vùng bảng mà không đọc ra chữ nào -> mất nội dung thật.

    Đo trên Onboarding Kit: cả 3 bảng rỗng (p20 hệ số OT, p33 mã giảm giá Vinpearl, p38 quy
    định XLVP) là ẢNH CHỤP bảng dán vào slide — không có text layer, OCR tắt. VLM cũng không
    chạm tới vì docling gán nhãn `table` chứ không phải `picture`. Chỉ vá tay được.
    """
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


def _layout_failed(doc: ParsedDocument) -> list[Flag]:
    """VLM sắp bố cục mà trượt kiểm tra (sót chữ, bịa id, bỏ quên vùng) -> trang dùng docling.

    Docling có thể trộn cột, băm vụn chữ (Onboarding p10: đảo nghĩa quy định xe xăng) —
    trang này cần người xem lại, hoặc gọi lại VLM: `cli.py run <file> --pages N`.
    """
    return [
        Flag(kind="layout_failed", page_no=p.page_no, detail=p.layout_error, severity="warn")
        for p in doc.pages
        if p.layout_error
    ]


def _no_sections(doc: ParsedDocument) -> list[Flag]:
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
    flags = (
        _empty_page(doc)
        + _image_not_described(doc)
        + _table_empty(doc)
        + _layout_failed(doc)
        + _no_sections(doc)
    )
    order = {"error": 0, "warn": 1, "info": 2}
    flags.sort(key=lambda f: (order[f.severity], f.page_no or 0))
    return flags


def apply_flags(doc: ParsedDocument) -> ParsedDocument:
    doc.flags = build_flags(doc)
    by_kind: dict[str, int] = {}
    for f in doc.flags:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    log.info("  co: %s", by_kind or "(khong co)")
    return doc
