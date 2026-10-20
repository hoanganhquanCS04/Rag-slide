"""Thử nhiều cấu hình gộp RRF (K, trọng số dense / sparse) trên một deck -> bảng so sánh.

    bash scripts/tune.sh <doc_id>
    python src/evaluation/tune.py <doc_id> --k 5,10,20 --wd 1,2 --ws 1

Đọc bản ghi của deck từ kho vector thật (chạy `src/offline/chunker/cli.py` trước). Đo cách chunk khác
mà không đè kho thật: `CHROMA_PATH=<thư mục khác>` cho cả lệnh chunk lẫn lệnh này.
Ra: `src/output/evaluation/<doc_id>/tune.md` (người đọc) + `tune.json` (máy đọc)
+ `tune_misses.json` (các câu trượt).
Cách làm: mỗi câu hỏi lấy kết quả từng nhánh MỘT lần, rồi gộp lại cho mọi cấu hình mà không gọi
API thêm. Đây là công cụ ĐO — không tự đổi mặc định của utils/ensemble_search.py.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in (None, ""):  # chạy thẳng file: THAY src/evaluation/ bằng src/
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from utils.config import EVALUATION_OUTPUT, MODEL_ID, console_setup
from utils.tokens import count_tokens
from evaluation.eval import SearchSetup, default_queries, first_hit
from models.kb_chunk import StoredChunk
from utils.ensemble_search import POOL, RRF_K, TOP_K, W_DENSE, W_SPARSE, rrf_fuse

log = logging.getLogger(__name__)

DEFAULT_K = "1,5,10,20,40,60"
DEFAULT_WD = "0.5,1,1.5,2,3"
DEFAULT_WS = "1"

Pool = tuple[dict[str, int], dict[str, int], set[int]]     # hạng dense, hạng sparse (theo mã đoạn), trang đúng


def _floats(s: str) -> list[float]:
    """Đọc chuỗi "1,2,3" thành danh sách số."""
    return [float(x) for x in s.split(",") if x.strip()]


def _num(x: float) -> str:
    """In số gọn: 1.0 -> "1"."""
    return f"{x:g}"


def branch_ranks(setup: SearchSetup, query: str) -> tuple[dict[str, int], dict[str, int]]:
    """Chạy riêng từng cách tìm cho một câu hỏi -> (hạng ở cách tìm vector, hạng ở cách tìm BM25), CHƯA gộp.

    Mỗi câu chỉ chạy một lần (gọi API nhúng một lần), rồi thử mọi cấu hình gộp trên kết quả này.
    Hỏi thẳng hai kho, cùng số kết quả (POOL) và cùng bộ lọc như lúc chạy thật.
    """
    where = setup.where(True)
    dense = {record.chunk_id: r for r, (record, _) in enumerate(setup.vector_search(query, where), 1)}
    sparse = {record.chunk_id: r for r, (record, _) in enumerate(setup.keyword_search(query, where), 1)}
    return dense, sparse


def page_order(fused: dict[str, float], page_of: dict[str, int]) -> list[int]:
    """Điểm từng chunk -> danh sách trang theo thứ tự, mỗi trang lấy chunk mạnh nhất."""
    return list(dict.fromkeys(page_of[i] for i, _ in sorted(fused.items(), key=lambda kv: -kv[1])))


def chroma_check(setup: SearchSetup, queries: list[dict[str, Any]]) -> int:
    """Đếm số câu mà kết quả nhánh vector của kho KHÁC kết quả tính tay (để phát hiện kho trả sai).

    Cách làm: lấy vector của deck ra khỏi kho, tự tính cosine với câu hỏi, so với danh sách kho trả về.
    """
    ids, mat = setup.store.get_vectors(setup.doc_id)
    mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
    content = {r.chunk_id for r in setup.records if r.metadata.get("content_type") == "content"}
    ok = np.array([cid in content for cid in ids])
    where = setup.where(True)
    bad = 0
    for spec in queries:
        qv = np.asarray(setup.embedder.embed([spec["q"]])[0], dtype=np.float64)
        sims = mat @ (qv / np.linalg.norm(qv))
        exact = [ids[j] for j in np.argsort(-sims, kind="stable") if ok[j]][:POOL]
        bad += exact != [record.chunk_id for record, _ in setup.store.retrieve_context(qv, POOL, where)]
    return bad


def page_chunks(fused: dict[str, float], page_of: dict[str, int]) -> list[str]:
    """Điểm từng chunk -> chunk mạnh nhất của mỗi trang, theo thứ tự trang (để đếm token ngữ cảnh)."""
    best: dict[int, str] = {}
    for i, _ in sorted(fused.items(), key=lambda kv: -kv[1]):
        best.setdefault(page_of[i], i)
    return list(best.values())


def score(pools: list[Pool], page_of: dict[str, int], tokens: dict[str, int],
          k: int, wd: float, ws: float) -> dict[str, Any]:
    """Chấm một cấu hình -> top-1/3/5, MRR, số token ngữ cảnh trung bình, hạng từng câu."""
    ranks: list[int | None] = []
    ctx: list[int] = []
    for rd, rs, want in pools:
        fused = rrf_fuse(rd if wd else {}, rs if ws else {}, k=k, w_dense=wd, w_sparse=ws)
        ranks.append(first_hit(page_order(fused, page_of), want))
        ctx.append(sum(tokens[i] for i in page_chunks(fused, page_of)[:TOP_K]))
    return {
        "k": k, "w_dense": wd, "w_sparse": ws,
        **{f"top{n}": sum(1 for r in ranks if r and r <= n) for n in (1, 3, 5)},
        "mrr": round(sum(1 / r for r in ranks if r) / len(ranks), 4),
        "ctx_tokens": round(sum(ctx) / len(ctx)),
        "ranks": ranks,
    }


def build_configs(pools: list[Pool], page_of: dict[str, int], tokens: dict[str, int], ks: list[float],
                  wds: list[float], wss: list[float]) -> list[dict[str, Any]]:
    """Chấm mọi cấu hình trong lưới (kèm cấu hình đang dùng, chỉ dense, chỉ sparse) rồi sắp xếp.

    Cách làm: so với cấu hình đang dùng để biết mỗi cấu hình cứu được / làm hỏng câu nào; sắp theo
    top-5, rồi top-1, top-3, MRR.
    """
    grid = {(int(k), wd, ws) for k in ks for wd in wds for ws in wss}
    grid.add((RRF_K, W_DENSE, W_SPARSE))                          # cấu hình ensemble_search.py đang dùng
    out = [score(pools, page_of, tokens, k, wd, ws) | {"label": f"K={k} dense={_num(wd)} sparse={_num(ws)}"}
           for k, wd, ws in sorted(grid)]
    out += [score(pools, page_of, tokens, RRF_K, 1, 0) | {"label": "chỉ dense", "k": None},
            score(pools, page_of, tokens, RRF_K, 0, 1) | {"label": "chỉ sparse", "k": None}]
    base = next(c for c in out if (c["k"], c["w_dense"], c["w_sparse"]) == (RRF_K, W_DENSE, W_SPARSE))
    ok5 = [bool(r and r <= TOP_K) for r in base["ranks"]]
    for c in out:
        mine = [bool(r and r <= TOP_K) for r in c["ranks"]]
        c["gained"] = [i for i, (a, b) in enumerate(zip(ok5, mine), 1) if b and not a]
        c["lost"] = [i for i, (a, b) in enumerate(zip(ok5, mine), 1) if a and not b]
        c["is_base"] = c is base
    out.sort(key=lambda c: (-c["top5"], -c["top1"], -c["top3"], -c["mrr"]))
    return out


def by_type(cfg: dict[str, Any], queries: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Đếm theo loại câu hỏi -> {loại: [số câu, đúng top-1, đúng top-5]}."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for spec, r in zip(queries, cfg["ranks"]):
        row = out[spec.get("type") or "?"]
        row[0] += 1
        row[1] += bool(r and r == 1)
        row[2] += bool(r and r <= TOP_K)
    return dict(sorted(out.items()))


def write_md(path: Path, meta: dict[str, Any], configs: list[dict[str, Any]],
             best: dict[str, Any], base: dict[str, Any], misses: list[dict[str, Any]],
             types: dict[str, dict[str, list[int]]]) -> None:
    """Ghi báo cáo markdown: bảng các cấu hình, theo loại câu, so tốt nhất với đang dùng, các câu trượt."""
    n = meta["n_queries"]

    def pct(x: int) -> str:
        return f"{x} ({x / n:.1%})"

    L = [f"# Tune tham số gộp RRF — {meta['doc_id']}", "",
         f"- Chunks: kho {meta['store']} ({meta['n_chunks']} chunk, {meta['n_searchable']} tìm được, {meta['n_pages']} trang)",
         f"- Câu hỏi: `{meta['queries']}` ({n} câu)",
         f"- Model: {meta['model']} · kho: {meta['store']} · pool {meta['pool']}/nhánh · top-k {meta['top_k']}"
         " · gộp theo trang: điểm max · lọc trang phân mục: bật",
         f"- Kiểm kho: {meta['store_check']}",
         f"- Chạy lúc {meta['time']} ({meta['sec']}s)", "",
         f"Sắp theo top-{meta['top_k']}, hoà thì top-1, top-3, MRR. **cứu / hỏng** = câu top-{meta['top_k']} "
         "đúng thêm / sai thêm so với cấu hình ensemble_search.py đang dùng.", "",
         "**token 5 kq** = tổng token chunk mạnh nhất của 5 trang đầu, trung bình — độ dài ngữ cảnh "
         "LLM phải đọc.", "",
         "| # | K | dense | sparse | top-1 | top-3 | top-5 | MRR | token 5 kq | cứu | hỏng | ghi chú |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(configs, 1):
        note = []
        if c is best:
            note.append("★ tốt nhất")
        if c["is_base"]:
            note.append("đang dùng")
        if c["k"] is None:
            note.append(c["label"])
        L.append(f"| {i} | {c['k'] if c['k'] is not None else '—'} | {_num(c['w_dense'])} | {_num(c['w_sparse'])} "
                 f"| {pct(c['top1'])} | {pct(c['top3'])} | {pct(c['top5'])} | {c['mrr']:.3f} "
                 f"| {c['ctx_tokens']} "
                 f"| {'+' + str(len(c['gained'])) if c['gained'] else '—'} "
                 f"| {'−' + str(len(c['lost'])) if c['lost'] else '—'} | {' · '.join(note)} |")

    L += ["", f"## Theo loại câu — top-1 / top-{meta['top_k']}", "",
          f"| type | số câu | đang dùng ({base['label']}) | tốt nhất ({best['label']}) |", "|---|---|---|---|"]
    for t, (cnt, t1, t5) in types["base"].items():
        b = types["best"].get(t, [cnt, 0, 0])
        L.append(f"| {t} | {cnt} | {t1} / {t5} | {b[1]} / {b[2]} |")

    L += ["", f"## Tốt nhất so với đang dùng — {best['label']}", "",
          f"- cứu ({len(best['gained'])}): {best['gained'] or '—'}",
          f"- hỏng ({len(best['lost'])}): {best['lost'] or '—'}", "",
          f"## Câu trượt top-{meta['top_k']} của cấu hình tốt nhất ({len(misses)})", "",
          "Hạng = hạng TRANG đúng; — = không lọt pool.", "",
          "| id | câu hỏi | cần trang | top-5 ra | hạng gộp | dense | sparse |", "|---|---|---|---|---|---|---|"]
    for m in misses:
        L.append(f"| {m['id']} | {m['q']} | {m['page']} | {' '.join(map(str, m['top5']))} "
                 f"| {m['rank'] or '—'} | {m['rank_dense'] or '—'} | {m['rank_sparse'] or '—'} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def page_titles(records: list[StoredChunk]) -> dict[int, str]:
    """Trang -> tiêu đề, đọc từ tiền tố "[chương · tiêu đề · trang N/M] " ở đầu chữ của chunk."""
    out: dict[int, str] = {}
    for r in records:
        if not r.document.startswith("[") or "] " not in r.document:
            continue
        parts = r.document[1:].split("] ", 1)[0].split(" · ")
        if len(parts) >= 2 and r.page_no not in out:
            out[r.page_no] = parts[-2]
    return out


def write_misses(path: Path, meta: dict[str, Any], best: dict[str, Any], misses: list[dict[str, Any]],
                 queries: list[dict[str, Any]], titles: dict[int, str]) -> None:
    """Ghi các câu trượt top-5 của cấu hình tốt nhất, cùng dạng file câu hỏi (dùng lại được với `--queries`)."""
    rows = []
    for m in misses:
        spec = queries[m["id"] - 1]
        rows.append({"id": m["id"], "q": spec["q"], "page": spec["page"], "type": spec.get("type"),
                     "by": spec.get("by"), "why": spec.get("why"),
                     "rank": {"gop": m["rank"], "dense": m["rank_dense"], "sparse": m["rank_sparse"]},
                     "top5": [{"page": p, "title": titles.get(p, "")} for p in m["top5"]]})
    path.write_text(json.dumps({
        "note": (f"Câu trượt top-{meta['top_k']} của cấu hình tốt nhất ({best['label']}). Cùng dạng file câu "
                 "hỏi (q, page) nên đưa thẳng vào --queries được. rank = hạng TRANG đúng, null = không lọt pool."),
        "doc_id": meta["doc_id"], "config": best["label"],
        "score": {"top1": best["top1"], "top3": best["top3"], "top5": best["top5"], "n": meta["n_queries"]},
        "queries": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def out_paths(doc_id: str) -> tuple[Path, Path]:
    """Đường dẫn hai file báo cáo (.md, .json) trong `src/output/evaluation/<doc_id>/`."""
    base = EVALUATION_OUTPUT / doc_id / "tune"
    base.parent.mkdir(parents=True, exist_ok=True)
    return base.with_name(base.name + ".md"), base.with_name(base.name + ".json")


def main(argv: list[str] | None = None) -> int:
    """Đọc tham số, lấy kết quả từng nhánh cho mọi câu, chấm mọi cấu hình, kiểm kho, ghi báo cáo."""
    ap = argparse.ArgumentParser(prog="tune", description="quet K + trong so dense/sparse cua RRF")
    ap.add_argument("doc", help="doc_id, vd onboarding_kit")
    ap.add_argument("--queries", default=None, help="mac dinh data/eval/<doc_id>.queries.json")
    ap.add_argument("--k", default=DEFAULT_K, help=f"danh sach K, mac dinh {DEFAULT_K}")
    ap.add_argument("--wd", default=DEFAULT_WD, help=f"trong so dense, mac dinh {DEFAULT_WD}")
    ap.add_argument("--ws", default=DEFAULT_WS, help=f"trong so sparse, mac dinh {DEFAULT_WS}")
    ap.add_argument("--model", default=MODEL_ID)
    args = ap.parse_args(argv)

    console_setup()

    t0 = time.perf_counter()
    setup = SearchSetup(args.doc, model_id=args.model)
    records = setup.records
    qpath = Path(args.queries) if args.queries else default_queries(setup.doc_id)
    queries = json.loads(qpath.read_text(encoding="utf-8"))["queries"]
    log.info("%s: %d chunk | %s: %d cau", setup.doc_id, len(records), qpath.name, len(queries))
    for name in ("embedders", "httpx"):                 # 200 dòng "cache: dung lai 1/1" -> tắt
        logging.getLogger(name).setLevel(logging.WARNING)

    page_of = {r.chunk_id: r.page_no for r in records}
    pools: list[Pool] = []
    for spec in queries:
        rd, rs = branch_ranks(setup, spec["q"])
        pools.append((rd, rs, set(spec["page"] if isinstance(spec["page"], list) else [spec["page"]])))

    bad = chroma_check(setup, queries)
    store_check = (f"pool dense của kho khớp cosine tính tay ở cả {len(queries)} câu" if not bad else
                   f"⚠ {bad}/{len(queries)} câu pool dense của kho KHÁC cosine tính tay — bảng lượt này "
                   "KHÔNG tin được, chạy lại")
    if bad:
        log.warning("kho %s: %d/%d cau pool dense lech cosine tinh tay -> chay lai", setup.store.kind, bad, len(queries))

    tokens = {r.chunk_id: count_tokens(r.document) for r in records}
    configs = build_configs(pools, page_of, tokens, _floats(args.k), _floats(args.wd), _floats(args.ws))
    best = next(c for c in configs if c["k"] is not None)      # dòng một nhánh không tính là cấu hình gộp
    base = next(c for c in configs if c["is_base"])

    misses = []
    for i, (spec, r, (rd, rs, want)) in enumerate(zip(queries, best["ranks"], pools), 1):
        if r and r <= TOP_K:
            continue
        fused = page_order(rrf_fuse(rd, rs, k=best["k"], w_dense=best["w_dense"],
                                    w_sparse=best["w_sparse"]), page_of)
        misses.append({"id": i, "q": spec["q"], "page": spec["page"], "type": spec.get("type"),
                       "top5": fused[:TOP_K], "rank": r,
                       "rank_dense": first_hit(page_order(rrf_fuse(rd, {}), page_of), want),
                       "rank_sparse": first_hit(page_order(rrf_fuse({}, rs), page_of), want)})

    meta = {"doc_id": setup.doc_id, "queries": str(qpath),
            "n_chunks": len(records),
            "n_searchable": sum(1 for r in records if r.metadata.get("content_type") == "content"),
            "n_pages": len({r.page_no for r in records}), "n_queries": len(queries),
            "model": args.model, "store": setup.store.kind, "store_check": store_check,
            "store_mismatch": bad, "pool": POOL, "top_k": TOP_K,
            "time": time.strftime("%Y-%m-%d %H:%M"), "sec": round(time.perf_counter() - t0, 1)}
    types = {"base": by_type(base, queries), "best": by_type(best, queries)}

    md, js = out_paths(setup.doc_id)
    write_md(md, meta, configs, best, base, misses, types)
    js.write_text(json.dumps(
        {"meta": meta, "best": best["label"], "base": base["label"], "configs": configs,
         "misses_best": misses, "by_type": types}, ensure_ascii=False, indent=1), encoding="utf-8")
    ms = md.with_name("tune_misses.json")
    write_misses(ms, meta, best, misses, queries, page_titles(records))

    log.info("")
    for name, c in (("dang dung", base), ("tot nhat ", best)):
        log.info("%s : %-28s top-1/3/5 %d/%d/%d | MRR %.3f | token 5kq %d%s", name, c["label"],
                 c["top1"], c["top3"], c["top5"], c["mrr"], c["ctx_tokens"],
                 f"  (cuu {len(c['gained'])}, hong {len(c['lost'])})" if c is best else "")
    log.info("kiem kho  : %s", "khop" if not bad else f"LECH {bad} cau")
    log.info("ghi -> %s, %s, %s", md, js, ms)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
