"""Biến thể `inmem` — kho trong RAM (bản gốc: langchain InMemoryVectorStore).

Không ghi đĩa: mỗi lần khởi động `sync` nạp lại từ .npy (vài ms). Tìm = quét vét cạn
`M @ q` — 52 vector 0.01ms, 100.000 vector 25ms (CLAUDE.md §7.0), chưa cần index.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from kb.models import KBChunk
from kb.store.base import Hit, VectorStore, Where, chunk_metadata, matches


class InMemoryStore(VectorStore):
    kind = "inmem"
    persistent = False

    def __init__(self, model_id: str):
        super().__init__(model_id)
        self._ids: list[str] = []
        self._meta: list[dict[str, Any]] = []
        self._M = np.zeros((0, 0), dtype=np.float32)

    def _keep(self, rows: list[int]) -> None:
        self._ids = [self._ids[i] for i in rows]
        self._meta = [self._meta[i] for i in rows]
        self._M = self._M[rows]

    def upsert(self, chunks: list[KBChunk], vectors: np.ndarray) -> None:
        new = {c.chunk_id for c in chunks}
        self._keep([i for i, cid in enumerate(self._ids) if cid not in new])
        vectors = np.asarray(vectors, dtype=np.float32)
        self._M = np.vstack([self._M, vectors]) if self._ids else vectors
        self._ids += [c.chunk_id for c in chunks]
        self._meta += [chunk_metadata(c, self.model_id) for c in chunks]

    def delete(self, where: Where) -> None:
        self._keep([i for i, m in enumerate(self._meta) if not matches(m, where)])

    def query(self, vector: np.ndarray, k: int, where: Where | None = None) -> list[Hit]:
        rows = [i for i, m in enumerate(self._meta) if matches(m, where)]
        if not rows:
            return []
        scores = self._M[rows] @ vector              # đã chuẩn hoá L2 -> đây là cosine
        order = np.argsort(-scores, kind="stable")[:k]
        return [(self._ids[rows[j]], float(scores[j])) for j in order]

    def metadata(self, doc_id: str) -> dict[str, dict[str, Any]]:
        return {cid: m for cid, m in zip(self._ids, self._meta) if m["doc_id"] == doc_id}
