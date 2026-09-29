"""Kiểm tra index có dùng được không — bằng số, không bằng cảm tính.

Xem docs/spec/search.md §10. Hai bài kiểm tra:

  1. **Self-retrieval** (§5 S6a · gate §11 ≥ 90%)
     Lấy nội dung trang `i` làm câu hỏi -> top-1 phải ra đúng trang `i`.
     Nhãn ở đây LÀ CHÍNH NÓ, không ai bịa được — khác hẳn mấy câu hỏi người viết code
     tự nghĩ ra rồi tự chấm.
     Trang nào tự tìm không ra chính mình thì trang đó không có danh tính riêng: nó lẫn
     với trang khác, và R2 sẽ nhảy sai ở runtime.

  2. **Quét trùng lặp**
     So từng cặp vector. Hai chunk gần như trùng nhau thì chúng cướp suất của nhau trong
     top-k, và người duyệt ở S7 phải đọc hai lần cùng một nội dung.

Chạy CẢ BA kiểu (dense / sparse / hybrid) rồi in ra một bảng. Hybrid không hơn dense thì
phải nói thẳng ra, chứ không giữ BM25 cho đủ lệ.

    python src/kb/audit.py out/kb/<ten>.chunks.json
    python src/kb/audit.py out/kb/<ten>.chunks.json --query-from raw --show-fail
"""

from __future__ import annotations

import argparse
import itertools
import logging
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.embed import MODEL_ID, load_vectors
from kb.search import Mode, Searcher

log = logging.getLogger(__name__)

GATE_TOP1 = 0.90        # §11: self-retrieval top-1 >= 90%

# Ngưỡng coi hai chunk là trùng. Đo trên 3_datavisualization:
#     0.969  p024 == p024.b02   trùng thật (chunk trang nuốt luôn mô tả ảnh)
#     0.950  p034 == p034.b02   trùng thật
#     0.934  p027 == p028       hai trang KHÁC nhau, chỉ là nội dung gần nhau
# 0.95 hụt mất p034 (0.9498); 0.93 bắt oan p027/p028. Chốt 0.94 — khe hẹp nhưng
# đây là deck duy nhất đo được. Deck thứ hai vào là phải xem lại con số này.
DUP_THRESHOLD = 0.94

QueryFrom = Literal["enriched", "raw", "title"]


class Failure(BaseModel):
    chunk_id: str
    want_page: int
    got_page: int
    got_text: str = ""
    rank_of_want: int | None = None     # trang đúng rơi xuống hạng mấy


class AuditReport(BaseModel):
    """Cả bộ kết quả, ghi ra JSON theo §7.1 (`audit/self_retrieval.json`)."""

    doc_id: str
    model: str
    n_chunks: int
    query_from: str
    dup_threshold: float
    duplicates: list[tuple[str, str, float]] = Field(default_factory=list)
    results: list["AuditResult"] = Field(default_factory=list)
    gate_top1: float = 0.0
    passed: bool = False
    caveat: str = (
        "Self-retrieval lay cau hoi TU CHINH van ban cua chunk nen gan nhu luon 100%. "
        "No chi chung minh khong co hai chunk trung nhau, KHONG chung minh index tim tot. "
        "Gate §11 chi co nghia sau khi co S1 sinh `message` (xem docs/spec/search.md §10)."
    )


class AuditResult(BaseModel):
    mode: str
    query_from: str
    n: int = 0
    top1: int = 0
    top3: int = 0
    failures: list[Failure] = Field(default_factory=list)

    @property
    def top1_rate(self) -> float:
        return self.top1 / self.n if self.n else 0.0

    @property
    def top3_rate(self) -> float:
        return self.top3 / self.n if self.n else 0.0

    @property
    def passed(self) -> bool:
        return self.top1_rate >= GATE_TOP1


def _query_text(chunk, query_from: QueryFrom) -> str:
    if query_from == "raw":
        return chunk.text_raw                       # bỏ tiền tố -> đo tiền tố đóng góp gì
    if query_from == "title":
        return (chunk.section_title or "") + " " + chunk.text_raw[:120]
    return chunk.text_enriched


# --------------------------------------------------------------- self-retrieval


