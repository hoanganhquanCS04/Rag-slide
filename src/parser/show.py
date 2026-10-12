"""Xem `out/parsed/<doc_id>/document.json` đã enrich (link / chương / vá tay / cờ).

    python src/parsing/cli.py show onboarding_kit                  # tổng quan + chương + cờ
    python src/parsing/cli.py show onboarding_kit --page 7 --full  # một trang, không cắt chữ

CHỈ để xem. Parse (tạo document.json) đi MỘT đường: `python src/parser/cli.py <file>`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file, không qua -m
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parser.cli import OUT, parse_pages, report
from parsing.enrich import enrich
from parsing.models import ParsedDocument, ParsedParagraph

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("parsing")


# ------------------------------------------------------------------------- chạy


def cmd_show(args: argparse.Namespace) -> int:
    p = Path(args.doc)
    doc = enrich(ParsedDocument.load(p if p.suffix == ".json" else OUT / args.doc / "document.json"))
    if args.page:
        show_pages(doc, parse_pages(args.page), args.full)
    else:
        report(doc)
    return 0


# ------------------------------------------------------------------------ hiện


def _cut(s: str, full: bool, n: int = 72) -> str:
    s = s.replace("\n", " ⏎ ")
    return s if full else (s[:n] + ("…" if len(s) > n else ""))


def show_pages(doc: ParsedDocument, pages: list[int], full: bool) -> None:
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
            log.info("    %-10s %-12s %6.2f%% %-10s %s",
                     b.id, kind, b.area * 100, b.provenance.value, _cut(body, full))
            for h in b.hrefs:
                log.info("    %-10s ↳ link %s -> %s", "", h.text, h.url)

        for f in doc.flags:
            if f.page_no == pg:
                log.info("    [%s] %s — %s", f.severity, f.kind, f.detail)


# ------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="parsing", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    show = sub.add_parser("show", help="xem document.json (da enrich)")
    show.add_argument("doc", help="doc_id (vd onboarding_kit) hoac duong dan .json")
    show.add_argument("--page", default=None, help="'11' | '9,11' | '9-15'")
    show.add_argument("--full", action="store_true", help="in du, khong cat chu")
    show.set_defaults(fn=cmd_show)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
