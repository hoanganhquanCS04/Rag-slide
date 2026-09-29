"""Kho vector cho KB — inmem | chroma, chọn bằng VECTOR_DB trong .env. Xem base.py."""

from kb.store.base import VectorStore
from kb.store.factory import create_store

__all__ = ["VectorStore", "create_store"]
