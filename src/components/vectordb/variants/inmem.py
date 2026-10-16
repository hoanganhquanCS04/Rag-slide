"""Kho vector `inmem`: giữ trong RAM, không ghi đĩa.

Cách làm: mỗi lần chạy `sync` nạp lại từ file .npy (vài ms); tìm = nhân ma trận với vector câu
hỏi rồi sắp xếp (quét hết, đủ nhanh với vài trăm nghìn vector).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from utils.filters import Hit, Where, matches
from components.vectordb.base import VectorStore, chunk_metadata
from models.kb_chunk import KBChunk


class InMemoryStore(VectorStore):
    """Kho vector trong RAM: danh sách chunk_id, metadata và ma trận vector."""
    kind = "inmem"
    persistent = False

    def __init__(self, model_id: str):
        """Tạo kho rỗng."""
        super().__init__(model_id)
        self._ids: list[str] = []
        self._meta: list[dict[str, Any]] = []
        self._M = np.zeros((0, 0), dtype=np.float32)

    def _keep(self, rows: list[int]) -> None:
        """Chỉ giữ lại các hàng có số thứ tự trong `rows`."""
        self._ids = [self._ids[i] for i in rows]
        self._meta = [self._meta[i] for i in rows]
        self._M = self._M[rows]

    def index_documents(self, chunks: list[KBChunk], vectors: np.ndarray) -> None:
        """Thêm vector; chunk_id đã có thì bỏ bản cũ trước rồi thêm bản mới."""
        new = {c.chunk_id for c in chunks}
        self._keep([i for i, cid in enumerate(self._ids) if cid not in new])
        vectors = np.asarray(vectors, dtype=np.float32)
        self._M = np.vstack([self._M, vectors]) if self._ids else vectors
        self._ids += [c.chunk_id for c in chunks]
        self._meta += [chunk_metadata(c, self.model_id) for c in chunks]

    def retrieve_context(self, query_vector: np.ndarray, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk gần nhất trong số khớp bộ lọc -> [(chunk_id, cosine)].

        Cách làm: lọc hàng theo metadata, nhân ma trận với vector câu hỏi (đã chuẩn hoá nên ra cosine),
        lấy k điểm cao nhất.
        """
        rows = [i for i, m in enumerate(self._meta) if matches(m, where)]
        if not rows:
            return []
        scores = self._M[rows] @ query_vector              # đã chuẩn hoá L2 -> đây là cosine
        order = np.argsort(-scores, kind="stable")[:k]
        return [(self._ids[rows[j]], float(scores[j])) for j in order]

    def delete_document(self, doc_id: str) -> None:
        """Xoá mọi vector của một deck."""
        self._keep([i for i, m in enumerate(self._meta) if m["doc_id"] != doc_id])

    def metadata(self, doc_id: str) -> dict[str, dict[str, Any]]:
        """Metadata mọi vector của một deck -> {chunk_id: metadata}."""
        return {cid: m for cid, m in zip(self._ids, self._meta) if m["doc_id"] == doc_id}
