"""Cắt ParsedDocument thành KBChunk.

Luật (chi tiết ở docs/spec/kb-chunk.md):

    đơn vị    1 trang = 1 chunk chính                    <- vì R2 nhảy tới TRANG
    + phụ     trang >= 2 ảnh có mô tả -> mỗi ảnh 1 vector phụ, cùng trỏ về trang
    tiền tố   [<chương> · <tiêu đề trang> · trang N/M]   <- KHÔNG gọi LLM, thiếu phần nào bỏ phần đó
    cắt thêm  chunk > 500 token -> cắt thành ÍT khúc nhất (không cắt thêm khúc nào), nhưng
              chọn CHỖ cắt: trước đề mục > giữa hai block > giữa dòng / hàng bảng > giữa câu,
              cấm bỏ đề mục trơ trọi cuối khúc. MỘT block đã > 500 -> bảng theo hàng (mảnh nào
              cũng lặp hàng tiêu đề), chữ theo dòng, dòng vẫn dài thì theo câu
    vụn       khúc < 100 token (kể cả tiền tố) -> gộp vào khúc bên cạnh, được vượt tới 600
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
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from kb.models import ChunkSet, KBChunk
from parsing.models import Block, ParsedDocument, ParsedPage, ParsedParagraph, ParsedTable

log = logging.getLogger(__name__)

TOKENIZER_ID = "cl100k_base"   # tokenizer THẬT của text-embedding-3-*
MAX_TOKENS = 500
MIN_TOKENS = 100               # khúc nhỏ hơn (kể cả tiền tố) là vụn -> gộp sang khúc bên cạnh
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
HEADING_MAX_WORDS = 12         # "Bảo hiểm sức khỏe Vingroup:" là đề mục, câu dài kết bằng ':' thì không

# Phạt khi cắt NGAY TRƯỚC một mảnh. Thấp = chỗ cắt tự nhiên. Số chỉ có nghĩa THỨ TỰ.
CUT_COST = {"heading": 0, "block": 1, "line": 2, "sentence": 4}
CUT_AFTER_HEADING = 8          # đề mục nằm cuối khúc này, nội dung của nó sang khúc sau
SMALL_PENALTY = 5              # khúc vụn — chỉ chịu khi mọi cách cắt khác đều tệ hơn


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


# ------------------------------------------------------------------ mảnh nhỏ nhất


def _is_heading_line(line: str) -> bool:
    s = line.strip()
    return s.endswith(":") and len(s.split()) <= HEADING_MAX_WORDS


@dataclass(eq=False)
class _Unit:
    """Mảnh không cắt nữa: cả block (vừa budget), hoặc một dòng / hàng bảng / câu của block to.

    `level` = mảnh này cách mảnh trước bằng ranh giới gì (cắt ở đó phạt bao nhiêu).
    `opens` = mảnh mở đầu bằng đề mục -> cắt ngay trước nó là chỗ đẹp nhất.
    `heading` = mảnh CHỈ là đề mục -> cắt ngay sau nó là chỗ tệ nhất.
    `head` = mảnh là hàng bảng: hàng tiêu đề + |---| phải lặp ở đầu khúc chứa nó.
    """

    block: Block
    text: str
    tokens: int
    level: Literal["block", "line", "sentence"]
    opens: bool = False
    heading: bool = False
    head: str | None = None
    head_tokens: int = 0
    first_row: bool = False


def _units(b: Block, budget: int) -> list[_Unit]:
    """Block vừa `budget` -> một mảnh. To hơn -> mảnh ở ranh giới tự nhiên.

    Đo trên Onboarding: 7 block một mình đã > 500 token (bảng chấm công p21: 1570, list thuế
    p19: 848). Cắt ở ranh giới block thì không đụng được chúng -> một vector bình quân cả
    bảng 13 hàng, hỏi một dòng quy định là loãng.

        bảng   theo hàng, khúc nào cũng lặp hàng tiêu đề + |---| — không có nó khúc sau chỉ
               còn số, không biết cột nào là gì
        chữ    theo dòng (list là mỗi gạch đầu dòng một dòng); dòng vẫn dài thì theo câu

    Một câu đơn lẻ dài hơn budget thì để nguyên — cắt giữa câu còn tệ hơn chunk to.

    Đề mục: block `title` không phải đầu trang (p9 "Quy tắc phản hồi email:", "LƯU Ý:"), hoặc
    dòng ngắn kết bằng ':' (p29 "Bảo hiểm sức khỏe Vingroup:" mở đầu block list của nó).
    """
    text = b.content or ""
    is_par = isinstance(b, ParsedParagraph)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if count_tokens(text) <= budget:
        is_title = is_par and b.role == "title"
        return [_Unit(b, text, count_tokens(text), "block",
                      opens=is_title or (is_par and bool(lines) and _is_heading_line(lines[0])),
                      heading=is_title or (is_par and len(lines) == 1 and _is_heading_line(lines[0])))]

    if isinstance(b, ParsedTable) and len(lines) > 3:
        head = "\n".join(lines[:2])                    # hàng tiêu đề + dòng |---|
        n_head = count_tokens(head)
        return [_Unit(b, ln, count_tokens(ln), "block" if i == 0 else "line",
                      head=head, head_tokens=n_head, first_row=i == 0)
                for i, ln in enumerate(lines[2:])]

    out: list[_Unit] = []
    for i, ln in enumerate(lines):
        parts = [ln] if count_tokens(ln) <= budget else SENTENCE_END.split(ln)
        for j, s in enumerate(parts):
            hd = j == 0 and len(parts) == 1 and is_par and _is_heading_line(ln)
            out.append(_Unit(b, s, count_tokens(s),
                             "block" if i == j == 0 else "line" if j == 0 else "sentence",
                             opens=hd, heading=hd))
    return out


def _needs_head(us: list[_Unit], k: int, start: int) -> bool:
    """Hàng bảng `k` có phải in hàng tiêu đề trước nó không, trong khúc bắt đầu ở `start`."""
    u = us[k]
    return u.head is not None and (k == start or us[k - 1].block is not u.block)


def _size(us: list[_Unit], i: int, j: int) -> int:
    """Token của khúc us[i:j]. Mỗi mảnh +1 cho dấu `\\n` nối — không tính thì đo ra chunk 511."""
    return sum(us[k].tokens + 1 + (us[k].head_tokens + 1 if _needs_head(us, k, i) else 0)
               for k in range(i, j))


def _seam(us: list[_Unit], k: int) -> int:
    """Phạt khi cắt ngay trước us[k]."""
    if us[k - 1].heading:
        return CUT_AFTER_HEADING
    return CUT_COST["heading" if us[k].opens else us[k].level]


def _render(us: list[_Unit], i: int, j: int) -> str:
    out: list[str] = []
    for k in range(i, j):
        if _needs_head(us, k, i):
            out.append(us[k].head or "")
        out.append(us[k].text)
    return "\n".join(out)


# ------------------------------------------------------------------ chọn chỗ cắt


def _cut(us: list[_Unit], budget: int, fixed: int, max_tokens: int) -> list[tuple[int, int]]:
    """Chia `us` thành các khúc LIỀN NHAU [(i, j)] — mỗi khúc vừa `budget`.

    Thứ tự ưu tiên (so từ trái sang):

        1. ÍT khúc nhất         — chỉ đổi CHỖ cắt, không cắt thêm. Số khúc = đúng như nhồi đầy.
        2. tổng phạt chỗ cắt    — trước đề mục 0 · giữa block 1 · giữa dòng/hàng 2 · giữa câu 4 ·
                                  ngay sau đề mục 8 · mỗi khúc vụn (< MIN_TOKENS) +5
        3. đều nhất             — tổng bình phương cỡ khúc

    Bản cũ nhồi cho đầy 500 rồi cắt: p29 bảng gói bảo hiểm rơi sang khúc sau, đề mục
    "Bảo hiểm sức khỏe Vingroup:" ở lại khúc trước. Quy hoạch động, trang có ~40 mảnh -> tức thì.

    Một mảnh đơn lẻ to hơn budget (câu dài, hàng bảng dài) vẫn đứng riêng một khúc.
    `fixed` = token tiền tố, chỉ để so ngưỡng vụn trên đúng con số `token_count` sẽ ghi.
    """
    m = len(us)
    best: list[tuple[int, int, int] | None] = [None] * (m + 1)
    back = [0] * (m + 1)
    best[0] = (0, 0, 0)
    for j in range(1, m + 1):
        for i in range(j - 1, -1, -1):
            size = _size(us, i, j)
            if size > budget and i < j - 1:
                break                                  # lùi i thì khúc chỉ to thêm
            if best[i] is None:
                continue
            n, cost, sq = best[i]  # type: ignore[misc]
            cost += (_seam(us, i) if i else 0) + (SMALL_PENALTY if fixed + size < MIN_TOKENS else 0)
            cand = (n + 1, cost, sq + size * size)
            if best[j] is None or cand < best[j]:     # type: ignore[operator]
                best[j], back[j] = cand, i

    spans: list[tuple[int, int]] = []
    j = m
    while j > 0:
        spans.append((back[j], j))
        j = back[j]
    spans.reverse()
    return _merge_small(us, spans, fixed, max_tokens)


def _merge_small(us: list[_Unit], spans: list[tuple[int, int]], fixed: int,
                 max_tokens: int) -> list[tuple[int, int]]:
    """Khúc < MIN_TOKENS gộp vào khúc bên cạnh — bỏ chỗ cắt TỆ HƠN trong hai chỗ cắt của nó.

    Được vượt `max_tokens` tới `max_tokens + MIN_TOKENS`: p32 tiêu đề (41 token) + bảng 445
    không vừa 500 cùng nhau, để riêng thì khúc đầu chỉ có tiêu đề — vector rỗng nghĩa.
    """
    hard = max_tokens + MIN_TOKENS - fixed
    changed = True
    while changed and len(spans) > 1:
        changed = False
        for x, (i, j) in enumerate(spans):
            if fixed + _size(us, i, j) >= MIN_TOKENS:
                continue
            # (vị trí chỗ cắt trong spans, phạt) — trước khúc / sau khúc
            seams = [(y, _seam(us, spans[y][0])) for y in (x, x + 1) if 0 < y < len(spans)]
            for y, _ in sorted(seams, key=lambda s: -s[1]):
                a, b = spans[y - 1][0], spans[y][1]
                if _size(us, a, b) <= hard:
                    spans[y - 1:y + 1] = [(a, b)]
                    changed = True
                    break
            if changed:
                break
    return spans


def _pieces(us: list[_Unit], prefix: str, max_tokens: int) -> list[tuple[str, list[Block]]]:
    """[(chữ khúc, block trong khúc)] — block cắt nhiều mảnh chỉ tính 1 lần."""
    fixed = count_tokens(prefix)
    return [(_render(us, i, j), list({id(u.block): u.block for u in us[i:j]}.values()))
            for i, j in _cut(us, max_tokens - fixed, fixed, max_tokens)]


# ------------------------------------------------------------------ chunk


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
    us = [u for b in page.blocks if b.content for u in _units(b, budget)]
    if not us:
        return []

    # Luật phân loại nằm ở ParsedPage.slide_type — ở đây chỉ đọc. Trang bài tập vẫn là
    # "content" với KB: nó có nội dung thật, phải tìm được.
    ctype = "section_divider" if page.slide_type == "section_divider" else "content"
    pieces = _pieces(us, prefix, max_tokens)
    base = f"p{page.page_no:03d}"
    return [
        _chunk(doc, page, base if len(pieces) == 1 else f"{base}.{i}",
               role="page", ctype=ctype, prefix=prefix, raw=raw, blocks=blocks)
        for i, (raw, blocks) in enumerate(pieces, 1)
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
        pieces = _pieces(_units(im, budget), prefix, max_tokens)
        for j, (raw, _) in enumerate(pieces, 1):
            out.append(_chunk(doc, page, im.id if len(pieces) == 1 else f"{im.id}.{j}",
                              role="image", ctype="content", prefix=prefix,
                              raw=raw, blocks=[im]))
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
