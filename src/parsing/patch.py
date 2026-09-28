"""Vá nội dung bằng tay cho những trang parser bỏ sót.

Vì sao cần: docling có lúc bỏ sót cả một vùng nội dung. Ví dụ đo được — trang 15 của
`3_DataVisualization` có đoạn code `mpl.colors.cnames.items()` và cả bảng tên màu, mà
parse ra KHÔNG thấy chữ lẫn ảnh. Nội dung đó là ảnh chụp màn hình dán vào slide, và
layout model không nhận ra đó là ảnh nên đường VLM cũng không chạm tới.

Bật OCR thì đọc được nhưng hỏng dấu tiếng Việt ("Liệt kê" -> "Lit kê") và chậm 8.4×.
Nên với vài trang lẻ, người gõ tay là rẻ nhất và đúng nhất.

KHÔNG sửa thẳng vào `out/parsed/*.json` — chạy lại parse là mất. Patch nằm ở file
riêng, áp lại mỗi lần parse, nên bền qua mọi lần chạy lại.

Block vá vào mang `provenance: "manual"` — tin được như `text_layer`, và truy được là
người gõ chứ không phải model sinh (NT2).

File patch:

    data/patches/<doc_id_bat_ky_phan_nao_cua_ten>.json
    {
      "note": "vi sao phai va",
      "vlm_pages": [15, 31, 41],
      "pages": {
        "15": {
          "note": "docling bo sot code + bang mau",
          "add_blocks": [
            {"role": "body", "text": "...", "bbox": [l, t, r, b]}
          ],
          "fix_images": {
            "p015.b00": "mô tả đúng, người viết lại"
          }
        }
      }
    }

`fix_images` thay mô tả VLM tả SAI (vd ảnh Tết bị tả thành "mâm cỗ Trung Thu"). Key là
`block_id` — lấy bằng `python src/parsing/cli.py out/parsed/<ten>.json --page N --full`.
Chuỗi rỗng "" = ảnh trang trí, bỏ khỏi index. Ảnh sửa xong mang `provenance: "manual"`.

`vlm_pages` = NGƯỜI chọn trang nào được gọi VLM tả ảnh. Deck ảnh nền trang trí (Onboarding
Kit: gần như trang nào cũng có một ảnh phủ 100% trang) mà để docling tả hết thì tốn tiền và
nhét mô tả nền vào index. `scripts/parse_api.py` đọc CÙNG key này để chỉ gửi ảnh các trang đó
cho VLM; ở đây ảnh trang ngoài danh sách đổi `not_described` -> `skipped`, không bắn cờ đỏ.
Không có key = như cũ, tả mọi ảnh.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from parsing.models import (
    ParsedDocument,
    ParsedImage,
    ParsedParagraph,
    Provenance,
    polygon_from_box,
)

log = logging.getLogger(__name__)


def load_patch(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"khong thay file patch: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def find_patch(doc_id: str, patch_dir: str | Path = "data/patches") -> Path | None:
    """Tìm file patch hợp với doc_id. Khớp lỏng vì doc_id hay dài và bẩn."""
    d = Path(patch_dir)
    if not d.exists():
        return None
    key = doc_id.lower()
    for f in sorted(d.glob("*.json")):
        if f.stem.lower() in key:
            return f
    return None


def apply_patch(doc: ParsedDocument, patch: dict[str, Any]) -> ParsedDocument:
    """Thêm block / sửa mô tả ảnh. Sửa tại chỗ, trả về chính doc."""
    pages = patch.get("pages") or {}
    n_block = n_fix = 0

    # Áp lại nhiều lần phải ra như một lần: nạp lại file ĐÃ vá rồi vá tiếp là nhân đôi block
    # (đã dính: p015.m00/m01 thành 2 bản). File patch là nguồn DUY NHẤT của block vá -> xoá
    # mọi block vá cũ trước, kể cả của trang đã bị bỏ khỏi patch.
    for page in doc.pages:
        if any(".m" in b.id for b in page.blocks):
            page.blocks = [b for b in page.blocks if ".m" not in b.id]
            page.page_hash = page.compute_hash()

    n_skip = 0
    if patch.get("vlm_pages") is not None:
        keep = {int(p) for p in patch["vlm_pages"]}
        for page in doc.pages:
            if page.page_no in keep:
                continue
            for im in page.images:
                if im.why_empty == "not_described":
                    im.why_empty = "skipped"
                    n_skip += 1

    for raw_no, spec in pages.items():
        page = doc.page(int(raw_no))
        if page is None:
            log.warning("  patch: khong co trang %s, bo qua", raw_no)
            continue

        for i, b in enumerate(spec.get("add_blocks") or []):     # nối vào CUỐI trang
            page.blocks.append(
                ParsedParagraph(
                    id=f"p{page.page_no:03d}.m{i:02d}",     # m = manual
                    role=b.get("role", "body"),
                    content=b.get("text", "").strip(),
                    polygon=polygon_from_box(*(b.get("bbox") or [0.05, 0.20, 0.95, 0.90])),
                    provenance=Provenance.MANUAL,
                )
            )
            n_block += 1

        images = {b.id: b for b in page.blocks if isinstance(b, ParsedImage)}
        for bid, desc in (spec.get("fix_images") or {}).items():
            im = images.get(bid)
            if im is None:
                # block_id sai thì BÁO, không lờ đi — lờ đi là mô tả bịa vẫn nằm trong index
                log.warning("  patch: trang %s khong co anh %s, bo qua", raw_no, bid)
                continue
            im.content = desc.strip() or None
            im.why_empty = None if desc.strip() else "decorative"
            im.provenance = Provenance.MANUAL
            n_fix += 1

        page.page_hash = page.compute_hash()   # vá xong hash đổi -> S4 biết trang này khác

    log.info("  patch: them %d block, sua %d mo ta anh, tren %d trang, %d anh ngoai vlm_pages",
             n_block, n_fix, len(pages), n_skip)
    return doc
