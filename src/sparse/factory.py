"""Chọn index từ khoá theo `SPARSE_INDEX` trong .env (hiện chỉ có rank_bm25)."""

from __future__ import annotations

import importlib
import os

import utils.config  # noqa: F401 — import là nạp .env luôn
from sparse.base import SparseIndex

# import lúc chọn — biến thể này hỏng không kéo biến thể kia theo
SPARSE = {
    "rank_bm25": "sparse.variants.rankbm25:RankBM25Index",
}


def create_sparse(kind: str | None = None) -> SparseIndex:
    """Tạo index từ khoá theo tên -> object index (bỏ trống thì đọc SPARSE_INDEX, mặc định rank_bm25)."""
    kind = (kind or os.environ.get("SPARSE_INDEX") or "rank_bm25").lower()
    if kind not in SPARSE:
        raise SystemExit(f"SPARSE_INDEX='{kind}' khong hop le — chon {' | '.join(SPARSE)}")
    module, cls = SPARSE[kind].split(":")
    return getattr(importlib.import_module(module), cls)()
