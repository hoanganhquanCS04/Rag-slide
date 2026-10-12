"""Chọn kho theo `VECTOR_DB` trong .env (bản gốc: `create_vector_database`)."""

from __future__ import annotations

import importlib
import os

from index.config import MODEL_ID
from index.store.base import VectorStore

# import lúc chọn — chạy inmem thì máy không cần có chromadb
STORES = {
    "inmem": "index.store.variants.inmem:InMemoryStore",
    "chroma": "index.store.variants.chroma:ChromaStore",
}


def create_store(kind: str | None = None, *, model_id: str = MODEL_ID) -> VectorStore:
    """`kind` bỏ trống -> đọc VECTOR_DB (mặc định inmem: không cần cài thêm gì)."""
    kind = (kind or os.environ.get("VECTOR_DB") or "inmem").lower()
    if kind not in STORES:
        raise SystemExit(f"VECTOR_DB='{kind}' khong hop le — chon {' | '.join(STORES)}")
    module, cls = STORES[kind].split(":")
    return getattr(importlib.import_module(module), cls)(model_id)
