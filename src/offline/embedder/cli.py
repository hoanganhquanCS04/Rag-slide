"""Lệnh dựng index (S5): chunks.json -> file vector + nạp kho vector.

    python src/offline/embedder/cli.py src/output/chunker/<ten>/chunks.json
    python src/offline/embedder/cli.py src/output/chunker/<ten>/chunks.json --no-cache   # bỏ cache, gọi API lại

Ra: `src/output/embedders/<doc_id>/vectors__<model>.{npy,json}`, cache vector ở
`src/output/embedders/cache/`, kho Chroma ở `src/output/vectordb/chroma/` (chỉ khi VECTOR_DB=chroma).
BM25 không ghi gì ra đĩa — dựng trong RAM lúc tìm.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file: THAY src/offline/embedder/ bằng src/
    sys.path[0] = str(Path(__file__).resolve().parents[2])

from utils.config import MODEL_ID, console_setup
from components.embedders.base import embed_chunkset
from components.embedders.factory import EMBEDDERS, create_embedder
from components.vectordb.factory import create_store
from models.kb_chunk import ChunkSet

log = logging.getLogger("embedders")


def main(argv: list[str] | None = None) -> int:
    """Đọc tham số, nạp chunks.json, nhúng, ghi file vector; kho ghi đĩa (chroma) thì nạp kho luôn."""
    ap = argparse.ArgumentParser(prog="embedders", description=__doc__.splitlines()[0])
    ap.add_argument("chunks", type=Path, help="src/output/chunker/<ten>/chunks.json")
    ap.add_argument("--embedder", default=None, choices=EMBEDDERS, help="mac dinh EMBEDDER trong .env")
    ap.add_argument("--model", default=MODEL_ID, help="mac dinh EMBED_MODEL trong .env")
    ap.add_argument("--no-cache", action="store_true", help="bo qua cache vector tren dia")
    args = ap.parse_args(argv)
    console_setup()

    if not args.chunks.exists():
        raise SystemExit(f"khong thay file: {args.chunks}")
    cs = ChunkSet.model_validate_json(args.chunks.read_text(encoding="utf-8"))
    log.info("%s: %d chunk", args.chunks, len(cs.chunks))

    emb = create_embedder(args.embedder, model_id=args.model, cache=not args.no_cache)
    p_npy, _ = embed_chunkset(cs, emb)

    # Kho ghi đĩa (chroma) nạp luôn ở offline (§2 NT1). inmem thì lúc chạy mới nạp.
    store = create_store(model_id=args.model)
    if store.persistent:
        n = store.sync(cs, p_npy)
        log.info("kho %s (%s): %s", store.kind, store.name,
                 f"nap {n} vector" if n else "da khop, khong doi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
