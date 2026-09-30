"""Index từ khoá cho KB (nhánh sparse của search) — chọn bằng SPARSE_INDEX trong .env. Xem base.py."""

from kb.sparse.base import SparseIndex, tokenize
from kb.sparse.factory import create_sparse

__all__ = ["SparseIndex", "create_sparse", "tokenize"]
