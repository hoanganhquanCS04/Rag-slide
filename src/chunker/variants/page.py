"""Biến thể `page`: 1 trang = 1 chunk, quá `max_tokens` thì cắt ở ranh giới tự nhiên.

    đơn vị    1 trang = 1 chunk                          <- vì R2 nhảy tới TRANG
    cắt thêm  chunk > 500 token -> cắt thành ÍT khúc nhất (không cắt thêm khúc nào), nhưng
              chọn CHỖ cắt: trước đề mục > giữa hai block > giữa dòng / hàng bảng > giữa câu,
              cấm bỏ đề mục trơ trọi cuối khúc. MỘT block đã > 500 -> bảng theo hàng (mảnh nào
              cũng lặp hàng tiêu đề), chữ theo dòng, dòng vẫn dài thì theo câu
    vụn       khúc < 100 token (kể cả tiền tố) -> gộp vào khúc bên cạnh, được vượt tới 600

Mô tả ảnh là một block có chữ như mọi block khác -> nằm trong chunk trang. Không còn vector phụ
mỗi ảnh (bỏ 2026-10-10): nó chỉ nhân bản chữ đã có trong chunk trang.

KHÔNG overlap. Overlap sinh ra cho văn xuôi cắt ở điểm tuỳ tiện — ở đây ranh giới luôn là
trang / block / dòng / câu, không câu nào bị cắt đôi. Ngữ cảnh vắt qua ranh giới thì tiền tố
lo: mảnh `.2` của trang dài vẫn biết mình thuộc chương nào, trang nào, nói về gì.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from chunker.base import BaseChunker
from chunker.tokens import count_tokens
from kb.models import KBChunk
from parsing.models import Block, ParsedDocument, ParsedPage, ParsedParagraph, ParsedTable

MIN_TOKENS = 100               # khúc nhỏ hơn (kể cả tiền tố) là vụn -> gộp sang khúc bên cạnh
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
HEADING_MAX_WORDS = 12         # "Bảo hiểm sức khỏe Vingroup:" là đề mục, câu dài kết bằng ':' thì không

# ------------------------------------------------------------------ mảnh nhỏ nhất: Block -> Piece


class Boundary(Enum):
    """Ranh giới NGAY TRƯỚC một piece — cắt ở đó phạt bao nhiêu."""

    BLOCK = "block"
    LINE = "line"
    SENTENCE = "sentence"


@dataclass(eq=False)
class Piece:
    """Mảnh không cắt nữa: cả block (vừa budget), hoặc một dòng / hàng bảng / câu của block to.

    `starts_heading` = mảnh mở đầu bằng đề mục -> cắt ngay trước nó là chỗ đẹp nhất.
    `is_heading` = mảnh CHỈ là đề mục -> cắt ngay sau nó là chỗ tệ nhất.
    `header` = mảnh là hàng bảng: hàng tiêu đề + |---| phải lặp ở đầu khúc chứa nó.
    """

    block: Block
    text: str
    tokens: int
    boundary: Boundary
    starts_heading: bool = False
    is_heading: bool = False
    header: str | None = None
    header_tokens: int = 0


def _is_heading_line(line: str) -> bool:
    s = line.strip()
    return s.endswith(":") and len(s.split()) <= HEADING_MAX_WORDS


def split_block(block: Block, budget: int) -> list[Piece]:
    """Block vừa `budget` -> một piece. To hơn -> piece ở ranh giới tự nhiên.

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
    text = block.content or ""
    is_par = isinstance(block, ParsedParagraph)
    lines = [ln for ln in text.split("\n") if ln.strip()]

    if count_tokens(text) <= budget:
        is_title = is_par and block.role == "title"
        return [Piece(block, text, count_tokens(text), Boundary.BLOCK,
                      starts_heading=is_title or (is_par and bool(lines) and _is_heading_line(lines[0])),
                      is_heading=is_title or (is_par and len(lines) == 1 and _is_heading_line(lines[0])))]

    if isinstance(block, ParsedTable) and len(lines) > 3:
        header = "\n".join(lines[:2])                  # hàng tiêu đề + dòng |---|
        header_tokens = count_tokens(header)
        return [Piece(block, row, count_tokens(row), Boundary.BLOCK if i == 0 else Boundary.LINE,
                      header=header, header_tokens=header_tokens)
                for i, row in enumerate(lines[2:])]

    out: list[Piece] = []
    for i, line in enumerate(lines):
        parts = [line] if count_tokens(line) <= budget else SENTENCE_END.split(line)
        for j, part in enumerate(parts):
            heading = j == 0 and len(parts) == 1 and is_par and _is_heading_line(line)
            boundary = (Boundary.BLOCK if i == j == 0
                        else Boundary.LINE if j == 0 else Boundary.SENTENCE)
            out.append(Piece(block, part, count_tokens(part), boundary,
                             starts_heading=heading, is_heading=heading))
    return out


