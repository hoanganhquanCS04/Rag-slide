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

    python src/kb/search.py out/kb/<ten>.chunks.json "cau hoi" -k 5 --explain
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Literal

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.embed import MODEL_ID, Embedder, model_slug
from kb.models import ChunkSet, KBChunk, SearchHit

log = logging.getLogger(__name__)

RRF_K = 60          # hằng số gốc của paper RRF (Cormack 2009), không phải số bịa
TOP_K = 5           # khớp "top-k = 5" của §6
POOL = 50           # lấy sâu ở mỗi nhánh rồi mới gộp — gộp trên top-5 là mất tín hiệu

Mode = Literal["hybrid", "dense", "sparse"]

# Tách dính liền của code: plt.savefig(x) -> plt savefig x ; np.arange -> np arange
_SPLIT = re.compile(r"[^0-9A-Za-zÀ-ỹ]+")


def tokenize(text: str) -> list[str]:
    """Tách chữ cho BM25.

    Tiếng Việt viết rời từng âm tiết nên tách theo khoảng trắng là đủ dùng. Điểm mấu chốt
    là **tách tên hàm**: người hỏi gõ "savefig" chứ không gõ "plt.savefig('line_graph.png')".
    Không tách thì cả cụm là một token và không bao giờ khớp.

    GIỮ NGUYÊN DẤU tiếng Việt. Hệ quả: gõ thiếu dấu là nhánh này về 0 (`bieu` != `biểu`).
    Dense còn vớt được mờ mờ, BM25 thì không. Ghi ở search.md §3.
    """
    return [t for t in _SPLIT.split(text.lower()) if len(t) > 1 or t.isdigit()]


def _ranks(scores: np.ndarray, idx: list[int], pool: int) -> dict[int, int]:
    """-> {chỉ số chunk: hạng bắt đầu từ 1}, chỉ lấy `pool` cái đầu."""
    order = sorted(idx, key=lambda i: -scores[i])[:pool]
    return {i: r for r, i in enumerate(order, 1)}


class Searcher:
    """Nạp một lần, hỏi nhiều lần. Dựng bảng BM25 lúc khởi tạo (~10ms cho 52 chunk)."""

    def __init__(self, chunks_path: str | Path, *, vectors_path: str | Path | None = None,
                 model_id: str = MODEL_ID, embed_retry: int = 4):
        from rank_bm25 import BM25Okapi

        cp = Path(chunks_path)
        self.cs = ChunkSet.model_validate(json.loads(cp.read_text(encoding="utf-8")))
        self.chunks: list[KBChunk] = self.cs.chunks

        vp = Path(vectors_path) if vectors_path else (
            cp.parent / f"{self.cs.doc_id}__{model_slug(model_id)}.vectors.npy")
        if not vp.exists():
            raise SystemExit(f"khong thay vector: {vp}\nchay `--embed` truoc da")
        self.M = np.load(vp)
        if len(self.M) != len(self.chunks):
            raise SystemExit(
                f"lech so luong: {len(self.M)} vector vs {len(self.chunks)} chunk. "
                "chunk da doi ma chua nhung lai?")

        # KHÔNG dùng text_raw (§10) — index phải khớp đúng thứ đã đem đi nhúng.
        self.bm25 = BM25Okapi([tokenize(c.text_enriched) for c in self.chunks])
        self.model_id = model_id
        self.embed_retry = embed_retry
        self._emb: Embedder | None = None
        log.info("nap %d chunk + vector %s | model %s",
                 len(self.chunks), tuple(self.M.shape), model_id)

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
        # Lọc TRƯỚC khi xếp hạng, để hạng của hai nhánh tính trên cùng một tập ứng viên.
        cand = [i for i in range(n)
                if not filter_dividers or self.chunks[i].is_searchable]
        if not cand:
            return []

        s_dense = np.zeros(n, dtype=np.float32)
        s_sparse = np.zeros(n, dtype=np.float32)
        r_dense: dict[int, int] = {}
        r_sparse: dict[int, int] = {}

        if mode in ("hybrid", "dense"):
            qv = self.embedder.embed([query], use_cache=True)[0]
            s_dense = self.M @ qv            # vector đã chuẩn hoá L2 -> đây là cosine
            r_dense = _ranks(s_dense, cand, POOL)

        if mode in ("hybrid", "sparse"):
            s_sparse = np.asarray(self.bm25.get_scores(tokenize(query)), dtype=np.float32)
            r_sparse = _ranks(s_sparse, cand, POOL)

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
    ap.add_argument("chunks", help="out/kb/<ten>.chunks.json")
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
