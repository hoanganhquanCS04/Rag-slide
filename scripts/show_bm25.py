"""In ra index BM25 đang nằm trong RAM của một deck — để soi, không ghi gì ra đĩa.

Chạy: python scripts/show_bm25.py <doc_id> [--chunk 3] [--word chấm] [--query "nghỉ phép"]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console
from rich.table import Table

from models.kb_chunk import ChunkSet
from sparse.base import tokenize
from sparse.factory import create_sparse
from utils.config import CHUNKER_OUTPUT, console_setup

console = Console()


def main() -> None:
    """Dựng index BM25 cho deck rồi in từng tầng của nó: dict ngoài cùng -> bảng BM25 -> một chunk -> một từ."""
    console_setup()
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id")
    ap.add_argument("--chunk", type=int, default=0, help="thứ tự chunk muốn xem túi từ")
    ap.add_argument("--word", default=None, help="xem IDF + số lần xuất hiện của một từ")
    ap.add_argument("--query", default=None, help="tìm thử, in điểm từng chunk")
    args = ap.parse_args()

    cs = ChunkSet.model_validate_json((CHUNKER_OUTPUT / args.doc_id / "chunks.json").read_text("utf-8"))
    index = create_sparse()
    built = index.sync(cs)  # đúng đường Retriever đi lúc khởi tạo

    # Tầng 1: thứ thật sự nằm trong RAM — một dict, mỗi deck một bộ 4
    console.rule("[bold]index._docs  (dict trong RAM)")
    console.print(f"kiểu: {type(index).__name__} · dựng lại {built} chunk")
    for doc_id, (bm25, ids, meta, finger) in index._docs.items():
        console.print(f"  '{doc_id}' -> (BM25Okapi, ids[{len(ids)}], meta[{len(meta)}], finger[{len(finger)}])")
    bm25, ids, meta, _ = index._docs[args.doc_id]

    # Tầng 2: bên trong BM25Okapi
    console.rule("[bold]BM25Okapi")
    console.print(f"corpus_size = {bm25.corpus_size}  (số chunk)")
    console.print(f"avgdl       = {bm25.avgdl:.1f}  (số từ trung bình mỗi chunk)")
    console.print(f"k1 = {bm25.k1} · b = {bm25.b} · epsilon = {bm25.epsilon}")
    console.print(f"idf         = dict {len(bm25.idf)} từ")
    console.print(f"doc_len     = {bm25.doc_len[:10]}{' …' if len(bm25.doc_len) > 10 else ''}")
    console.print(f"doc_freqs   = list {len(bm25.doc_freqs)} dict (túi từ từng chunk)")

    # Tầng 3: túi từ của một chunk
    i = args.chunk
    console.rule(f"[bold]chunk #{i}: {ids[i]}  (trang {meta[i].get('page_no')})")
    console.print(f"meta = {meta[i]}")
    bag = sorted(bm25.doc_freqs[i].items(), key=lambda kv: -kv[1])
    t = Table("từ", "tf", "idf", show_edge=False)
    for w, tf in bag[:25]:
        t.add_row(w, str(tf), f"{bm25.idf[w]:.3f}")
    console.print(t)
    if len(bag) > 25:
        console.print(f"… còn {len(bag) - 25} từ")

    # Từ hiếm nhất / phổ biến nhất cả deck
    console.rule("[bold]IDF cả deck")
    by_idf = sorted(bm25.idf.items(), key=lambda kv: kv[1])
    console.print("phổ biến nhất (idf thấp):", ", ".join(f"{w}={v:.2f}" for w, v in by_idf[:10]))
    console.print("hiếm nhất (idf cao):    ", ", ".join(f"{w}={v:.2f}" for w, v in by_idf[-10:]))

    if args.word:
        w = args.word.lower()
        console.rule(f"[bold]từ '{w}'")
        console.print(f"idf = {bm25.idf.get(w)}")
        for j, bag in enumerate(bm25.doc_freqs):
            if w in bag:
                console.print(f"  {ids[j]}  tf={bag[w]}")

    if args.query:
        console.rule(f"[bold]tìm thử: {args.query!r}")
        console.print(f"tokenize -> {tokenize(args.query)}")
        for cid, score in index.retrieve_context(args.query, k=5, where={"doc_id": args.doc_id}):
            console.print(f"  {score:7.3f}  {cid}")


if __name__ == "__main__":
    main()
