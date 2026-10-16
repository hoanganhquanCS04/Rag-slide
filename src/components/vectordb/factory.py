"""Chọn kho vector theo `VECTOR_DB` trong .env (inmem hoặc chroma)."""

from __future__ import annotations

import importlib
import os

from utils.config import MODEL_ID
from components.vectordb.base import VectorStore

# import lúc chọn — chạy inmem thì máy không cần có chromadb
STORES = {
    "inmem": "components.vectordb.variants.inmem:InMemoryStore",
    "chroma": "components.vectordb.variants.chroma:ChromaStore",
}


def create_store(kind: str | None = None, *, model_id: str = MODEL_ID) -> VectorStore:
    """Tạo kho vector theo tên -> object kho (bỏ trống thì đọc VECTOR_DB, mặc định inmem).

    Cách làm: tra tên trong `STORES` ("module:Class"), chỉ import module khi được chọn
    (chạy inmem thì máy không cần cài chromadb).
    """
    kind = (kind or os.environ.get("VECTOR_DB") or "inmem").lower()
    if kind not in STORES:
        raise SystemExit(f"VECTOR_DB='{kind}' khong hop le — chon {' | '.join(STORES)}")
    module, cls = STORES[kind].split(":")
    return getattr(importlib.import_module(module), cls)(model_id)
