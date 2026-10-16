"""Kho vector `chroma`: ghi đĩa, mọi deck chung một collection, tách bằng metadata `doc_id`.

Cách làm: đổi 3 mặc định của Chroma — tắt embedding có sẵn (vector luôn do mình đưa), dùng cosine
thay cho L2, tắt gửi số liệu sử dụng ra ngoài.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from utils.config import ROOT, VECTORDB_OUTPUT
from utils.filters import Hit, Where
from vectordb.base import VectorStore, chunk_metadata
from models.kb_chunk import KBChunk



def _where(where: Where | None) -> dict[str, Any] | None:
    """Đổi bộ lọc dict phẳng sang cú pháp Chroma: nhiều điều kiện bọc `$and`, list thành `$in`."""
    conds = [{k: {"$in": v} if isinstance(v, list) else v} for k, v in (where or {}).items()]
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


class ChromaStore(VectorStore):
    """Kho vector dùng Chroma, lưu ở `src/output/vectordb/chroma` (hoặc CHROMA_PATH)."""
    kind = "chroma"
    persistent = True

    def __init__(self, model_id: str, path: str | Path | None = None):
        """Mở / tạo kho Chroma và collection theo tên model, kiểm collection dùng cosine."""
        super().__init__(model_id)
        import chromadb
        from chromadb.config import Settings

        env = os.environ.get("CHROMA_PATH")            # tính từ gốc repo
        self.path = Path(path) if path else (ROOT / env if env else VECTORDB_OUTPUT / "chroma")
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

    def index_documents(self, chunks: list[KBChunk], vectors: np.ndarray) -> None:
        """Thêm hoặc ghi đè vector theo chunk_id, chia lô theo giới hạn của Chroma."""
        vectors = np.asarray(vectors, dtype=np.float32)
        step = self._client.get_max_batch_size()
        for s in range(0, len(chunks), step):
            part = chunks[s:s + step]
            self._col.upsert(
                ids=[c.chunk_id for c in part],
                embeddings=vectors[s:s + step],
                metadatas=[chunk_metadata(c, self.model_id) for c in part],
            )

    def retrieve_context(self, query_vector: np.ndarray, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk gần nhất -> [(chunk_id, cosine)] (cosine = 1 - khoảng cách Chroma trả về)."""
        r = self._col.query(query_embeddings=[np.asarray(query_vector, dtype=np.float32)],
                            n_results=k, where=_where(where), include=["distances"])
        return [(cid, 1.0 - d) for cid, d in zip(r["ids"][0], r["distances"][0])]

    def delete_document(self, doc_id: str) -> None:
        """Xoá mọi vector của một deck."""
        self._col.delete(where={"doc_id": doc_id})

    def metadata(self, doc_id: str) -> dict[str, dict[str, Any]]:
        """Metadata mọi vector của một deck trong kho -> {chunk_id: metadata}."""
        r = self._col.get(where={"doc_id": doc_id}, include=["metadatas"])
        return {cid: dict(m or {}) for cid, m in zip(r["ids"], r["metadatas"])}
