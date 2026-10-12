"""Lọc + kiểu kết quả dùng CHUNG cho kho vector (`store/`) và index từ khoá (`sparse/`).

Hai nhánh của search lọc bằng CÙNG một dict `where` và trả CÙNG kiểu `Hit` — để đi qua
`search.py` một đường như nhau. Để ở đây thì `sparse/` không phải import `store/`.
"""

from __future__ import annotations

from typing import Any

from models.kb_chunk import KBChunk

# Lọc metadata: {"doc_id": "x", "page_no": [3, 4]}  =  doc_id == "x"  VÀ  page_no ∈ {3, 4}
Where = dict[str, Any]
Hit = tuple[str, float]          # (chunk_id, điểm) — càng cao càng giống


def filter_metadata(c: KBChunk) -> dict[str, Any]:
    """Các trường `where` lọc được. PHẲNG: Chroma không nhận None / list / dict lồng."""
    meta = {
        "doc_id": c.doc_id,
        "page_no": c.page_no,
        "section_id": c.section_id,
        "vector_role": c.vector_role,
        "content_type": c.content_type,
        "is_searchable": c.is_searchable,
    }
    return {k: v for k, v in meta.items() if v is not None}


def matches(meta: dict[str, Any], where: Where | None) -> bool:
    """Luật lọc của bản gốc (`make_metadata_filter`): giá trị là list -> thuộc, còn lại -> bằng."""
    for k, v in (where or {}).items():
        if (meta.get(k) not in v) if isinstance(v, list) else (meta.get(k) != v):
            return False
    return True