def self_retrieval(se: Searcher, *, mode: Mode = "hybrid",
                   query_from: QueryFrom = "enriched", k: int = 3) -> AuditResult:
    """Mỗi chunk tìm được tự làm câu hỏi cho chính nó."""
    res = AuditResult(mode=mode, query_from=query_from)
    targets = [c for c in se.chunks if c.is_searchable]

    for c in targets:
        q = _query_text(c, query_from)
        if not q.strip():
            continue
        res.n += 1
        # Lọc phân mục BẬT: đây là luật của nhánh R4, và trang phân mục không nằm
        # trong tập cần tìm nên để chúng vào chỉ tạo nhiễu.
        hits = se.search(q, k=k, mode=mode, filter_dividers=True, group_by_page=True)
        pages = [h.page_no for h in hits]
        if pages and pages[0] == c.page_no:
            res.top1 += 1
        else:
            rank = pages.index(c.page_no) + 1 if c.page_no in pages else None
            res.failures.append(Failure(
                chunk_id=c.chunk_id, want_page=c.page_no,
                got_page=pages[0] if pages else -1,
                got_text=hits[0].text_enriched[:70].replace("\n", " ") if hits else "",
                rank_of_want=rank,
            ))
        if c.page_no in pages:
            res.top3 += 1
    return res


# ------------------------------------------------------------------- trùng lặp


def find_duplicates(se: Searcher, threshold: float = DUP_THRESHOLD
                    ) -> list[tuple[str, str, float]]:
    """So từng cặp vector. Vector đã chuẩn hoá L2 nên tích vô hướng CHÍNH LÀ cosine.

    Đọc thẳng .npy chứ không hỏi kho: cần CẢ ma trận, kho chỉ trả top-k.
    """
    M = load_vectors(se.cs, se.vectors_path, se.model_id)
    sim = M @ M.T
    out: list[tuple[str, str, float]] = []
    for i, j in itertools.combinations(range(len(M)), 2):
        if sim[i, j] >= threshold:
            out.append((se.chunks[i].chunk_id, se.chunks[j].chunk_id, float(sim[i, j])))
    return sorted(out, key=lambda t: -t[2])


# ------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="audit")
    ap.add_argument("chunks", help="out/kb/<ten>.chunks.json")
    ap.add_argument("--vectors", default=None)
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("--query-from", default="enriched", choices=["enriched", "raw", "title"])
    ap.add_argument("--mode", default=None, choices=["hybrid", "dense", "sparse"],
                    help="mac dinh chay ca ba de so")
    ap.add_argument("--show-fail", action="store_true", help="liet ke tung ca truot")
    ap.add_argument("--dup-threshold", type=float, default=DUP_THRESHOLD)
    ap.add_argument("-o", "--out", default=None,
                    help="ghi ket qua ra JSON, vd out/kb/audit/self_retrieval.json")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    se = Searcher(args.chunks, vectors_path=args.vectors, model_id=args.model)

    # ---- 1. trùng lặp (không tốn API, chạy trước)
    dups = find_duplicates(se, args.dup_threshold)
    log.info("")
    log.info("=== TRUNG LAP (cosine >= %.2f)", args.dup_threshold)
    if dups:
        for a, b, s in dups:
            log.info("    %.4f  %s  ==  %s", s, a.split("#")[-1], b.split("#")[-1])
        log.info("    -> %d cap. Chung cuop suat cua nhau trong top-k.", len(dups))
    else:
        log.info("    khong co cap nao")

    # ---- 2. self-retrieval
    modes: list[Mode] = [args.mode] if args.mode else ["dense", "sparse", "hybrid"]
    results: list[AuditResult] = []
    log.info("")
    log.info("=== SELF-RETRIEVAL (cau hoi lay tu '%s')", args.query_from)
    for m in modes:
        r = self_retrieval(se, mode=m, query_from=args.query_from)
        results.append(r)
        log.info("    %-8s n=%-3d top-1 %3d/%-3d = %5.1f%%   top-3 %5.1f%%  %s",
                 m, r.n, r.top1, r.n, r.top1_rate * 100, r.top3_rate * 100,
                 "DAT" if r.passed else "TRUOT")

    best = max(results, key=lambda r: r.top1_rate)
    log.info("")
    log.info("    gate §11: top-1 >= %.0f%%  ->  tot nhat la '%s' voi %.1f%%  %s",
             GATE_TOP1 * 100, best.mode, best.top1_rate * 100,
             "DAT" if best.passed else "TRUOT")

    if args.show_fail:
        for r in results:
            if not r.failures:
                continue
            log.info("")
            log.info("--- %s truot %d ca:", r.mode, len(r.failures))
            for f in r.failures:
                log.info("    p%-3d -> p%-3d (dung roi xuong hang %s)  %s",
                         f.want_page, f.got_page, f.rank_of_want or ">3", f.got_text)

    if args.out:
        rep = AuditReport(
            doc_id=se.cs.doc_id, model=args.model, n_chunks=len(se.chunks),
            query_from=args.query_from, dup_threshold=args.dup_threshold,
            duplicates=dups, results=results,
            gate_top1=GATE_TOP1, passed=best.passed,
        )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rep.model_dump_json(indent=2), encoding="utf-8")
        log.info("")
        log.info("ghi -> %s", out)

    return 0 if best.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
