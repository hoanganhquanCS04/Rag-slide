"""Đo chất lượng tìm kiếm bằng bộ câu hỏi có nhãn `data/eval/<doc_id>.queries.json`.

    python src/evaluation/eval.py <doc_id>                       # vd onboarding_kit — bộ câu hỏi của deck
    python src/evaluation/eval.py <doc_id> --by nguoi            # chỉ câu người viết
    python src/evaluation/eval.py <doc_id> --rrf-k 7 --wd 1.25

Đo trên kho vector thật (Chroma) — chạy `src/offline/chunker/cli.py` trước. Muốn đo thử một cách chunk
khác mà không đè kho thật: đặt `CHROMA_PATH=<thư mục khác>` cho cả lệnh chunk lẫn lệnh này.

Để làm gì: câu hỏi do người viết (không lấy từ chính tài liệu) nên sát thực tế. Quét cấu hình RRF
bằng `tune.py`, dùng lại các hàm ở đây. Cách làm: với mỗi câu, tìm rồi xem trang đúng có ở hạng 1 / trong top-k không.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Literal

if __package__ in (None, ""):  # chạy thẳng file: THAY src/evaluation/ bằng src/
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from components.embedders.base import Embedder
from components.embedders.factory import create_embedder
from models.kb_chunk import SearchHit
from utils.config import EVAL_QUERIES_DIR, MODEL_ID, console_setup
from utils.ensemble_search import POOL, RRF_K, TOP_K, W_DENSE, W_SPARSE, ensemble_search
from utils.filters import Hit, Where
from components.keyword_search import BM25Index
from components.vectordb.factory import create_store

log = logging.getLogger(__name__)

# Cách tìm: cả hai / chỉ vector / chỉ BM25
Mode = Literal["hybrid", "dense", "sparse"]


class SearchSetup:
    """Hai kho tìm kiếm của MỘT deck, nạp sẵn để đo — cùng cách nạp và cùng 3 bước tìm như chat_engine.py.

    Không dùng thẳng ChatEngine: nó kéo theo LLM + prompt, mà đo tìm kiếm không cần.
    """

    def __init__(self, doc_id: str, *, model_id: str = MODEL_ID):
        """Đọc mọi bản ghi của deck từ kho vector, rồi dựng BM25 từ chính các bản ghi đó."""
        self.doc_id = doc_id
        self.model_id = model_id
        self.store = create_store(model_id=model_id)
        self.records = self.store.get_document(doc_id)
        if not self.records:
            raise SystemExit(f"{doc_id}: kho vector chua co bai nay — chay "
                             f"`python src/offline/chunker/cli.py src/output/parser/{doc_id}/document.json`")
        self.bm25 = BM25Index()
        self.bm25.index_documents(self.records)
        self._embedder: Embedder | None = None

    @property
    def embedder(self) -> Embedder:
        """Công cụ nhúng câu hỏi — chỉ tạo khi cần, nên đo chỉ BM25 thì không cần khoá API."""
        if self._embedder is None:
            self._embedder = create_embedder(model_id=self.model_id)
        return self._embedder

    def where(self, filter_dividers: bool = True) -> Where:
        """Bộ lọc chung hai nhánh: chỉ bộ slide này; filter_dividers=True thì bỏ trang chuyển chương."""
        return {"doc_id": self.doc_id, **({"content_type": "content"} if filter_dividers else {})}

    def vector_search(self, query: str, where: Where) -> list[Hit]:
        """Tìm bằng vector -> POOL đoạn gần nhất [(mã đoạn, cosine)] (gọi API nhúng)."""
        return self.store.retrieve_context(self.embedder.embed([query])[0], POOL, where)

    def keyword_search(self, query: str, where: Where) -> list[Hit]:
        """Tìm bằng BM25 -> POOL đoạn khớp chữ nhất [(mã đoạn, điểm)] (không gọi API)."""
        return self.bm25.retrieve_context(query, POOL, where)

    def search(self, query: str, *, k: int = TOP_K, mode: Mode = "hybrid", filter_dividers: bool = True,
               rrf_k: int = RRF_K, w_dense: float = W_DENSE, w_sparse: float = W_SPARSE) -> list[SearchHit]:
        """Tìm -> k trang đầu. mode="dense"/"sparse" thì nhánh còn lại để rỗng (và không gọi tới)."""
        if not any(r.metadata.get("content_type") == "content" or not filter_dividers for r in self.records):
            return []
        where = self.where(filter_dividers)
        dense = self.vector_search(query, where) if mode in ("hybrid", "dense") else []
        sparse = self.keyword_search(query, where) if mode in ("hybrid", "sparse") else []
        return ensemble_search(dense, sparse, k=k,
                               rrf_k=rrf_k, w_dense=w_dense, w_sparse=w_sparse)


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


def run(setup: SearchSetup, queries: list[dict], *, mode: Mode = "hybrid", k: int = 5,
        filter_dividers: bool = True, rrf: dict | None = None) -> tuple[int, int, list[str]]:
    """Chạy cả bộ câu hỏi -> (số câu đúng top-1, số câu đúng top-k, các dòng kết quả để in)."""
    top1 = topk = 0
    lines: list[str] = []
    for spec in queries:
        want = spec["page"]
        want = want if isinstance(want, list) else [want]
        hits = setup.search(spec["q"], k=k, mode=mode, filter_dividers=filter_dividers, **(rrf or {}))
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
    ap.add_argument("doc", help="doc_id, vd onboarding_kit")
    ap.add_argument("--queries", default=None, help="mac dinh data/eval/<doc_id>.queries.json")
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--by", default=None, choices=["ai", "nguoi"],
                    help="chi chay cau do ai/nguoi viet")
    ap.add_argument("--mode", default=None, choices=["hybrid", "dense", "sparse"])
    ap.add_argument("--rrf-k", type=int, default=RRF_K, help=f"K cua RRF, mac dinh {RRF_K} (utils/ensemble_search.py)")
    ap.add_argument("--wd", type=float, default=W_DENSE, help=f"trong so dense, mac dinh {W_DENSE:g}")
    ap.add_argument("--ws", type=float, default=W_SPARSE, help=f"trong so sparse, mac dinh {W_SPARSE:g}")
    ap.add_argument("-o", "--out", default=None, help="ghi ket qua ra JSON")
    args = ap.parse_args(argv)

    console_setup()

    setup = SearchSetup(args.doc, model_id=args.model)
    qpath = Path(args.queries) if args.queries else default_queries(setup.doc_id)
    qs = load_queries(qpath, args.by)
    if not qs:
        raise SystemExit(f"{qpath}: khong co cau hoi nao khop")
    n_ai = sum(1 for q in qs if q.get("by") == "ai")
    modes: list[Mode] = [args.mode] if args.mode else ["dense", "sparse", "hybrid"]
    # trọng số chỉ có tác dụng ở hybrid — dense/sparse riêng thì thứ hạng là thứ hạng của nhánh đó
    is_default = (args.rrf_k, args.wd, args.ws) == (RRF_K, W_DENSE, W_SPARSE)
    rrf = {"k": args.rrf_k, "w_dense": args.wd, "w_sparse": args.ws, "is_default": is_default}
    report: dict = {"doc_id": setup.doc_id, "model": args.model, "k": args.k,
                    "queries": str(qpath), "rrf": rrf,
                    "n_queries": len(qs), "n_by_ai": n_ai, "modes": {}}

    log.info("")
    log.info("=== %s: %d cau hoi  (%d do AI tu bia, %d nguoi that viet)",
             qpath.name, len(qs), n_ai, len(qs) - n_ai)
    log.info("=== gop RRF: K=%d dense=%g sparse=%g (%s)", args.rrf_k, args.wd, args.ws,
             "mac dinh ensemble_search.py" if is_default else "KHAC mac dinh ensemble_search.py")
    for m in modes:
        t1, tk, lines = run(setup, qs, mode=m, k=args.k,
                            rrf={"rrf_k": args.rrf_k, "w_dense": args.wd, "w_sparse": args.ws})
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
