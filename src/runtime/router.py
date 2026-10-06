"""R2 + R3 + R4 — tìm, 1 lần gọi LLM trả lời, rồi CODE kiểm trước khi nói.

    [có lịch sử] LLM viết lại câu hỏi (r_rewrite.md) ─► tìm (retrieve.py) ─► 1 lần gọi LLM ─► …
                                                  │
                        code kiểm ◄───────────────┘
                          answer   grounding phải trỏ vào đoạn ĐÃ đưa cho nó, sai -> escalate
                          goto     qua CỔNG TIN CẬY: không chắc -> hỏi lại, không nhảy
                          lỗi API  -> escalate + gợi ý trang tìm được (không bịa, không treo)

Viết lại câu hỏi (chốt 2026-10-06, CLAUDE.md §6): câu nối tiếp cụt ("vậy hạn chót là ngày nào")
tìm bằng chính nó là lạc đề, mà bước tìm chạy TRƯỚC lần gọi trả lời. Không đoán câu nào là nối tiếp
bằng từ khoá: LLM xem các câu hỏi gần nhất rồi tự quyết. Chỉ gọi khi đã có lịch sử — câu đầu tiên
không tốn thêm. Đổi lại chậm thêm một lần gọi (~1–2s). Lỗi -> tìm bằng câu gốc, không chặn.
Câu trả lời vẫn sinh từ câu hỏi GỐC (R3b); câu viết lại chỉ dùng để tìm.

Vì sao code kiểm: LLM tự tin thái quá có hệ thống, và bịa nguồn được.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from kb.deck_map import page_label
from kb.models import SearchHit
from llm import LLM, ROOT
from parsing.models import ParsedDocument
from runtime.models import Context, Reply, RuntimeConfig, Turn
from runtime.retrieve import Retriever

PROMPT_PATH = ROOT / "prompts" / "r_router.md"
QA_PROMPT_PATH = ROOT / "prompts" / "r_qa.md"       # chỉ hỏi đáp: không trang đang chiếu, không điều hướng
REWRITE_PROMPT_PATH = ROOT / "prompts" / "r_rewrite.md"

# Robot nói gì khi không trả lời — KHÔNG lặp lại câu hỏi (đọc to câu troll là troll thành công)
ESCALATE_TEXT = {
    "no_info": "Phần này slide không nói tới, mình chưa trả lời chắc được. "
               "Mình ghi lại để giảng viên giải đáp sau nhé.",
    "off_topic": "Câu này nằm ngoài nội dung bài, mình xin phép quay lại bài giảng nhé.",
    "inappropriate": "Mình xin phép tiếp tục bài giảng nhé.",
}
CITATION = re.compile(r"\s*[\[(](?:slide|trang)\s*\d+[\])]", re.I)    # §10: TTS đọc thành "ngoặc vuông"
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


class Router:
    def __init__(self, doc: ParsedDocument, retriever: Retriever, cfg: RuntimeConfig,
                 deck_map: str, model: str, log_dir: Path):
        self.doc = doc
        self.retriever = retriever
        self.cfg = cfg
        self.deck_map = deck_map.strip()
        self.template = PROMPT_PATH.read_text(encoding="utf-8")
        self.qa_template = QA_PROMPT_PATH.read_text(encoding="utf-8")
        self.rewrite_template = REWRITE_PROMPT_PATH.read_text(encoding="utf-8")
        self.llm = LLM(model, log_dir, retry=cfg.llm_retry, timeout=cfg.llm_timeout_s,
                       extra=cfg.llm_extra)
        self.llm.json_mode = cfg.json_mode

    async def handle(self, question: str, at_slide: int,
                     history: list[Turn]) -> tuple[Reply, dict[str, int]]:
        ms: dict[str, int] = {}
        prev = history[-1] if history else None
        query = await self._rewrite(question, history, ms)
        t0 = time.perf_counter()
        nav, qa = await asyncio.to_thread(self.retriever.search, query, self.doc.page(at_slide))
        ms["search"] = _ms(t0)

        current = self.retriever.page_context(at_slide)
        found = self._found(nav, qa, at_slide, self._carry(prev))
        prompt = self._render(question, at_slide, current, found, history)

        t1 = time.perf_counter()
        try:
            raw = await self.llm.chat([{"role": "user", "content": prompt}],
                                      tag=f"p{at_slide:03d}_{int(time.time() * 1000)}")
        except RuntimeError as e:
            ms["llm"] = _ms(t1)
            return self._fallback(qa or nav, f"llm_error: {str(e)[:160]}"), ms
        ms["llm"] = _ms(t1)
        return self._check(raw, current + found, nav), ms

    async def answer(self, question: str,
                     history: list[Turn]) -> tuple[Reply, dict[str, int], dict]:
        """CHỈ hỏi đáp (scripts/try_ask.py): không trang đang chiếu, không điều hướng.

        Tìm (lọc trang phân mục) -> top `max_contexts` trang vào prompt r_qa.md -> 1 lần LLM ->
        code kiểm y như `handle` (grounding phải trỏ vào đoạn đã đưa). LLM chỉ được answer /
        escalate. -> (reply, thời gian, debug: hits tìm được, đoạn vào prompt, prompt, LLM trả thô,
        file log).
        """
        dbg: dict = {}
        ms: dict[str, int] = {}
        prev = history[-1] if history else None
        query = await self._rewrite(question, history, ms)
        t0 = time.perf_counter()
        _, qa = await asyncio.to_thread(self.retriever.search, query, None)
        ms["search"] = _ms(t0)

        found = self._found([], qa, 0, self._carry(prev))
        hist = "\n".join(f"- Hỏi: {t.question} → Đáp: {t.reply.text or t.reply.action}"
                         for t in history[-self.cfg.history_turns:])
        fill = {"deck_map": self.deck_map, "found": _ctx(found) or "(không tìm được đoạn nào)",
                "history": hist or "(chưa có)", "question": question}
        prompt = self.qa_template
        for k, v in fill.items():
            prompt = prompt.replace("{{" + k + "}}", v)

        tag = f"qa_{int(time.time() * 1000)}"
        dbg.update(hits=qa, found=found, prompt=prompt, query=query,
                   log=self.llm.log_dir / f"{tag}.json")
        t1 = time.perf_counter()
        try:
            raw = await self.llm.chat([{"role": "user", "content": prompt}], tag=tag)
        except RuntimeError as e:
            ms["llm"] = _ms(t1)
            return self._fallback(qa, f"llm_error: {str(e)[:160]}"), ms, dbg
        ms["llm"] = _ms(t1)
        dbg["raw"] = raw
        if raw.get("action") not in ("answer", "escalate"):
            raw = {"action": "escalate", "reason": "no_info"}
        # deck_map là nguồn hợp lệ cho câu hỏi cấu trúc bài (mấy trang, mấy chương): dựng bằng LUẬT
        # từ mục lục (kb/deck_map.py), không do model sinh — vẫn đúng NT3 "mọi câu phải có nguồn"
        dm = Context(chunk_id="deck_map", page_no=0, text=self.deck_map)
        return self._check(raw, [*found, dm], []), ms, dbg

    async def close(self) -> None:
        await self.llm.close()

    # ------------------------------------------------------------------ dựng prompt

    async def _rewrite(self, question: str, history: list[Turn], ms: dict[str, int]) -> str:
        """LLM xem câu hỏi của `history_turns` lượt gần nhất -> câu ĐEM ĐI TÌM. Câu hỏi nối tiếp
        được viết lại cho đủ ý; câu đủ ý / đổi chủ đề giữ nguyên. Chưa có lịch sử -> không gọi."""
        recent = history[-self.cfg.history_turns:]
        if not recent:
            return question
        prompt = (self.rewrite_template
                  .replace("{{history}}", "\n".join(f"- {t.question}" for t in recent))
                  .replace("{{question}}", question))
        t0 = time.perf_counter()
        try:
            raw = await self.llm.chat([{"role": "user", "content": prompt}],
                                      tag=f"rw_{int(time.time() * 1000)}")
            query = str(raw.get("query") or "").strip()
        except Exception:                      # lỗi API / JSON hỏng -> tìm bằng câu gốc, không chặn
            query = ""
        ms["rewrite"] = _ms(t0)
        return query or question

    def _carry(self, prev: Turn | None) -> list[Context]:
        """Đoạn câu trả lời LƯỢT TRƯỚC đã dùng — TRẠNG THÁI hội thoại, đưa lại thẳng vào prompt.

        "Giải thích câu trả lời vừa rồi" không có từ khoá nào để tìm: tìm lại thì trang vừa nói
        rơi khỏi top-5, LLM nhớ câu trả lời (lịch sử) mà không có đoạn nào để trích -> từ chối.
        """
        if prev is None or prev.reply.action != "answer":
            return []
        return self.retriever.contexts(prev.reply.sources)

    def _found(self, nav: list[SearchHit], qa: list[SearchHit], at_slide: int,
               carry: list[Context] | None = None) -> list[Context]:
        """`carry` (đoạn lượt trước) + top `max_contexts` trang tìm được, BỎ trang đang chiếu (đã
        có sẵn) và trang đã có trong `carry`. qa trước: cần nội dung; nav sau: bù trang mở chương
        cho câu điều hướng. `carry` KHÔNG tính vào `max_contexts` — nó là trạng thái, không phải
        kết quả tìm."""
        out: list[Context] = [c for c in carry or [] if c.page_no != at_slide]
        seen = {at_slide, *(c.page_no for c in out)}
        n = 0
        for h in qa + nav:
            if h.page_no in seen or n >= self.cfg.max_contexts:
                continue
            seen.add(h.page_no)
            n += 1
            out.append(Context(chunk_id=h.chunk_id, page_no=h.page_no,
                               text=h.text_enriched, vlm_ratio=h.vlm_ratio))
        return out

    def _render(self, question: str, at_slide: int, current: list[Context],
                found: list[Context], history: list[Turn]) -> str:
        recent = history[-self.cfg.history_turns:]
        hist = "\n".join(f"- (trang {t.at_slide}) Hỏi: {t.question} → Đáp: "
                         f"{t.reply.text or t.reply.action}" for t in recent)
        fill = {
            "page_no": str(at_slide),
            "n_pages": str(self.doc.n_pages),
            "deck_map": self.deck_map,
            "current": _ctx(current) or "(trang này không có nội dung chữ)",
            "found": _ctx(found) or "(không tìm được trang nào)",
            "history": hist or "(chưa có)",
            "question": question,
        }
        out = self.template
        for k, v in fill.items():
            out = out.replace("{{" + k + "}}", v)
        return out

    # ------------------------------------------------------------------ code kiểm

    def _check(self, raw: dict, contexts: list[Context], nav: list[SearchHit]) -> Reply:
        action = raw.get("action")

        if action == "goto_slide":
            try:
                page = int(raw.get("page"))
            except (TypeError, ValueError):
                return self._escalate("no_info", note=f"goto thieu trang: {raw}")
            if not 1 <= page <= self.doc.n_pages:
                return self._escalate("no_info", note=f"goto trang khong ton tai: {page}")
            return self._gate(page, nav, str(raw.get("reason", "")))

        if action == "answer":
            text = CITATION.sub("", str(raw.get("text", ""))).strip()
            # LLM hay chép cả ngoặc vuông "[doc#p011]" -> bỏ ngoặc rồi mới so
            refs = [str(g).strip().strip("[]").strip() for g in raw.get("grounding") or []]
            valid = {c.chunk_id for c in contexts}
            bad = [g for g in refs if g not in valid]
            if not text or not refs or bad:
                # Không nguồn hoặc nguồn BỊA -> không cho nói (NT3)
                return self._escalate("no_info", note=f"answer grounding sai: refs={refs} bad={bad}")
            if re.search(r"https?://|www\.", text):
                return self._escalate("no_info", note="answer doc URL thanh tieng")
            sents = SENTENCE_END.split(text)
            text = " ".join(sents[: self.cfg.max_answer_sentences])
            return Reply(action="answer", text=text, sources=refs,
                         note=f"cat {len(sents)}->{self.cfg.max_answer_sentences} cau"
                         if len(sents) > self.cfg.max_answer_sentences else "")

        if action == "meta" and raw.get("command") in ("repeat", "stop"):
            return Reply(action="meta", command=raw["command"])

        if action == "escalate":
            reason = raw.get("reason")
            return self._escalate(reason if reason in ESCALATE_TEXT else "no_info")

        return self._escalate("no_info", note=f"LLM tra hanh dong la: {raw}")

    def _gate(self, page: int, nav: list[SearchHit], reason: str) -> Reply:
        """Cổng tin cậy — nhảy sai trước cả lớp tệ hơn hỏi lại một câu.

        Chỉ nhảy khi trang LLM chọn CŨNG là trang tìm kiếm xếp đầu, VÀ đầu bảng hơn hẳn trang
        thứ hai (biên RRF >= ngưỡng). Chỉ 1 ứng viên -> coi như biên = 0 -> hỏi lại (§6).
        Biên RRF không phải xác suất; không có reranker (bỏ 2026-10-01) nên config ghi
        calibrated=false — ngưỡng chưa fit trên bộ eval có nhãn.
        """
        pages = [h.page_no for h in nav]
        margin = nav[0].score - nav[1].score if len(nav) > 1 else 0.0
        note = f"margin={margin:.4f} nguong={self.cfg.gate_min_margin} top={pages[:3]} ly_do={reason}"
        if pages and page == pages[0] and margin >= self.cfg.gate_min_margin:
            return Reply(action="goto", page=page, note=note)
        cands = list(dict.fromkeys([page, *pages[:2]]))
        return Reply(action="ask_back", candidates=cands, note=note,
                     text="Bạn muốn xem phần nào: " + self._list_pages(cands)
                          + "? Gõ “sang trang” kèm số nhé.")

    def _escalate(self, reason: str, *, note: str = "") -> Reply:
        return Reply(action="escalate", text=ESCALATE_TEXT[reason], note=note or reason)

    def _fallback(self, hits: list[SearchHit], note: str) -> Reply:
        """LLM lỗi -> vẫn có ích: chỉ ra trang liên quan, không bịa, không treo."""
        pages = [h.page_no for h in hits[:2]]
        text = "Mình chưa trả lời được ngay lúc này."
        if pages:
            text += f" Nội dung liên quan nằm ở {self._list_pages(pages)}."
        return Reply(action="escalate", text=text, candidates=pages, via="fallback", note=note)

    def _list_pages(self, pages: list[int]) -> str:
        return ", ".join(f"trang {p} – {page_label(self.doc.page(p))}" for p in pages)


def _ctx(items: list[Context]) -> str:
    return "\n\n".join(
        f"[{c.chunk_id}] trang {c.page_no}"
        + (" (vừa dùng ở câu trả lời trước)" if c.prev else "")
        + (f" (có {c.vlm_ratio:.0%} do máy tả ảnh — có thể sai con số)" if c.vlm_ratio else "")
        + f"\n{c.text}"
        for c in items)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
