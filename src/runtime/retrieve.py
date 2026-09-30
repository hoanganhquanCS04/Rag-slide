"""R2 + R3a — tìm trang cho câu hỏi, TRƯỚC khi gọi LLM.

LLM không thuộc bộ slide: phải đưa cho nó đúng mấy trang liên quan, không thì nó bịa.
Không đưa cả deck (§10). Nên: tìm trước -> đưa top vài trang vào prompt.

Hai chế độ tìm, cùng một câu hỏi:
    nav  KHÔNG lọc trang phân mục — "quay lại phần 3D" thì trang MỞ CHƯƠNG là đáp án đúng
    qa   LỌC trang phân mục       — trả lời cần trang có nội dung

R3a (mở rộng câu hỏi, bằng CODE, không gọi model): "cái hình bên trái" không có từ khoá
nào để tìm. Nối thêm tiêu đề trang đang chiếu + chữ của mẩu nằm bên trái/phải (so
`block.center[0]`) thì tìm mới trúng.

API nhúng lỗi (hết quota, rớt mạng) -> lùi về CHỈ BM25 cho cả phiên, không làm khán giả chờ.
"""

from __future__ import annotations

import logging
import re

from kb.embed import EmbedError
from kb.models import SearchHit
from kb.search import Searcher
from parsing.models import ParsedPage
from runtime.models import Context

log = logging.getLogger(__name__)

DEICTIC = re.compile(r"\b(?:này|đây|kia|bên trái|bên phải|phía trên|phía dưới)\b", re.I)


def expand(question: str, page: ParsedPage | None) -> str:
    """R3a — câu hỏi trỏ vào màn hình thì nối thêm chữ của trang đang chiếu."""
    if page is None or not DEICTIC.search(question):
        return question
    extra = [page.title or ""]
    q = question.lower()
    side = "left" if "trái" in q else "right" if "phải" in q else None
    if side:
        blocks = [b for b in page.blocks if b.content
                  and (b.center[0] < 0.5 if side == "left" else b.center[0] >= 0.5)]
        extra += [b.content[:160] for b in blocks[:1]]
    return " ".join([question, *[x for x in extra if x]])


class Retriever:
    def __init__(self, doc_id: str, top_k: int):
        # embed_retry=1: lỗi là lùi về BM25 ngay, không đợi 1+2+4+8s như offline
        self.searcher = Searcher(f"out/kb/{doc_id}/chunks.json", embed_retry=1)
        self.top_k = top_k
        self.mode = "hybrid"

    def page_context(self, page_no: int) -> list[Context]:
        """Chunk CHÍNH của trang đang chiếu — TRẠNG THÁI, đưa thẳng vào prompt."""
        return [Context(chunk_id=c.chunk_id, page_no=c.page_no, text=c.text_enriched,
                        vlm_ratio=c.vlm_ratio)
                for c in self.searcher.cs.by_page(page_no) if c.vector_role == "page"]

    def search(self, question: str, page: ParsedPage | None) -> tuple[list[SearchHit], list[SearchHit]]:
        """-> (nav_hits, qa_hits), mỗi danh sách đã gộp theo trang, xếp giảm dần."""
        q = expand(question, page)
        try:
            return self._both(q)
        except EmbedError as e:
            if self.mode == "hybrid":
                log.warning("  [API nhung loi -> tu gio chi dung BM25] %s", str(e)[:120])
                self.mode = "sparse"
            return self._both(q)

    def _both(self, q: str) -> tuple[list[SearchHit], list[SearchHit]]:
        # Cùng câu hỏi -> lần nhúng thứ hai lấy từ cache, chỉ tốn MỘT lần gọi API
        nav = self.searcher.search(q, k=self.top_k, mode=self.mode, filter_dividers=False)
        qa = self.searcher.search(q, k=self.top_k, mode=self.mode, filter_dividers=True)
        return nav, qa
