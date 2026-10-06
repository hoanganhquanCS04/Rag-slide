"""S0 — một lệnh cho cả luồng: file gốc (.pdf / .pptx) -> `out/parsed/<doc_id>/`.

    ① docling.json    docling đọc file: chữ + toạ độ + vùng ảnh/bảng          docling_run.py
    ② layout.json     VLM nhìn cả trang, sắp chữ thành khối — TỐN API         layout.py
       └ ②b bảng      cắt ảnh từng bảng, VLM chép ra cells — TỐN API (lần đầu) layout.py
    ③ document.json   ParsedDocument chuẩn — KB, kịch bản, runtime đọc file này build.py

    python src/parsing/cli.py run "data/raw/Onboarding Kit.pdf"                # đủ 3 bước
    python src/parsing/cli.py run "data/raw/Onboarding Kit.pdf" --pages 7,10   # chỉ gọi VLM 2 trang
    python src/parsing/cli.py run "data/raw/Onboarding Kit.pdf" --no-vlm       # không gọi API
    python src/parsing/cli.py run "data/raw/Onboarding Kit.pdf" --redo         # chạy lại docling
    python src/parsing/cli.py show onboarding_kit --page 7 --full

Bước nào có sẵn thì bỏ qua: docling.json có rồi thì không chạy lại docling (trừ --redo);
trang có trong layout.json mà không đổi gì thì không gọi VLM lại (trừ khi nêu trong --pages).
③ luôn dựng lại. Trang chưa có bố cục VLM dùng block docling.
Mã thoát: 0 xong · 3 xong nhưng có cờ mức error (file vẫn ghi đủ, CI bắt được) · 1 hỏng thật
(không thấy file, thiếu khoá API…) — `run_deck.sh` gặp 3 thì chạy tiếp, gặp 1 thì dừng.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file, không qua -m
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parsing.build import build_document
from parsing.docling_run import run_docling
from parsing.from_docling import slugify_doc_id
from parsing.layout import page_pdf, run_layout, run_tables
from parsing.models import ParsedDocument, ParsedParagraph

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "parsed"          # out/parsed/<doc_id>/{docling,layout,document}.json

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")          # VLM_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL
except ImportError:
    pass

log = logging.getLogger("parsing")

# Riêng cho "có cờ error": mã 1 là mã Python tự trả khi dừng vì lỗi (SystemExit("...")),
# trùng nhau thì run_deck.sh không phân biệt được — từng chạy tiếp khi không thấy file.
EXIT_FLAGS = 3


# ------------------------------------------------------------------------- chạy


def cmd_run(args: argparse.Namespace) -> int:
    src = Path(args.file)
    if not src.exists():
        raise SystemExit(f"khong thay file: {src}")
    doc_id = slugify_doc_id(src.stem)
    d = OUT / doc_id
    docling_json, layout_json, document_json = d / "docling.json", d / "layout.json", d / "document.json"

    if args.redo or not docling_json.exists():
        run_docling(src, docling_json)
    else:
        log.info("① docling.json co san (--redo de chay lai)")

    if args.no_vlm:
        log.info("② bo qua VLM (--no-vlm), dung bo cuc co san trong layout.json")
    elif (pdf := page_pdf(src)) is None:
        log.warning("② khong co PDF cung ten de ve anh trang (%s) -> bo qua, dung docling",
                    src.with_suffix(".pdf").name)
    else:
        raw = json.loads(docling_json.read_text(encoding="utf-8"))
        asyncio.run(run_layout(raw, pdf, layout_json, tag=doc_id, model=args.model,
                               pages=parse_pages(args.pages) if args.pages else None,
                               concurrency=args.concurrency))

    doc = build_document(src, docling_json, layout_json)
    # ②b cần khung bảng của bố cục cuối -> chạy trên doc vừa dựng; có gọi VLM thì dựng ③ lại
    if not args.no_vlm and (pdf := page_pdf(src)) is not None and asyncio.run(run_tables(
            doc, pdf, layout_json, tag=doc_id, model=args.table_model,
            pages=parse_pages(args.pages) if args.pages else None, concurrency=args.concurrency)):
        doc = build_document(src, docling_json, layout_json)
    document_json.write_text(doc.to_json(), encoding="utf-8")
    report(doc)
    log.info("ghi -> %s (%d KB)", document_json, document_json.stat().st_size // 1024)

    n_err = sum(1 for f in doc.flags if f.severity == "error")
    if n_err:
        log.info("exit %d: co %d co muc error (de CI bat duoc). File van ghi binh thuong.",
                 EXIT_FLAGS, n_err)
    return EXIT_FLAGS if n_err else 0


def cmd_show(args: argparse.Namespace) -> int:
    p = Path(args.doc)
    doc = ParsedDocument.load(p if p.suffix == ".json" else OUT / args.doc / "document.json")
    if args.page:
        show_pages(doc, parse_pages(args.page), args.full)
    else:
        report(doc)
    return 0


# ------------------------------------------------------------------------ hiện


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


def report(doc: ParsedDocument) -> None:
    n_vlm = sum(1 for p in doc.pages if any(".v" in b.id for b in p.blocks))
    log.info("")
    log.info("=== %s", doc.doc_id)
    log.info("    %d trang (bo cuc VLM %d, docling %d) | %d block | %d link an",
             doc.n_pages, n_vlm, doc.n_pages - n_vlm, sum(len(p.blocks) for p in doc.pages),
             sum(len(b.hrefs) for p in doc.pages for b in p.blocks))
    log.info("    anh co mo ta %d | bang %d", doc.n_described_images,
             sum(len(p.tables) for p in doc.pages))

    if doc.sections:
        log.info("    %d section:", len(doc.sections))
        for s in doc.sections:
            log.info("        %s  p%d-%d  conf=%.2f  %s",
                     s.id, s.start_page, s.end_page, s.confidence, s.title)
    else:
        log.info("    0 section (khong tim thay trang muc luc khop)")

    if doc.flags:
        log.info("    %d co:", len(doc.flags))
        for f in doc.flags:
            where = f"p{f.page_no}" if f.page_no else "-"
            log.info("        [%s] %-22s %-5s %s", f.severity, f.kind, where, f.detail)
    else:
        log.info("    khong co co nao")


# ------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="parsing", description="S0: file goc -> out/parsed/<doc_id>/")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="chay ①②③")
    run.add_argument("file", help="data/raw/<ten>.pdf | .pptx")
    run.add_argument("--pages", default=None,
                     help="chi goi VLM cac trang nay, ke ca da co: '7' | '7,10' | '5-9'")
    run.add_argument("--no-vlm", action="store_true", help="khong goi API, dung layout.json co san")
    run.add_argument("--redo", action="store_true", help="chay lai docling du da co docling.json")
    run.add_argument("--model", default=os.environ.get("VLM_MODEL", "gemini-3.8-flash"),
                     help="mac dinh VLM_MODEL trong .env")
    run.add_argument("--table-model", default=os.environ.get("TABLE_MODEL", "gemini-3.8-flash"),
                     help="model chep bang (②b), mac dinh TABLE_MODEL trong .env")
    run.add_argument("--concurrency", type=int, default=4)
    run.set_defaults(fn=cmd_run)

    show = sub.add_parser("show", help="xem document.json")
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
