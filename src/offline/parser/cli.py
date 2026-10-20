"""Lệnh parse (S0): file gốc -> `src/output/parser/<doc_id>/document.json`.

    python src/offline/parser/cli.py data/raw/onboarding_kit.pdf                     # có cache thì dùng cache
    python src/offline/parser/cli.py data/raw/onboarding_kit.pdf --redo              # gọi lại VLM cả file
    python src/offline/parser/cli.py data/raw/onboarding_kit.pdf --table-pages 21 22 # chỉ chép lại bảng 2 trang

Cách làm: ① bố cục -> ②b chép bảng -> ② xét ảnh -> document.json; cache nằm cùng thư mục.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file: THAY src/offline/parser/ bằng src/ — để tables.py,
    sys.path[0] = str(Path(__file__).resolve().parents[2])  # sections.py… không che module trùng tên

from offline.parser.variants.docling import DoclingParser
from utils.config import PARSER_OUTPUT, console_setup
from offline.parser.enrich import enrich, page_pdf
from models.parsed_document import ParsedDocument, slugify_doc_id

log = logging.getLogger("parser")


def parse_file(src: Path, *, model: str, redo: bool = False,
               table_pages: set[int] | None = None) -> int:
    """Parse một file và ghi `document.json`.

    Cách làm: tìm file PDF để vẽ trang -> DoclingParser chạy ① ②b ② -> ghi file -> tính chương
    (enrich) để in báo cáo.
    """
    if not src.exists():
        raise SystemExit(f"khong thay file: {src}")
    if (pdf := page_pdf(src)) is None:
        raise SystemExit(f"can PDF cung ten: {src.with_suffix('.pdf')} (scripts/pptx2pdf.ps1)")

    p = DoclingParser(model, PARSER_OUTPUT / slugify_doc_id(src.stem))
    doc = p.run(src, pdf, redo=redo, table_pages=table_pages)
    p.save(doc)
    report(enrich(doc))      # chương không nằm trong document.json — tính lại để in
    return 0


def report(doc: ParsedDocument) -> None:
    """In tóm tắt deck ra màn hình: số trang, số khối, ảnh có mô tả, bảng và các chương."""
    log.info("")
    log.info("=== %s", doc.doc_id)
    log.info("    %d trang | %d block", doc.n_pages, sum(len(p.blocks) for p in doc.pages))
    log.info("    anh co mo ta %d | bang %d", doc.n_described_images,
             sum(len(p.tables) for p in doc.pages))

    for s in doc.sections:
        log.info("    %s  p%d-%d  conf=%.2f  %s", s.id, s.start_page, s.end_page, s.confidence, s.title)


def main(argv: list[str] | None = None) -> int:
    """Đọc tham số dòng lệnh rồi gọi `parse_file`."""
    ap = argparse.ArgumentParser(prog="parser", description=__doc__.splitlines()[0])
    ap.add_argument("file", type=Path, help="data/raw/<ten>.pdf | .pptx (can .pdf cung ten)")
    ap.add_argument("--redo", action="store_true", help="goi VLM lai du da co cache")
    ap.add_argument("--model", default=os.environ.get("VLM_MODEL", "gemma4"))
    ap.add_argument("--table-pages", nargs="+", type=int, default=None,
                    help="chi chep lai bang o cac trang nay (②b), khong goi lai ①: 21 22 | {6..9}")
    args = ap.parse_args(argv)
    console_setup()
    logging.getLogger("httpx").setLevel(logging.WARNING)

    table_pages = set(args.table_pages) if args.table_pages else None
    return parse_file(args.file, model=args.model, redo=args.redo,
                      table_pages=table_pages)


if __name__ == "__main__":
    raise SystemExit(main())
