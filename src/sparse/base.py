"""Lớp cha của index từ khoá (BM25 — nhánh sparse của tìm kiếm).

Để làm gì: `runtime/retrieve.py` chỉ gọi lớp cha; đổi cách làm BM25 là thêm một biến thể, không sửa retrieve.py.
Cách làm: cùng bộ tên hàm với kho vector (`index_documents` / `retrieve_context` / `delete_document`
/ `sync`), trả cùng kiểu và lọc cùng kiểu, để hai nhánh đi chung một đường.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from utils.filters import Hit, Where, filter_metadata
from models.kb_chunk import ChunkSet, KBChunk

# Tách dính liền của code: plt.savefig(x) -> plt savefig x ; np.arange -> np arange
_SPLIT = re.compile(r"[^0-9A-Za-zÀ-ỹ]+")


def tokenize(text: str) -> list[str]:
    """Tách chữ thành từ cho BM25.

    Cách làm: viết thường, tách theo mọi ký tự không phải chữ / số (tách cả tên hàm: "plt.savefig"
    -> "plt", "savefig"), giữ dấu tiếng Việt, bỏ từ 1 ký tự (trừ số).
    """
    return [t for t in _SPLIT.split(text.lower()) if len(t) > 1 or t.isdigit()]


def fingerprint(chunks: list[KBChunk]) -> list[tuple[str, str, dict[str, Any]]]:
    """Dấu vân tay của bộ chunk: (chunk_id, chữ có tiền tố, metadata lọc) — đổi một trong ba là dựng lại."""
    # KHÔNG dùng text_raw (§10) — index phải khớp đúng thứ đã đem đi nhúng.
    return [(c.chunk_id, c.text_enriched, filter_metadata(c)) for c in chunks]


class SparseIndex(ABC):
    """Khung của index từ khoá."""
    kind: ClassVar[str]

    @abstractmethod
    def index_documents(self, chunks: list[KBChunk]) -> None:
        """Dựng index cho MỘT deck từ NGUYÊN bộ chunk của deck đó (lớp con tự viết).

        KHÔNG thêm dần được: điểm BM25 (IDF) tính trên cả tập chunk, nên gọi với một phần chunk là
        mất phần còn lại của deck. Gọi qua `sync` thay vì gọi thẳng.
        """

    @abstractmethod
    def retrieve_context(self, query: str, k: int, where: Where | None = None) -> list[Hit]:
        """Tìm k chunk điểm cao nhất trong số khớp `where` -> [(chunk_id, điểm)] giảm dần (lớp con tự viết)."""

    @abstractmethod
    def delete_document(self, doc_id: str) -> None:
        """Bỏ index của một deck (lớp con tự viết)."""

    @abstractmethod
    def fingerprint(self, doc_id: str) -> list[tuple[str, str, dict[str, Any]]] | None:
        """Dấu vân tay của bộ chunk đang có trong index của deck; chưa có thì None (lớp con tự viết)."""

    def sync(self, cs: ChunkSet) -> int:
        """Đưa index về đúng bộ chunk này -> số chunk dựng lại (khớp sẵn thì 0).

        Cách làm: so dấu vân tay; lệch thì bỏ index cũ của deck và dựng lại từ đầu.
        """
        if self.fingerprint(cs.doc_id) == fingerprint(cs.chunks):
            return 0
        self.delete_document(cs.doc_id)
        self.index_documents(cs.chunks)
        return len(cs.chunks)
