"""Chunker `page`: mỗi trang thành 1 chunk; trang dài quá `max_tokens` thì cắt thành vài chunk.

Để làm gì: R2 điều hướng theo TRANG nên đơn vị tìm kiếm là trang. Trang quá dài thì một vector
bình quân cả trang bị loãng, phải cắt nhỏ.

Cách làm (3 bước):
    split_block   khối -> các mảnh (Piece), đánh dấu chỗ nào cắt được, mảnh nào là đề mục
    CutPlanner    chọn chỗ cắt: ít khúc nhất, cắt ở chỗ tự nhiên nhất, khúc vụn gộp vào bên cạnh
    PageChunker   gắn tiền tố + đặt id -> KBChunk
Không overlap giữa các chunk: tiền tố đã cho mỗi chunk biết nó thuộc chương / trang nào.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from offline.chunker.base import BaseChunker
from models.kb_chunk import KBChunk
from models.parsed_document import Block, ParsedDocument, ParsedPage, ParsedParagraph, ParsedTable
from utils.tokens import count_tokens

MIN_TOKENS = 100               # khúc nhỏ hơn (kể cả tiền tố) là vụn -> gộp sang khúc bên cạnh
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
HEADING_MAX_WORDS = 12         # dòng kết bằng ':' mà dài hơn thế là câu, không phải đề mục

# ------------------------------------------------------------------ mảnh nhỏ nhất: Block -> Piece


class Boundary(Enum):
    """Loại đường nối NGAY TRƯỚC một mảnh: giữa hai khối, hai dòng, hay hai câu.

    Để làm gì: CutPlanner dùng nó để biết cắt ở đây đẹp hay xấu (đường nối càng nhỏ càng xấu).
    """

    BLOCK = "block"
    LINE = "line"
    SENTENCE = "sentence"


@dataclass(eq=False)
class Piece:
    """Mảnh nhỏ nhất, không cắt thêm nữa: cả một khối, hoặc một dòng / hàng bảng / câu của khối to.

    Để làm gì: là đơn vị mà CutPlanner xếp thành các khúc.

        block           khối gốc chứa mảnh này
        text, tokens    nội dung và độ dài
        boundary        đường nối ngay trước mảnh
        starts_heading  mảnh MỞ ĐẦU bằng đề mục -> cắt ngay trước nó là đẹp nhất
        is_heading      mảnh CHỈ là đề mục -> cắt ngay sau nó là xấu nhất
        header          mảnh là hàng bảng: hàng tiêu đề bảng + dòng |---|, in lại ở đầu khúc
        header_tokens   độ dài của header
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
    """Dòng này có phải đề mục không. Cách làm: kết thúc bằng ':' và không quá 12 từ ("LƯU Ý:")."""
    s = line.strip()
    return s.endswith(":") and len(s.split()) <= HEADING_MAX_WORDS


