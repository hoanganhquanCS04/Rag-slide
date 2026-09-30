"""Tìm trang từ câu hỏi — hybrid dense (vector) + sparse (BM25), gộp bằng RRF.

Xem docs/spec/search.md. Bốn điểm dễ sai:

  1. **Phải có CẢ HAI nhánh.** Đo được: dense một mình trượt câu "làm sao lưu biểu đồ ra
     file ảnh" — trang chứa `plt.savefig` rơi xuống hạng 11. Vector hiểu Ý chứ không nhìn
     CHỮ; thuật ngữ hiếm là chỗ nó mù. §5 S5 ghi hybrid là BẮT BUỘC.
  2. **Gộp bằng RRF, KHÔNG cộng điểm.** cosine nằm trong [0,1], BM25 không có trần —
     cộng thẳng thì BM25 nuốt sạch dense. RRF chỉ nhìn THỨ HẠNG nên không cần đoán hệ số.
  3. **`score` là điểm RRF, KHÔNG phải confidence.** Cấm dùng làm gate (§10). Gate phải
     lấy điểm reranker — xem search.md §9, chỗ đó còn là món nợ chưa trả.
  4. **Nhánh điều hướng (R2) phải TẮT lọc trang phân mục.** Hỏi "quay lại phần đồ thị ba
     chiều" thì trang mở chương mới là đáp án đúng. Lọc là luật của R4, không phải của R2.

    python src/kb/search.py out/kb/<ten>/chunks.json "cau hoi" -k 5 --explain
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Literal

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.embed import MODEL_ID, Embedder, vectors_file
from kb.models import ChunkSet, KBChunk, SearchHit
from kb.sparse import SparseIndex, create_sparse
from kb.store import VectorStore, create_store

log = logging.getLogger(__name__)

RRF_K = 60          # hằng số gốc của paper RRF (Cormack 2009), không phải số bịa
TOP_K = 5           # khớp "top-k = 5" của §6
POOL = 50           # lấy sâu ở mỗi nhánh rồi mới gộp — gộp trên top-5 là mất tín hiệu

Mode = Literal["hybrid", "dense", "sparse"]


class Searcher:
    """Nạp một lần, hỏi nhiều lần.

    Nhánh dense hỏi KHO VECTOR (`VECTOR_DB` trong .env: inmem | chroma, xem kb/store/).
    Nhánh sparse hỏi INDEX TỪ KHOÁ (`SPARSE_INDEX`: rank_bm25, xem kb/sparse/).
    Khởi tạo gọi `sync` cả hai: lệch `chunks.json` thì nạp / dựng lại, khớp thì thôi.
    """

    def __init__(self, chunks_path: str | Path, *, vectors_path: str | Path | None = None,
                 model_id: str = MODEL_ID, embed_retry: int = 4,
                 store: VectorStore | None = None, sparse: SparseIndex | None = None):
        cp = Path(chunks_path)
        self.cs = ChunkSet.model_validate(json.loads(cp.read_text(encoding="utf-8")))
        self.chunks: list[KBChunk] = self.cs.chunks
        self._pos = {c.chunk_id: i for i, c in enumerate(self.chunks)}

        self.vectors_path = Path(vectors_path) if vectors_path else vectors_file(cp.parent, model_id)
        self.store = store or create_store(model_id=model_id)
        n_sync = self.store.sync(self.cs, self.vectors_path)
        self.sparse = sparse or create_sparse()
        self.sparse.sync(self.cs)

        self.model_id = model_id
        self.embed_retry = embed_retry
        self._emb: Embedder | None = None
        log.info("nap %d chunk | kho %s%s | tu khoa %s | model %s", len(self.chunks),
                 self.store.kind, f" (nap lai {n_sync} vector)" if n_sync else "",
                 self.sparse.kind, model_id)

    @property
    def embedder(self) -> Embedder:
        """Nạp lười: chạy --sparse-only thì khỏi cần khoá API."""
        if self._emb is None:
            self._emb = Embedder(self.model_id, retry=self.embed_retry)
        return self._emb

    # ------------------------------------------------------------------ tìm

    def search(self, query: str, *, k: int = TOP_K, mode: Mode = "hybrid",
               filter_dividers: bool = True, group_by_page: bool = True) -> list[SearchHit]:
        n = len(self.chunks)
        if not any(c.is_searchable or not filter_dividers for c in self.chunks):
            return []
        # Lọc TRƯỚC khi xếp hạng, CÙNG một `where` cho hai nhánh — để hạng của hai nhánh
        # tính trên cùng một tập ứng viên: đúng tài liệu này, lọc phân mục nếu được yêu cầu.
        where = {"doc_id": self.cs.doc_id, **({"is_searchable": True} if filter_dividers else {})}

        # Chunk ngoài pool thì điểm = 0 — điểm chỉ để debug, RRF không dùng điểm.
        s_dense = np.zeros(n, dtype=np.float32)
        s_sparse = np.zeros(n, dtype=np.float32)
        r_dense: dict[int, int] = {}
        r_sparse: dict[int, int] = {}

        if mode in ("hybrid", "dense"):
            qv = self.embedder.embed([query], use_cache=True)[0]
            for r, (cid, s) in enumerate(self.store.query(qv, POOL, where), 1):
                if (i := self._pos.get(cid)) is not None:
                    s_dense[i] = s
                    r_dense[i] = r

        if mode in ("hybrid", "sparse"):
            for r, (cid, s) in enumerate(self.sparse.query(query, POOL, where), 1):
                if (i := self._pos.get(cid)) is not None:
                    s_sparse[i] = s
                    r_sparse[i] = r

        # RRF: chỉ nhìn thứ hạng, vứt điểm đi. Không lọt pool thì không góp gì.
        rrf: dict[int, float] = {}
        for ranks in (r_dense, r_sparse):
            for i, r in ranks.items():
                rrf[i] = rrf.get(i, 0.0) + 1.0 / (RRF_K + r)

        hits = [
            SearchHit(
                chunk_id=self.chunks[i].chunk_id,
                page_no=self.chunks[i].page_no,
                section_id=self.chunks[i].section_id,
                section_title=self.chunks[i].section_title,
                score=sc,
                rank_dense=r_dense.get(i),
                rank_sparse=r_sparse.get(i),
                score_dense=float(s_dense[i]),
                score_sparse=float(s_sparse[i]),
                text_enriched=self.chunks[i].text_enriched,
                vlm_ratio=self.chunks[i].vlm_ratio,
            )
            for i, sc in sorted(rrf.items(), key=lambda kv: -kv[1])
        ]
        if group_by_page:
            hits = _group_by_page(hits)
        return hits[:k]


def _group_by_page(hits: list[SearchHit]) -> list[SearchHit]:
    """Trang 34 có 3 chunk — trả 3 dòng cùng trỏ p34 là phí suất trong top-k.

    Lấy điểm CAO NHẤT, **không cộng dồn**. Bản đầu cộng dồn và sai ngay: trang 20 có 5
    chunk, mỗi cái góp một tí rồi leo lên hạng 1 trong khi không chunk nào của nó vào nổi
    top-3 của cả hai nhánh. Cộng dồn là thưởng cho trang NHIỀU MẨU, không phải trang
    ĐÚNG Ý — mà số mẩu chỉ phản ánh trang đó lắm ảnh, chẳng liên quan gì tới câu hỏi.
    """
    out: dict[int, SearchHit] = {}
    for h in hits:                       # đã xếp giảm dần -> cái đầu là đại diện
        if (cur := out.get(h.page_no)) is None:
            out[h.page_no] = h.model_copy()
        else:
            cur.n_chunks += 1            # score giữ nguyên của chunk mạnh nhất
    return sorted(out.values(), key=lambda h: -h.score)


# ------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="search")
    ap.add_argument("chunks", help="out/kb/<ten>/chunks.json")
    ap.add_argument("query", nargs="+", help="cau hoi")
    ap.add_argument("-k", type=int, default=TOP_K)
    ap.add_argument("--vectors", default=None)
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("--dense-only", action="store_true")
    ap.add_argument("--sparse-only", action="store_true")
    ap.add_argument("--no-filter", action="store_true",
                    help="giu ca trang phan muc — dung cho nhanh R2 dieu huong")
    ap.add_argument("--no-group", action="store_true", help="khong gop chunk cung trang")
    ap.add_argument("--explain", action="store_true", help="in hang + diem tho tung nhanh")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    if args.dense_only and args.sparse_only:
        raise SystemExit("chon mot trong hai, khong the ca hai")
    mode: Mode = "dense" if args.dense_only else "sparse" if args.sparse_only else "hybrid"

    se = Searcher(args.chunks, vectors_path=args.vectors, model_id=args.model)
    q = " ".join(args.query)
    hits = se.search(q, k=args.k, mode=mode,
                     filter_dividers=not args.no_filter, group_by_page=not args.no_group)

    log.info("")
    log.info("HOI (%s%s): %s", mode, "" if not args.no_filter else ", khong loc", q)
    if not hits:
        log.info("   khong co ket qua")
        return 1
    for n, h in enumerate(hits, 1):
        log.info("%2d. trang %-3d %.4f  %s", n, h.page_no, h.score,
                 h.text_enriched[:62].replace("\n", " "))
        if args.explain:
            log.info("      dense hang %-4s cos=%.3f  |  bm25 hang %-4s diem=%.2f"
                     "  |  gop %d chunk  |  vlm %.0f%%",
                     h.rank_dense or "-", h.score_dense,
                     h.rank_sparse or "-", h.score_sparse, h.n_chunks, h.vlm_ratio * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
