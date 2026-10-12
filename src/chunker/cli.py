"""ParsedDocument -> `out/kb/<doc_id>/chunks.json`.

    python src/chunker/cli.py out/parsed/<ten>/document.json [--chunker page]
    python src/chunker/cli.py out/parsed/<ten>/document.json --page 19     # in chunk của 1 trang
    python src/chunker/cli.py out/parsed/<ten>/document.json --stats       # chỉ in thống kê

Mỗi bài một thư mục `out/kb/<doc_id>/`. Bước sau — nhúng + nạp kho: `src/index/cli.py`.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import statistics
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file, không qua -m
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from chunker.base import MAX_TOKENS, BaseChunker
from kb.models import ChunkSet
from parsing.enrich import enrich
from parsing.models import ParsedDocument

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("chunker")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "kb"

# import lúc chọn — biến thể này hỏng không kéo biến thể kia theo
CHUNKERS = {
    "page": "chunker.variants.page:PageChunker",
}


def make_chunker(kind: str, out_dir: Path, max_tokens: int) -> BaseChunker:
    module, cls = CHUNKERS[kind].split(":")
    return getattr(importlib.import_module(module), cls)(out_dir, max_tokens)


def _short(s: str, full: bool, n: int = 78) -> str:
    s = s.replace("\n", " ⏎ ")
    return s if full else (s[:n] + ("…" if len(s) > n else ""))


def show(cs: ChunkSet, pages: list[int] | None, full: bool) -> None:
    rows = cs.chunks if pages is None else [c for c in cs.chunks if c.page_no in pages]
    for c in rows:
        mark = "  " if c.is_searchable else "✗ "
        log.info("%s%-28s %4dtok  anh=%3.0f%%  %s", mark, c.chunk_id, c.token_count,
                 c.image_ratio * 100, _short(c.text_enriched, full))


def stats(cs: ChunkSet) -> None:
    n = [c.token_count for c in cs.chunks]
    s = [c.token_count for c in cs.searchable]
    log.info("")
    log.info("=== %s", cs.doc_id)
    log.info("    %d chunk | tim duoc %d | bo qua %d", len(n), len(s), len(n) - len(s))
    log.info("    loai    : %s", dict(Counter(c.content_type for c in cs.chunks)))
    if s:
        log.info("    token (chunk tim duoc): min=%d trung vi=%.0f max=%d tong=%d",
                 min(s), statistics.median(s), max(s), sum(s))
    over = [c.chunk_id for c in cs.chunks if c.token_count > cs.max_tokens]
    log.info("    vuot %d token: %s", cs.max_tokens, over or "khong co")
    div = [c.page_no for c in cs.chunks if c.content_type == "section_divider"]
    log.info("    trang phan muc: %s", div or "khong co")


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser(prog="chunker", description=__doc__.splitlines()[0])
    ap.add_argument("parsed", type=Path, help="out/parsed/<ten>/document.json")
    ap.add_argument("--chunker", default="page", choices=CHUNKERS)
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--page", default=None, help="chi in chunk cua trang: '19' | '19,20'")
    ap.add_argument("--stats", action="store_true", help="chi in thong ke")
    ap.add_argument("--full", action="store_true", help="in nguyen van chunk")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.parsed.exists():
        raise SystemExit(f"khong thay file: {args.parsed}")
    doc = enrich(ParsedDocument.load(args.parsed))   # chương / vá tay / cờ không nằm trong document.json

    chunker = make_chunker(args.chunker, OUT / doc.doc_id, args.max_tokens)
    cs = chunker.run(doc)

    if args.page:
        show(cs, [int(x) for x in args.page.replace(" ", "").split(",") if x], args.full)
    elif not args.stats:
        show(cs, None, args.full)
    stats(cs)

    chunker.save(cs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
