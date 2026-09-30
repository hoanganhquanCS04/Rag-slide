"""Gõ câu hỏi, xem KB trả về trang nào và trang đó có những block gì.

    python scripts/try_search.py tetnguyendan "mùng 1 tết là ngày gì"
    python scripts/try_search.py 3_datavisualization "lưu biểu đồ ra file ảnh" -k 3
    python scripts/try_search.py 3_datavisualization            # hỏi liên tục, Enter trống để thoát
    python scripts/try_search.py 3_datavisualization "savefig" --bm25   # không gọi API

Hai bước, đúng như runtime:
    1. TÌM   trên chunk (vector + BM25)          -> ra page_no
    2. XEM   trang đó trong ParsedDocument        -> block.content, block.polygon
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kb.search import Searcher
from parsing.models import ParsedDocument

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def ask(s: Searcher, doc: ParsedDocument, query: str, k: int, mode: str) -> None:
    hits = s.search(query, k=k, mode=mode)
    print(f"\n>>> {query}   ({mode}, top-{k})")
    for i, h in enumerate(hits, 1):
        print(f"\n#{i}  trang {h.page_no}   rrf={h.score:.4f}   "
              f"hang dense={h.rank_dense} bm25={h.rank_sparse}")
        for b in doc.page(h.page_no).blocks:
            if b.content is None:
                continue
            box = [tuple(round(x, 2) for x in p) for p in b.polygon]
            text = b.content.replace("\n", " / ")
            print(f"    {b.id} {b.kind:<9} {b.provenance.value:<10} {box[0]}->{box[2]}")
            print(f"        {text[:160]}{'…' if len(text) > 160 else ''}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc", help="doc_id, vd tetnguyendan | 3_datavisualization")
    ap.add_argument("query", nargs="*", help="bỏ trống -> hỏi liên tục")
    ap.add_argument("-k", type=int, default=3)
    ap.add_argument("--bm25", action="store_true", help="chỉ BM25, không gọi API nhúng")
    args = ap.parse_args()

    s = Searcher(f"out/kb/{args.doc}/chunks.json")
    doc = ParsedDocument.load(f"out/parsed/{args.doc}/document.json")
    mode = "sparse" if args.bm25 else "hybrid"

    if args.query:
        ask(s, doc, " ".join(args.query), args.k, mode)
        return
    while q := input("\nhỏi: ").strip():
        ask(s, doc, q, args.k, mode)


if __name__ == "__main__":
    main()
