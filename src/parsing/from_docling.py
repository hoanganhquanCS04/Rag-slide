"""Đổi file .json của docling (DoclingDocument) sang ParsedDocument.

Năm việc:

  1. Đi theo `body.children` -> ĐÚNG THỨ TỰ ĐỌC. Mảng `texts[]` trong file KHÔNG
     theo thứ tự này, đọc tuần tự mảng là loạn.
  2. Đổi toạ độ: gốc DƯỚI-TRÁI của docling -> gốc TRÊN-TRÁI, chuẩn hoá [0,1], ra `polygon`.
  3. Bỏ header/footer/số trang lặp ở mọi trang (docling gắn `page_header` / `page_footer` /
     `content_layer: furniture`) — không phải nội dung; chương đã lấy từ trang mục lục.
  4. Gắn `provenance`: chữ -> text_layer (`docling_run.py` tắt OCR). Ảnh ở đây CHƯA có mô tả —
     mô tả do VLM viết ở bước ② (`layout.py`) khi nhìn cả trang.
  5. Tính `page_hash` để incremental build biết trang nào đổi.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterator

from parsing.models import (
    AnyBlock,
    ParsedDocument,
    ParsedImage,
    ParsedPage,
    ParsedParagraph,
    ParsedTable,
    ParserInfo,
    Provenance,
    SourceInfo,
    polygon_from_box,
)

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float]   # (trái, trên, phải, dưới), [0,1], gốc trên-trái

# docling label -> role của ParsedParagraph
_ROLE = {
    "section_header": "title",
    "title": "title",
    "caption": "caption",
}
_FURNITURE_LABELS = {"page_header", "page_footer"}


def norm_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def from_pptx(raw: dict[str, Any]) -> bool:
    """docling.json này đọc từ .pptx? Hệ toạ độ khác PDF — xem `docling_box`."""
    return "presentationml" in ((raw.get("origin") or {}).get("mimetype") or "")


def _clamp(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def docling_box(prov: dict[str, Any], page_w: float, page_h: float, *, is_pptx: bool = False) -> Box:
    """docling BOTTOMLEFT (t > b, đo từ đáy lên) -> TOPLEFT chuẩn hoá.

    NGOẠI LỆ .pptx: backend pptx của docling gắn nhãn `BOTTOMLEFT` nhưng số thật đo từ
    ĐỈNH xuống (EMU của python-pptx) — tin nhãn là lật trục y. Đo trên tetnguyendan p1:
    "LỄ HỘI LỚN NHẤT NĂM" nằm trên "Tết Nguyên Đán" có t=2557462 < 3548062; lật theo nhãn
    thì ra nằm DƯỚI. Không tin nhãn, chỉ lấy min/max.
    """
    b = prov["bbox"]
    if is_pptx:
        top, bottom = b["b"], b["t"]
    elif b.get("coord_origin", "BOTTOMLEFT").upper() == "BOTTOMLEFT":
        top, bottom = page_h - b["t"], page_h - b["b"]
    else:
        top, bottom = b["t"], b["b"]
    if top > bottom:                      # phòng trường hợp dữ liệu lật ngược
        top, bottom = bottom, top
    return (_clamp(b["l"] / page_w), _clamp(top / page_h),
            _clamp(b["r"] / page_w), _clamp(bottom / page_h))


def resolve_ref(doc: dict[str, Any], ref: str) -> dict[str, Any] | None:
    """'#/texts/12' -> doc['texts'][12]"""
    cur: Any = doc
    for part in ref.lstrip("#/").split("/"):
        if cur is None:
            return None
        cur = cur[int(part)] if part.isdigit() else cur.get(part)
    return cur


def _walk(doc: dict[str, Any], node: dict[str, Any], seen: set[str]) -> Iterator[dict[str, Any]]:
    """Duyệt cây theo thứ tự đọc. Group trả về chính nó rồi mới tới con."""
    for ref in node.get("children", []):
        item = resolve_ref(doc, ref["$ref"])
        if item is None:
            continue
        sref = item.get("self_ref", "")
        if sref in seen:
            continue
        seen.add(sref)
        yield item
        # Group `list` tự gom con ở bước sau (thành một ParsedParagraph role="list").
        # Group KHÁC thì phải đi vào: docling đọc PPTX nhét MỖI SLIDE vào một group
        # `chapter` — không đi vào là mất sạch chữ (đo được: 62 đoạn -> 0 khối).
        # PDF chỉ có group `list` nên luồng PDF không đổi.
        if not (sref.startswith("#/groups/") and item.get("label") == "list"):
            yield from _walk(doc, item, seen)


def _page_no(item: dict[str, Any]) -> int | None:
    prov = item.get("prov") or []
    return prov[0]["page_no"] if prov else None


def _make_image(bid: str, box: Box, area_threshold: float) -> ParsedImage:
    """Ảnh docling khoanh được — chưa có mô tả (VLM tả ở bước ②). Trang trượt bố cục VLM thì
    giữ nguyên thế này: ảnh to thành `not_described` -> cờ cho người duyệt."""
    im = ParsedImage(id=bid, polygon=polygon_from_box(*box), provenance=Provenance.VLM)
    im.why_empty = "area_below_threshold" if im.area < area_threshold else "not_described"
    return im


def _make_table(item: dict[str, Any], bid: str, box: Box) -> ParsedTable:
    grid = (item.get("data") or {}).get("grid") or []
    return ParsedTable(
        id=bid, polygon=polygon_from_box(*box), provenance=Provenance.TEXT_LAYER,   # chữ trong ô
        cells=[[norm_text(c.get("text")) for c in row] for row in grid],
    )


def slugify_doc_id(stem: str) -> str:
    """Tên file gốc -> doc_id sạch: "3_DataVisualization (1)" -> "3_datavisualization".

    `doc_id` chui vào MỌI `chunk_id`, mọi tên file vector, và sau này là tên collection
    Qdrant. Để nguyên tên file thô thì nó mang theo dấu cách, "(1)", dấu tiếng Việt —
    những thứ không liên quan gì tới danh tính tài liệu.
    """
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)         # bỏ "(1)" của bản tải trùng
    # bỏ dấu TRƯỚC khi lọc ký tự: không thì "Thời gian" -> "th_i_gian"
    stem = unicodedata.normalize("NFD", stem.replace("đ", "d").replace("Đ", "D"))
    stem = "".join(c for c in stem if not unicodedata.combining(c))
    stem = re.sub(r"[^0-9A-Za-z]+", "_", stem).strip("_")
    return stem.lower() or "doc"


def from_docling_json(
    path: str | Path,
    *,
    doc_id: str | None = None,
    source_pdf: str | None = None,
    vlm_model: str | None = None,
    picture_area_threshold: float = 0.05,
) -> ParsedDocument:
    """Nạp file .json docling sinh ra -> ParsedDocument (chưa có sections/flags)."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    is_pptx = from_pptx(raw)

    sizes = {int(k): (v["size"]["width"], v["size"]["height"]) for k, v in raw["pages"].items()}
    pages = {no: ParsedPage(page_no=no) for no in sorted(sizes)}
    counters: dict[int, int] = {no: 0 for no in pages}

    def next_id(pg: int) -> str:
        n = counters[pg]
        counters[pg] += 1
        return f"p{pg:03d}.b{n:02d}"

    seen: set[str] = set()
    # chỉ đi cây `body` — cây `furniture` của docling là header/footer lặp
    for item in _walk(raw, raw.get("body") or {}, seen):
        sref = item.get("self_ref", "")
        label = item.get("label", "")

        # Bó bullet -> MỘT ParsedParagraph role="list", các dòng ngăn bằng xuống dòng.
        # Không tách block riêng cho từng dòng: dòng lẻ chỉ vài chữ, không trả lời được gì.
        if sref.startswith("#/groups/"):
            kids = [
                k
                for ref in item.get("children", [])
                if (k := resolve_ref(raw, ref["$ref"])) is not None
            ]
            kids = [k for k in kids if k.get("label") == "list_item" and (k.get("prov") or [])]
            if not kids:
                continue
            pg = _page_no(kids[0])
            if pg is None or pg not in pages:
                continue
            boxes = [docling_box(k["prov"][0], *sizes[pg], is_pptx=is_pptx) for k in kids]
            box = (min(x[0] for x in boxes), min(x[1] for x in boxes),
                   max(x[2] for x in boxes), max(x[3] for x in boxes))
            for k in kids:
                seen.add(k.get("self_ref", ""))
            lines = [t for k in kids if (t := norm_text(k.get("text")))]
            if not lines:
                continue
            pages[pg].blocks.append(ParsedParagraph(
                id=next_id(pg), role="list", content="\n".join(lines),
                polygon=polygon_from_box(*box), provenance=Provenance.TEXT_LAYER,
            ))
            continue

        prov = item.get("prov") or []
        if not prov:
            continue
        pg = prov[0]["page_no"]
        if pg not in pages:
            continue
        box = docling_box(prov[0], *sizes[pg], is_pptx=is_pptx)

        if item.get("content_layer") == "furniture" or label in _FURNITURE_LABELS:
            continue                         # header/footer lặp — không phải nội dung

        block: AnyBlock
        if sref.startswith("#/pictures/"):
            block = _make_image(next_id(pg), box, picture_area_threshold)
        elif sref.startswith("#/tables/"):
            block = _make_table(item, next_id(pg), box)
        else:
            text = norm_text(item.get("text"))
            if not text:
                continue                 # bỏ TRƯỚC khi cấp id — không để lại lỗ trong dãy id
            block = ParsedParagraph(
                id=next_id(pg), content=text, polygon=polygon_from_box(*box),
                provenance=Provenance.TEXT_LAYER,
                # bullet mồ côi (không nằm trong group nào) vẫn là role="list"
                role="list" if label == "list_item" else _ROLE.get(label, "body"),  # type: ignore[arg-type]
            )
        pages[pg].blocks.append(block)

    for page in pages.values():
        page.title = next(
            (b.content for b in page.paragraphs if b.role == "title" and b.content), None
        )
        page.page_hash = page.compute_hash()

    # `vlm_model` = model đã sắp bố cục trang, build.py đọc từ layout.json. KHÔNG lấy từ .env —
    # .env có thể đã đổi sau lần gọi.
    doc = ParsedDocument(
        doc_id=doc_id or slugify_doc_id(path.stem),
        source=SourceInfo(
            path=source_pdf or raw.get("name", path.stem),
            sha256=str((raw.get("origin") or {}).get("binary_hash", "")),
        ),
        parser=ParserInfo(
            docling_version=_docling_version(raw),
            vlm_model=vlm_model,
            picture_area_threshold=picture_area_threshold,
        ),
        pages=list(pages.values()),
    )
    log.info("%s -> %d trang, %d anh", path.name, doc.n_pages, doc.n_images)
    return doc


def _docling_version(raw: dict[str, Any]) -> str:
    try:
        import importlib.metadata as md

        return md.version("docling")
    except Exception:
        return f"schema-{raw.get('version', '?')}"
