"""Lớp cha của kho vector: cất bản ghi của chunk và tìm chunk gần nhất theo vector.

Để làm gì: code phía trên chỉ gọi lớp cha; đổi kho là sửa `VECTOR_DB` trong .env, không sửa code.
    chroma   ghi đĩa, mọi deck chung một collection, lọc theo metadata
Cách làm: mỗi bản ghi đủ 4 trường chuẩn — id (chunk_id) · vector · chữ (text_enriched) · metadata lọc —
nên tìm ra là có luôn chữ, không phải tra file nào. Kho nhận vector ĐÃ nhúng (không tự gọi
API); tên collection có tên model; chỉ làm nhánh vector (BM25 ở `components/keyword_search.py`).
Ghi vào kho chỉ làm ở offline (`src/offline/chunker/cli.py`: chunk xong nhúng rồi ghi luôn); lúc chạy chỉ đọc.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import ClassVar

import numpy as np

from utils.filters import Hit, Where
from utils.vectors import model_slug
from models.kb_chunk import KBChunk, StoredChunk


def collection_name(model_id: str) -> str:
    """Tên collection theo model: "kb__text-embedding-3-small" (chỉ giữ ký tự Chroma nhận)."""
    return "kb__" + re.sub(r"[^a-z0-9._-]+", "-", model_slug(model_id))


class VectorStore(ABC):
    """Khung của kho vector."""
    kind: ClassVar[str]

    def __init__(self, model_id: str):
        """Nhớ tên model và đặt tên collection."""
        self.model_id = model_id
        self.name = collection_name(model_id)

    @abstractmethod
    def index_documents(self, chunks: list[KBChunk], vectors: np.ndarray) -> None:
        """Thêm hoặc ghi đè bản ghi theo chunk_id; `vectors[i]` là vector của `chunks[i]` (lớp con tự viết)."""

    @abstractmethod
    def retrieve_context(self, query_vector: np.ndarray, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk gần `query_vector` nhất trong số khớp `where` -> [(bản ghi, cosine)] giảm dần (lớp con tự viết)."""

    @abstractmethod
    def get_document(self, doc_id: str) -> list[StoredChunk]:
        """Mọi bản ghi của một deck đang có trong kho (lớp con tự viết)."""

    @abstractmethod
    def get_vectors(self, doc_id: str) -> tuple[list[str], np.ndarray]:
        """Vector của mọi bản ghi một deck -> (chunk_id, ma trận) — để kiểm kho tìm đúng (lớp con tự viết)."""

    @abstractmethod
    def delete_chunks(self, chunk_ids: list[str]) -> None:
        """Xoá các bản ghi theo chunk_id (lớp con tự viết)."""

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Xoá mọi bản ghi của một deck (lớp con tự viết)."""

    def replace_document(self, chunks: list[KBChunk], vectors: np.ndarray) -> int:
        """Đưa kho về đúng bộ chunk mới của một deck -> số bản ghi cũ đã xoá.

        Cách làm: ghi bộ mới trước, rồi mới xoá các chunk cũ không còn trong bộ mới — không có
        lúc nào deck trong kho bị rỗng.
        """
        if not chunks:
            return 0
        self.index_documents(chunks, vectors)
        keep = {c.chunk_id for c in chunks}
        stale = [r.chunk_id for r in self.get_document(chunks[0].doc_id) if r.chunk_id not in keep]
        if stale:
            self.delete_chunks(stale)
        return len(stale)
