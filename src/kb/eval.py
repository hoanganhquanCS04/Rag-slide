"""Chạy bộ câu hỏi có nhãn ở `data/eval/queries.json`.

Khác `audit.py`: audit lấy câu hỏi TỪ CHÍNH tài liệu (nhãn không ai bịa được, nhưng bài
thi quá dễ vì đề bài là đáp án). File này dùng câu hỏi NGƯỜI VIẾT — sát thực tế hơn,
nhưng nhãn do người gán nên có thể sai, và người gán dễ thiên vị.

Hai bài bổ sung cho nhau, không thay được nhau.

    python src/kb/eval.py out/kb/<ten>/chunks.json
    python src/kb/eval.py out/kb/<ten>/chunks.json --by nguoi     # chi cau nguoi that viet
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.embed import MODEL_ID
from kb.search import Mode, Searcher

log = logging.getLogger(__name__)

DEFAULT_EVAL = Path("data/eval/queries.json")


def load_queries(path: str | Path, by: str | None = None) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    qs = raw.get("queries", [])
    if by:
        qs = [q for q in qs if q.get("by") == by]
    return qs


def run(se: Searcher, queries: list[dict], *, mode: Mode = "hybrid",
        k: int = 5, filter_dividers: bool = True) -> tuple[int, int, list[str]]:
    """-> (so cau top-1 dung, so cau top-k dung, cac dong ket qua de in)."""
    top1 = topk = 0
    lines: list[str] = []
    for spec in queries:
        want = spec["page"]
        want = want if isinstance(want, list) else [want]
        hits = se.search(spec["q"], k=k, mode=mode, filter_dividers=filter_dividers)
        pages = [h.page_no for h in hits]

        ok1 = bool(pages) and pages[0] in want
        okk = any(p in want for p in pages)
        top1 += ok1
        topk += okk
        rank = next((i for i, p in enumerate(pages, 1) if p in want), None)

        mark = "DUNG" if ok1 else (f"hang {rank}" if rank else "TRUOT")
        lines.append("  %-6s %-42s can p%-9s ra %s" % (
            mark, spec["q"][:42], ",".join(map(str, want)),
            " ".join(f"p{p}" for p in pages[:3])))
    return top1, topk, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="eval")
    ap.add_argument("chunks")
    ap.add_argument("--queries", default=str(DEFAULT_EVAL))
    ap.add_argument("--vectors", default=None)
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--by", default=None, choices=["ai", "nguoi"],
                    help="chi chay cau do ai/nguoi viet")
    ap.add_argument("--mode", default=None, choices=["hybrid", "dense", "sparse"])
    ap.add_argument("-o", "--out", default=None, help="ghi ket qua ra JSON")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    qs = load_queries(args.queries, args.by)
    if not qs:
        raise SystemExit("khong co cau hoi nao khop")
    n_ai = sum(1 for q in qs if q.get("by") == "ai")

    se = Searcher(args.chunks, vectors_path=args.vectors, model_id=args.model)
    modes: list[Mode] = [args.mode] if args.mode else ["dense", "sparse", "hybrid"]
    report: dict = {"doc_id": se.cs.doc_id, "model": args.model, "k": args.k,
                    "n_queries": len(qs), "n_by_ai": n_ai, "modes": {}}

    log.info("")
    log.info("=== %d cau hoi  (%d do AI tu bia, %d nguoi that viet)", len(qs), n_ai, len(qs) - n_ai)
    for m in modes:
        t1, tk, lines = run(se, qs, mode=m, k=args.k)
        report["modes"][m] = {"top1": t1, f"top{args.k}": tk, "n": len(qs),
                              "top1_rate": round(t1 / len(qs), 4),
                              "detail": [ln.strip() for ln in lines]}
        log.info("")
        log.info("--- %s : top-1 %d/%d = %.0f%%  |  top-%d %d/%d = %.0f%%",
                 m, t1, len(qs), t1 / len(qs) * 100, args.k, tk, len(qs), tk / len(qs) * 100)
        for ln in lines:
            log.info("%s", ln)

    if args.out:
        import json as _json

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("")
        log.info("ghi -> %s", out)

    if n_ai == len(qs):
        log.info("")
        log.info("CANH BAO: 100% cau hoi do AI tu nghi ra roi tu cham. So nay THIEN VI,")
        log.info("          chua dung de ket luan duoc. §6 doi 50 cau nguoi that viet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
