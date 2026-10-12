"""File gốc -> `out/parsed/<doc_id>/document.json`. ĐƯỜNG DUY NHẤT để parse (S0).

    python src/parser/cli.py data/raw/onboarding_kit.pdf                     # dùng cache nếu có
    python src/parser/cli.py data/raw/onboarding_kit.pdf --redo              # gọi VLM lại cả file
    python src/parser/cli.py data/raw/onboarding_kit.pdf --table-pages 21,22 # chỉ chép lại bảng 2 trang
    python src/parser/cli.py data/raw/onboarding_kit.pdf --parser pdfplumber

Luồng: ① bố cục -> ②b chép bảng -> ② xét ảnh -> document.json. Cache cùng thư mục:
docling_vlmpipeline.json · tables.json · pictures.json. Link / chương / vá tay / cờ: `parsing.enrich`.
Xem lại kết quả: `python src/parsing/cli.py show <doc_id> [--page 7]`.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file, không qua -m
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from parser.base import BaseParser
from parsing.enrich import enrich, page_pdf
from parsing.models import ParsedDocument, slugify_doc_id

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("parser")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "parsed"

# import lúc chọn — biến thể này hỏng không kéo biến thể kia theo
PARSERS = {
    "docling": "parser.variants.docling:DoclingParser",
    "pdfplumber": "parser.variants.pdf_plumber:PDFPlumberParser",
}


def make_parser(kind: str, model: str, out_dir: Path) -> BaseParser:
    module, cls = PARSERS[kind].split(":")
    return getattr(importlib.import_module(module), cls)(model, out_dir)


def parse_file(src: Path, *, model: str, parser: str = "docling", redo: bool = False,
               table_pages: set[int] | None = None) -> int:
    """`table_pages`: chỉ chép lại bảng ở các trang này (bảng khác dùng cache)."""
    if not src.exists():
        raise SystemExit(f"khong thay file: {src}")
    if (pdf := page_pdf(src)) is None:
        raise SystemExit(f"can PDF cung ten: {src.with_suffix('.pdf')} (scripts/pptx2pdf.ps1)")

    p = make_parser(parser, model, OUT / slugify_doc_id(src.stem))
    doc = p.run(src, pdf, redo=redo, table_pages=table_pages)
    p.save(doc)
    report(enrich(doc))      # chương / cờ không nằm trong document.json — tính lại để in
    return 0


def parse_pages(spec: str) -> list[int]:
    """'11' | '9,11' | '9-15'"""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    return sorted(out)


def report(doc: ParsedDocument) -> None:
    log.info("")
    log.info("=== %s", doc.doc_id)
    log.info("    %d trang | %d block | %d link an", doc.n_pages,
             sum(len(p.blocks) for p in doc.pages), sum(len(b.hrefs) for p in doc.pages for b in p.blocks))
    log.info("    anh co mo ta %d | bang %d", doc.n_described_images,
             sum(len(p.tables) for p in doc.pages))

    for s in doc.sections:
        log.info("    %s  p%d-%d  conf=%.2f  %s", s.id, s.start_page, s.end_page, s.confidence, s.title)
    for f in doc.flags:
        where = f"p{f.page_no}" if f.page_no else "-"
        log.info("    [%s] %-22s %-5s %s", f.severity, f.kind, where, f.detail)


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(prog="parser", description=__doc__.splitlines()[0])
    ap.add_argument("file", type=Path, help="data/raw/<ten>.pdf | .pptx (can .pdf cung ten)")
    ap.add_argument("--parser", default="docling", choices=PARSERS)
    ap.add_argument("--redo", action="store_true", help="goi VLM lai du da co cache")
    ap.add_argument("--model", default=os.environ.get("VLM_MODEL", "gemma4"))
    ap.add_argument("--table-pages", default=None,
                    help="chi chep lai bang o cac trang nay (②b), khong goi lai ①: '21,22' | '6-9'")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    table_pages = set(parse_pages(args.table_pages)) if args.table_pages else None
    return parse_file(args.file, model=args.model, parser=args.parser, redo=args.redo,
                      table_pages=table_pages)


if __name__ == "__main__":
    raise SystemExit(main())
