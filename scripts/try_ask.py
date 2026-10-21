"""Thử RIÊNG phần hỏi đáp — không trang đang chiếu, không điều hướng, không kịch bản.

Chạy SAU phần offline (`bash scripts/run_deck.sh <file raw>`).

    python scripts/try_ask.py onboarding_kit                              # hỏi liên tục, Ctrl+C để thoát
    python scripts/try_ask.py data/raw/onboarding_kit.pdf                 # tên file gốc cũng được
    python scripts/try_ask.py onboarding_kit "nghỉ phép năm được mấy ngày"
    python scripts/try_ask.py onboarding_kit "..." --prompt               # in NGUYÊN VĂN prompt gửi LLM
    python scripts/try_ask.py onboarding_kit "..." -q                     # chỉ câu trả lời, không log
    python scripts/try_ask.py onboarding_kit < cau_hoi.txt                # mỗi dòng một câu

Luồng (`ChatEngine.process_question`, src/chat_engine.py): tìm (hybrid, RRF của utils/ensemble_search.py) -> top 5 trang vào
prompt src/prompts/r_qa.md -> 1 lần gọi LLM (LLM_MODEL trong .env) -> code kiểm grounding. LLM chỉ được
trả lời hoặc từ chối.

Log từng bước để soi: [1] tìm được chunk nào, hạng dense / BM25, LLM chọn đoạn nào (★) · [2] prompt
dài bao nhiêu, gồm gì, file log · [3] LLM trả thô · [4] code kiểm ra sao.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent       # chỉ để nạp src/ — sau đó dùng ROOT của utils.config
sys.path.insert(0, str(REPO / "src"))
os.chdir(REPO)                                       # --config mặc định config/runtime.json tính từ gốc repo

from utils.config import ROOT
from utils.tokens import count_tokens
from utils.ensemble_search import RRF_K, W_DENSE, W_SPARSE
from utils.llm import DEFAULT_MODEL
from models.parsed_document import slugify_doc_id
from chat_engine import ChatEngine
from models.runtime import Answer, RuntimeConfig

PAGE_OF_CHUNK = re.compile(r"#p(\d+)")            # chunk_id "onboarding_kit#p022.3" -> trang 22

for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def show_debug(ans: Answer, cfg: RuntimeConfig, full_prompt: bool) -> None:
    """In 3 bước đầu; chunk LLM ghi trong grounding (đã qua code kiểm) -> đánh ★."""
    ms, chosen = ans.ms, set(ans.reply.sources)
    found = ans.hits[: cfg.max_contexts]
    used = {h.chunk_id for h in found}
    print(f"   [1] tim: {ans.search_mode}, RRF K={RRF_K} dense {W_DENSE:g} : bm25 {W_SPARSE:g} · {ms['search'] / 1000:.1f}s"
          f" · top-{cfg.top_k}, {cfg.max_contexts} trang dau vao prompt")
    print("       #  trang  chunk           rrf     dense      bm25       LLM chon")
    for i, h in enumerate(ans.hits, 1):
        print(f"       {i}  p{h.page_no:<4}  {h.chunk_id.split('#', 1)[1]:<14} {h.score:.4f}"
              f"  {('hang ' + str(h.rank_dense)) if h.rank_dense else '—':<9}"
              f"  {('hang ' + str(h.rank_sparse)) if h.rank_sparse else '—':<9}"
              f"  {'★' if h.chunk_id in chosen else '' if h.chunk_id in used else '(ngoai prompt)'}")
    if ans.prompt:
        p = ans.prompt
        print(f"   [2] prompt: {len(p)} ky tu · {count_tokens(p)} token · log: {ans.log_path}")
        for h in found:
            head = h.text_enriched.split("] ", 1)[-1].replace("\n", " ⏎ ")
            print(f"     {'★' if h.chunk_id in chosen else ' '} [{h.chunk_id.split('#', 1)[1]}] {count_tokens(h.text_enriched)} tok"
                  f" · {head[:90]}…")
        if full_prompt:
            print("       ----- prompt -----")
            print("\n".join("       " + ln for ln in p.splitlines()))
            print("       ------------------")
    if ans.raw is not None:
        print(f"   [3] LLM tra ({ms.get('llm', 0) / 1000:.1f}s): {json.dumps(ans.raw, ensure_ascii=False)[:600]}")


async def run(doc_id: str, questions: list[str], model: str, cfg: RuntimeConfig,
              quiet: bool, full_prompt: bool) -> None:
    engine = ChatEngine(doc_id, cfg, model, ROOT / "logs" / "runtime" / doc_id / "llm")
    print(f"{doc_id}: LLM {model}" + ("" if questions else " | Ctrl+C de thoat"))

    def ask_forever():
        while True:                                  # Enter trống -> hỏi lại; Ctrl+C / hết file -> thoát
            if q := input("\nhoi > ").strip():
                yield q

    try:
        for q in questions or ask_forever():
            t0 = time.perf_counter()
            ans = await engine.process_question(q)
            reply, ms = ans.reply, ans.ms
            ms["total"] = int((time.perf_counter() - t0) * 1000)

            pages = sorted({int(m.group(1)) for s in reply.sources if (m := PAGE_OF_CHUNK.search(s))})
            src = [f"trang {', '.join(map(str, pages))}"] if pages else []
            print(f"\n>>> {q}")
            if not quiet:
                show_debug(ans, cfg, full_prompt)
                print(f"   [4] code kiem: {reply.action}" + (f" · {reply.note}" if reply.note else " · dat"))
            print(f"🤖 {reply.text}")
            print(f"   ↳ {reply.action}"
                  + (f" · nguon: {' + '.join(src)}" if src else "")
                  + f" · {ms['total'] / 1000:.1f}s ("
                  + f"tim {ms['search'] / 1000:.1f}s, LLM {ms.get('llm', 0) / 1000:.1f}s)")
    except (EOFError, KeyboardInterrupt):
        print("\n(thoat)")
    finally:
        await engine.close()


def main() -> int:
    ap = argparse.ArgumentParser(prog="try_ask")
    ap.add_argument("doc", help="doc_id (vd onboarding_kit) hoac ten file goc (vd data/raw/onboarding_kit.pdf)")
    ap.add_argument("question", nargs="*", help="cau hoi; bo trong = hoi lien tuc")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="mac dinh LLM_MODEL trong .env")
    ap.add_argument("--config", default="config/runtime.json")
    ap.add_argument("-q", "--quiet", action="store_true", help="chi in cau tra loi, khong log")
    ap.add_argument("--prompt", action="store_true", help="in nguyen van prompt gui LLM")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    qs = [" ".join(args.question)] if args.question else []
    # Tên file gốc -> doc_id bằng ĐÚNG hàm run_deck.sh dùng; doc_id đưa vào thì giữ nguyên
    doc_id = slugify_doc_id(Path(args.doc).stem)
    try:
        asyncio.run(run(doc_id, qs, args.model, RuntimeConfig.load(args.config), args.quiet, args.prompt))
    except KeyboardInterrupt:                        # Ctrl+C lúc đang chờ tìm / LLM
        print("\n(thoat)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
