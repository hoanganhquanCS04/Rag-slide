"""Lệnh chunk (S5): document.json -> cắt chunk -> nhúng vector -> ghi thẳng vào kho vector (Chroma).

    python src/offline/chunker/cli.py src/output/parser/<ten>/document.json                       # cắt + nhúng + ghi kho
    python src/offline/chunker/cli.py src/output/parser/<ten>/document.json --dry-run --page 19   # chỉ xem chunk trang 19
    python src/offline/chunker/cli.py src/output/parser/<ten>/document.json --dry-run --stats     # chỉ xem thống kê
    python src/offline/chunker/cli.py src/output/parser/<ten>/document.json --no-cache            # bỏ cache nhúng, gọi API lại

Không ghi file chunk nào: chunk nằm trong bộ nhớ rồi vào thẳng kho. Bản lưu duy nhất là kho Chroma
(`src/output/vectordb/chroma/`). Nhúng có cache theo hash chữ (`src/output/embedders/cache/`) nên chữ
không đổi thì không tốn API. Đổi document.json / cách chunk thì chạy lại lệnh này — lúc chạy hỏi đáp
không tự phát hiện kho cũ.
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file, không qua -m
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from components.embedders.base import embed_chunkset
from components.embedders.factory import EMBEDDERS, create_embedder
from components.vectordb.factory import create_store
from offline.chunker.base import MAX_TOKENS
from offline.chunker.factory import CHUNKERS, create_chunker
from models.kb_chunk import ChunkSet
from utils.config import MODEL_ID
from models.parsed_document import ParsedDocument

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("chunker")



def _short(s: str, full: bool, n: int = 78) -> str:
    """Rút gọn chữ để in một dòng: xuống dòng thành "⏎", dài quá thì cắt (trừ khi `full`)."""
    s = s.replace("\n", " ⏎ ")
    return s if full else (s[:n] + ("…" if len(s) > n else ""))


def show(cs: ChunkSet, pages: list[int] | None, full: bool) -> None:
    """In từng chunk (hoặc chỉ chunk của các trang được chọn): id, số token, % ảnh, chữ."""
    rows = cs.chunks if pages is None else [c for c in cs.chunks if c.page_no in pages]
    for c in rows:
        mark = "  " if c.is_searchable else "✗ "
        log.info("%s%-28s %4dtok  anh=%3.0f%%  %s", mark, c.chunk_id, c.token_count,
                 c.image_ratio * 100, _short(c.text_enriched, full))


def stats(cs: ChunkSet) -> None:
    """In thống kê bộ chunk: số chunk, số chunk tìm được, token nhỏ nhất / trung vị / lớn nhất, trang chuyển chương."""
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
    """Đọc tham số, nạp document.json, cắt chunk, in kết quả, rồi nhúng và ghi vào kho vector."""
    ap = argparse.ArgumentParser(prog="chunker", description=__doc__.splitlines()[0])
    ap.add_argument("parsed", type=Path, help="src/output/parser/<ten>/document.json")
    ap.add_argument("--chunker", default="page", choices=CHUNKERS)
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--page", nargs="+", type=int, default=None, help="chi in chunk cua trang: 19 | 19 20")
    ap.add_argument("--stats", action="store_true", help="chi in thong ke")
    ap.add_argument("--full", action="store_true", help="in nguyen van chunk")
    ap.add_argument("--dry-run", action="store_true", help="chi xem chunk, khong nhung, khong ghi kho")
    ap.add_argument("--embedder", default=None, choices=EMBEDDERS, help="mac dinh EMBEDDER trong .env")
    ap.add_argument("--model", default=MODEL_ID, help="mac dinh EMBED_MODEL trong .env")
    ap.add_argument("--no-cache", action="store_true", help="bo qua cache vector tren dia")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.parsed.exists():
        raise SystemExit(f"khong thay file: {args.parsed}")
    doc = ParsedDocument.load(args.parsed)

    cs = create_chunker(args.chunker, args.max_tokens).run(doc)

    if args.page:
        show(cs, args.page, args.full)
    elif not args.stats:
        show(cs, None, args.full)
    stats(cs)
    if args.dry_run:
        return 0

    # Nhúng rồi ghi kho: ghi đè bộ chunk này, xoá chunk cũ của deck không còn nữa
    vectors = embed_chunkset(cs, create_embedder(args.embedder, model_id=args.model, cache=not args.no_cache))
    store = create_store(model_id=args.model)
    n_stale = store.replace_document(cs.chunks, vectors)
    log.info("kho %s (%s): nap %d ban ghi, xoa %d ban ghi cu", store.kind, store.name,
             len(cs.chunks), n_stale)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
