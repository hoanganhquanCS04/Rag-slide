"""Cắt chunk từ ParsedDocument (§9: chạy riêng được qua CLI).

    python src/kb/cli.py out/parsed/<ten>/document.json -o out/kb/<ten>.chunks.json
    python src/kb/cli.py out/parsed/<ten>/document.json --page 19        # xem chunk của 1 trang
    python src/kb/cli.py out/parsed/<ten>/document.json --stats          # phân bố token
    python src/kb/cli.py out/parsed/<ten>/document.json -o out/kb/<ten>.chunks.json --embed
                                                    # nhúng qua API + nạp kho (VECTOR_DB)
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.chunk import chunk_document
from kb.models import ChunkSet
from parsing.models import ParsedDocument

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("kb")


def _cut(s: str, full: bool, n: int = 78) -> str:
    s = s.replace("\n", " ⏎ ")
    return s if full else (s[:n] + ("…" if len(s) > n else ""))


def show(cs: ChunkSet, pages: list[int] | None, full: bool) -> None:
    rows = cs.chunks if pages is None else [c for c in cs.chunks if c.page_no in pages]
    for c in rows:
        mark = "  " if c.is_searchable else "✗ "
        log.info("%s%-28s %-5s %4dtok  vlm=%3.0f%%  %s",
                 mark, c.chunk_id, c.vector_role, c.token_count,
                 c.vlm_ratio * 100, _cut(c.text_enriched, full))


def stats(cs: ChunkSet) -> None:
    n = [c.token_count for c in cs.chunks]
    s = [c.token_count for c in cs.searchable]
    log.info("")
    log.info("=== %s", cs.doc_id)
    log.info("    %d chunk | tim duoc %d | bo qua %d", len(cs.chunks), len(s), len(n) - len(s))
    log.info("    vai tro : %s", dict(Counter(c.vector_role for c in cs.chunks)))
    log.info("    loai    : %s", dict(Counter(c.content_type for c in cs.chunks)))
    if s:
        log.info("    token (chunk tim duoc): min=%d trung vi=%.0f max=%d tong=%d",
                 min(s), statistics.median(s), max(s), sum(s))
    over = [c.chunk_id for c in cs.chunks if c.token_count > cs.max_tokens]
    log.info("    vuot %d token: %s", cs.max_tokens, over or "khong co")
    div = [c.page_no for c in cs.chunks if c.content_type == "section_divider"]
    log.info("    trang phan muc: %s", div or "khong co")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kb")
    ap.add_argument("parsed", help="out/parsed/<ten>/document.json")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--page", default=None, help="chi xem chunk cua trang: '19' | '19,20'")
    ap.add_argument("--stats", action="store_true", help="chi in thong ke")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=500)
    ap.add_argument("--embed", action="store_true",
                    help="nhung vector qua API OpenAI (can OPENAI_API_KEY)")
    ap.add_argument("--embed-model", default=None, help="mac dinh EMBED_MODEL trong .env")
    ap.add_argument("--vector-dir", default="out/kb", help="noi ghi .vectors.npy")
    ap.add_argument("--no-cache", action="store_true", help="bo qua cache tren dia")
    args = ap.parse_args(argv)
    if args.embed and not args.out:
        # vector ghi theo thứ tự chunk — chunk không được lưu thì vector không khớp file nào
        ap.error("--embed can -o <chunks.json>: vector phai di cung bo chunk da luu")

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    p = Path(args.parsed)
    if not p.exists():
        raise SystemExit(f"khong thay file: {p}")
    doc = ParsedDocument.load(p)
    cs = chunk_document(doc, max_tokens=args.max_tokens)

    if args.page:
        pages = [int(x) for x in args.page.replace(" ", "").split(",") if x]
        show(cs, pages, args.full)
    elif not args.stats:
        show(cs, None, args.full)
    stats(cs)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(cs.model_dump_json(indent=2), encoding="utf-8")
        log.info("ghi -> %s (%d KB)", out, out.stat().st_size // 1024)

    if args.embed:
        # Nhúng CẢ BỘ, kể cả trang phân mục — lọc là việc của lúc truy vấn.
        from kb.embed import MODEL_ID, embed_chunkset
        from kb.store import create_store

        model_id = args.embed_model or MODEL_ID
        log.info("")
        p_npy, _ = embed_chunkset(cs, args.vector_dir, model_id=model_id,
                                  use_cache=not args.no_cache)
        # Kho ghi đĩa (chroma) nạp luôn ở offline (§2 NT1). inmem thì lúc chạy mới nạp.
        store = create_store(model_id=model_id)
        if store.persistent:
            n = store.sync(cs, p_npy)
            log.info("kho %s (%s): %s", store.kind, store.name,
                     f"nap {n} vector" if n else "da khop, khong doi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
