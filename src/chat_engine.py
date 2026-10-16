"""Trả lời câu hỏi của người nghe dựa trên nội dung slide.

Các bước cho mỗi câu hỏi:
    1. Tìm các trang slide liên quan tới câu hỏi (dùng Retriever trong retrieve.py).
    2. Ghép các trang đó + câu hỏi thành prompt (mẫu prompt ở src/prompts/r_qa.md).
    3. Gọi LLM đúng MỘT lần. LLM chỉ được chọn: trả lời, hoặc từ chối.
    4. Code kiểm lại câu trả lời trước khi cho robot nói:
         - câu trả lời phải ghi rõ lấy từ trang nào, và trang đó phải nằm trong số trang đã đưa cho LLM
           (không có nguồn hoặc nguồn bịa ra -> không nói, chuyển cho người thật)
         - không đọc đường link, không nói quá 4 câu

Nếu API nhúng vector bị lỗi: riêng câu hỏi đó chỉ tìm bằng từ khoá (BM25), câu sau thử lại bình thường.
Mỗi câu hỏi xử lý riêng, không nhớ các câu hỏi trước.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

from embedders.base import EmbedError
from models.kb_chunk import SearchHit
from models.parsed_document import ParsedDocument
from parser.enrich import enrich
from utils.config import CHUNKER_OUTPUT, PARSER_OUTPUT, PROMPTS_DIR
from utils.llm import LLM
from runtime.models import Answer, Reply, RuntimeConfig
from runtime.retrieve import Retriever

log = logging.getLogger(__name__)

# File mẫu prompt gửi cho LLM
PROMPT_PATH = PROMPTS_DIR / "r_qa.md"

# Câu robot nói khi từ chối, chọn theo lý do.
# Không nhắc lại câu hỏi: nếu câu hỏi là câu troll, đọc to nó lên là troll thành công.
ESCALATE_TEXT = {
    "no_info": "Phần này slide không nói tới, mình chưa trả lời chắc được. "
               "Mình ghi lại để giảng viên giải đáp sau nhé.",
    "off_topic": "Câu này nằm ngoài nội dung bài, mình xin phép quay lại bài giảng nhé.",
    "inappropriate": "Mình xin phép tiếp tục bài giảng nhé.",
}

# Bắt kiểu trích dẫn "[trang 7]" / "(slide 7)" để xoá đi — máy đọc sẽ đọc thành "ngoặc vuông trang bảy"
CITATION = re.compile(r"\s*[\[(](?:slide|trang)\s*\d+[\])]", re.I)

# Chỗ ngắt câu: sau dấu . ! ? … và một khoảng trắng
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class ChatEngine:
    """Bộ hỏi đáp cho MỘT bộ slide. Tạo một lần, hỏi nhiều lần."""

    def __init__(self, doc_id: str, cfg: RuntimeConfig, model: str, log_dir: Path):
        """Chuẩn bị mọi thứ cần để trả lời.

        doc_id   tên bộ slide, vd "onboarding_kit"
        cfg      cấu hình (số trang tìm, số câu trả lời tối đa, ...)
        model    tên model LLM
        log_dir  thư mục ghi lại prompt và câu LLM trả về
        """
        # Nội dung bộ slide — chỉ dùng để lấy tên trang khi gợi ý "xem trang 3 – ..."
        self.doc = enrich(ParsedDocument.load(PARSER_OUTPUT / doc_id / "document.json"))
        # Bộ tìm kiếm. embed_retry=1: API nhúng lỗi thì bỏ qua ngay, không thử lại nhiều lần
        # (người nghe đang chờ, đợi lâu còn tệ hơn tìm kém một chút)
        self.retriever = Retriever(CHUNKER_OUTPUT / doc_id / "chunks.json", embed_retry=1)
        self.cfg = cfg
        self.template = PROMPT_PATH.read_text(encoding="utf-8")
        self.llm = LLM(model, log_dir, retry=cfg.llm_retry, timeout=cfg.llm_timeout_s,
                       extra=cfg.llm_extra)
        self.llm.json_mode = cfg.json_mode

    async def answer(self, question: str) -> Answer:
        """Trả lời một câu hỏi.

        Vào: câu hỏi.
        Ra:  Answer — gồm câu robot sẽ nói, và các thông tin để xem lại
             (tìm được trang nào, prompt gửi đi, LLM trả gì, mất bao lâu).
        """
        out = Answer(reply=Reply(action="escalate"))

        # Bước 1: tìm trang liên quan
        t0 = time.perf_counter()
        out.hits, out.search_mode = await asyncio.to_thread(self._search, question)
        out.ms["search"] = _ms(t0)

        # Bước 2: lấy vài trang đầu, ghép thành prompt
        found = out.hits[: self.cfg.max_contexts]
        out.prompt = self._render(question, found)
        tag = f"qa_{int(time.time() * 1000)}"
        out.log_path = self.llm.log_dir / f"{tag}.json"

        # Bước 3: gọi LLM một lần. Lỗi thì vẫn chỉ cho người nghe trang liên quan
        t1 = time.perf_counter()
        try:
            raw = await self.llm.chat([{"role": "user", "content": out.prompt}], tag=tag)
        except RuntimeError as e:
            out.ms["llm"] = _ms(t1)
            out.reply = self._fallback(out.hits, f"llm_error: {str(e)[:160]}")
            return out
        out.ms["llm"] = _ms(t1)
        out.raw = raw

        # Bước 4: kiểm câu trả lời trước khi cho nói
        out.reply = self._check(raw, found)
        return out

    async def close(self) -> None:
        """Đóng kết nối tới LLM. Gọi khi dùng xong."""
        await self.llm.close()

    # ------------------------------------------------------------------ tìm + dựng prompt

    def _search(self, question: str) -> tuple[list[SearchHit], str]:
        """Tìm các trang liên quan tới câu hỏi.

        Bình thường tìm bằng cả vector lẫn từ khoá ("hybrid").
        Nếu API nhúng vector lỗi thì tìm lại chỉ bằng từ khoá ("sparse").
        Ra: (danh sách trang tìm được, cách đã dùng để tìm).
        """
        k = self.cfg.top_k
        try:
            return self.retriever.search(question, k=k, mode="hybrid"), "hybrid"
        except EmbedError as e:
            log.warning("  [API nhúng lỗi -> câu này chỉ tìm bằng từ khoá] %s", str(e)[:120])
            return self.retriever.search(question, k=k, mode="sparse"), "sparse"

    def _render(self, question: str, found: list[SearchHit]) -> str:
        """Điền các trang tìm được và câu hỏi vào mẫu prompt -> prompt hoàn chỉnh."""
        fill = {"found": _ctx(found) or "(không tìm được đoạn nào)", "question": question}
        out = self.template
        for k, v in fill.items():
            out = out.replace("{{" + k + "}}", v)
        return out

    # ------------------------------------------------------------------ kiểm câu trả lời

    def _check(self, raw: dict, found: list[SearchHit]) -> Reply:
        """Kiểm câu LLM trả về, quyết định robot có được nói hay không.

        raw    câu LLM trả về (dạng JSON đã đọc ra dict)
        found  các trang đã đưa cho LLM

        - LLM trả lời: phải ghi nguồn, và nguồn phải là trang đã đưa cho nó.
          Thiếu nguồn / nguồn lạ / có đường link -> không cho nói, từ chối.
          Quá dài thì cắt còn tối đa N câu.
        - LLM từ chối: dùng câu từ chối soạn sẵn theo lý do.
        - LLM trả thứ khác: coi như không trả lời được.
        """
        action = raw.get("action")

        if action == "answer":
            text = CITATION.sub("", str(raw.get("text", ""))).strip()
            # LLM hay ghi nguồn kèm ngoặc vuông "[doc#p011]" -> bỏ ngoặc rồi mới so
            refs = [str(g).strip().strip("[]").strip() for g in raw.get("grounding") or []]
            valid = {h.chunk_id for h in found}
            bad = [g for g in refs if g not in valid]
            if not text or not refs or bad:
                # Không có nguồn, hoặc nguồn không nằm trong số trang đã đưa -> có thể là bịa
                return self._escalate("no_info", note=f"answer grounding sai: refs={refs} bad={bad}")
            if re.search(r"https?://|www\.", text):
                # Không đọc đường link thành tiếng
                return self._escalate("no_info", note="answer doc URL thanh tieng")
            sents = SENTENCE_END.split(text)
            text = " ".join(sents[: self.cfg.max_answer_sentences])
            return Reply(action="answer", text=text, sources=refs,
                         note=f"cat {len(sents)}->{self.cfg.max_answer_sentences} cau"
                         if len(sents) > self.cfg.max_answer_sentences else "")

        if action == "escalate":
            reason = raw.get("reason")
            return self._escalate(reason if reason in ESCALATE_TEXT else "no_info")

        return self._escalate("no_info", note=f"LLM tra hanh dong la: {raw}")

    def _escalate(self, reason: str, *, note: str = "") -> Reply:
        """Tạo câu từ chối (chuyển cho người thật) theo lý do: no_info / off_topic / inappropriate."""
        return Reply(action="escalate", text=ESCALATE_TEXT[reason], note=note or reason)

    def _fallback(self, hits: list[SearchHit], note: str) -> Reply:
        """Dùng khi gọi LLM bị lỗi: nói chưa trả lời được, nhưng chỉ ra 1–2 trang liên quan."""
        pages = [h.page_no for h in hits[:2]]
        text = "Mình chưa trả lời được ngay lúc này."
        if pages:
            text += f" Nội dung liên quan nằm ở {self._list_pages(pages)}."
        return Reply(action="escalate", text=text, candidates=pages, via="fallback", note=note)

    def _list_pages(self, pages: list[int]) -> str:
        """Viết danh sách trang kèm tên, vd: "trang 3 – Thời gian làm việc, trang 5 – Chế độ phúc lợi"."""
        return ", ".join(f"trang {p} – {self.doc.page(p).label}" for p in pages)


def _ctx(hits: list[SearchHit]) -> str:
    """Ghép các trang tìm được thành một đoạn chữ để đưa vào prompt.

    Mỗi trang có dòng đầu "[mã đoạn] trang N" (LLM dùng mã này để ghi nguồn), rồi tới nội dung.
    Trang có nhiều chữ do máy tự mô tả từ ảnh thì kèm cảnh báo "có thể sai con số".
    """
    return "\n\n".join(
        f"[{h.chunk_id}] trang {h.page_no}"
        + (f" (có {h.image_ratio:.0%} là mô tả ảnh do máy viết — có thể sai con số)" if h.image_ratio else "")
        + f"\n{h.text_enriched}"
        for h in hits)


def _ms(t0: float) -> int:
    """Tính đã mất bao nhiêu mili giây kể từ lúc t0."""
    return int((time.perf_counter() - t0) * 1000)
