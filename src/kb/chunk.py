"""Cắt ParsedDocument thành KBChunk.

Luật (chi tiết ở docs/spec/kb-chunk.md):

    đơn vị    1 trang = 1 chunk chính                    <- vì R2 nhảy tới TRANG
    + phụ     trang >= 2 ảnh có mô tả -> mỗi ảnh 1 vector phụ, cùng trỏ về trang
    tiền tố   [<chương> · <tiêu đề trang> · trang N/M]   <- KHÔNG gọi LLM, thiếu phần nào bỏ phần đó
    cắt thêm  chunk > 500 token -> cắt ở ranh giới block
              MỘT block đã > 500 -> bảng theo hàng (mảnh nào cũng lặp hàng tiêu đề),
                                    chữ theo dòng, dòng vẫn dài thì theo câu
    đánh dấu  trang phân mục -> section_divider, lọc lúc tìm (KHÔNG xoá)

Đầu vào chỉ là `document.json`: `blocks[].content` là chữ, `id` + `provenance` để truy ngược.
Header/footer lặp S0 đã bỏ, ảnh không có mô tả thì `content` rỗng -> tự rơi.

KHÔNG overlap. Overlap sinh ra cho văn xuôi cắt ở điểm tuỳ tiện — ở đây ranh giới luôn là
trang / block / dòng / câu, không câu nào bị cắt đôi. Ngữ cảnh vắt qua ranh giới thì tiền tố
lo: mảnh `.2` của trang dài vẫn biết mình thuộc chương nào, trang nào, nói về gì.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from functools import lru_cache
from typing import Callable, TypeVar

from kb.models import ChunkSet, KBChunk
from parsing.models import Block, ParsedDocument, ParsedPage, ParsedTable

log = logging.getLogger(__name__)

TOKENIZER_ID = "cl100k_base"   # tokenizer THẬT của text-embedding-3-*
MAX_TOKENS = 500
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")

T = TypeVar("T")


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding(TOKENIZER_ID)


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def prefix_for(doc: ParsedDocument, page: ParsedPage) -> str:
    """'[Nhân sự · Thuế và đăng ký giảm trừ gia cảnh · trang 18/51] '.

    Tiêu đề trang nằm ở TIỀN TỐ chứ không chỉ trong thân: trang dài bị cắt thì mảnh sau vẫn
    mang nó (Onboarding p9 mảnh 2 mở bằng "Chỉ gửi email cho đúng người…" — thiếu tiêu đề là
    không biết đang nói chuyện email). Trang phân mục có tiêu đề trùng tên chương -> ghi một lần.
    """
    sec = doc.section_of(page.page_no)
    heads = [h for h in dict.fromkeys([sec.title if sec else None, page.title]) if h]
    return "[" + " · ".join([*heads, f"trang {page.page_no}/{doc.n_pages}"]) + "] "


def _pack(items: list[T], budget: int, size: Callable[[T], int]) -> list[list[T]]:
    """Gom liên tiếp cho tới khi chạm `budget`. Một món đã vượt budget thì đứng riêng một nhóm.

    Mỗi món cộng 1 token cho dấu `\\n` nối — không tính thì đo ra chunk 511 token.
    """
    groups: list[list[T]] = []
    cur: list[T] = []
    used = 0
    for it in items:
        n = size(it) + 1
        if cur and used + n > budget:
            groups.append(cur)
            cur, used = [], 0
        cur.append(it)
        used += n
    if cur:
        groups.append(cur)
    return groups


def _split_block(b: Block, budget: int) -> list[str]:
    """Chữ của một block — vừa `budget` thì nguyên khối, không thì cắt ở ranh giới tự nhiên.

    Đo trên Onboarding: 7 block một mình đã > 500 token (bảng chấm công p21: 1570, list thuế
    p19: 848). Cắt ở ranh giới block thì không đụng được chúng -> một vector bình quân cả
    bảng 13 hàng, hỏi một dòng quy định là loãng.

        bảng   theo hàng, mảnh nào cũng lặp hàng tiêu đề + |---| — không có nó mảnh sau chỉ
               còn số, không biết cột nào là gì
        chữ    theo dòng (list là mỗi gạch đầu dòng một dòng); dòng vẫn dài thì theo câu

    Một câu đơn lẻ dài hơn budget thì để nguyên — cắt giữa câu còn tệ hơn chunk to.
    """
    text = b.content or ""
    if count_tokens(text) <= budget:
        return [text]

    lines = [ln for ln in text.split("\n") if ln.strip()]
    if isinstance(b, ParsedTable) and len(lines) > 3:
        head = "\n".join(lines[:2])                    # hàng tiêu đề + dòng |---|
        groups = _pack(lines[2:], budget - count_tokens(head), count_tokens)
        return [head + "\n" + "\n".join(g) for g in groups]

    units = [u for ln in lines
             for u in ([ln] if count_tokens(ln) <= budget else SENTENCE_END.split(ln))]
    return ["\n".join(g) for g in _pack(units, budget, count_tokens)]


def _chunk(doc: ParsedDocument, page: ParsedPage, key: str, *, role: str, ctype: str,
           prefix: str, raw: str, blocks: list[Block]) -> KBChunk:
    sec = doc.section_of(page.page_no)
    enriched = prefix + raw
    return KBChunk(
        chunk_id=f"{doc.doc_id}#{key}",
        doc_id=doc.doc_id,
        page_no=page.page_no,
        page_hash=page.page_hash,
        section_id=sec.id if sec else None,
        section_title=sec.title if sec else None,
        vector_role=role,
        content_type=ctype,
        text_raw=raw,
        text_enriched=enriched,
        token_count=count_tokens(enriched),
        block_ids=[b.id for b in blocks],
        provenance=dict(Counter(b.provenance.value for b in blocks)),
    )


def _page_chunks(doc: ParsedDocument, page: ParsedPage, max_tokens: int) -> list[KBChunk]:
    """Chunk chính của trang: mọi block có chữ, đúng thứ tự trong `page.blocks`."""
    prefix = prefix_for(doc, page)
    budget = max_tokens - count_tokens(prefix)
    # (block, mảnh chữ) — block to quá thành nhiều mảnh, cùng trỏ về block đó
    parts = [(b, piece) for b in page.blocks if b.content for piece in _split_block(b, budget)]
    if not parts:
        return []

    # Luật phân loại nằm ở ParsedPage.slide_type — ở đây chỉ đọc. Trang bài tập vẫn là
    # "content" với KB: nó có nội dung thật, phải tìm được.
    ctype = "section_divider" if page.slide_type == "section_divider" else "content"
    groups = _pack(parts, budget, lambda p: count_tokens(p[1]))
    base = f"p{page.page_no:03d}"
    return [
        _chunk(doc, page, base if len(groups) == 1 else f"{base}.{i}",
               role="page", ctype=ctype, prefix=prefix,
               raw="\n".join(piece for _, piece in g),
               blocks=list({b.id: b for b, _ in g}.values()))   # block cắt nhiều mảnh chỉ tính 1 lần
        for i, g in enumerate(groups, 1)
    ]


def _image_chunks(doc: ParsedDocument, page: ParsedPage, max_tokens: int) -> list[KBChunk]:
    """Mỗi mô tả ảnh một vector phụ — chống loãng khi trang có nhiều ảnh.

    Đo trên 3_datavisualization: 5/40 trang có >= 2 ảnh được mô tả, thường là cặp
    'code + biểu đồ kết quả'. Gộp một vector thì bình quân hai chủ đề, loãng.
    Tách vector nhưng CÙNG trỏ về page_no nên điều hướng không đổi.
    """
    imgs = [b for b in page.images if b.content]
    if len(imgs) < 2:          # 1 ảnh thì chunk trang đã đủ, không nhân bản
        return []

    prefix = prefix_for(doc, page)
    budget = max_tokens - count_tokens(prefix)
    out: list[KBChunk] = []
    for im in imgs:
        pieces = _split_block(im, budget)
        for j, piece in enumerate(pieces, 1):
            out.append(_chunk(doc, page, im.id if len(pieces) == 1 else f"{im.id}.{j}",
                              role="image", ctype="content", prefix=prefix,
                              raw=piece, blocks=[im]))
    return out


def chunk_document(doc: ParsedDocument, *, max_tokens: int = MAX_TOKENS) -> ChunkSet:
    chunks = [c for page in doc.pages
              for c in (*_page_chunks(doc, page, max_tokens), *_image_chunks(doc, page, max_tokens))]
    cs = ChunkSet(doc_id=doc.doc_id, max_tokens=max_tokens, tokenizer=TOKENIZER_ID, chunks=chunks)

    n_img = sum(1 for c in chunks if c.vector_role == "image")
    n_split = len({c.page_no for c in chunks if c.vector_role == "page" and "." in c.chunk_id.rsplit("#", 1)[1]})
    log.info(
        "%s -> %d chunk (%d trang + %d anh) | %d trang bi cat | %d phan muc | tim duoc %d",
        doc.doc_id, len(chunks), len(chunks) - n_img, n_img, n_split,
        sum(1 for c in chunks if c.content_type == "section_divider"), len(cs.searchable),
    )
    return cs
