"""Index từ khoá (nhánh sparse) — cùng hình với `kb/store/` của nhánh dense.

Một lớp cha chung + các biến thể THAY THẾ nhau + factory chọn theo `SPARSE_INDEX` trong .env.
`search.py` chỉ gọi lớp cha — đổi cách làm BM25 là thêm một file biến thể, không sửa search.

    rank_bm25   RAM, dựng lại mỗi lần khởi động từ chunks.json. Hiện là biến thể duy nhất.

Vì sao tách ra dù mới có một biến thể — đo trên kho nhân bản từ onboarding_kit:

    chunk      dựng (1 lần)   mỗi câu hỏi
    88           2.7 ms         0.4 ms
    10.000       260 ms          32 ms
    50.000       1.2 s          126 ms      <- rank_bm25 duyệt HẾT kho cho mỗi từ của câu hỏi

Mỗi phiên một deck (~100 chunk) thì chẳng sao. KB nguồn dùng chung phình lên thì phải đổi
sang thứ có inverted index (bm25s, sparse vector của Qdrant — §7.1). Lúc đó viết thêm biến
thể ở đây, giữ `tokenize()`: tokenizer dựng sẵn của thư viện thường là tiếng Anh.

`query` trả CÙNG kiểu với `VectorStore.query` — (chunk_id, điểm) đã xếp giảm dần, lọc bằng
CÙNG dict `where` — để hai nhánh trong `search.py` đi qua một đường như nhau.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from kb.models import ChunkSet, KBChunk
from kb.store.base import Hit, Where

# Tách dính liền của code: plt.savefig(x) -> plt savefig x ; np.arange -> np arange
_SPLIT = re.compile(r"[^0-9A-Za-zÀ-ỹ]+")


def tokenize(text: str) -> list[str]:
    """Tách chữ cho BM25.

    Tiếng Việt viết rời từng âm tiết nên tách theo khoảng trắng là đủ dùng. Điểm mấu chốt
    là **tách tên hàm**: người hỏi gõ "savefig" chứ không gõ "plt.savefig('line_graph.png')".
    Không tách thì cả cụm là một token và không bao giờ khớp.

    GIỮ NGUYÊN DẤU tiếng Việt. Hệ quả: gõ thiếu dấu là nhánh này về 0 (`bieu` != `biểu`).
    Dense còn vớt được mờ mờ, BM25 thì không. Ghi ở search.md §3.
    """
    return [t for t in _SPLIT.split(text.lower()) if len(t) > 1 or t.isdigit()]


def filter_metadata(c: KBChunk) -> dict[str, Any]:
    """Các trường `where` lọc được — cùng tên với `chunk_metadata` của kho vector."""
    meta = {
        "doc_id": c.doc_id,
        "page_no": c.page_no,
        "section_id": c.section_id,
        "vector_role": c.vector_role,
        "content_type": c.content_type,
        "is_searchable": c.is_searchable,
    }
    return {k: v for k, v in meta.items() if v is not None}


class SparseIndex(ABC):
    kind: ClassVar[str]
    persistent: ClassVar[bool]       # False = tắt là mất, phải dựng lại mỗi lần chạy

    @abstractmethod
    def sync(self, cs: ChunkSet) -> int:
        """Đưa index về ĐÚNG bộ chunk này. Đã khớp -> không làm gì, trả 0; lệch -> dựng lại
        phần của tài liệu đó, trả số chunk."""

    @abstractmethod
    def query(self, text: str, k: int, where: Where | None = None) -> list[Hit]:
        """k chunk điểm cao nhất trong số khớp `where`, xếp giảm dần."""