def split_block(block: Block, budget: int) -> list[Piece]:
    """Chia một khối thành các mảnh (Piece).

    Để làm gì: khối quá to (vd bảng 1575 token) thì phải có chỗ để cắt bên trong nó.

    Cách làm:
        khối vừa `budget`  -> 1 mảnh nguyên khối; là đề mục nếu role title hoặc dòng kết bằng ':'
        bảng quá to        -> mỗi hàng 1 mảnh, mảnh nào cũng mang hàng tiêu đề bảng
        chữ quá to         -> mỗi dòng 1 mảnh; dòng vẫn quá to thì tách theo câu
    Một câu dài hơn `budget` vẫn để nguyên: cắt giữa câu còn tệ hơn chunk to.
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

# Điểm phạt khi cắt NGAY TRƯỚC một mảnh. Thấp = chỗ cắt tự nhiên. Số chỉ có nghĩa THỨ TỰ.
CUT_BEFORE_HEADING = 0
CUT_PENALTY = {Boundary.BLOCK: 1, Boundary.LINE: 2, Boundary.SENTENCE: 4}
CUT_AFTER_HEADING = 8          # đề mục nằm cuối khúc này, nội dung của nó sang khúc sau
SMALL_PENALTY = 5              # khúc vụn — chỉ chịu khi mọi cách cắt khác đều tệ hơn


@dataclass
class Segment:
    """Một khúc = dãy mảnh liền nhau = một chunk.

    Để làm gì: gom mảnh lại, tính độ dài / chữ / các khối gốc của khúc.
    """

    pieces: list[Piece]

    def _with_header(self, k: int) -> bool:
        """Hàng bảng thứ k có cần in hàng tiêu đề trước nó không: có, nếu nó mở khúc hoặc sang bảng khác."""
        p = self.pieces[k]
        return p.header is not None and (k == 0 or self.pieces[k - 1].block is not p.block)

    @property
    def size(self) -> int:
        """Số token của khúc. Cách làm: cộng token từng mảnh + 1 cho dấu xuống dòng nối
        + hàng tiêu đề bảng nếu phải in lại."""
        return sum(p.tokens + 1 + (p.header_tokens + 1 if self._with_header(k) else 0)
                   for k, p in enumerate(self.pieces))

    @property
    def text(self) -> str:
        """Chữ của khúc. Cách làm: nối các mảnh bằng dấu xuống dòng, chèn hàng tiêu đề bảng khi cần."""
        out: list[str] = []
        for k, p in enumerate(self.pieces):
            if self._with_header(k):
                out.append(p.header or "")
            out.append(p.text)
        return "\n".join(out)

    @property
    def blocks(self) -> list[Block]:
        """Các khối gốc trong khúc, mỗi khối một lần -> ghi vào `KBChunk.block_ids`."""
        return list({id(p.block): p.block for p in self.pieces}.values())


class CutPlanner:
    """Chia các mảnh của một trang thành các khúc liền nhau, mỗi khúc vừa ngân sách token.

    Để làm gì: chọn chỗ cắt sao cho mỗi chunk trọn ý (không tách đề mục khỏi nội dung của nó).

    Cách làm: thử mọi cách chia (quy hoạch động), chọn cách tốt nhất theo thứ tự:
        1. ít khúc nhất
        2. tổng phạt chỗ cắt thấp nhất: trước đề mục 0 · giữa khối 1 · giữa dòng 2 · giữa câu 4 ·
           ngay sau đề mục 8 · mỗi khúc vụn +5
        3. các khúc dài đều nhau
    Rồi gộp khúc vụn (< 100 token kể cả tiền tố) vào khúc bên cạnh.
    """

    def __init__(self, pieces: list[Piece], max_tokens: int, prefix_tokens: int):
        """Nhớ các mảnh, token tiền tố; tính ngân sách thân chunk và giới hạn khi gộp khúc vụn."""
        self.pieces = pieces
        self.prefix_tokens = prefix_tokens
        self.budget = max_tokens - prefix_tokens
        # gộp vụn được vượt tới max_tokens + MIN_TOKENS: để tiêu đề đứng riêng một khúc thì vector rỗng nghĩa
        self.merge_limit = max_tokens + MIN_TOKENS - prefix_tokens

    def plan(self) -> list[Segment]:
        """Chọn chỗ cắt rồi gộp khúc vụn -> danh sách Segment."""
        return [Segment(self.pieces[i:j]) for i, j in self._merge_small(self._best_cuts())]

    def _size(self, i: int, j: int) -> int:
        """Số token nếu gom mảnh i..j-1 thành một khúc."""
        return Segment(self.pieces[i:j]).size

    def _is_small(self, size: int) -> bool:
        """Khúc có vụn không: tính cả tiền tố mà vẫn < MIN_TOKENS."""
        return self.prefix_tokens + size < MIN_TOKENS

    def _penalty(self, k: int) -> int:
        """Điểm phạt nếu cắt ngay trước mảnh k: sau đề mục 8 · trước đề mục 0 · còn lại theo loại đường nối."""
        if self.pieces[k - 1].is_heading:
            return CUT_AFTER_HEADING
        if self.pieces[k].starts_heading:
            return CUT_BEFORE_HEADING
        return CUT_PENALTY[self.pieces[k].boundary]

    def _best_cuts(self) -> list[tuple[int, int]]:
        """Tìm cách chia tốt nhất -> danh sách (đầu, cuối) của từng khúc.

        Cách làm (quy hoạch động): best[j] = cách chia tốt nhất cho j mảnh đầu, tính từ mọi best[i]
        cộng thêm khúc i..j-1; so theo (số khúc, tổng phạt, tổng bình phương độ dài). Lần ngược
        theo `back` để lấy ra các khúc.
        """
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
        """Gộp khúc vụn vào khúc bên cạnh.

        Cách làm: với mỗi khúc vụn, bỏ chỗ cắt XẤU HƠN trong hai chỗ cắt hai bên nó (miễn gộp xong
        không vượt `merge_limit`); lặp tới khi không gộp được nữa.
        """
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
    """Chunker 1 trang = 1 chunk; trang quá dài thì cắt ở chỗ tự nhiên."""

    kind = "page"

    def chunk_page(self, doc: ParsedDocument, page: ParsedPage) -> list[KBChunk]:
        """Một trang -> các KBChunk của nó.

        Cách làm: tạo tiền tố -> split_block từng khối (ngân sách = max_tokens - token tiền tố) ->
        CutPlanner chia khúc -> make_chunk. Id: `p009` nếu 1 khúc, `p009.1`, `p009.2`… nếu nhiều khúc.
        """
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
