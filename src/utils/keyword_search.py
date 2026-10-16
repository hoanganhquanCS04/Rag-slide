"""Tìm theo từ khoá (BM25) — nhánh thứ hai của tìm kiếm, bên cạnh tìm bằng vector.

Để làm gì: vector hiểu Ý câu hỏi nhưng hay bỏ sót từ hiếm (tên hàm, mã số); BM25 khớp đúng CHỮ.
Cách làm: bảng BM25 nằm trong RAM, mỗi deck một bảng, không ghi đĩa — mỗi lần khởi động `sync` dựng lại
từ chunks.json (vài ms). Cùng bộ tên hàm với kho vector (`index_documents` / `retrieve_context` / `sync`)
và cùng kiểu kết quả `[(chunk_id, điểm)]`, để hai nhánh đi chung một đường.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from models.kb_chunk import ChunkSet, KBChunk
from utils.filters import Hit, Where, filter_metadata, matches

# Tách dính liền của code: plt.savefig(x) -> plt savefig x ; np.arange -> np arange
_SPLIT = re.compile(r"[^0-9A-Za-zÀ-ỹ]+")

# Dấu vân tay một chunk: (chunk_id, chữ đem đi dựng bảng, metadata dùng để lọc)
Fingerprint = list[tuple[str, str, dict[str, Any]]]


def tokenize(text: str) -> list[str]:
    """Tách chữ thành từ cho BM25.

    Cách làm: viết thường, tách theo mọi ký tự không phải chữ / số (tách cả tên hàm: "plt.savefig"
    -> "plt", "savefig"), giữ dấu tiếng Việt, bỏ từ 1 ký tự (trừ số).
    """
    return [t for t in _SPLIT.split(text.lower()) if len(t) > 1 or t.isdigit()]


def fingerprint(chunks: list[KBChunk]) -> Fingerprint:
    """Dấu vân tay của bộ chunk: đổi chữ, mã chunk hay metadata lọc thì phải dựng lại bảng."""
    # Dùng text_enriched, KHÔNG dùng text_raw — bảng phải khớp đúng thứ đã đem đi nhúng vector.
    return [(c.chunk_id, c.text_enriched, filter_metadata(c)) for c in chunks]


class BM25Index:
    """Bảng BM25 trong RAM (thư viện rank_bm25), mỗi deck một bảng."""

    kind = "rank_bm25"

    def __init__(self) -> None:
        """Tạo kho rỗng: doc_id -> (bảng BM25, danh sách chunk_id, metadata từng chunk, dấu vân tay)."""
        self._documents: dict[str, tuple[Any, list[str], list[dict[str, Any]], Fingerprint]] = {}

    def index_documents(self, chunks: list[KBChunk]) -> None:
        """Dựng bảng BM25 cho deck của bộ chunk này (thay bảng cũ nếu có).

        Phải đưa NGUYÊN bộ chunk của một deck: điểm BM25 (IDF) tính trên cả bộ, nên không thêm dần được.
        Thường gọi qua `sync`, không gọi thẳng.
        """
        from rank_bm25 import BM25Okapi

        if not chunks:
            return
        finger = fingerprint(chunks)
        bm25 = BM25Okapi([tokenize(c.text_enriched) for c in chunks])
        self._documents[chunks[0].doc_id] = (bm25, [f[0] for f in finger], [f[2] for f in finger], finger)

    def retrieve_context(self, query: str, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk điểm BM25 cao nhất trong số khớp bộ lọc -> [(chunk_id, điểm)], cao đứng trước.

        `where` bắt buộc có `doc_id`, vì mỗi deck một bảng riêng.
        Chunk điểm 0 (không khớp chữ nào) KHÔNG được xếp hạng.
        """
        doc_id = (where or {}).get("doc_id")
        if not isinstance(doc_id, str) or doc_id not in self._documents:
            raise ValueError(f"where phai co doc_id da sync (dang co: {sorted(self._documents)}), "
                             f"nhan {doc_id!r}")
        bm25, ids, meta, _ = self._documents[doc_id]
        scores = np.asarray(bm25.get_scores(tokenize(query)), dtype=np.float32)
        # Điểm 0 = không khớp chữ nào -> KHÔNG xếp hạng. Bản đầu xếp cả chúng: các chunk 0 điểm
        # hoà nhau nên giữ thứ tự trong file, p1 hạng 2, p2 hạng 3… -> trang đầu deck được
        # cộng RRF không vì lý do gì (hỏi "VGRFOC": 49/50 chunk trong pool là điểm 0).
        rows = [i for i, m in enumerate(meta) if scores[i] > 0 and matches(m, where)]
        order = sorted(rows, key=lambda i: -scores[i])[:k]
        return [(ids[i], float(scores[i])) for i in order]

    def sync(self, chunk_set: ChunkSet) -> int:
        """Đưa bảng về đúng bộ chunk này -> số chunk đã dựng lại (khớp sẵn thì 0).

        Cách làm: so dấu vân tay; lệch thì bỏ bảng cũ của deck và dựng lại từ đầu.
        """
        current = self._documents.get(chunk_set.doc_id)
        if current and current[3] == fingerprint(chunk_set.chunks):
            return 0
        self._delete_document(chunk_set.doc_id)
        self.index_documents(chunk_set.chunks)
        return len(chunk_set.chunks)

    def _delete_document(self, doc_id: str) -> None:
        """Bỏ bảng BM25 của một deck."""
        self._documents.pop(doc_id, None)