# ------------------------------------------------------------------ chọn chỗ cắt: Piece[] -> Segment[]

# Phạt khi cắt NGAY TRƯỚC một piece. Thấp = chỗ cắt tự nhiên. Số chỉ có nghĩa THỨ TỰ.
CUT_BEFORE_HEADING = 0
CUT_PENALTY = {Boundary.BLOCK: 1, Boundary.LINE: 2, Boundary.SENTENCE: 4}
CUT_AFTER_HEADING = 8          # đề mục nằm cuối khúc này, nội dung của nó sang khúc sau
SMALL_PENALTY = 5              # khúc vụn — chỉ chịu khi mọi cách cắt khác đều tệ hơn


@dataclass
class Segment:
    """Một khúc = dải piece liền nhau -> một chunk."""

    pieces: list[Piece]

    def _with_header(self, k: int) -> bool:
        """Hàng bảng `k` có phải in hàng tiêu đề trước nó không: mở khúc, hoặc sang bảng khác."""
        p = self.pieces[k]
        return p.header is not None and (k == 0 or self.pieces[k - 1].block is not p.block)

    @property
    def size(self) -> int:
        """Token của khúc. Mỗi piece +1 cho dấu `\\n` nối — không tính thì đo ra chunk 511."""
        return sum(p.tokens + 1 + (p.header_tokens + 1 if self._with_header(k) else 0)
                   for k, p in enumerate(self.pieces))

    @property
    def text(self) -> str:
        out: list[str] = []
        for k, p in enumerate(self.pieces):
            if self._with_header(k):
                out.append(p.header or "")
            out.append(p.text)
        return "\n".join(out)

    @property
    def blocks(self) -> list[Block]:
        """Block gốc trong khúc — block cắt nhiều piece chỉ tính 1 lần."""
        return list({id(p.block): p.block for p in self.pieces}.values())


