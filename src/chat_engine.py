"""Trả lời câu hỏi của người nghe dựa trên nội dung slide.

Các bước cho mỗi câu hỏi:
    1. Tìm các trang slide liên quan tới câu hỏi, bằng hai cách rồi gộp lại:
         vector_search   tìm bằng vector — hiểu Ý câu hỏi (gọi API nhúng)
         keyword_search  tìm bằng từ khoá BM25 — khớp đúng CHỮ (không gọi API)
         ensemble_search gộp hai kết quả bằng RRF, gộp các đoạn cùng trang, lấy 5 trang đầu
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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from components.embedders.base import EmbedError, Embedder
from components.embedders.factory import create_embedder
from models.kb_chunk import SearchHit
from models.parsed_document import ParsedDocument
from models.runtime import Answer, Reply, RuntimeConfig
from utils.config import MODEL_ID, PARSER_OUTPUT, PROMPTS_DIR
from utils.ensemble_search import POOL, ensemble_search
from utils.filters import Hit, Where
from components.keyword_search import BM25Index
from utils.llm import LLM
from components.vectordb.factory import create_store

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
        self.doc = ParsedDocument.load(PARSER_OUTPUT / doc_id / "document.json")

        # Hai kho tìm kiếm. Kho vector (Chroma) đã nạp sẵn ở offline, chứa cả chữ lẫn metadata;
        # bảng BM25 dựng trong RAM từ chính các bản ghi đó, nên hai nhánh tìm trên CÙNG một bản chữ.
        self.doc_id = doc_id
        self.store = create_store(model_id=MODEL_ID)                        # kho vector
        records = self.store.get_document(doc_id)
        if not records:
            raise SystemExit(f"{doc_id}: kho vector chua co bai nay — chay "
                             f"`python src/offline/chunker/cli.py src/output/parser/{doc_id}/document.json`")
        self.bm25 = BM25Index()                                             # kho từ khoá, nằm trong RAM
        self.bm25.index_documents(records)
        # Bộ slide có trang nội dung nào không (toàn trang chuyển chương thì không cần tìm)
        self.has_content = any(r.metadata.get("content_type") == "content" for r in records)
        self._embedder: Embedder | None = None

        self.cfg = cfg
        self.template = PROMPT_PATH.read_text(encoding="utf-8")
        self.llm = LLM(model, log_dir, retry=cfg.llm_retry, timeout=cfg.llm_timeout_s,
                       extra=cfg.llm_extra)
        self.llm.json_mode = cfg.json_mode

    @property
    def embedder(self) -> Embedder:
        """Công cụ biến câu hỏi thành vector.

        Chỉ tạo khi cần lần đầu. retry=1: API nhúng lỗi thì bỏ qua ngay, không thử lại nhiều lần
        (người nghe đang chờ, đợi lâu còn tệ hơn tìm kém một chút).
        """
        if self._embedder is None:
            self._embedder = create_embedder(model_id=MODEL_ID, retry=1)
        return self._embedder

    async def process_question(self, question: str) -> Answer:
        """Trả lời một câu hỏi.

        Vào: câu hỏi.
        Ra:  Answer — gồm câu robot sẽ nói, và các thông tin để xem lại
             (tìm được trang nào, prompt gửi đi, LLM trả gì, mất bao lâu).
        """
        out = Answer(reply=Reply(action="escalate"))

        # Bước 1: tìm trang liên quan
        t0 = time.perf_counter()
        out.hits, out.search_mode = await asyncio.to_thread(self.retrieve_context, question)
        out.ms["search"] = _ms(t0)

        # Bước 2: lấy vài trang đầu, ghép thành prompt
        found = out.hits[: self.cfg.max_contexts]
        out.prompt = self._prepare_context(question, found)
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
        out.reply = self._check_answer(raw, found)
        return out

    async def close(self) -> None:
        """Đóng kết nối tới LLM. Gọi khi dùng xong."""
        await self.llm.close()

    # ------------------------------------------------------------------ tìm + dựng prompt

    def _where(self) -> Where:
        """Điều kiện lọc dùng cho cả hai cách tìm.

        Chỉ tìm trong bộ slide này, và bỏ các trang chỉ có tên chương (trang chuyển chương),
        vì chúng không có nội dung để trả lời.
        """
        return {"doc_id": self.doc_id, "content_type": "content"}

    def vector_search(self, question: str) -> list[Hit]:
        """Tìm bằng vector: biến câu hỏi thành vector (gọi API) -> 50 đoạn gần nhất [(mã đoạn, cosine)].

        API nhúng lỗi thì báo EmbedError ra ngoài, để retrieve_context quyết định.
        """
        query_vector = self.embedder.embed([question])[0]
        return self.store.retrieve_context(query_vector, POOL, self._where())

    def keyword_search(self, question: str) -> list[Hit]:
        """Tìm bằng từ khoá BM25 -> 50 đoạn khớp chữ nhất [(mã đoạn, điểm)]. Không gọi API."""
        return self.bm25.retrieve_context(question, POOL, self._where())

    def retrieve_context(self, question: str) -> tuple[list[SearchHit], str]:
        """Tìm các trang liên quan tới câu hỏi -> (các trang, cách đã dùng để tìm).

        Hai cách tìm chạy CÙNG LÚC, mỗi cách một luồng: vector (chờ API nhúng) và từ khoá BM25
        (chạy trong máy). Xong cả hai thì gộp ("hybrid").
        Nếu API nhúng vector lỗi thì câu này chỉ dùng kết quả từ khoá ("sparse").
        """
        if not self.has_content:
            return [], "hybrid"
        with ThreadPoolExecutor(max_workers=2) as pool:
            dense_job = pool.submit(self.vector_search, question)
            sparse_job = pool.submit(self.keyword_search, question)
            mode = "hybrid"
            try:
                dense = dense_job.result()      # lỗi trong luồng vector được ném lại ở đây
            except EmbedError as e:
                log.warning("  [API nhúng lỗi -> câu này chỉ tìm bằng từ khoá] %s", str(e)[:120])
                dense, mode = [], "sparse"
            sparse = sparse_job.result()
        return ensemble_search(dense, sparse, k=self.cfg.top_k), mode

    def _prepare_context(self, question: str, found: list[SearchHit]) -> str:
        """Điền các trang tìm được và câu hỏi vào mẫu prompt -> prompt hoàn chỉnh."""
        fill = {"found": _ctx(found) or "(không tìm được đoạn nào)", "question": question}
        out = self.template
        for k, v in fill.items():
            out = out.replace("{{" + k + "}}", v)
        return out

    # ------------------------------------------------------------------ kiểm câu trả lời

    def _check_answer(self, raw: dict, found: list[SearchHit]) -> Reply:
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
        """Tạo câu TỪ CHỐI trả lời — robot nói câu soạn sẵn, để người thật giải đáp sau.

        reason  lý do từ chối, chọn câu nói tương ứng trong ESCALATE_TEXT:
                  no_info        slide không có thông tin  -> "Phần này slide không nói tới..."
                  off_topic      câu hỏi ngoài bài          -> "Câu này nằm ngoài nội dung bài..."
                  inappropriate  câu chửi bới, quấy rối     -> "Mình xin phép tiếp tục bài giảng nhé."
        note    ghi chú nội bộ để xem lại vì sao từ chối (robot KHÔNG nói ra); bỏ trống thì ghi lý do.
        """
        return Reply(action="escalate", text=ESCALATE_TEXT[reason], note=note or reason)

    def _fallback(self, hits: list[SearchHit], note: str) -> Reply:
        """Câu robot nói khi GỌI LLM BỊ LỖI (mạng rớt, quá giờ...) — không có câu trả lời của LLM.

        Không im lặng: báo chưa trả lời được, và chỉ người nghe sang 2 trang tìm kiếm xếp đầu
        (việc tìm vẫn chạy được, chỉ LLM hỏng).
        Vd: "Mình chưa trả lời được ngay lúc này. Nội dung liên quan nằm ở trang 12 – PHÚC LỢI KHÁC,
             trang 7 – CÁCH TÍNH LÀM THÊM GIỜ."

        hits  các trang tìm được, trang tốt nhất đứng đầu
        note  lý do lỗi, để xem lại (robot KHÔNG nói ra)
        """
        pages = [h.page_no for h in hits[:2]]
        text = "Mình chưa trả lời được ngay lúc này."
        if pages:
            text += f" Nội dung liên quan nằm ở {self._list_pages(pages)}."
        return Reply(action="escalate", text=text, candidates=pages, via="fallback", note=note)

    def _list_pages(self, pages: list[int]) -> str:
        """Đổi danh sách số trang thành câu đọc được, kèm tên từng trang.

        Vd: [12, 7] -> "trang 12 – PHÚC LỢI KHÁC, trang 7 – CÁCH TÍNH LÀM THÊM GIỜ"
        Tên trang = tiêu đề trang; trang không có tiêu đề thì lấy dòng chữ đầu tiên (ParsedPage.label).
        """
        return ", ".join(f"trang {p} – {self.doc.page(p).label}" for p in pages)


def _ctx(hits: list[SearchHit]) -> str:
    """Ghép các trang tìm được thành MỘT đoạn chữ, điền vào chỗ {{found}} của prompt.

    Mỗi trang gồm 2 phần, các trang cách nhau một dòng trống:
        dòng đầu   [mã đoạn] trang N   — LLM chép mã này vào "grounding" để ghi nguồn
        bên dưới   chữ của trang (đã có tiền tố chương · tiêu đề · trang)

    Vd với 1 trang:
        [thoi_gian_lam_viec#p012] trang 12
        [PHÚC LỢI KHÁC · PHÚC LỢI KHÁC · trang 12/17] PHÚC LỢI KHÁC ...
    """
    return "\n\n".join(f"[{h.chunk_id}] trang {h.page_no}\n{h.text_enriched}" for h in hits)


def _ms(t0: float) -> int:
    """Đo thời gian: từ lúc t0 tới bây giờ là bao nhiêu mili giây (số nguyên).

    Dùng: t0 = time.perf_counter() trước một bước, xong bước đó gọi _ms(t0). Vd tìm mất 0.73s -> 730.
    """
    return int((time.perf_counter() - t0) * 1000)
