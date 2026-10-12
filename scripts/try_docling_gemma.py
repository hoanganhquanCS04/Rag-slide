"""Chạy S0 (docling VlmPipeline + Gemma -> document.json) rồi VẼ khung từng trang để soi bằng mắt.

Lớp mỏng: không có logic VLM / parse riêng — gọi đúng `src/parser/cli.py` (= `parser/cli.py <file> --redo`), nên ảnh
khung chính là document.json mà KB / kịch bản đọc.

    python scripts/try_docling_gemma.py "data/raw/3_DataVisualization (1).pdf"
    python scripts/try_docling_gemma.py data/raw/thoi_gian_lam_viec.pdf --pages 3-5 --draw-only

Ghi:
  out/parsed/<doc_id>/docling_vlmpipeline.json · document.json     (ĐÈ bản chính, như parser/cli.py --redo)
  logs/vlm_pipeline/<doc_id>/pNNN.txt                               phản hồi thô của Gemma
  out/try_docling_gemma/<doc_id>/pages/pNNN.png                     khung = polygon block, số = thứ tự
  out/try_docling_gemma/<doc_id>/sheet_NN.png                       8 trang một tấm để lướt nhanh
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pypdfium2 as pdfium  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from parser.cli import parse_file, parse_pages  # noqa: E402
from parsing.enrich import enrich, page_pdf  # noqa: E402
from parsing.models import ParsedDocument, ParsedPage, ParsedParagraph, slugify_doc_id  # noqa: E402

load_dotenv(ROOT / ".env")     # VLM_MODEL làm mặc định cho --model bên dưới

log = logging.getLogger("try_docling_gemma")

COLORS = {
    "title": (220, 40, 40), "body": (30, 110, 220), "list": (0, 160, 160), "links": (120, 80, 40),
    "caption": (100, 100, 100), "table": (150, 60, 200), "image": (40, 170, 40),
}
GRAY = (160, 160, 160)


def draw_page(pdf: pdfium.PdfDocument, page: ParsedPage, scale: float) -> Image.Image:
    img = pdf[page.page_no - 1].render(scale=scale).to_pil().convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.load_default(size=max(12, int(7 * scale)))
    for i, b in enumerate(page.blocks, 1):
        tag = b.role if isinstance(b, ParsedParagraph) else b.kind
        c = COLORS.get(tag, (0, 0, 0)) if b.content else GRAY      # ảnh không mô tả: xám
        l, t, r, bt = b.box
        box = [l * W, t * H, max(r * W, l * W + 1), max(bt * H, t * H + 1)]
        draw.rectangle(box, outline=c + (255,), fill=c + (28,), width=3)
        label = f"{i} {tag}"
        draw.rectangle([box[0], box[1], box[0] + font.getlength(label) + 6, box[1] + font.size + 4],
                       fill=c + (230,))
        draw.text((box[0] + 3, box[1] + 1), label, fill=(255, 255, 255), font=font)
    return img


def contact_sheets(pages: list[Path], out_dir: Path, per: int = 8, width: int = 900) -> None:
    for k in range(0, len(pages), per):
        imgs = [Image.open(p) for p in pages[k:k + per]]
        thumbs = [im.resize((width, int(im.height * width / im.width))) for im in imgs]
        th = max(t.height for t in thumbs)
        sheet = Image.new("RGB", (width * 2 + 10, (th + 10) * ((len(thumbs) + 1) // 2)), "white")
        for j, t in enumerate(thumbs):
            sheet.paste(t, ((j % 2) * (width + 10), (j // 2) * (th + 10)))
        sheet.save(out_dir / f"sheet_{k // per + 1:02d}.png")


def main() -> int:
    ap = argparse.ArgumentParser(description="S0 VlmPipeline + Gemma -> document.json, roi ve khung")
    ap.add_argument("pdf", help="data/raw/<ten>.pdf | .pptx (can .pdf cung ten)")
    ap.add_argument("--model", default=os.environ.get("VLM_MODEL", "gemma4"))
    ap.add_argument("--pages", default=None, help="chi VE cac trang nay: '18-19' | '5' | '3,7'")
    ap.add_argument("--draw-only", action="store_true", help="khong goi Gemma, ve tu document.json co san")
    ap.add_argument("--out", default=str(ROOT / "out" / "try_docling_gemma"))
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name in ("httpx", "urllib3", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)

    src = Path(args.pdf)
    if not src.is_file():
        raise SystemExit(f"khong thay file: {src}  (file goc nam o data/raw/)")
    pdf_path = page_pdf(src)
    if pdf_path is None:
        raise SystemExit(f"khong thay {src.with_suffix('.pdf').name} de ve trang")
    doc_id = slugify_doc_id(src.stem)
    d = ROOT / "out" / "parsed" / doc_id

    code = 0
    if not args.draw_only:                                   # = parser/cli.py <file> --redo
        code = parse_file(src, redo=True, model=args.model)
    doc = enrich(ParsedDocument.load(d / "document.json"))

    out = Path(args.out) / doc_id
    (out / "pages").mkdir(parents=True, exist_ok=True)
    wanted = set(parse_pages(args.pages)) if args.pages else None
    pdf = pdfium.PdfDocument(str(pdf_path))
    drawn = []
    try:
        for page in doc.pages:
            if wanted is None or page.page_no in wanted:
                path = out / "pages" / f"p{page.page_no:03d}.png"
                draw_page(pdf, page, scale=2.0).save(path)
                drawn.append(path)
    finally:
        pdf.close()
    if drawn:
        contact_sheets(drawn, out)
    log.info("ve %d trang -> %s", len(drawn), out)
    return code


if __name__ == "__main__":
    sys.exit(main())
