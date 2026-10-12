"""Lớp cha của mọi parser: file gốc -> ParsedDocument đã xét bảng + ảnh.

Biến thể chỉ cần viết `parse_layout` (bước ①). Chép bảng (②b) và xét ảnh (②) chỉ cần
ParsedDocument + PDF nên dùng chung ở đây.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from models.parsed_document import ParsedDocument
from parser.pictures import apply_pictures, run_pictures
from parser.tables import apply_tables, run_tables

log = logging.getLogger(__name__)


class BaseParser(ABC):
    """File gốc -> ParsedDocument. Mọi cache nằm trong `out_dir`."""

    kind: ClassVar[str]

    def __init__(self, model: str, out_dir: Path):
        self.model = model
        self.out_dir = out_dir

    @abstractmethod
    def parse_layout(self, src: Path, pdf: Path, *, redo: bool = False) -> ParsedDocument:
        """① Bố cục + chữ + bảng. Có cache thì không gọi API, trừ `redo`."""

    def post_processing(self, doc: ParsedDocument, pdf: Path, *, redo: bool = False,
                        table_pages: set[int] | None = None) -> ParsedDocument:
        """②b chép lại bảng (chỉ ở `table_pages` nếu có), ② xét ảnh -> áp vào `doc`."""
        tables_json = self.out_dir / "tables.json"
        pictures_json = self.out_dir / "pictures.json"
        # Chọn trang = ÉP chép lại bảng ở đó (trang khác giữ cache) — không cần --redo, mà --redo
        # thì gọi lại ① cả file.
        run_tables(doc, pdf, tables_json, self.model, redo=redo or table_pages is not None,
                   pages=table_pages)
        run_pictures(doc, pdf, pictures_json, self.model, redo=redo)   # cần thấy MỌI ảnh -> chạy trước apply
        log.info("  ap bang (②b): %s", dict(apply_tables(doc, tables_json)))
        log.info("  ap anh (②): %s", dict(apply_pictures(doc, pictures_json)))
        return doc

    def run(self, src: Path, pdf: Path, *, redo: bool = False,
            table_pages: set[int] | None = None) -> ParsedDocument:
        """① rồi ②. `src` cho doc_id / nguồn, `pdf` để vẽ trang."""
        doc = self.parse_layout(src, pdf, redo=redo)
        doc = self.post_processing(doc, pdf, redo=redo, table_pages=table_pages)
        return doc.link()                 # ②b dựng lại ParsedTable mới -> gán lại block.page

    def save(self, doc: ParsedDocument) -> Path:
        """-> out_dir/document.json."""
        path = self.out_dir / "document.json"
        path.write_text(doc.to_json(), encoding="utf-8")
        log.info("ghi -> %s (%d KB)", path, path.stat().st_size // 1024)
        return path
