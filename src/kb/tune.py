"""Quét tham số gộp RRF (K, trọng số dense / sparse) trên MỘT bộ chunk -> bảng top-1/3/5.

Đổi cách chunk thì chỉ đổi file đầu vào, chạy lại, so hai báo cáo — không chạy lẻ từng script.

    bash scripts/tune.sh out/kb/<ten>/chunks.json        # -> out/kb/<ten>/audit/chunks_tune.md + .json
    python src/kb/tune.py out/kb/<ten>/chunks.json --queries data/eval/<ten>.queries.json
    python src/kb/tune.py out/kb/<ten>/chunks.json --k 5,10,20 --wd 1,2 --ws 1

Ra HAI file đặt theo tên file vào: `<thư mục chunks>/audit/<tên chunks>_tune.md` (người đọc) +
`.json` cùng tên (máy đọc, để so giữa các bộ chunk). Một file vào = một bộ báo cáo: chạy lại thì
ghi đè báo cáo của chính file đó; bộ chunk khác tên thì báo cáo khác tên.

Cách làm: pool 50 của mỗi nhánh lấy MỘT lần mỗi câu (`Searcher.ranks`, đúng thứ search chạy),
rồi gộp offline bằng `rrf_fuse` cho mọi tổ hợp — cả lưới chỉ vài giây, không gọi API thêm.
Gộp theo trang = điểm chunk cao nhất, giống `_group_by_page`.

Kho vector theo `VECTOR_DB` trong .env. Chroma từng trả pool lệch thỉnh thoảng (đo 2026-10-01:
~3/14 lượt, nặng nhất ngay sau khi dựng lại S5) -> báo cáo tự so pool dense của kho với cosine
tính tay từ .npy; lệch là bảng lượt đó không tin được, chạy lại.

Bộ chunk thử cùng `doc_id` với bộ thật thì `store.sync` THAY dữ liệu của doc đó trong kho dùng
chung; lần sau chạy với bộ thật nó tự nạp lại (sync so metadata). Đừng tune lúc runtime đang chạy.

Chỉ TỈ LỆ dense:sparse có nghĩa (nhân cả hai cùng một số thì thứ hạng không đổi) nên mặc định
giữ sparse = 1, quét dense. Đây là công cụ ĐO — không đổi mặc định của search.py.
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

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.embed import MODEL_ID, embed_chunkset, load_vectors, vectors_file
from kb.eval import default_queries
from kb.models import ChunkSet
from kb.search import POOL, RRF_K, TOP_K, W_DENSE, W_SPARSE, Searcher, rrf_fuse

log = logging.getLogger(__name__)

DEFAULT_K = "1,5,10,20,40,60"
DEFAULT_WD = "0.5,1,1.5,2,3"
DEFAULT_WS = "1"

Pool = tuple[dict[int, int], dict[int, int], set[int]]     # hạng dense, hạng sparse, trang đúng


def _floats(s: str) -> list[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def _num(x: float) -> str:
    return f"{x:g}"


def ensure_vectors(cs: ChunkSet, kb_dir: Path, model_id: str) -> None:
    """Bộ chunk mới chưa nhúng (hoặc nhúng lệch) -> nhúng luôn. Cache theo nội dung: chữ không
    đổi thì không gọi API — đổi file chunk là chạy được ngay, không phải nhớ bước --embed."""
    try:
        load_vectors(cs, vectors_file(kb_dir, model_id), model_id)
    except SystemExit as e:
        log.info("vector chua khop bo chunk (%s) -> nhung", str(e).splitlines()[0])
        embed_chunkset(cs, kb_dir, model_id=model_id)


def page_order(fused: dict[int, float], page_of: list[int]) -> list[int]:
    """Điểm chunk -> danh sách trang, mỗi trang lấy chunk mạnh nhất (như `_group_by_page`)."""
    return list(dict.fromkeys(page_of[i] for i, _ in sorted(fused.items(), key=lambda kv: -kv[1])))


def first_hit(pages: list[int], want: set[int]) -> int | None:
    return next((r for r, p in enumerate(pages, 1) if p in want), None)


def chroma_check(se: Searcher, queries: list[dict[str, Any]]) -> int:
    """Số câu mà pool dense của kho KHÁC cosine tính tay từ .npy (cùng bộ lọc). inmem luôn 0."""
    mat = np.load(se.vectors_path)
    mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
    ok = np.array([c.is_searchable for c in se.chunks])
    ids = [c.chunk_id for c in se.chunks]
    where = se.where(True)
    bad = 0
    for spec in queries:
        qv = np.asarray(se.embedder.embed([spec["q"]], use_cache=True)[0], dtype=np.float64)
        sims = mat @ (qv / np.linalg.norm(qv))
        exact = [ids[j] for j in np.argsort(-sims, kind="stable") if ok[j]][:POOL]
        bad += exact != [cid for cid, _ in se.store.query(qv, POOL, where)]
    return bad


def page_chunks(fused: dict[int, float], page_of: list[int]) -> list[int]:
    """Điểm chunk -> chunk mạnh nhất của mỗi trang, theo thứ tự trang (để đếm token ngữ cảnh)."""
    best: dict[int, int] = {}
    for i, _ in sorted(fused.items(), key=lambda kv: -kv[1]):
        best.setdefault(page_of[i], i)
    return list(best.values())


def score(pools: list[Pool], page_of: list[int], tokens: list[int],
          k: int, wd: float, ws: float) -> dict[str, Any]:
    """Một cấu hình -> top-1/3/5, MRR, token 5 trang đầu, hạng từng câu."""
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


def build_configs(pools: list[Pool], page_of: list[int], tokens: list[int], ks: list[float],
                  wds: list[float], wss: list[float]) -> list[dict[str, Any]]:
    grid = {(int(k), wd, ws) for k in ks for wd in wds for ws in wss}
    grid.add((RRF_K, W_DENSE, W_SPARSE))                          # cấu hình search.py đang dùng
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
    """type -> [số câu, top-1, top-5]."""
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
    n = meta["n_queries"]

    def pct(x: int) -> str:
        return f"{x} ({x / n:.1%})"

    L = [f"# Tune tham số gộp RRF — {meta['doc_id']}", "",
         f"- Chunks: `{meta['chunks']}` ({meta['n_chunks']} chunk, {meta['n_searchable']} tìm được, {meta['n_pages']} trang)",
         f"- Câu hỏi: `{meta['queries']}` ({n} câu)",
         f"- Model: {meta['model']} · kho: {meta['store']} · pool {meta['pool']}/nhánh · top-k {meta['top_k']}"
         " · gộp theo trang: điểm max · lọc trang phân mục: bật",
         f"- Kiểm kho: {meta['store_check']}",
         f"- Chạy lúc {meta['time']} ({meta['sec']}s)", "",
         f"Sắp theo top-{meta['top_k']}, hoà thì top-1, top-3, MRR. **cứu / hỏng** = câu top-{meta['top_k']} "
         "đúng thêm / sai thêm so với cấu hình search.py đang dùng.", "",
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


def page_titles(chunks: list[Any]) -> dict[int, str]:
    """Trang -> tiêu đề, đọc từ tiền tố '[chương · tiêu đề · trang N/M] ' của chunk."""
    out: dict[int, str] = {}
    for c in chunks:
        head = c.text_enriched[: len(c.text_enriched) - len(c.text_raw)].strip().strip("[]")
        parts = head.split(" · ")
        if len(parts) >= 2 and c.page_no not in out:
            out[c.page_no] = parts[-2]
    return out


def write_misses(path: Path, meta: dict[str, Any], best: dict[str, Any], misses: list[dict[str, Any]],
                 queries: list[dict[str, Any]], titles: dict[int, str]) -> None:
    """Câu trượt top-5 của cấu hình tốt nhất — CÙNG DẠNG file câu hỏi (q, page…) nên đưa thẳng vào
    `--queries` được để chạy lại riêng các câu này."""
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
        "doc_id": meta["doc_id"], "chunks": meta["chunks"], "config": best["label"],
        "score": {"top1": best["top1"], "top3": best["top3"], "top5": best["top5"], "n": meta["n_queries"]},
        "queries": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


def out_paths(chunks: Path) -> tuple[Path, Path]:
    """-> (file .md, file .json) đặt theo tên file vào: <thư mục chunks>/audit/<tên chunks>_tune.*

    MỘT file vào = MỘT bộ báo cáo: chạy lại thì ghi đè báo cáo của chính file đó; bộ chunk
    khác tên ra báo cáo khác tên, không đè nhau. Đuôi `_tune` để khỏi lẫn với file chunk thật.
    """
    base = chunks.parent / "audit" / f"{chunks.stem}_tune"
    base.parent.mkdir(parents=True, exist_ok=True)
    return base.with_name(base.name + ".md"), base.with_name(base.name + ".json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tune", description="quet K + trong so dense/sparse cua RRF")
    ap.add_argument("chunks", help="out/kb/<ten>/chunks.json (hoac bo chunk thu bat ky)")
    ap.add_argument("--queries", default=None, help="mac dinh data/eval/<doc_id>.queries.json")
    ap.add_argument("--k", default=DEFAULT_K, help=f"danh sach K, mac dinh {DEFAULT_K}")
    ap.add_argument("--wd", default=DEFAULT_WD, help=f"trong so dense, mac dinh {DEFAULT_WD}")
    ap.add_argument("--ws", default=DEFAULT_WS, help=f"trong so sparse, mac dinh {DEFAULT_WS}")
    ap.add_argument("--model", default=MODEL_ID)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    t0 = time.perf_counter()
    cp = Path(args.chunks)
    cs = ChunkSet.model_validate(json.loads(cp.read_text(encoding="utf-8")))
    qpath = Path(args.queries) if args.queries else default_queries(cs.doc_id)
    queries = json.loads(qpath.read_text(encoding="utf-8"))["queries"]
    log.info("%s: %d chunk | %s: %d cau", cp, len(cs.chunks), qpath.name, len(queries))

    ensure_vectors(cs, cp.parent, args.model)
    se = Searcher(cp, model_id=args.model)
    for name in ("kb.embed", "httpx"):                 # 200 dòng "cache: dung lai 1/1" -> tắt
        logging.getLogger(name).setLevel(logging.WARNING)

    page_of = [c.page_no for c in se.chunks]
    pools: list[Pool] = []
    for spec in queries:
        rd, rs, _, _ = se.ranks(spec["q"])
        pools.append((rd, rs, set(spec["page"] if isinstance(spec["page"], list) else [spec["page"]])))

    bad = chroma_check(se, queries)
    store_check = (f"pool dense của kho khớp cosine tính tay ở cả {len(queries)} câu" if not bad else
                   f"⚠ {bad}/{len(queries)} câu pool dense của kho KHÁC cosine tính tay — bảng lượt này "
                   "KHÔNG tin được, chạy lại")
    if bad:
        log.warning("kho %s: %d/%d cau pool dense lech cosine tinh tay -> chay lai", se.store.kind, bad, len(queries))

    tokens = [c.token_count for c in se.chunks]
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

    meta = {"doc_id": cs.doc_id, "chunks": str(cp), "queries": str(qpath),
            "n_chunks": len(cs.chunks), "n_searchable": len(cs.searchable),
            "n_pages": len({c.page_no for c in cs.chunks}), "n_queries": len(queries),
            "model": args.model, "store": se.store.kind, "store_check": store_check,
            "store_mismatch": bad, "pool": POOL, "top_k": TOP_K,
            "time": time.strftime("%Y-%m-%d %H:%M"), "sec": round(time.perf_counter() - t0, 1)}
    types = {"base": by_type(base, queries), "best": by_type(best, queries)}

    md, js = out_paths(cp)
    write_md(md, meta, configs, best, base, misses, types)
    js.write_text(json.dumps(
        {"meta": meta, "best": best["label"], "base": base["label"], "configs": configs,
         "misses_best": misses, "by_type": types}, ensure_ascii=False, indent=1), encoding="utf-8")
    ms = md.with_name(f"{cp.stem}_misses.json")
    write_misses(ms, meta, best, misses, queries, page_titles(se.chunks))

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
