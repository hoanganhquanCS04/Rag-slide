"""Lớp cha của parser: file gốc -> ParsedDocument đã chép lại bảng và xét ảnh.

Để làm gì: phần dùng chung cho mọi parser; parser cụ thể chỉ phải viết bước ① (`parse_layout`).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from models.parsed_document import ParsedDocument
from offline.parser.pictures import apply_pictures, run_pictures
from offline.parser.tables import apply_tables, run_tables

log = logging.getLogger(__name__)


class BaseParser(ABC):
    """Khung chạy S0: ① bố cục -> ②b chép bảng -> ② xét ảnh -> ghi `document.json`.

    Cách làm: mọi file cache nằm trong `out_dir` (thư mục output của deck).
    """

    kind: ClassVar[str]

    def __init__(self, model: str, out_dir: Path):
        """Nhớ tên model VLM và thư mục output của deck."""
        self.model = model
        self.out_dir = out_dir

    @abstractmethod
    def parse_layout(self, src: Path, pdf: Path, *, redo: bool = False) -> ParsedDocument:
        """Bước ①: đọc bố cục + chữ + bảng của từng trang -> ParsedDocument (lớp con tự viết)."""

    def post_processing(self, doc: ParsedDocument, pdf: Path, *, redo: bool = False,
                        table_pages: set[int] | None = None) -> ParsedDocument:
        """Bước ②b + ②: chép lại bảng, xét ảnh, rồi gắn kết quả vào doc.

        Cách làm: gọi VLM (có cache) ghi ra `tables.json`, `pictures.json`, sau đó áp hai file đó vào doc;
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
        return doc

    def run(self, src: Path, pdf: Path, *, redo: bool = False,
            table_pages: set[int] | None = None) -> ParsedDocument:
        """Chạy đủ S0 cho một file: bước ① rồi ②b + ② -> ParsedDocument."""
        doc = self.parse_layout(src, pdf, redo=redo)
        return self.post_processing(doc, pdf, redo=redo, table_pages=table_pages)

    def save(self, doc: ParsedDocument) -> Path:
        """Ghi doc ra `out_dir/document.json` -> đường dẫn file."""
        path = self.out_dir / "document.json"
        path.write_text(doc.to_json(), encoding="utf-8")
        log.info("ghi -> %s (%d KB)", path, path.stat().st_size // 1024)
        return path
