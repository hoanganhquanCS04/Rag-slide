"""Biến thể `chroma` — kho ghi đĩa (bản gốc: langchain_chroma.Chroma).

MỘT collection cho mọi tài liệu, tách bằng metadata `doc_id` — đúng hình §7.1 (KB dùng
chung, truy vấn luôn kèm filter).

Ba mặc định của Chroma phải đổi:

    embedding_function     mặc định TỰ tải model MiniLM chạy local -> trái §12 "không model
                           local", lại khác model với câu hỏi. Tắt: vector luôn do mình đưa.
    space                  mặc định L2 -> cosine. Chroma trả distance = 1 - cosine.
    anonymized_telemetry   mặc định BẬT, gửi số liệu sử dụng ra ngoài -> tắt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from kb.models import KBChunk
from kb.store.base import Hit, VectorStore, Where, chunk_metadata


def _where(where: Where | None) -> dict[str, Any] | None:
    """dict phẳng -> cú pháp Chroma: nhiều điều kiện phải bọc `$and`, list -> `$in`."""
    conds = [{k: {"$in": v} if isinstance(v, list) else v} for k, v in (where or {}).items()]
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


class ChromaStore(VectorStore):
    kind = "chroma"
    persistent = True

    def __init__(self, model_id: str, path: str | Path):
        super().__init__(model_id)
        import chromadb
        from chromadb.config import Settings

        self.path = Path(path)
        self._client = chromadb.PersistentClient(
            path=str(self.path), settings=Settings(anonymized_telemetry=False))
        self._col = self._client.get_or_create_collection(
            self.name,
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={"model_id": model_id},
        )
        # configuration chỉ áp lúc TẠO — collection có sẵn từ trước thì phải kiểm lại
        space = (self._col.configuration.get("hnsw") or {}).get("space")
        if space != "cosine":
            raise SystemExit(f"collection {self.name} dang dung '{space}', can 'cosine' — "
                             f"xoa {self.path} roi chay lai (tu dung lai tu .npy, khong ton API)")

    def upsert(self, chunks: list[KBChunk], vectors: np.ndarray) -> None:
        vectors = np.asarray(vectors, dtype=np.float32)
        step = self._client.get_max_batch_size()
        for s in range(0, len(chunks), step):
            part = chunks[s:s + step]
            self._col.upsert(
                ids=[c.chunk_id for c in part],
                embeddings=vectors[s:s + step],
                metadatas=[chunk_metadata(c, self.model_id) for c in part],
            )

    def delete(self, where: Where) -> None:
        self._col.delete(where=_where(where))

    def query(self, vector: np.ndarray, k: int, where: Where | None = None) -> list[Hit]:
        r = self._col.query(query_embeddings=[np.asarray(vector, dtype=np.float32)],
                            n_results=k, where=_where(where), include=["distances"])
        return [(cid, 1.0 - d) for cid, d in zip(r["ids"][0], r["distances"][0])]

    def metadata(self, doc_id: str) -> dict[str, dict[str, Any]]:
        r = self._col.get(where={"doc_id": doc_id}, include=["metadatas"])
        return {cid: dict(m or {}) for cid, m in zip(r["ids"], r["metadatas"])}
