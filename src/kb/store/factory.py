"""Chọn kho theo `VECTOR_DB` trong .env (bản gốc: `create_vector_database`)."""

from __future__ import annotations

import os

from kb.embed import MODEL_ID            # import là nạp .env luôn
from kb.store.base import VectorStore

DEFAULT_CHROMA_PATH = "out/kb/chroma"


def create_store(kind: str | None = None, *, model_id: str = MODEL_ID) -> VectorStore:
    """`kind` bỏ trống -> đọc VECTOR_DB (mặc định inmem: không cần cài thêm gì).

    Import biến thể lúc cần — chạy inmem thì máy không cần có chromadb.
    """
    kind = (kind or os.environ.get("VECTOR_DB") or "inmem").lower()
    if kind == "inmem":
        from kb.store.inmem import InMemoryStore

        return InMemoryStore(model_id)
    if kind == "chroma":
        from kb.store.chroma import ChromaStore

        return ChromaStore(model_id, os.environ.get("CHROMA_PATH") or DEFAULT_CHROMA_PATH)
    raise SystemExit(f"VECTOR_DB='{kind}' khong hop le — chon inmem | chroma")
