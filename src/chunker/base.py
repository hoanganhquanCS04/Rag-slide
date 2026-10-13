"""Lớp cha của mọi chunker: ParsedDocument -> ChunkSet.

Biến thể chỉ cần viết `chunk_page`. Tiền tố, dựng KBChunk, gom cả tài liệu và ghi file dùng
chung ở đây. Luật chung (chi tiết ở docs/spec/kb-chunk.md):

    tiền tố   [<chương> · <tiêu đề trang> · trang N/M]   <- KHÔNG gọi LLM, thiếu phần nào bỏ phần đó
    đánh dấu  trang phân mục -> section_divider, lọc lúc tìm (KHÔNG xoá)

Đầu vào chỉ là `document.json` đã `enrich()`: `blocks[].content` là chữ, `id` để truy ngược. Header/footer lặp S0 đã bỏ, ảnh không có mô tả thì `content` rỗng -> tự rơi.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from utils.tokens import TOKENIZER_ID, count_tokens
from models.kb_chunk import ChunkSet, KBChunk
from models.parsed_document import Block, ParsedDocument, ParsedPage

log = logging.getLogger(__name__)

MAX_TOKENS = 500


class BaseChunker(ABC):
    """ParsedDocument -> ChunkSet. Ghi ra `out_dir/chunks.json`."""

    kind: ClassVar[str]

    def __init__(self, out_dir: Path, max_tokens: int = MAX_TOKENS):
        self.out_dir = out_dir
        self.max_tokens = max_tokens

    @abstractmethod
    def chunk_page(self, doc: ParsedDocument, page: ParsedPage) -> list[KBChunk]:
        """Một trang -> các chunk của nó, đúng thứ tự đọc."""

    def run(self, doc: ParsedDocument) -> ChunkSet:
        chunks = [c for page in doc.pages for c in self.chunk_page(doc, page)]
        cs = ChunkSet(doc_id=doc.doc_id, max_tokens=self.max_tokens, tokenizer=TOKENIZER_ID,
                      chunks=chunks)
        n_split = len({c.page_no for c in chunks if "." in c.chunk_id.rsplit("#", 1)[1]})
        log.info("%s -> %d chunk | %d trang bi cat | %d phan muc | tim duoc %d",
                 doc.doc_id, len(chunks), n_split,
                 sum(1 for c in chunks if c.content_type == "section_divider"), len(cs.searchable))
        return cs

    def save(self, cs: ChunkSet) -> Path:
        """-> out_dir/chunks.json."""
        path = self.out_dir / "chunks.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cs.model_dump_json(indent=2), encoding="utf-8")
        log.info("ghi -> %s (%d KB)", path, path.stat().st_size // 1024)
        return path

    # ------------------------------------------------------------------ dùng chung cho biến thể

    def prefix(self, doc: ParsedDocument, page: ParsedPage) -> str:
        """ 
        tạo dòng đầu gắn vào mỗi chunk trước khi đem đi nhúng vector. 
        Dòng đó cho biết mẩu chữ thuộc chương nào, trang nào.
        """
        sec = doc.section_of(page.page_no)
        heads = [h for h in dict.fromkeys([sec.title if sec else None, page.title]) if h]
        return "[" + " · ".join([*heads, f"trang {page.page_no}/{doc.n_pages}"]) + "] "

    def make_chunk(self, doc: ParsedDocument, page: ParsedPage, key: str, text: str,
                   blocks: list[Block], prefix: str) -> KBChunk:
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
