"""Kho vector — cất vector của chunk, tìm theo vector.

Khung lấy từ minhbtrc/chatbot-template (MIT, src/base/components/vector_databases):
một lớp cha chung + hai biến thể THAY THẾ nhau + factory chọn theo config. Code phía trên
chỉ gọi lớp cha — đổi kho là sửa `VECTOR_DB` trong .env, không sửa code.

    inmem    RAM, tắt là mất — mỗi lần khởi động nạp lại từ .npy. Không cần cài gì.
    chroma   ghi đĩa, nhiều tài liệu chung một collection, lọc theo metadata.

Khác bản gốc — CỐ Ý, đừng sửa lại cho giống:

    bản gốc                                   ở đây
    kho tự gọi embedding khi thêm             nhận vector ĐÃ nhúng (.npy, có cache) — không gọi API lần 2
    collection tên "default"                  kb__<model_id> — trỏ index cũ bằng model mới là rác im lặng (§10)
    tìm trả về chữ (page_content)             trả (chunk_id, cosine) — R2 cần page_no, RRF cần hạng
    lọc: inmem nhận HÀM, chroma nhận DICT     cả hai nhận CÙNG một dict phẳng
         (lệch nhau -> lọc ở inmem hỏng)
    chỉ dense                                 vẫn chỉ dense — BM25 + RRF ở search.py

Kho chỉ là BẢN SAO để tìm. Nguồn vẫn là `chunks.json` + `.npy` (§9 resume được từ bất kỳ
stage nào): xoá cả thư mục chroma thì lần chạy sau `sync` tự dựng lại, không tốn API.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from kb.embed import load_vectors, model_slug, text_key
from kb.models import ChunkSet, KBChunk

# Lọc metadata: {"doc_id": "x", "page_no": [3, 4]}  =  doc_id == "x"  VÀ  page_no ∈ {3, 4}
Where = dict[str, Any]
Hit = tuple[str, float]          # (chunk_id, cosine) — càng cao càng giống


def collection_name(model_id: str) -> str:
    """'kb__text-embedding-3-small'. Chroma chỉ nhận [a-zA-Z0-9._-]."""
    return "kb__" + re.sub(r"[^a-z0-9._-]+", "-", model_slug(model_id))


def chunk_metadata(c: KBChunk, model_id: str) -> dict[str, Any]:
    """Metadata PHẲNG (Chroma không nhận None / list / dict lồng) — thứ để lọc + đối chiếu.

    `text_key` = vân tay 'vector này nhúng từ đúng chữ này, bằng đúng model này'.
    """
    meta = {
        "doc_id": c.doc_id,
        "page_no": c.page_no,
        "page_hash": c.page_hash,
        "section_id": c.section_id,
        "vector_role": c.vector_role,
        "content_type": c.content_type,
        "is_searchable": c.is_searchable,
        "text_key": text_key(c.text_enriched, model_id),
    }
    return {k: v for k, v in meta.items() if v is not None}


def matches(meta: dict[str, Any], where: Where | None) -> bool:
    """Luật lọc của bản gốc (`make_metadata_filter`): giá trị là list -> thuộc, còn lại -> bằng."""
    for k, v in (where or {}).items():
        if (meta.get(k) not in v) if isinstance(v, list) else (meta.get(k) != v):
            return False
    return True


class VectorStore(ABC):
    kind: ClassVar[str]
    persistent: ClassVar[bool]       # False = tắt là mất, phải nạp lại mỗi lần chạy

    def __init__(self, model_id: str):
        self.model_id = model_id
        self.name = collection_name(model_id)

    @abstractmethod
    def upsert(self, chunks: list[KBChunk], vectors: np.ndarray) -> None:
        """Thêm / ghi đè theo chunk_id. `vectors[i]` là vector của `chunks[i]`, đã chuẩn hoá L2."""

    @abstractmethod
    def delete(self, where: Where) -> None: ...

    @abstractmethod
    def query(self, vector: np.ndarray, k: int, where: Where | None = None) -> list[Hit]:
        """k chunk gần nhất trong số khớp `where`, xếp giảm dần theo cosine."""

    @abstractmethod
    def metadata(self, doc_id: str) -> dict[str, dict[str, Any]]:
        """{chunk_id: metadata} đang nằm trong kho của tài liệu này."""

    def sync(self, cs: ChunkSet, vectors_path: str | Path) -> int:
        """Đưa kho về ĐÚNG bộ chunk này. Đã khớp -> không làm gì, trả 0.

        So cả metadata chứ không chỉ `page_hash`: đổi luật chunk (tiền tố, cắt) thì chữ đổi mà
        trang không đổi; đổi luật `slide_type` thì `is_searchable` đổi mà chữ không đổi.
        Lệch là thay CẢ tài liệu — vài trăm vector, vài chục ms. Phần đắt (gọi API nhúng) thì
        cache của embed.py đã lo.
        """
        want = {c.chunk_id: chunk_metadata(c, self.model_id) for c in cs.chunks}
        if self.metadata(cs.doc_id) == want:
            return 0
        vectors = load_vectors(cs, vectors_path, self.model_id)
        self.delete({"doc_id": cs.doc_id})
        self.upsert(cs.chunks, vectors)
        return len(cs.chunks)
