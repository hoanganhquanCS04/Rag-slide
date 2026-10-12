"""S5 — chunks.json -> vector + kho vector.

    python src/retrieval/build_index.py src/output/chunker/<ten>/chunks.json
    python src/retrieval/build_index.py src/output/chunker/<ten>/chunks.json --no-cache          # bỏ qua cache, gọi API lại
    python src/retrieval/build_index.py src/output/chunker/<ten>/chunks.json --model text-embedding-3-large

Vào:  src/output/chunker/<doc_id>/chunks.json                      (src/chunker/cli.py)
Ra:   src/output/embedders/<doc_id>/vectors__<model>.{npy,json}   ma trận + sidecar (rows, keys)
      src/output/vectordb/chroma/                                CHỈ khi VECTOR_DB=chroma
      src/output/embedders/cache/<model>/                  cache từng vector, dùng chung mọi deck

BM25 không có gì ghi đĩa — dựng trong RAM lúc `Searcher` khởi động.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file: THAY src/retrieval/ bằng src/ — để `eval.py`,
    sys.path[0] = str(Path(__file__).resolve().parents[1])  # `search.py` không che module trùng tên

from utils.config import MODEL_ID, ROOT
from embedders.base import Embedder
from embedders.factory import EMBEDDERS, create_embedder
from vectordb.factory import create_store
from utils.vectors import save_vectors
from models.kb_chunk import ChunkSet

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("build_index")


def embed_chunkset(cs: ChunkSet, embedder: Embedder) -> tuple[Path, Path]:
    """Nhúng CẢ BỘ, kể cả trang phân mục — lọc là việc của lúc truy vấn. -> (.npy, .json)"""
    texts = [c.text_enriched for c in cs.chunks]      # KHÔNG dùng text_raw (§10)
    t0 = time.perf_counter()
    mat = embedder.embed(texts)
    dt = time.perf_counter() - t0

    paths = save_vectors(cs, mat, model_id=embedder.model_id, backend=embedder.backend,
                         n_api_calls=embedder.n_api_calls, embed_sec=round(dt, 2))
    log.info("nhung %d vector trong %.1fs (%.0fms/chunk, %d lan goi API) -> %s",
             len(texts), dt, dt / max(len(texts), 1) * 1000, embedder.n_api_calls,
             paths[0].relative_to(ROOT))
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="index", description=__doc__.splitlines()[0])
    ap.add_argument("chunks", type=Path, help="src/output/chunker/<ten>/chunks.json")
    ap.add_argument("--embedder", default=None, choices=EMBEDDERS, help="mac dinh EMBEDDER trong .env")
    ap.add_argument("--model", default=MODEL_ID, help="mac dinh EMBED_MODEL trong .env")
    ap.add_argument("--no-cache", action="store_true", help="bo qua cache vector tren dia")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

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
