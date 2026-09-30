"""Chọn index từ khoá theo `SPARSE_INDEX` trong .env — cùng kiểu `kb/store/factory.py`."""

from __future__ import annotations

import os

from kb.embed import MODEL_ID  # noqa: F401 — import là nạp .env luôn
from kb.sparse.base import SparseIndex


def create_sparse(kind: str | None = None) -> SparseIndex:
    """`kind` bỏ trống -> đọc SPARSE_INDEX (mặc định rank_bm25)."""
    kind = (kind or os.environ.get("SPARSE_INDEX") or "rank_bm25").lower()
    if kind == "rank_bm25":
        from kb.sparse.rankbm25 import RankBM25Index

        return RankBM25Index()
    raise SystemExit(f"SPARSE_INDEX='{kind}' khong hop le — chon rank_bm25")
