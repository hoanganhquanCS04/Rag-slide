"""Lớp cha của chunker: ParsedDocument -> ChunkSet (danh sách chunk, nằm trong bộ nhớ).

Để làm gì: phần dùng chung cho mọi chunker — gắn tiền tố, dựng KBChunk, gom cả deck.
Không ghi file: `cli.py` nhúng rồi ghi thẳng ChunkSet vào kho vector.
Chunker cụ thể chỉ phải viết `chunk_page`.
Cách làm: đầu vào là document.json đã `enrich()`; trang chuyển chương được đánh dấu
`section_divider` để lọc lúc tìm (không xoá).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import ClassVar

from utils.tokens import TOKENIZER_ID, count_tokens
from models.kb_chunk import ChunkSet, KBChunk
from models.parsed_document import Block, ParsedDocument, ParsedPage

log = logging.getLogger(__name__)

MAX_TOKENS = 500


class BaseChunker(ABC):
    """Khung chạy chunk: cắt từng trang -> gom thành ChunkSet."""

    kind: ClassVar[str]

    def __init__(self, max_tokens: int = MAX_TOKENS):
        """Nhớ số token tối đa mỗi chunk."""
        self.max_tokens = max_tokens

    @abstractmethod
    def chunk_page(self, doc: ParsedDocument, page: ParsedPage) -> list[KBChunk]:
        """Một trang -> các chunk của trang đó, đúng thứ tự đọc (lớp con tự viết)."""

    def run(self, doc: ParsedDocument) -> ChunkSet:
        """Cắt cả deck -> ChunkSet.

        Cách làm: gọi `chunk_page` cho từng trang, gom lại, in thống kê (số chunk, số trang bị cắt...).
        """
        chunks = [c for page in doc.pages for c in self.chunk_page(doc, page)]
        cs = ChunkSet(doc_id=doc.doc_id, max_tokens=self.max_tokens, tokenizer=TOKENIZER_ID,
                      chunks=chunks)
        n_split = len({c.page_no for c in chunks if "." in c.chunk_id.rsplit("#", 1)[1]})
        log.info("%s -> %d chunk | %d trang bi cat | %d phan muc | tim duoc %d",
                 doc.doc_id, len(chunks), n_split,
                 sum(1 for c in chunks if c.content_type == "section_divider"), len(cs.searchable))
        return cs

    # ------------------------------------------------------------------ dùng chung cho biến thể

    def prefix(self, doc: ParsedDocument, page: ParsedPage) -> str:
        """Tạo dòng đầu gắn vào mỗi chunk trước khi nhúng: "[<chương> · <tiêu đề trang> · trang N/M] ".

        Để làm gì: mảnh sau của một trang dài vẫn biết mình thuộc chương nào, trang nào, nói về gì.
        Cách làm: lấy tên chương + tiêu đề trang, bỏ phần trống và phần trùng nhau, thêm "trang N/M".
        """
        sec = doc.section_of(page.page_no)
        heads = [h for h in dict.fromkeys([sec.title if sec else None, page.title]) if h]
        return "[" + " · ".join([*heads, f"trang {page.page_no}/{doc.n_pages}"]) + "] "

    def make_chunk(self, doc: ParsedDocument, page: ParsedPage, key: str, text: str,
                   blocks: list[Block], prefix: str) -> KBChunk:
        """Dựng một KBChunk từ phần chữ đã cắt của trang.

        Cách làm: `text_enriched` = tiền tố + chữ (thứ đem nhúng); điền trang, chương, loại trang, các
        khối gốc, số token, số khối là ảnh.
        """
        sec = doc.section_of(page.page_no)
        enriched = prefix + text
        return KBChunk(
            chunk_id=f"{doc.doc_id}#{key}",
            doc_id=doc.doc_id,
            page_no=page.page_no,
            page_hash=page.page_hash,
            section_id=sec.id if sec else None,
            section_title=sec.title if sec else None,
            vector_role="page",
            # Luật phân loại nằm ở ParsedPage.slide_type — ở đây chỉ đọc. Trang bài tập vẫn là
            # "content" với KB: nó có nội dung thật, phải tìm được.
            content_type="section_divider" if page.slide_type == "section_divider" else "content",
            text_raw=text,
            text_enriched=enriched,
            token_count=count_tokens(enriched),
            block_ids=[b.id for b in blocks],
            n_images=sum(1 for b in blocks if b.kind == "image"),
        )
