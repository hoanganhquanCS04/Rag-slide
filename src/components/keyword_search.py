"""Tìm theo từ khoá (BM25) — nhánh thứ hai của tìm kiếm, bên cạnh tìm bằng vector.

Để làm gì: vector hiểu Ý câu hỏi nhưng hay bỏ sót từ hiếm (tên hàm, mã số); BM25 khớp đúng CHỮ.
Cách làm: bảng BM25 nằm trong RAM, mỗi deck một bảng, không ghi đĩa — lúc khởi động dựng từ chính các
bản ghi đọc ra từ kho vector (`get_document`), nên chữ của hai nhánh luôn là MỘT bản. Cùng bộ tên hàm
với kho vector (`index_documents` / `retrieve_context`) và cùng kiểu kết quả `[(bản ghi, điểm)]`.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from models.kb_chunk import StoredChunk
from utils.filters import Hit, Where, matches

# Tách dính liền của code: plt.savefig(x) -> plt savefig x ; np.arange -> np arange
_SPLIT = re.compile(r"[^0-9A-Za-zÀ-ỹ]+")


def tokenize(text: str) -> list[str]:
    """Tách chữ thành từ cho BM25.

    Cách làm: viết thường, tách theo mọi ký tự không phải chữ / số (tách cả tên hàm: "plt.savefig"
    -> "plt", "savefig"), giữ dấu tiếng Việt, bỏ từ 1 ký tự (trừ số).
    """
    return [t for t in _SPLIT.split(text.lower()) if len(t) > 1 or t.isdigit()]


class BM25Index:
    """Bảng BM25 trong RAM (thư viện rank_bm25), mỗi deck một bảng."""

    kind = "rank_bm25"

    def __init__(self) -> None:
        """Tạo kho rỗng: doc_id -> (bảng BM25, các bản ghi theo đúng thứ tự trong bảng)."""
        self._documents: dict[str, tuple[Any, list[StoredChunk]]] = {}

    def index_documents(self, records: list[StoredChunk]) -> None:
        """Dựng bảng BM25 cho deck của các bản ghi này (thay bảng cũ nếu có).

        Phải đưa NGUYÊN bộ bản ghi của một deck (kể cả trang chuyển chương): điểm BM25 (IDF) tính
        trên cả bộ, nên không thêm dần được.
        """
        from rank_bm25 import BM25Okapi

        if not records:
            return
        bm25 = BM25Okapi([tokenize(r.document) for r in records])
        self._documents[records[0].metadata["doc_id"]] = (bm25, list(records))

    def retrieve_context(self, query: str, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk điểm BM25 cao nhất trong số khớp bộ lọc -> [(bản ghi, điểm)], cao đứng trước.

        `where` bắt buộc có `doc_id`, vì mỗi deck một bảng riêng.
        Chunk điểm 0 (không khớp chữ nào) KHÔNG được xếp hạng.
        """
        doc_id = (where or {}).get("doc_id")
        if not isinstance(doc_id, str) or doc_id not in self._documents:
            raise ValueError(f"where phai co doc_id da dung bang (dang co: {sorted(self._documents)}), "
                             f"nhan {doc_id!r}")
        bm25, records = self._documents[doc_id]
        scores = np.asarray(bm25.get_scores(tokenize(query)), dtype=np.float32)
        # Điểm 0 = không khớp chữ nào -> KHÔNG xếp hạng. Bản đầu xếp cả chúng: các chunk 0 điểm
        # hoà nhau nên giữ thứ tự trong file, p1 hạng 2, p2 hạng 3… -> trang đầu deck được
        # cộng RRF không vì lý do gì (hỏi "VGRFOC": 49/50 chunk trong pool là điểm 0).
        rows = [i for i, r in enumerate(records) if scores[i] > 0 and matches(r.metadata, where)]
        order = sorted(rows, key=lambda i: -scores[i])[:k]
        return [(records[i], float(scores[i])) for i in order]
