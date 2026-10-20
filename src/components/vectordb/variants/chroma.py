"""Kho vector `chroma`: ghi đĩa, mọi deck chung một collection, tách bằng metadata `doc_id`.

Cách làm: mỗi bản ghi = id · vector · chữ (`documents`) · metadata lọc. Đổi 3 mặc định của Chroma —
tắt embedding có sẵn (vector luôn do mình đưa), dùng cosine thay cho L2, tắt gửi số liệu sử dụng ra ngoài.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from utils.config import ROOT, VECTORDB_OUTPUT
from utils.filters import Hit, Where, filter_metadata
from components.vectordb.base import VectorStore
from models.kb_chunk import KBChunk, StoredChunk

REBUILD = "chay lai `python src/offline/chunker/cli.py src/output/parser/<ten>/document.json` (trung cache, khong ton API)"


def _where(where: Where | None) -> dict[str, Any] | None:
    """Đổi bộ lọc dict phẳng sang cú pháp Chroma: nhiều điều kiện bọc `$and`, list thành `$in`."""
    conds = [{k: {"$in": v} if isinstance(v, list) else v} for k, v in (where or {}).items()]
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


def _records(ids: list[str], documents: list[str | None], metadatas: list[Any]) -> list[StoredChunk]:
    """Ghép 3 cột Chroma trả về thành danh sách bản ghi; bản ghi thiếu chữ (kho cũ) thì dừng."""
    missing = [cid for cid, d in zip(ids, documents) if d is None]
    if missing:
        raise SystemExit(f"kho Chroma co {len(missing)} ban ghi chua co chu (vd {missing[0]}) — {REBUILD}")
    return [StoredChunk(chunk_id=cid, document=d, metadata=dict(m or {}))
            for cid, d, m in zip(ids, documents, metadatas)]


class ChromaStore(VectorStore):
    """Kho vector dùng Chroma, lưu ở `src/output/vectordb/chroma` (hoặc CHROMA_PATH)."""
    kind = "chroma"

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
                             f"xoa {self.path} roi {REBUILD}")

    def index_documents(self, chunks: list[KBChunk], vectors: np.ndarray) -> None:
        """Thêm hoặc ghi đè bản ghi (vector + chữ + metadata) theo chunk_id, chia lô theo giới hạn của Chroma.

        Cách làm: xoá id cũ rồi mới thêm, KHÔNG dùng `upsert` — upsert của Chroma GỘP metadata mới vào
        metadata cũ, khoá đã bỏ (vd `page_hash`) vẫn nằm lại trong kho.
        """
        vectors = np.asarray(vectors, dtype=np.float32)
        step = self._client.get_max_batch_size()
        for s in range(0, len(chunks), step):
            part = chunks[s:s + step]
            ids = [c.chunk_id for c in part]
            self._col.delete(ids=ids)
            self._col.add(
                ids=ids,
                embeddings=vectors[s:s + step],
                documents=[c.text_enriched for c in part],
                metadatas=[filter_metadata(c) for c in part],
            )

    def retrieve_context(self, query_vector: np.ndarray, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk gần nhất -> [(bản ghi, cosine)] (cosine = 1 - khoảng cách Chroma trả về)."""
        r = self._col.query(query_embeddings=[np.asarray(query_vector, dtype=np.float32)],
                            n_results=k, where=_where(where),
                            include=["distances", "documents", "metadatas"])
        records = _records(r["ids"][0], r["documents"][0], r["metadatas"][0])
        return [(rec, 1.0 - d) for rec, d in zip(records, r["distances"][0])]

    def get_document(self, doc_id: str) -> list[StoredChunk]:
        """Mọi bản ghi (chữ + metadata) của một deck trong kho."""
        r = self._col.get(where={"doc_id": doc_id}, include=["documents", "metadatas"])
        return _records(r["ids"], r["documents"], r["metadatas"])

    def get_vectors(self, doc_id: str) -> tuple[list[str], np.ndarray]:
        """Vector của mọi bản ghi một deck -> (danh sách chunk_id, ma trận vector cùng thứ tự)."""
        r = self._col.get(where={"doc_id": doc_id}, include=["embeddings"])
        return list(r["ids"]), np.asarray(r["embeddings"], dtype=np.float32)

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        """Xoá các bản ghi theo chunk_id."""
        self._col.delete(ids=chunk_ids)

    def delete_document(self, doc_id: str) -> None:
        """Xoá mọi bản ghi của một deck."""
        self._col.delete(where={"doc_id": doc_id})
