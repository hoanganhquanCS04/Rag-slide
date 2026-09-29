"""S6a — deck_map.txt: bản đồ bộ slide ~150 token cho prompt runtime.

Runtime KHÔNG nhồi cả deck vào prompt (§10). Nhưng LLM cần biết bộ slide có những phần
nào để hiểu "quay lại phần đồ thị ba chiều" hay "phần bài tập". deck_map là TRẠNG THÁI
gọn (danh sách chương + khoảng trang), không phải tri thức — tri thức vẫn phải tìm.

    python src/kb/deck_map.py out/parsed/<ten>/document.json      -> out/deck/<ten>/deck_map.txt

Dựng bằng LUẬT từ `sections` + `slide_type`, không gọi model:
    có chương   -> mỗi chương một dòng, kèm khoảng trang; trang ngoài chương gom "Mở đầu"
    không chương -> mỗi trang một dòng: tiêu đề, không có thì dòng chữ đầu tiên
"""

from __future__ import annotations

import argparse
import logging
import sys
from itertools import groupby
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb.chunk import count_tokens
from parsing.models import ParsedDocument, ParsedPage

log = logging.getLogger("deck_map")

LINE_CHARS = 48          # cắt dòng chữ đầu khi trang không có tiêu đề


def _span(pages: list[ParsedPage]) -> str:
    a, b = pages[0].page_no, pages[-1].page_no
    return f"trang {a}" if a == b else f"trang {a}–{b}"


def page_label(page: ParsedPage) -> str:
    """Tiêu đề; không có thì dòng CHỮ đầu tiên trên slide — mô tả ảnh chỉ là đường lui
    cuối (nó là lời VLM tả, không phải tên trang mà khán giả nhìn thấy)."""
    if page.title:
        return page.title
    first = next((b.content for b in page.paragraphs if b.content), "") or \
        next((b.content for b in page.blocks if b.content), "")
    first = first.split("\n")[0].strip()
    return first[:LINE_CHARS] + ("…" if len(first) > LINE_CHARS else "") or "(không có chữ)"


def build(doc: ParsedDocument) -> str:
    lines = [f"Bộ slide {doc.doc_id} · {doc.n_pages} trang"]
    if not doc.sections:
        lines += [f"- trang {p.page_no}: {page_label(p)}" for p in doc.pages]
        return "\n".join(lines) + "\n"

    for sec_id, group in groupby(doc.pages, key=lambda p: p.section_id):
        pages = list(group)
        sec = next((s for s in doc.sections if s.id == sec_id), None)
        name = sec.title if sec else ("Mở đầu" if pages[0].page_no == 1 else "Phần khác")
        extra = [p for p in pages if p.slide_type == "exercise"]
        note = f" (bài tập: {_span(extra)})" if extra else ""
        lines.append(f"- {_span(pages)}: {name}{note}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deck_map")
    ap.add_argument("parsed", help="out/parsed/<ten>/document.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    doc = ParsedDocument.load(args.parsed)
    text = build(doc)
    out = Path("out/deck") / doc.doc_id / "deck_map.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    log.info("%s", text.rstrip())
    log.info("ghi -> %s (%d token)", out, count_tokens(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
