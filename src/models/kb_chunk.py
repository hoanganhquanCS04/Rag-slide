"""KBChunk — mẩu tài liệu để đem đi nhúng vector và tìm kiếm.

Xem [docs/spec/kb-chunk.md](../../docs/spec/kb-chunk.md) cho lý do từng quyết định.

Tóm tắt: 1 trang = 1 chunk (dài thì cắt `.1`, `.2`…), mọi chunk trỏ về `page_no` vì R2
điều hướng theo TRANG. Cắt ở `src/chunker/`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KBChunk(BaseModel):
    chunk_id: str                 # "onboarding_kit#p018" | "...#p018.2" (trang bị cắt)
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
    provenance: dict[str, int] = Field(default_factory=dict)  # {"vlm": 3, "manual": 1}
    n_images: int = 0             # bao nhiêu khối trong block_ids là MÔ TẢ ẢNH

    @property
    def is_searchable(self) -> bool:
        """Trang phân mục không có nội dung -> lọc khỏi truy vấn mặc định.

        KHÔNG xoá (§10 cấm vứt chunk) — chỉ lọc, để luật nhận diện sai còn sửa được
        mà khỏi parse lại (parse lại tốn tiền API). Nhận diện là việc của `slide_type` —
        không thêm ngưỡng token ngầm ở đây (ngưỡng cũ `>= 20` đo lại không lọc chunk nào).
        """
        return self.content_type == "content"

    @property
    def image_ratio(self) -> float:
        """Bao nhiêu phần nội dung là MÔ TẢ ẢNH do VLM viết — dễ sai số / bịa bối cảnh hơn chữ
        VLM chép từ slide. Không tính theo `provenance`: từ khi bỏ text layer (2026-10-08) mọi
        khối chữ cũng là `vlm`."""
        return self.n_images / len(self.block_ids) if self.block_ids else 0.0


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
    image_ratio: float = 0.0      # bao nhiêu phần là mô tả ảnh do VLM viết
    n_chunks: int = 1             # gộp mấy chunk của cùng trang lại
