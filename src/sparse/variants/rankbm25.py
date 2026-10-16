"""Index từ khoá `rank_bm25`: BM25 trong RAM, mỗi deck một bảng riêng.

Cách làm: không ghi đĩa — mỗi lần chạy `sync` dựng lại từ chunks.json (vài ms). Mỗi deck một bảng
vì điểm BM25 phụ thuộc cả tập chunk đem dựng; vì vậy `retrieve_context` bắt buộc lọc theo `doc_id`.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from utils.filters import Hit, Where, matches
from sparse.base import SparseIndex, fingerprint, tokenize
from models.kb_chunk import KBChunk


class RankBM25Index(SparseIndex):
    """Index BM25 dùng thư viện rank_bm25."""
    kind = "rank_bm25"

    def __init__(self) -> None:
        """Tạo index rỗng: doc_id -> (bảng BM25, chunk_id, metadata, dấu vân tay để so khi sync)."""
        self._docs: dict[str, tuple[Any, list[str], list[dict[str, Any]], list[tuple]]] = {}

    def index_documents(self, chunks: list[KBChunk]) -> None:
        """Dựng bảng BM25 cho deck của bộ chunk này (thay bảng cũ nếu có)."""
        from rank_bm25 import BM25Okapi

        if not chunks:
            return
        finger = fingerprint(chunks)
        bm25 = BM25Okapi([tokenize(c.text_enriched) for c in chunks])
        self._docs[chunks[0].doc_id] = (bm25, [f[0] for f in finger], [f[2] for f in finger], finger)

    def retrieve_context(self, query: str, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk điểm BM25 cao nhất trong số khớp bộ lọc -> [(chunk_id, điểm)].

        Cách làm: chunk điểm 0 (không khớp chữ nào) KHÔNG được xếp hạng.
        """
        doc_id = (where or {}).get("doc_id")
        if not isinstance(doc_id, str) or doc_id not in self._docs:
            raise ValueError(f"where phai co doc_id da sync (dang co: {sorted(self._docs)}), "
                             f"nhan {doc_id!r}")
        bm25, ids, meta, _ = self._docs[doc_id]
        scores = np.asarray(bm25.get_scores(tokenize(query)), dtype=np.float32)
        # Điểm 0 = không khớp chữ nào -> KHÔNG xếp hạng. Bản đầu xếp cả chúng: các chunk 0 điểm
        # hoà nhau nên giữ thứ tự trong file, p1 hạng 2, p2 hạng 3… -> trang đầu deck được
        # cộng RRF không vì lý do gì (hỏi "VGRFOC": 49/50 chunk trong pool là điểm 0).
        rows = [i for i, m in enumerate(meta) if scores[i] > 0 and matches(m, where)]
        order = sorted(rows, key=lambda i: -scores[i])[:k]
        return [(ids[i], float(scores[i])) for i in order]

    def delete_document(self, doc_id: str) -> None:
        """Bỏ bảng BM25 của một deck."""
        self._docs.pop(doc_id, None)

    def fingerprint(self, doc_id: str) -> list[tuple] | None:
        """Dấu vân tay của bộ chunk đã dựng bảng cho deck; chưa dựng thì None."""
        cur = self._docs.get(doc_id)
        return cur[3] if cur else None
