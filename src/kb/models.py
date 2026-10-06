"""KBChunk — mẩu tài liệu để đem đi nhúng vector và tìm kiếm.

Xem [docs/spec/kb-chunk.md](../../docs/spec/kb-chunk.md) cho lý do từng quyết định.

Tóm tắt: 1 trang = 1 chunk chính, trang >= 2 ảnh thì mỗi mô tả ảnh thêm 1 vector phụ,
tất cả cùng trỏ về `page_no` vì R2 điều hướng theo TRANG.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KBChunk(BaseModel):
    chunk_id: str                 # "onboarding_kit#p018" | "...#p018.2" (trang bị cắt) | "...#p019.v02" (ảnh)
    doc_id: str
    page_no: int                  # R2 nhảy tới đây — mọi vector đều trỏ về một trang
    page_hash: str = ""           # copy từ ParsedPage — trang đổi hash thì chỉ nạp lại chunk của trang đó (§8)
    section_id: str | None = None
    section_title: str | None = None

    vector_role: Literal["page", "image"] = "page"
    content_type: Literal["content", "section_divider"] = "content"

    text_raw: str                 # chưa có tiền tố — giữ để đối chiếu
    text_enriched: str            # CÓ tiền tố — đây là cái đem đi embed (§10)
    token_count: int = 0

    block_ids: list[str] = Field(default_factory=list)   # truy ngược ParsedDocument
    provenance: dict[str, int] = Field(default_factory=dict)  # {"text_layer": 1, "vlm": 2}

    @property
    def is_searchable(self) -> bool:
        """Trang phân mục không có nội dung -> lọc khỏi truy vấn mặc định.

        KHÔNG xoá (§10 cấm vứt chunk) — chỉ lọc, để luật nhận diện sai còn sửa được
        mà khỏi parse lại (parse lại tốn tiền API). Nhận diện là việc của `slide_type` —
        không thêm ngưỡng token ngầm ở đây (ngưỡng cũ `>= 20` đo lại không lọc chunk nào).
        """
        return self.content_type == "content"

    @property
    def vlm_ratio(self) -> float:
        """Bao nhiêu phần nội dung do model sinh ra. NT2: biết phần nào tin được."""
        total = sum(self.provenance.values())
        return self.provenance.get("vlm", 0) / total if total else 0.0


class ChunkSet(BaseModel):
    """Cả bộ chunk của một tài liệu — ghi ra JSON đọc lại được (§9)."""

    doc_id: str
    max_tokens: int = 500
    tokenizer: str = ""           # tokenizer dùng để đếm, ghi lại cho khỏi lẫn
    chunks: list[KBChunk] = Field(default_factory=list)

    @property
    def searchable(self) -> list[KBChunk]:
        return [c for c in self.chunks if c.is_searchable]

    def by_page(self, page_no: int) -> list[KBChunk]:
        return [c for c in self.chunks if c.page_no == page_no]


class SearchHit(BaseModel):
    """Một kết quả tìm được. Xem docs/spec/search.md.

    Giữ CẢ hạng lẫn điểm thô của từng nhánh: nhìn `rank_dense=11, rank_sparse=1` là biết
    ngay kết quả này do BM25 kéo lên. Chỉ trả mỗi `score` thì không chẩn đoán được gì.
    """

    chunk_id: str
    page_no: int                  # R2 nhảy tới đây

    score: float                  # điểm RRF — CHỈ để xếp thứ tự, KHÔNG phải confidence
    rank_dense: int | None = None     # hạng ở nhánh vector; None = không có nhánh này
    rank_sparse: int | None = None    # hạng ở nhánh BM25
    score_dense: float = 0.0          # cosine thô, để debug
    score_sparse: float = 0.0         # BM25 thô, để debug

    text_enriched: str = ""       # đưa thẳng cho LLM ở R4
    vlm_ratio: float = 0.0        # bao nhiêu phần do model sinh (NT2)
    n_chunks: int = 1             # gộp mấy chunk của cùng trang lại
