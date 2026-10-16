"""Kiểu dữ liệu của chunk (S5) và kết quả tìm kiếm.

Để làm gì: chunk là mẩu chữ đem đi nhúng vector và tìm. Một trang = một chunk (trang dài thì
cắt `.1`, `.2`...), chunk nào cũng trỏ về `page_no` vì runtime nhảy tới TRANG.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class KBChunk(BaseModel):
    """Một chunk: chữ đem nhúng, trang nguồn, chương, loại trang và các khối gốc.

    Cách làm: `text_enriched` = tiền tố [chương · tiêu đề · trang] + chữ, là thứ đem nhúng;
    `text_raw` = chữ không tiền tố; `block_ids` trỏ về các khối trong ParsedDocument.
    """
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
    n_images: int = 0             # bao nhiêu khối trong block_ids là MÔ TẢ ẢNH

    @property
    def is_searchable(self) -> bool:
        """Chunk có được tìm mặc định không: trang chuyển chương thì không (chỉ lọc, không xoá)."""
        return self.content_type == "content"

    @property
    def image_ratio(self) -> float:
        """Bao nhiêu phần nội dung là mô tả ảnh do VLM viết (dễ sai hơn chữ chép từ slide)."""
        return self.n_images / len(self.block_ids) if self.block_ids else 0.0


class ChunkSet(BaseModel):
    """Cả bộ chunk của một deck — nội dung file `chunks.json`."""

    doc_id: str
    max_tokens: int = 500
    tokenizer: str = ""           # tokenizer dùng để đếm, ghi lại cho khỏi lẫn
    chunks: list[KBChunk] = Field(default_factory=list)

    @property
    def searchable(self) -> list[KBChunk]:
        """Các chunk được tìm mặc định (bỏ trang chuyển chương)."""
        return [c for c in self.chunks if c.is_searchable]

    def by_page(self, page_no: int) -> list[KBChunk]:
        """Các chunk của một trang."""
        return [c for c in self.chunks if c.page_no == page_no]


class SearchHit(BaseModel):
    """Một kết quả tìm kiếm: trang, điểm RRF, hạng và điểm thô của từng nhánh (vector / BM25).

    Cách làm: `score` chỉ để xếp thứ tự, KHÔNG phải độ tin cậy; giữ hạng từng nhánh để biết nhánh
    nào kéo kết quả lên.
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
