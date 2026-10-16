"""Đo chất lượng tìm kiếm bằng bộ câu hỏi có nhãn `data/eval/<doc_id>.queries.json`.

    python src/evaluation/eval.py src/output/chunker/<ten>/chunks.json                 # bộ câu hỏi của deck
    python src/evaluation/eval.py src/output/chunker/<ten>/chunks.json --by nguoi      # chỉ câu người viết
    python src/evaluation/eval.py src/output/chunker/<ten>/chunks.json --rrf-k 7 --wd 1.25

Để làm gì: câu hỏi do người viết (không lấy từ chính tài liệu) nên sát thực tế. Quét cấu hình RRF
bằng `tune.py`, dùng lại các hàm ở đây. Cách làm: với mỗi câu, tìm rồi xem trang đúng có ở hạng 1 / trong top-k không.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):  # chạy thẳng file: THAY src/evaluation/ bằng src/
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from utils.config import EVAL_QUERIES_DIR, MODEL_ID, console_setup
from runtime.retrieve import RRF_K, W_DENSE, W_SPARSE, Mode, Retriever

log = logging.getLogger(__name__)


def default_queries(doc_id: str) -> Path:
    """File câu hỏi mặc định: `data/eval/<doc_id>.queries.json` nếu có, không thì `queries.json`."""
    own = EVAL_QUERIES_DIR / f"{doc_id}.queries.json"
    return own if own.exists() else EVAL_QUERIES_DIR / "queries.json"


def load_queries(path: str | Path, by: str | None = None) -> list[dict]:
    """Đọc file câu hỏi; `by` = chỉ lấy câu do "ai" hoặc "nguoi" viết."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    qs = raw.get("queries", [])
    if by:
        qs = [q for q in qs if q.get("by") == by]
    return qs


def first_hit(pages: list[int], want: set[int] | list[int]) -> int | None:
    """Hạng đầu tiên (đếm từ 1) có trang đúng trong danh sách trang; không có thì None."""
    return next((r for r, p in enumerate(pages, 1) if p in want), None)


def run(rt: Retriever, queries: list[dict], *, mode: Mode = "hybrid",
        k: int = 5, filter_dividers: bool = True) -> tuple[int, int, list[str]]:
    """Chạy cả bộ câu hỏi -> (số câu đúng top-1, số câu đúng top-k, các dòng kết quả để in)."""
    top1 = topk = 0
    lines: list[str] = []
    for spec in queries:
        want = spec["page"]
        want = want if isinstance(want, list) else [want]
        hits = rt.search(spec["q"], k=k, mode=mode, filter_dividers=filter_dividers)
        pages = [h.page_no for h in hits]

        rank = first_hit(pages, want)
        ok1 = rank == 1
        top1 += ok1
        topk += rank is not None

        mark = "DUNG" if ok1 else (f"hang {rank}" if rank else "TRUOT")
        lines.append("  %-6s %-42s can p%-9s ra %s" % (
            mark, spec["q"][:42], ",".join(map(str, want)),
            " ".join(f"p{p}" for p in pages[:3])))
    return top1, topk, lines


def main(argv: list[str] | None = None) -> int:
    """Đọc tham số, chạy bộ câu hỏi theo từng kiểu (dense / sparse / hybrid), in và ghi kết quả ra JSON."""
    ap = argparse.ArgumentParser(prog="eval")
    ap.add_argument("chunks")
    ap.add_argument("--queries", default=None, help="mac dinh data/eval/<doc_id>.queries.json")
    ap.add_argument("--vectors", default=None)
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--by", default=None, choices=["ai", "nguoi"],
                    help="chi chay cau do ai/nguoi viet")
    ap.add_argument("--mode", default=None, choices=["hybrid", "dense", "sparse"])
    ap.add_argument("--rrf-k", type=int, default=RRF_K, help=f"K cua RRF, mac dinh {RRF_K} (retrieve.py)")
    ap.add_argument("--wd", type=float, default=W_DENSE, help=f"trong so dense, mac dinh {W_DENSE:g}")
    ap.add_argument("--ws", type=float, default=W_SPARSE, help=f"trong so sparse, mac dinh {W_SPARSE:g}")
    ap.add_argument("-o", "--out", default=None, help="ghi ket qua ra JSON")
    args = ap.parse_args(argv)

    console_setup()

    rt = Retriever(args.chunks, vectors_path=args.vectors, model_id=args.model,
                   rrf_k=args.rrf_k, w_dense=args.wd, w_sparse=args.ws)
    qpath = Path(args.queries) if args.queries else default_queries(rt.cs.doc_id)
    qs = load_queries(qpath, args.by)
    if not qs:
        raise SystemExit(f"{qpath}: khong co cau hoi nao khop")
    n_ai = sum(1 for q in qs if q.get("by") == "ai")
    modes: list[Mode] = [args.mode] if args.mode else ["dense", "sparse", "hybrid"]
    # trọng số chỉ có tác dụng ở hybrid — dense/sparse riêng thì thứ hạng là thứ hạng của nhánh đó
    is_default = (args.rrf_k, args.wd, args.ws) == (RRF_K, W_DENSE, W_SPARSE)
    rrf = {"k": args.rrf_k, "w_dense": args.wd, "w_sparse": args.ws, "is_default": is_default}
    report: dict = {"doc_id": rt.cs.doc_id, "model": args.model, "k": args.k,
                    "queries": str(qpath), "rrf": rrf,
                    "n_queries": len(qs), "n_by_ai": n_ai, "modes": {}}

    log.info("")
    log.info("=== %s: %d cau hoi  (%d do AI tu bia, %d nguoi that viet)",
             qpath.name, len(qs), n_ai, len(qs) - n_ai)
    log.info("=== gop RRF: K=%d dense=%g sparse=%g (%s)", args.rrf_k, args.wd, args.ws,
             "mac dinh retrieve.py" if is_default else "KHAC mac dinh retrieve.py")
    for m in modes:
        t1, tk, lines = run(rt, qs, mode=m, k=args.k)
        report["modes"][m] = {"top1": t1, f"top{args.k}": tk, "n": len(qs),
                              "top1_rate": round(t1 / len(qs), 4),
                              "detail": [ln.strip() for ln in lines]}
        log.info("")
        log.info("--- %s : top-1 %d/%d = %.0f%%  |  top-%d %d/%d = %.0f%%",
                 m, t1, len(qs), t1 / len(qs) * 100, args.k, tk, len(qs), tk / len(qs) * 100)
        for ln in lines:
            log.info("%s", ln)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("")
        log.info("ghi -> %s", out)

    if n_ai == len(qs):
        log.info("")
        log.info("CANH BAO: 100% cau hoi do AI tu nghi ra roi tu cham. So nay THIEN VI,")
        log.info("          chua dung de ket luan duoc. §6 doi 50 cau nguoi that viet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
