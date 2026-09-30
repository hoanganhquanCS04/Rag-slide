"""Biến thể `rank_bm25` — BM25Okapi trong RAM, mỗi TÀI LIỆU một bảng riêng.

Không ghi đĩa: mỗi lần khởi động `sync` dựng lại từ chunks.json (~3ms cho 88 chunk).

Mỗi tài liệu một bảng vì IDF tính trên tập chunk đem dựng. Gộp nhiều tài liệu vào một bảng
là IDF đổi theo tài liệu KHÁC -> điểm của bài này đổi dù chunk của nó không đổi. Vì thế
`query` bắt buộc `where` có `doc_id`.

Nhược điểm phải biết: `get_scores` duyệt MỌI chunk cho mỗi từ của câu hỏi (không có
inverted index) — thời gian tìm tăng tuyến tính theo kho. Số đo ở `base.py`.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from kb.models import ChunkSet
from kb.sparse.base import SparseIndex, filter_metadata, tokenize
from kb.store.base import Hit, Where, matches


class RankBM25Index(SparseIndex):
    kind = "rank_bm25"
    persistent = False

    def __init__(self) -> None:
        # doc_id -> (bảng BM25, chunk_id theo hàng, metadata theo hàng, vân tay để so ở sync)
        self._docs: dict[str, tuple[Any, list[str], list[dict[str, Any]], list[tuple]]] = {}

    def sync(self, cs: ChunkSet) -> int:
        from rank_bm25 import BM25Okapi

        # KHÔNG dùng text_raw (§10) — index phải khớp đúng thứ đã đem đi nhúng.
        finger = [(c.chunk_id, c.text_enriched, filter_metadata(c)) for c in cs.chunks]
        if (cur := self._docs.get(cs.doc_id)) is not None and cur[3] == finger:
            return 0
        bm25 = BM25Okapi([tokenize(c.text_enriched) for c in cs.chunks])
        self._docs[cs.doc_id] = (bm25, [f[0] for f in finger], [f[2] for f in finger], finger)
        return len(cs.chunks)

    def query(self, text: str, k: int, where: Where | None = None) -> list[Hit]:
        doc_id = (where or {}).get("doc_id")
        if not isinstance(doc_id, str) or doc_id not in self._docs:
            raise ValueError(f"where phai co doc_id da sync (dang co: {sorted(self._docs)}), "
                             f"nhan {doc_id!r}")
        bm25, ids, meta, _ = self._docs[doc_id]
        scores = np.asarray(bm25.get_scores(tokenize(text)), dtype=np.float32)
        # Điểm 0 = không khớp chữ nào -> KHÔNG xếp hạng. Bản đầu xếp cả chúng: các chunk 0 điểm
        # hoà nhau nên giữ thứ tự trong file, p1 hạng 2, p2 hạng 3… -> trang đầu deck được
        # cộng RRF không vì lý do gì (hỏi "VGRFOC": 49/50 chunk trong pool là điểm 0).
        rows = [i for i, m in enumerate(meta) if scores[i] > 0 and matches(m, where)]
        order = sorted(rows, key=lambda i: -scores[i])[:k]
        return [(ids[i], float(scores[i])) for i in order]
