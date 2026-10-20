"""Kiểu bộ lọc (`Where`) và kiểu kết quả (`Hit`) dùng chung cho kho vector và BM25.

Để làm gì: hai nhánh tìm kiếm lọc cùng một kiểu và trả cùng một kiểu kết quả.
"""

from __future__ import annotations

from typing import Any

from models.kb_chunk import KBChunk, StoredChunk

# Lọc metadata: {"doc_id": "x", "page_no": [3, 4]}  =  doc_id == "x"  VÀ  page_no ∈ {3, 4}
Where = dict[str, Any]
Hit = tuple[StoredChunk, float]  # (bản ghi tìm được, điểm) — càng cao càng giống


def filter_metadata(c: KBChunk) -> dict[str, Any]:
    """Lấy các trường của chunk dùng để lọc, cất cạnh vector trong kho.

    Cách làm: lấy 4 trường, bỏ trường None (Chroma không nhận None). Không cất thứ suy ra được
    (trang có được tìm không = `content_type == "content"`).
    """
    meta = {
        "doc_id": c.doc_id,
        "page_no": c.page_no,
        "section_id": c.section_id,
        "content_type": c.content_type,
    }
    return {k: v for k, v in meta.items() if v is not None}


def matches(meta: dict[str, Any], where: Where | None) -> bool:
    """Kiểm một chunk có khớp bộ lọc `where` không.

    Cách làm: mọi điều kiện đều phải đúng; giá trị là list thì "thuộc list", còn lại thì "bằng".
    """
    for k, v in (where or {}).items():
        if (meta.get(k) not in v) if isinstance(v, list) else (meta.get(k) != v):
            return False
    return True
