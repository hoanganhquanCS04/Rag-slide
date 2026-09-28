"""R6 v0 — vòng thuyết trình: nói HẾT trang ─► mở phiên hỏi đáp ─► trang tiếp.

    ━━ trang N ━━  in từng câu kịch bản (--speed 1 = đợi đúng thời lượng câu nói)
         │
    hỏi > ...      Enter trống  -> trang tiếp ("mình quay lại bài nhé" nếu vừa hỏi đáp)
                   lệnh R1      -> nhảy / nói lại / dừng     (fastpath, không gọi model)
                   câu khác     -> Router: tìm + 1 lần LLM + code kiểm
                                     goto     -> nhảy, nói tiếp từ trang đó
                                     answer / ask_back / escalate -> vẫn ở trang này, hỏi tiếp

Chưa có: ngắt lời GIỮA trang (R7), giọng nói (R5), renderer có ACK. Session là nguồn chân lý
duy nhất về trang đang chiếu — `at_slide` chụp lúc NHẬN câu hỏi (§6).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path

from kb.deck_map import page_label
from parsing.models import ParsedDocument
from runtime import fastpath
from runtime.models import RuntimeConfig, Turn
from runtime.retrieve import Retriever
from runtime.router import Router
from scenario.models import SYL_PER_MIN, Scenario

log = logging.getLogger("runtime")

PROMPT = "\nhỏi (Enter = trang tiếp · 'dừng' = thoát) > "
PAGE_OF_CHUNK = re.compile(r"#p(\d+)")


class Session:
    def __init__(self, doc_id: str, cfg: RuntimeConfig, model: str, speed: float):
        deck = Path("out/deck") / doc_id
        for f, how in [(deck / "scenario.json", "src/scenario/cli.py"),
                       (deck / "deck_map.txt", "src/kb/deck_map.py")]:
            if not f.exists():
                raise SystemExit(f"thieu {f} — chay: python {how} out/parsed/{doc_id}.json")

        self.doc = ParsedDocument.load(f"out/parsed/{doc_id}.json")
        sc = Scenario.model_validate_json((deck / "scenario.json").read_text(encoding="utf-8"))
        self.scripts = {s.page_no: s for s in sc.slides}
        self.retriever = Retriever(doc_id, cfg.top_k)
        logs = Path("logs/runtime") / doc_id
        self.router = Router(self.doc, self.retriever, cfg,
                             (deck / "deck_map.txt").read_text(encoding="utf-8"),
                             model, logs / "llm")
        self.speed = speed
        self.history: list[Turn] = []
        self.log_path = logs / f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    async def run(self, start: int = 1) -> None:
        page: int | None = start
        try:
            while page is not None and 1 <= page <= self.doc.n_pages:
                await self._present(page)
                page = await self._qa(page)
            if page is not None:
                self._say("Buổi thuyết trình đến đây là hết. Cảm ơn mọi người đã lắng nghe!")
        finally:
            await self.router.close()
            if self.history:
                log.info("\n(ghi %d lượt hỏi đáp -> %s)", len(self.history), self.log_path)

    # ----------------------------------------------------------------- nói

    async def _present(self, page_no: int) -> None:
        page = self.doc.page(page_no)
        log.info("\n━━ Trang %d/%d · %s · %s", page_no, self.doc.n_pages,
                 page_label(page), page.slide_type)
        ss = self.scripts.get(page_no)
        if ss is None or not ss.sentences:
            log.info("   (trang này chưa có kịch bản)")
            return
        for s in ss.sentences:
            self._say(s.text)
            if self.speed:
                await asyncio.sleep(s.syllables / SYL_PER_MIN * 60 * self.speed)

    def _say(self, text: str) -> None:
        log.info("🤖 %s", text)

    # ----------------------------------------------------------------- hỏi đáp

    async def _qa(self, page_no: int) -> int | None:
        """-> trang nói tiếp; None = dừng."""
        asked = False
        while True:
            try:
                q = (await asyncio.to_thread(input, PROMPT)).strip()
            except EOFError:                              # hết input (chạy thử bằng file)
                return None
            if not q:
                if asked:
                    self._say("Mình quay lại bài nhé.")
                return page_no + 1

            at_slide = page_no                            # chụp lúc NHẬN câu hỏi
            t0 = time.perf_counter()
            reply, ms = fastpath.match(q, at_slide, self.doc.n_pages), {}
            if reply is None:
                reply, ms = await self.router.handle(q, at_slide, self.history)
            ms["total"] = int((time.perf_counter() - t0) * 1000)
            if not reply.text:                            # lời robot cho lệnh không kèm câu nói
                reply.text = {"goto": f"Mình chuyển sang trang {reply.page} nhé.",
                              "meta": "Mình nói lại trang này nhé." if reply.command == "repeat"
                              else "Mình xin dừng ở đây."}.get(reply.action, "")
            turn = Turn(at_slide=at_slide, question=q, reply=reply, ms=ms,
                        search_mode="" if reply.via == "fastpath" else self.retriever.mode)
            self.history.append(turn)
            self._show(turn)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(turn.model_dump_json() + "\n")

            if reply.action == "goto":
                return reply.page
            if reply.action == "meta":
                return page_no if reply.command == "repeat" else None
            asked = True

    def _show(self, t: Turn) -> None:
        r = t.reply
        if r.text:
            self._say(r.text)
        info = [r.via, r.action]
        if r.sources:
            pages = sorted({int(m.group(1)) for s in r.sources if (m := PAGE_OF_CHUNK.search(s))})
            info.append("nguồn: trang " + ", ".join(map(str, pages)))
        if "search" in t.ms:
            info.append(f"tìm {t.ms['search'] / 1000:.1f}s ({t.search_mode})")
        if "llm" in t.ms:
            info.append(f"LLM {t.ms['llm'] / 1000:.1f}s")
        if r.note:
            info.append(r.note)
        log.info("   ↳ %s", " · ".join(info))
