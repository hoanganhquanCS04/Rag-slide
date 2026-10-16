"""Lớp cha của kho vector: cất vector của chunk và tìm chunk gần nhất theo vector.

Để làm gì: code phía trên chỉ gọi lớp cha; đổi kho là sửa `VECTOR_DB` trong .env, không sửa code.
    inmem    trong RAM, tắt là mất — mỗi lần chạy nạp lại từ file .npy
    chroma   ghi đĩa, mọi deck chung một collection, lọc theo metadata
Cách làm: kho nhận vector ĐÃ nhúng (không tự gọi API); tên collection có tên model; chỉ làm nhánh
vector (BM25 ở `sparse/`, cùng bộ tên hàm). Kho chỉ là bản sao — xoá đi thì `sync` dựng lại từ .npy.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from utils.filters import Hit, Where, filter_metadata
from utils.vectors import load_vectors, model_slug, text_key
from models.kb_chunk import ChunkSet, KBChunk


def collection_name(model_id: str) -> str:
    """Tên collection theo model: "kb__text-embedding-3-small" (chỉ giữ ký tự Chroma nhận)."""
    return "kb__" + re.sub(r"[^a-z0-9._-]+", "-", model_slug(model_id))


def chunk_metadata(c: KBChunk, model_id: str) -> dict[str, Any]:
    """Metadata cất cạnh mỗi vector: các trường lọc + page_hash + hash chữ (để `sync` đối chiếu)."""
    return {**filter_metadata(c), "page_hash": c.page_hash,
            "text_key": text_key(c.text_enriched, model_id)}


class VectorStore(ABC):
    """Khung của kho vector. `persistent` = kho có ghi đĩa không (inmem thì không)."""
    kind: ClassVar[str]
    persistent: ClassVar[bool]       # False = tắt là mất, phải nạp lại mỗi lần chạy

    def __init__(self, model_id: str):
        """Nhớ tên model và đặt tên collection."""
        self.model_id = model_id
        self.name = collection_name(model_id)

    @abstractmethod
    def index_documents(self, chunks: list[KBChunk], vectors: np.ndarray) -> None:
        """Thêm hoặc ghi đè vector theo chunk_id; `vectors[i]` là vector của `chunks[i]` (lớp con tự viết)."""

    @abstractmethod
    def retrieve_context(self, query_vector: np.ndarray, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk gần `query_vector` nhất trong số khớp `where` -> [(chunk_id, cosine)] giảm dần (lớp con tự viết)."""

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Xoá mọi vector của một deck (lớp con tự viết)."""

    @abstractmethod
    def metadata(self, doc_id: str) -> dict[str, dict[str, Any]]:
        """Metadata của mọi vector đang có trong kho của một deck -> {chunk_id: metadata} (lớp con tự viết)."""

    def sync(self, cs: ChunkSet, vectors_path: str | Path) -> int:
        """Đưa kho về đúng bộ chunk hiện tại -> số vector đã nạp lại (khớp sẵn thì 0).

        Cách làm: so metadata trong kho với metadata mong muốn; lệch thì xoá hết vector của deck và nạp
        lại từ file .npy (không gọi API).
        """
        want = {c.chunk_id: chunk_metadata(c, self.model_id) for c in cs.chunks}
        if self.metadata(cs.doc_id) == want:
            return 0
        vectors = load_vectors(cs, vectors_path, self.model_id)
        self.delete_document(cs.doc_id)
        self.index_documents(cs.chunks, vectors)
        return len(cs.chunks)
