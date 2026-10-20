"""Chọn kho vector theo `VECTOR_DB` trong .env (hiện chỉ có chroma)."""

from __future__ import annotations

import importlib
import os

from utils.config import MODEL_ID
from components.vectordb.base import VectorStore

# import lúc chọn — chỉ nạp thư viện của kho được chọn
STORES = {
    "chroma": "components.vectordb.variants.chroma:ChromaStore",
}


def create_store(kind: str | None = None, *, model_id: str = MODEL_ID) -> VectorStore:
    """Tạo kho vector theo tên -> object kho (bỏ trống thì đọc VECTOR_DB, mặc định chroma).

    Cách làm: tra tên trong `STORES` ("module:Class"), chỉ import module khi được chọn.
    """
    kind = (kind or os.environ.get("VECTOR_DB") or "chroma").lower()
    if kind not in STORES:
        raise SystemExit(f"VECTOR_DB='{kind}' khong hop le — chon {' | '.join(STORES)}")
    module, cls = STORES[kind].split(":")
    return getattr(importlib.import_module(module), cls)(model_id)
