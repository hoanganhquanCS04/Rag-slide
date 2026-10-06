"""Thử RIÊNG phần hỏi đáp — không trang đang chiếu, không điều hướng, không kịch bản.

Chạy SAU phần offline (`bash scripts/run_deck.sh <file raw>`).

    python scripts/try_ask.py onboarding_kit                              # hỏi liên tục, Ctrl+C để thoát
    python scripts/try_ask.py "data/raw/Onboarding Kit.pdf"               # tên file gốc cũng được
    python scripts/try_ask.py onboarding_kit "nghỉ phép năm được mấy ngày"
    python scripts/try_ask.py onboarding_kit "..." --prompt               # in NGUYÊN VĂN prompt gửi LLM
    python scripts/try_ask.py onboarding_kit "..." -q                     # chỉ câu trả lời, không log
    python scripts/try_ask.py onboarding_kit < cau_hoi.txt                # mỗi dòng một câu

Luồng (`Router.answer`, src/runtime/router.py): tìm (hybrid, RRF của search.py) -> top 5 trang vào
prompt prompts/r_qa.md -> 1 lần gọi LLM (LLM_MODEL trong .env) -> code kiểm grounding. LLM chỉ được
trả lời hoặc từ chối. `deck_map` dựng tại chỗ bằng luật (không tốn API).

Log từng bước để soi: [1] tìm được chunk nào, hạng dense / BM25, đoạn nào vào prompt · [2] prompt
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)                                       # out/, logs/, config/ trong file này tính từ gốc repo

from kb import deck_map
from kb.chunk import count_tokens
from kb.search import RRF_K, W_DENSE, W_SPARSE
from llm import DEFAULT_MODEL
from parsing.from_docling import slugify_doc_id
from parsing.models import ParsedDocument
from runtime.models import RuntimeConfig, Turn
from runtime.retrieve import Retriever
from runtime.router import Router

PAGE_OF_CHUNK = re.compile(r"#p(\d+)")            # chunk_id "onboarding_kit#p022.3" -> trang 22

for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")


def show_debug(dbg: dict, ms: dict, cfg: RuntimeConfig, mode: str, full_prompt: bool) -> None:
    if not dbg:
        return
    used = {c.chunk_id for c in dbg.get("found", [])}
    if "rewrite" in ms:
        same = dbg.get("query") == dbg.get("question")
        print(f"   [0] LLM viet lai cau hoi ({ms['rewrite'] / 1000:.1f}s): "
              + ("giu nguyen" if same else f"tim bang \"{dbg.get('query', '')[:150]}\""))
    print(f"   [1] tim: {mode}, RRF K={RRF_K} dense {W_DENSE:g} : bm25 {W_SPARSE:g} · {ms['search'] / 1000:.1f}s"
          f" · top-{cfg.top_k}, {cfg.max_contexts} trang dau vao prompt")
    print("       #  trang  chunk           rrf     dense      bm25   vao prompt")
    for i, h in enumerate(dbg.get("hits", []), 1):
        print(f"       {i}  p{h.page_no:<4}  {h.chunk_id.split('#', 1)[1]:<14} {h.score:.4f}"
              f"  {('hang ' + str(h.rank_dense)) if h.rank_dense else '—':<9}"
              f"  {('hang ' + str(h.rank_sparse)) if h.rank_sparse else '—':<9}"
              f"  {'✓' if h.chunk_id in used else ''}")
    if "prompt" in dbg:
        p = dbg["prompt"]
        print(f"   [2] prompt: {len(p)} ky tu · {count_tokens(p)} token · log: {dbg['log']}")
        for c in dbg.get("found", []):
            head = c.text.split("] ", 1)[-1].replace("\n", " ⏎ ")
            print(f"       [{c.chunk_id.split('#', 1)[1]}] {count_tokens(c.text)} tok"
                  + (" · VUA DUNG o cau truoc" if c.prev else "")
                  + (f" · {c.vlm_ratio:.0%} vlm" if c.vlm_ratio else "") + f" · {head[:90]}…")
        if full_prompt:
            print("       ----- prompt -----")
            print("\n".join("       " + ln for ln in p.splitlines()))
            print("       ------------------")
    if "raw" in dbg:
        print(f"   [3] LLM tra ({ms.get('llm', 0) / 1000:.1f}s): {json.dumps(dbg['raw'], ensure_ascii=False)[:600]}")


async def run(doc_id: str, questions: list[str], model: str, cfg: RuntimeConfig,
              quiet: bool, full_prompt: bool) -> None:
    doc = ParsedDocument.load(f"out/parsed/{doc_id}/document.json")
    retriever = Retriever(doc_id, cfg.top_k)
    router = Router(doc, retriever, cfg, deck_map.build(doc), model, Path("logs/runtime") / doc_id / "llm")
    history: list[Turn] = []
    print(f"{doc_id}: {doc.n_pages} trang | LLM {model}" + ("" if questions else " | Ctrl+C de thoat"))

    def ask_forever():
        while True:                                  # Enter trống -> hỏi lại; Ctrl+C / hết file -> thoát
            if q := input("\nhoi > ").strip():
                yield q

    try:
        for q in questions or ask_forever():
            t0 = time.perf_counter()
            reply, ms, dbg = await router.answer(q, history)
            dbg["question"] = q
            ms["total"] = int((time.perf_counter() - t0) * 1000)
            history.append(Turn(at_slide=0, question=q, reply=reply, ms=ms))

            pages = sorted({int(m.group(1)) for s in reply.sources if (m := PAGE_OF_CHUNK.search(s))})
            src = [f"trang {', '.join(map(str, pages))}"] if pages else []
            src += [s for s in reply.sources if not PAGE_OF_CHUNK.search(s)]      # vd "deck_map"
            print(f"\n>>> {q}")
            if not quiet:
                show_debug(dbg, ms, cfg, retriever.mode, full_prompt)
                print(f"   [4] code kiem: {reply.action}" + (f" · {reply.note}" if reply.note else " · dat"))
            print(f"🤖 {reply.text}")
            print(f"   ↳ {reply.action}"
                  + (f" · nguon: {' + '.join(src)}" if src else "")
                  + f" · {ms['total'] / 1000:.1f}s ("
                  + (f"viet lai {ms['rewrite'] / 1000:.1f}s, " if "rewrite" in ms else "")
                  + f"tim {ms['search'] / 1000:.1f}s, LLM {ms.get('llm', 0) / 1000:.1f}s)")
    except (EOFError, KeyboardInterrupt):
        print("\n(thoat)")
    finally:
        await router.close()


def main() -> int:
    ap = argparse.ArgumentParser(prog="try_ask")
    ap.add_argument("doc", help="doc_id (vd onboarding_kit) hoac ten file goc (vd \"data/raw/Onboarding Kit.pdf\")")
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
    missing = [f for f in (f"out/parsed/{doc_id}/document.json", f"out/kb/{doc_id}/chunks.json")
               if not Path(f).exists()]
    if missing:
        raise SystemExit(f"chua dung '{doc_id}' (thieu {', '.join(missing)}) — chay truoc:\n"
                         f'    bash scripts/run_deck.sh "data/raw/<file goc>.pdf"')
    try:
        asyncio.run(run(doc_id, qs, args.model, RuntimeConfig.load(args.config), args.quiet, args.prompt))
    except KeyboardInterrupt:                        # Ctrl+C lúc đang chờ tìm / LLM
        print("\n(thoat)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
