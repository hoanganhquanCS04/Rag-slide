"""Lớp cha của parser: file gốc -> ParsedDocument HOÀN CHỈNH (bảng, ảnh, chương) -> `document.json`.

Để làm gì: phần dùng chung cho mọi parser; parser cụ thể chỉ phải viết bước ① (`parse_layout`).
`document.json` là bản cuối cùng: các bước sau (chunker, kịch bản, hỏi đáp) chỉ việc `ParsedDocument.load`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import Counter
from pathlib import Path

from models.parsed_document import ParsedDocument
from offline.parser.pictures import apply_pictures, run_pictures
from offline.parser.sections import apply_sections
from offline.parser.tables import apply_tables, run_tables

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


class BaseParser(ABC):
    """Khung chạy S0: ① bố cục -> ②b chép bảng -> ② xét ảnh -> bỏ khối rỗng, dò chương -> ghi `document.json`.

    Cách làm: mọi file cache nằm trong `out_dir` (thư mục output của deck).
    """

    def __init__(self, model: str, out_dir: Path):
        """Nhớ tên model VLM và thư mục output của deck."""
        self.model = model
        self.out_dir = out_dir

    @abstractmethod
    def parse_layout(self, src: Path, pdf: Path, *, redo: bool = False) -> ParsedDocument:
        """Bước ①: đọc bố cục + chữ + bảng của từng trang -> ParsedDocument (lớp con tự viết)."""

    def post_processing(self, doc: ParsedDocument, pdf: Path, *, redo: bool = False,
                        table_pages: set[int] | None = None) -> ParsedDocument:
        """Bước ②b + ② rồi hoàn thiện: chép lại bảng, xét ảnh, bỏ khối rỗng, dò chương.

        Cách làm: gọi VLM (có cache) ghi ra `tables.json`, `pictures.json`, áp hai file đó vào doc;
        rồi bỏ khối không có chữ và dò chương từ trang mục lục (luật, không gọi API).
        `table_pages` = chỉ chép lại bảng ở các trang này.
        """
        tables_json = self.out_dir / "tables.json"
        pictures_json = self.out_dir / "pictures.json"
        # Chọn trang = ÉP chép lại bảng ở đó (trang khác giữ cache) — không cần --redo, mà --redo
        # thì gọi lại ① cả file.
        run_tables(doc, pdf, tables_json, self.model, redo=redo or table_pages is not None,
                   pages=table_pages)
        run_pictures(doc, pdf, pictures_json, self.model, redo=redo)   # cần thấy MỌI ảnh -> chạy trước apply
        log.info("  ap bang (②b): %s", dict(apply_tables(doc, tables_json)))
        log.info("  ap anh (②): %s", dict(apply_pictures(doc, pictures_json)))
        if dropped := drop_empty_blocks(doc):
            log.info("  bo %d block rong: %s", sum(dropped.values()), dict(dropped))
        apply_sections(doc)
        return doc

    def run(self, src: Path, pdf: Path, *, redo: bool = False,
            table_pages: set[int] | None = None) -> ParsedDocument:
        """Chạy đủ S0 cho một file: bước ① rồi ②b + ② + hoàn thiện -> ParsedDocument."""
        doc = self.parse_layout(src, pdf, redo=redo)
        return self.post_processing(doc, pdf, redo=redo, table_pages=table_pages)

    def save(self, doc: ParsedDocument) -> Path:
        """Ghi doc ra `out_dir/document.json` -> đường dẫn file."""
        path = self.out_dir / "document.json"
        path.write_text(doc.to_json(), encoding="utf-8")
        log.info("ghi -> %s (%d KB)", path, path.stat().st_size // 1024)
        return path
