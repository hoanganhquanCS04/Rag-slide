"""Lệnh viết kịch bản (S4) cho từng trang.

    python src/offline/scenario/cli.py src/output/parser/<doc_id>/document.json                  # cả deck
    python src/offline/scenario/cli.py src/output/parser/<doc_id>/document.json --page 9 11 20   # vài trang
    python src/offline/scenario/cli.py src/output/parser/<doc_id>/document.json --dry-run --page 11
    python src/offline/scenario/cli.py src/output/parser/<doc_id>/document.json --show           # xem, không gọi API
    python src/offline/scenario/cli.py src/output/parser/<doc_id>/document.json --md             # chỉ ghi lại .md

Ra: `src/output/scenario/<doc_id>/scenario.json` + `scenario.md` (bản đọc).
Cách làm: chạy lại chỉ viết trang đã đổi (page_hash / prompt / model); lưu sau MỖI trang nên dừng
giữa chừng không mất trang đã viết. Mã thoát: 0 xong · 3 xong nhưng có trang cờ đỏ · 1 lỗi.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from utils.config import ROOT, SCENARIO_OUTPUT, console_setup
from utils.llm import DEFAULT_MODEL
from offline.parser.enrich import enrich
from models.parsed_document import ParsedDocument
from offline.scenario.generate import load_prompt, render_prompt, run_deck
from models.scenario import Scenario, SlideScript
from utils.syllables import STORE, Pronunciation

log = logging.getLogger("scenario")


def show(sc: Scenario, pages: set[int] | None) -> None:
    """In kịch bản ra màn hình: mỗi trang một dòng tóm tắt, rồi đoạn văn và các khối nguồn."""
    for ss in sc.slides:
        if pages and ss.page_no not in pages:
            continue
        log.info("")
        log.info("--- trang %d · %s · %d âm tiết · ~%.0fs · pass%d  %s",
                 ss.page_no, ss.slide_type, ss.syllables, ss.seconds,
                 ss.passes, ("CỜ: " + ",".join(ss.flags)) if ss.flags else "")
        log.info("    %s", ss.text)
        log.info("    → refs: %s", ", ".join(ss.refs) or "(không có)")


def to_markdown(sc: Scenario, doc: ParsedDocument, pages: set[int] | None) -> str:
    """Dựng `scenario.md`: kịch bản dạng đọc bằng mắt, mỗi trang một đoạn văn kèm khối nguồn."""
    out = [f"# Kịch bản · {sc.doc_id}", "",
           f"model `{sc.model}` · pronunciation `{sc.pronunciation_hash}` · "
           f"tổng ~{sc.seconds / 60:.1f} phút", ""]
    for ss in sc.slides:
        if pages and ss.page_no not in pages:
            continue
        p = doc.page(ss.page_no)
        title = (p.title if p else None) or "(không có tiêu đề)"
        out += [f"## Trang {ss.page_no} · {title}", "",
                f"`{ss.slide_type}` · {ss.syllables} âm tiết · ~{ss.seconds:.0f}s · pass {ss.passes}"
                + (f" · 🚩 **{', '.join(ss.flags)}**" if ss.flags else ""), "",
                ss.text or "*(chưa có)*", "",
                f"<sub>nguồn: {', '.join(f'`{r}`' for r in ss.refs) or '🚩 KHÔNG CÓ'}</sub>", ""]
        if ss.unknown_terms:
            out += [f"*Từ chưa có cách đọc:* {', '.join(ss.unknown_terms)}", ""]
    return "\n".join(out)


def report(sc: Scenario) -> None:
    """In thống kê kịch bản: tổng thời lượng, trang nội dung không có nguồn, trang chạy pass 2,
    trang cờ đỏ, từ chưa có cách đọc."""
    tot = sum(ss.syllables for ss in sc.slides)
    content = [ss for ss in sc.slides if ss.slide_type in ("content", "exercise")]
    no_ref = [ss.page_no for ss in content if not ss.refs]
    red = [ss.page_no for ss in sc.slides if ss.red_flags]
    p2 = [ss.page_no for ss in sc.slides if ss.passes == 2]
    unk = sorted({t for ss in sc.slides for t in ss.unknown_terms})

    log.info("")
    log.info("=== %s · %d trang · model %s", sc.doc_id, len(sc.slides), sc.model)
    log.info("    tổng thời lượng       %.1f phút  (%d âm tiết ÷ 200/phút)", sc.seconds / 60, tot)
    log.info("    trang không có nguồn  %d/%d %s", len(no_ref), len(content), no_ref or "")
    log.info("    phải chạy pass 2      %d trang %s", len(p2), p2 or "")
    log.info("    CỜ ĐỎ                 %d trang %s", len(red), red or "")
    log.info("    từ chưa có cách đọc   %s", unk or "không")


def main(argv: list[str] | None = None) -> int:
    """Đọc tham số, nạp + enrich document.json và kịch bản cũ (nếu có), rồi làm theo chế độ.

    Cách làm: `--md` chỉ ghi lại .md; `--show` chỉ in; `--dry-run` chỉ in prompt; còn lại thì viết
    kịch bản (lưu dần sau mỗi trang), in kết quả, trả mã 3 nếu có trang cờ đỏ.
    """
    ap = argparse.ArgumentParser(prog="scenario")
    ap.add_argument("parsed", help="src/output/parser/<doc_id>/document.json")
    ap.add_argument("--pron", default=str(STORE), help="kho phát âm chung, mặc định data/pronunciation.json")
    ap.add_argument("-o", "--out", default=None, help="mặc định src/output/scenario/<doc_id>/scenario.json")
    ap.add_argument("--page", nargs="+", type=int, default=None, help="11 | 9 11 20 | {9..15}")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--force", action="store_true", help="bỏ qua incremental, viết lại")
    ap.add_argument("--dry-run", action="store_true", help="in prompt, không gọi API")
    ap.add_argument("--show", action="store_true", help="chỉ in kịch bản đã có")
    ap.add_argument("--md", action="store_true",
                    help="CHỈ ghi lại scenario.md, không gọi API — chạy thường đã tự ghi; "
                         "dùng sau khi sửa tay scenario.json")
    args = ap.parse_args(argv)

    console_setup()
    logging.getLogger("httpx").setLevel(logging.WARNING)

    doc = enrich(ParsedDocument.load(args.parsed))
    out = Path(args.out) if args.out else SCENARIO_OUTPUT / doc.doc_id / "scenario.json"
    pron = Pronunciation.load(args.pron)
    pages = set(args.page) if args.page else None

    old: Scenario | None = None
    if out.exists():
        old = Scenario.model_validate(json.loads(out.read_text(encoding="utf-8")))

    if args.md:
        if not old:
            raise SystemExit(f"chưa có {out}")
        md = out.with_suffix(".md")
        md.write_text(to_markdown(old, doc, pages), encoding="utf-8")
        log.info("ghi -> %s", md)
        return 0

    if args.show:
        if not old:
            raise SystemExit(f"chưa có {out}")
        show(old, pages)
        report(old)
        return 0

    if args.dry_run:
        template, _ = load_prompt()
        for pg in sorted(pages or {doc.pages[0].page_no}):
            p = doc.page(pg)
            prev = [s for s in (old.slides if old else [])
                    if s.section_id == p.section_id and s.page_no < pg]
            log.info("=" * 78)
            log.info(render_prompt(template, doc, p, p.slide_type, prev))
        return 0

    existing = {s.page_no: s for s in old.slides} if old else {}
    _, prompt_hash = load_prompt()
    out.parent.mkdir(parents=True, exist_ok=True)

    def save(slides: list[SlideScript]) -> Scenario:
        sc = Scenario(doc_id=doc.doc_id, model=args.model, prompt_hash=prompt_hash,
                      pronunciation_hash=pron.hash_for(s.text for s in slides), slides=slides)
        tmp = out.with_suffix(".json.tmp")           # ghi tạm rồi thay: dừng giữa lúc ghi
        tmp.write_text(sc.model_dump_json(indent=2), encoding="utf-8")   # không hỏng file cũ
        tmp.replace(out)
        # bản đọc cho người — ghi cùng lúc với .json để hai file luôn khớp, CẢ deck
        # (kể cả khi chỉ chạy --page) để mở ra là thấy toàn bộ
        out.with_suffix(".md").write_text(to_markdown(sc, doc, None), encoding="utf-8")
        return sc

    def checkpoint(results: dict[int, SlideScript]) -> None:
        # trang chưa tới lượt giữ bản cũ — lưu dần không được làm mất trang nào
        merged = {**existing, **results}
        save([merged[k] for k in sorted(merged)])

    log.info("sinh kịch bản · %s · %s", doc.doc_id,
             f"trang {sorted(pages)}" if pages else "cả deck")
    slides = asyncio.run(run_deck(
        doc, pron, model=args.model, existing=existing, only=pages,
        force=args.force, workers=args.workers, log_dir=ROOT / "logs" / "s4" / doc.doc_id,
        on_page=checkpoint))
    sc = save(slides)

    show(sc, pages)
    report(sc)
    log.info("")
    log.info("ghi -> %s", out)
    log.info("ghi -> %s   (bản đọc — mở Preview: Ctrl+Shift+V)", out.with_suffix(".md"))
    return 3 if any(s.red_flags for s in sc.slides) else 0


if __name__ == "__main__":
    raise SystemExit(main())
