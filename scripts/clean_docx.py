"""Dọn file .docx ngay ở XML, trước khi đưa cho docling. Hai việc:

  1. Ảnh công thức -> chữ giữ chỗ ⟦eq:image25⟧ ĐÚNG vị trí trong câu.
     docling nhấc ảnh giữa câu ra thành khối riêng -> câu thủng lỗ, mất vị trí.
     File ảnh KHÔNG bị xoá: vẫn nằm ở word/media/image25.png, tên ảnh = phần sau "eq:".
     Ảnh cao hơn một dòng chữ thì coi là hình thật, để nguyên và cảnh báo.

  2. Xoá số trích dẫn: run chữ số mũ (vertAlign=superscript) chỉ chứa chữ số.
     Bắt theo ĐỊNH DẠNG, không đoán bằng regex trên text -> không xoá nhầm "(1964)".

    python scripts/clean_docx.py data/raw/paper_finance.docx
      -> out/sources/paper_finance.clean.docx

File gốc không bị sửa.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

from lxml import etree

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("clean_docx")

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}
W_T = f"{{{NS['w']}}}t"
W_DRAWING = f"{{{NS['w']}}}drawing"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

DOC_XML = "word/document.xml"
RELS_XML = "word/_rels/document.xml.rels"
DIGITS = re.compile(r"^\s*\d+(\s*,\s*\d+)*\s*$")   # "1" | "19" | "1, 7"
MAX_FORMULA_EMU = 457_200   # 0.5 inch. Công thức trong paper_finance cao 0.26 inch


def media_by_rid(zf: zipfile.ZipFile) -> dict[str, str]:
    """rId6 -> media/image25.png"""
    rels = etree.fromstring(zf.read(RELS_XML))
    return {r.get("Id"): r.get("Target") for r in rels}


def drop_citations(root: etree._Element) -> Counter[str]:
    dropped: Counter[str] = Counter()
    for run in root.xpath(".//w:r[w:rPr/w:vertAlign[@w:val='superscript']]", namespaces=NS):
        text = "".join(run.xpath("./w:t/text()", namespaces=NS))
        if DIGITS.match(text):
            run.getparent().remove(run)
            dropped[text.strip()] += 1
    return dropped


def replace_formulas(root: etree._Element, rid2media: dict[str, str]) -> tuple[Counter[str], int]:
    """-> (số lần chèn mỗi ảnh công thức, số ảnh cao bị bỏ qua)."""
    used: Counter[str] = Counter()
    skipped = 0
    for run in root.xpath(".//w:r[w:drawing]", namespaces=NS):
        rid = run.xpath(".//a:blip/@r:embed", namespaces=NS)
        cy = run.xpath(".//wp:extent/@cy", namespaces=NS)
        if len(rid) != 1 or not cy:
            log.warning("bo qua 1 drawing khong phai anh don (blip=%d)", len(rid))
            skipped += 1
            continue
        eq_id = Path(rid2media[rid[0]]).stem
        if int(cy[0]) > MAX_FORMULA_EMU:
            log.warning("bo qua %s: cao %.2f inch -> co the la hinh that, khong phai cong thuc",
                        eq_id, int(cy[0]) / 914_400)
            skipped += 1
            continue
        for d in run.findall(W_DRAWING):
            run.remove(d)
        t = etree.SubElement(run, W_T)
        t.set(XML_SPACE, "preserve")
        t.text = f"⟦eq:{eq_id}⟧"
        used[eq_id] += 1
    return used, skipped


def clean(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        raise SystemExit("file ra trung file goc — khong ghi de file goc")
    with zipfile.ZipFile(src) as zin:
        root = etree.fromstring(zin.read(DOC_XML))
        dropped = drop_citations(root)
        used, skipped = replace_formulas(root, media_by_rid(zin))
        xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

        dst.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dst, "w") as zout:
            for item in zin.infolist():
                zout.writestr(item, xml if item.filename == DOC_XML else zin.read(item.filename))

    log.info("%s -> %s", src, dst)
    log.info("  so trich dan da xoa : %d  (gia tri: %s)", sum(dropped.values()),
             ", ".join(sorted(dropped, key=int)))
    log.info("  anh cong thuc       : %d cho chen, %d anh khac nhau -> ⟦eq:...⟧",
             sum(used.values()), len(used))
    if skipped:
        log.info("  anh de nguyen       : %d (xem canh bao o tren)", skipped)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Don .docx: anh cong thuc -> cho giu cho, xoa so trich dan")
    ap.add_argument("docx", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="mac dinh out/sources/<ten>.clean.docx")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    clean(args.docx, args.out or Path("out/sources") / f"{args.docx.stem}.clean.docx")


if __name__ == "__main__":
    main()