class CutPlanner:
    """Chia piece của một trang thành các khúc LIỀN NHAU — mỗi khúc vừa ngân sách.

    Thứ tự ưu tiên (so từ trái sang):

        1. ÍT khúc nhất         — chỉ đổi CHỖ cắt, không cắt thêm. Số khúc = đúng như nhồi đầy.
        2. tổng phạt chỗ cắt    — trước đề mục 0 · giữa block 1 · giữa dòng/hàng 2 · giữa câu 4 ·
                                  ngay sau đề mục 8 · mỗi khúc vụn (< MIN_TOKENS) +5
        3. đều nhất             — tổng bình phương cỡ khúc
    Một piece đơn lẻ to hơn budget (câu dài, hàng bảng dài) vẫn đứng riêng một khúc.
    `prefix_tokens` tính vào ngưỡng vụn để so trên đúng con số `token_count` sẽ ghi.
    """

    def __init__(self, pieces: list[Piece], max_tokens: int, prefix_tokens: int):
        self.pieces = pieces
        self.prefix_tokens = prefix_tokens
        self.budget = max_tokens - prefix_tokens
        # p32 tiêu đề (41 token) + bảng 445 không vừa 500 cùng nhau, để riêng thì khúc đầu chỉ
        # có tiêu đề — vector rỗng nghĩa. Nên khi gộp vụn được vượt tới max_tokens + MIN_TOKENS.
        self.merge_limit = max_tokens + MIN_TOKENS - prefix_tokens

    def plan(self) -> list[Segment]:
        return [Segment(self.pieces[i:j]) for i, j in self._merge_small(self._best_cuts())]

    def _size(self, i: int, j: int) -> int:
        return Segment(self.pieces[i:j]).size

    def _is_small(self, size: int) -> bool:
        return self.prefix_tokens + size < MIN_TOKENS

    def _penalty(self, k: int) -> int:
        """Phạt khi cắt ngay trước pieces[k]."""
        if self.pieces[k - 1].is_heading:
            return CUT_AFTER_HEADING
        if self.pieces[k].starts_heading:
            return CUT_BEFORE_HEADING
        return CUT_PENALTY[self.pieces[k].boundary]

    def _best_cuts(self) -> list[tuple[int, int]]:
        """Quy hoạch động: best[j] = cách chia tốt nhất pieces[:j], so theo (số khúc, phạt, bình phương)."""
        m = len(self.pieces)
        best: list[tuple[int, int, int] | None] = [None] * (m + 1)
        back = [0] * (m + 1)
        best[0] = (0, 0, 0)
        for j in range(1, m + 1):
            for i in range(j - 1, -1, -1):
                size = self._size(i, j)
                if size > self.budget and i < j - 1:
                    break                                  # lùi i thì khúc chỉ to thêm
                if best[i] is None:
                    continue
                n, cost, sq = best[i]  # type: ignore[misc]
                cost += (self._penalty(i) if i else 0) + (SMALL_PENALTY if self._is_small(size) else 0)
                cand = (n + 1, cost, sq + size * size)
                if best[j] is None or cand < best[j]:     # type: ignore[operator]
                    best[j], back[j] = cand, i

        spans: list[tuple[int, int]] = []
        j = m
        while j > 0:
            spans.append((back[j], j))
            j = back[j]
        return spans[::-1]

    def _merge_small(self, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Khúc vụn gộp vào khúc bên cạnh — bỏ chỗ cắt TỆ HƠN trong hai chỗ cắt của nó."""
        changed = True
        while changed and len(spans) > 1:
            changed = False
            for x, (i, j) in enumerate(spans):
                if not self._is_small(self._size(i, j)):
                    continue
                # (vị trí chỗ cắt trong spans, phạt) — trước khúc / sau khúc
                seams = [(y, self._penalty(spans[y][0])) for y in (x, x + 1) if 0 < y < len(spans)]
                for y, _ in sorted(seams, key=lambda s: -s[1]):
                    a, b = spans[y - 1][0], spans[y][1]
                    if self._size(a, b) <= self.merge_limit:
                        spans[y - 1:y + 1] = [(a, b)]
                        changed = True
                        break
                if changed:
                    break
        return spans


# ------------------------------------------------------------------ biến thể


class PageChunker(BaseChunker):
    """1 trang = 1 chunk; > `max_tokens` thì cắt ở ranh giới tự nhiên."""

    kind = "page"

    def chunk_page(self, doc: ParsedDocument, page: ParsedPage) -> list[KBChunk]:
        prefix = self.prefix(doc, page)
        prefix_tokens = count_tokens(prefix)
        pieces = [p for b in page.blocks if b.content
                  for p in split_block(b, self.max_tokens - prefix_tokens)]
        if not pieces:
            return []

        segments = CutPlanner(pieces, self.max_tokens, prefix_tokens).plan()
        base = f"p{page.page_no:03d}"
        return [self.make_chunk(doc, page, base if len(segments) == 1 else f"{base}.{i}",
                                s.text, s.blocks, prefix)
                for i, s in enumerate(segments, 1)]
