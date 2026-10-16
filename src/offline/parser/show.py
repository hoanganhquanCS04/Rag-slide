"""Xem `document.json` đã enrich (chương / cờ) trên màn hình.

    python src/offline/parser/show.py onboarding_kit                  # tổng quan + chương + cờ
    python src/offline/parser/show.py onboarding_kit --page 7 --full  # từng khối của trang 7, không cắt chữ

CHỈ để xem. Parse (tạo document.json) đi đường `python src/offline/parser/cli.py <file>`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file: THAY src/offline/parser/ bằng src/ — để tables.py,
    sys.path[0] = str(Path(__file__).resolve().parents[2])  # flags.py… không che module trùng tên

from offline.parser.cli import OUT, report
from offline.parser.enrich import enrich
from models.parsed_document import ParsedDocument, ParsedParagraph

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("parser.show")


# ------------------------------------------------------------------------ hiện


def _cut(s: str, full: bool, n: int = 72) -> str:
    """Rút gọn chữ để in một dòng: xuống dòng thành "⏎", dài quá thì cắt (trừ khi `full`)."""
    s = s.replace("\n", " ⏎ ")
    return s if full else (s[:n] + ("…" if len(s) > n else ""))


def show_pages(doc: ParsedDocument, pages: list[int], full: bool) -> None:
    """In từng trang được chọn: tiêu đề, chương, hash, loại trang, mọi khối và các cờ của trang."""
    for pg in pages:
        page = doc.page(pg)
        if page is None:
            log.warning("khong co trang %d", pg)
            continue
        sec = doc.section_of(pg)
        where = f"{sec.id} ({sec.title}, p{sec.start_page}-{sec.end_page})" if sec else "—"
        log.info("")
        log.info("--- trang %d | %s | chuong: %s", pg, page.title or "(khong tieu de)", where)
        log.info("    hash=%s  slide_type=%s", page.page_hash, page.slide_type)

        for b in page.blocks:
            kind = f"para/{b.role}" if isinstance(b, ParsedParagraph) else b.kind
            body = b.content or f"(rong — {getattr(b, 'why_empty', None)})"
            log.info("    %-10s %-12s %6.2f%% %s",
                     b.id, kind, b.area * 100, _cut(body, full))

        for f in doc.flags:
            if f.page_no == pg:
                log.info("    [%s] %s — %s", f.severity, f.kind, f.detail)


# ------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    """Đọc tham số, nạp + enrich document.json; có `--page` thì in các trang đó, không thì in tổng quan."""
    ap = argparse.ArgumentParser(prog="parser.show", description=__doc__.splitlines()[0])
    ap.add_argument("doc", help="doc_id (vd onboarding_kit) hoac duong dan .json")
    ap.add_argument("--page", nargs="+", type=int, default=None, help="11 | 9 11 | {9..15}")
    ap.add_argument("--full", action="store_true", help="in du, khong cat chu")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    p = Path(args.doc)
    doc = enrich(ParsedDocument.load(p if p.suffix == ".json" else OUT / args.doc / "document.json"))
    if args.page:
        show_pages(doc, args.page, args.full)
    else:
        report(doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
