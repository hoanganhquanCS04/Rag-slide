"""S4 — sinh kịch bản robot nói cho từng trang (§9: chạy riêng được qua CLI).

    python src/scenario/cli.py out/parsed/<doc_id>/document.json                  # cả deck
    python src/scenario/cli.py out/parsed/<doc_id>/document.json --page 9,11,20   # vài trang
    python src/scenario/cli.py out/parsed/<doc_id>/document.json --dry-run --page 11
    python src/scenario/cli.py out/parsed/<doc_id>/document.json --show           # xem, không gọi API
    python src/scenario/cli.py out/parsed/<doc_id>/document.json --md             # chỉ ghi lại .md

Mỗi lần lưu scenario.json là ghi luôn scenario.md (bản đọc cả deck) bên cạnh.

Chạy lại là INCREMENTAL: trang có page_hash + prompt + model không đổi thì giữ nguyên,
không tốn tiền. Đổi kho phát âm data/pronunciation.json thì chỉ đếm lại âm tiết, không gọi LLM.
Lưu dần sau MỖI trang: dừng giữa chừng thì trang đã viết vẫn còn, chạy lại là đi tiếp.
Chạy xong -> scripts/extract_terms.py gom từ chưa có cách đọc vào kho -> người chốt -> chạy lại.

Mã thoát: 0 xong · 3 xong nhưng có trang CỜ ĐỎ (file vẫn ghi đủ) · 1 hỏng thật — giống parse,
`run_deck.sh` gặp 3 thì chạy tiếp.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import DEFAULT_MODEL, ROOT
from parsing.cli import parse_pages
from parsing.models import ParsedDocument
from scenario.generate import load_prompt, render_prompt, run_deck
from scenario.models import Scenario, SlideScript
from scenario.syllables import STORE, Pronunciation

log = logging.getLogger("scenario")


def show(sc: Scenario, pages: set[int] | None) -> None:
    for ss in sc.slides:
        if pages and ss.page_no not in pages:
            continue
        log.info("")
        log.info("--- trang %d · %s · %d câu · %d âm tiết · ~%.0fs · pass%d  %s",
                 ss.page_no, ss.slide_type, len(ss.sentences), ss.syllables, ss.seconds,
                 ss.passes, ("CỜ: " + ",".join(ss.flags)) if ss.flags else "")
        for s in ss.sentences:
            ref = f"  → {s.grounding.ref}" if s.kind == "content" and s.grounding else ""
            log.info("    [%-8s %2d] %s%s", s.kind, s.syllables, s.text, ref)


def to_markdown(sc: Scenario, doc: ParsedDocument, pages: set[int] | None) -> str:
    """Kịch bản dạng đọc được bằng mắt — mở Preview trong VSCode, đọc như bản thoại."""
    out = [f"# Kịch bản · {sc.doc_id}", "",
           f"model `{sc.model}` · pronunciation `{sc.pronunciation_hash}` · "
           f"tổng ~{sc.seconds / 60:.1f} phút", ""]
    for ss in sc.slides:
        if pages and ss.page_no not in pages:
            continue
        p = doc.page(ss.page_no)
        title = (p.title if p else None) or "(không có tiêu đề)"
        out += [f"## Trang {ss.page_no} · {title}", "",
                f"`{ss.slide_type}` · {len(ss.sentences)} câu · {ss.syllables} âm tiết · "
                f"~{ss.seconds:.0f}s · pass {ss.passes}"
                + (" · ✍️ người sửa" if ss.edited_by == "nguoi" else "")
                + (f" · 🚩 **{', '.join(ss.flags)}**" if ss.flags else ""), ""]
        for s in ss.sentences:
            if s.kind == "delivery":
                out.append(f"> *{s.text}*  ")
                out.append(f"> <sub>delivery · {s.syllables} ât</sub>")
            else:
                ref = s.grounding.ref if s.grounding and s.grounding.ref else "🚩 KHÔNG GROUNDING"
                out.append(f"{s.text}  ")
                out.append(f"<sub>content · {s.syllables} ât · → `{ref}`</sub>")
            out.append("")
        if ss.unknown_terms:
            out += [f"*Từ chưa có cách đọc:* {', '.join(ss.unknown_terms)}", ""]
    return "\n".join(out)


def report(sc: Scenario) -> None:
    S = [s for ss in sc.slides for s in ss.sentences]
    content = [s for s in S if s.kind == "content"]
    ungrounded = [s for s in content if not (s.grounding and s.grounding.ref)]
    tot = sum(s.syllables for s in S) or 1
    deliv = sum(s.syllables for s in S if s.kind == "delivery")
    red = [ss.page_no for ss in sc.slides if ss.red_flags]
    p2 = [ss.page_no for ss in sc.slides if ss.passes == 2]
    unk = sorted({t for ss in sc.slides for t in ss.unknown_terms})

    log.info("")
    log.info("=== %s · %d trang · model %s", sc.doc_id, len(sc.slides), sc.model)
    log.info("    tổng thời lượng       %.1f phút  (%d âm tiết ÷ 200/phút)", sc.seconds / 60, tot)
    log.info("    ungrounded (content)  %d/%d = %.1f%%   gate < 10%%",
             len(ungrounded), len(content), len(ungrounded) / max(len(content), 1) * 100)
    log.info("    tỉ lệ delivery        %.1f%%           gate 10–25%%", deliv / tot * 100)
    if len(S) > 1:
        log.info("    độ lệch chuẩn âm tiết %.1f            gate >= 6",
                 statistics.pstdev([s.syllables for s in S]))
    log.info("    phải chạy pass 2      %d trang %s", len(p2), p2 or "")
    log.info("    CỜ ĐỎ                 %d trang %s", len(red), red or "")
    log.info("    từ chưa có cách đọc   %s", unk or "không")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scenario")
    ap.add_argument("parsed", help="out/parsed/<doc_id>/document.json")
    ap.add_argument("--pron", default=str(STORE), help="kho phát âm chung, mặc định data/pronunciation.json")
    ap.add_argument("-o", "--out", default=None, help="mặc định out/deck/<doc_id>/scenario.json")
    ap.add_argument("--page", default=None, help="'11' | '9,11,20' | '9-15'")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--force", action="store_true", help="bỏ qua incremental, viết lại")
    ap.add_argument("--dry-run", action="store_true", help="in prompt, không gọi API")
    ap.add_argument("--show", action="store_true", help="chỉ in kịch bản đã có")
    ap.add_argument("--md", action="store_true",
                    help="CHỈ ghi lại scenario.md, không gọi API — chạy thường đã tự ghi; "
                         "dùng sau khi sửa tay scenario.json")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    doc = ParsedDocument.load(args.parsed)
    deck_dir = ROOT / "out" / "deck" / doc.doc_id
    out = Path(args.out) if args.out else deck_dir / "scenario.json"
    pron = Pronunciation.load(args.pron)
    pages = set(parse_pages(args.page)) if args.page else None

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
