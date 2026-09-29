"""Vá tay những chỗ parser vẫn sai sau cả docling lẫn VLM.

Vì sao cần: có nội dung không đường nào đọc đúng được. Ví dụ đo được — trang 15 của
`3_DataVisualization` có đoạn code `mpl.colors.cnames.items()` và bảng tên màu là ảnh chụp
màn hình mà layout model không nhận ra là ảnh. Bật OCR thì hỏng dấu tiếng Việt ("Liệt kê"
-> "Lit kê") và chậm 8.4×. Vài trang lẻ thì người gõ tay là rẻ nhất và đúng nhất.

KHÔNG sửa thẳng vào `out/parsed/<doc_id>/document.json` — dựng lại là mất. Patch nằm ở file riêng,
áp lại MỖI lần dựng (`build.py`), nên bền qua mọi lần chạy lại. `document.json` luôn dựng mới
từ `docling.json` nên patch không bao giờ bị áp chồng hai lần.

Block vá vào mang `provenance: "manual"` — tin được như `text_layer`, và truy được là người
gõ chứ không phải model sinh (NT2).

    data/patches/<doc_id_bat_ky_phan_nao_cua_ten>.json
    {
      "note": "vi sao phai va",
      "pages": {
        "15": {
          "note": "docling bo sot code + bang mau",
          "drop_blocks": ["p015.v01"],
          "add_blocks": [
            {"role": "body", "text": "...", "bbox": [l, t, r, b]},
            {"cells": [["DO", "DON'T"], ["...", "..."]], "bbox": [l, t, r, b]}
          ],
          "fix_images": {"p015.v00": "mô tả đúng, người viết lại"}
        }
      }
    }

`drop_blocks` bỏ block parse SAI. `add_blocks` nối vào CUỐI trang; có `cells` là BẢNG (hàng
đầu là header), chữ chép từ text layer. `fix_images` thay mô tả VLM tả sai; chuỗi rỗng "" =
ảnh trang trí, bị bỏ khỏi file. Id block lấy bằng `cli.py show <doc_id> --page N --full` —
`vNN` là khối VLM sắp, `bNN` là khối docling (trang không có bố cục VLM).
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
    ParsedTable,
    Provenance,
    polygon_from_box,
)

log = logging.getLogger(__name__)

PATCH_DIR = Path(__file__).resolve().parents[2] / "data" / "patches"


def find_patch(doc_id: str, patch_dir: Path = PATCH_DIR) -> Path | None:
    """Tìm file patch hợp với doc_id. Khớp lỏng vì doc_id hay dài và bẩn."""
    key = doc_id.lower()
    return next((f for f in sorted(patch_dir.glob("*.json")) if f.stem.lower() in key), None)


def apply_patch(doc: ParsedDocument, patch: dict[str, Any]) -> None:
    """Bỏ / thêm block, sửa mô tả ảnh. Sửa tại chỗ. Id sai thì BÁO — lờ đi là lỗi vẫn nằm đó."""
    n_drop = n_add = n_fix = 0
    for raw_no, spec in (patch.get("pages") or {}).items():
        page = doc.page(int(raw_no))
        if page is None:
            log.warning("  patch: khong co trang %s, bo qua", raw_no)
            continue

        drop = set(spec.get("drop_blocks") or [])
        if missing := drop - {b.id for b in page.blocks}:
            log.warning("  patch: trang %s khong co block %s", raw_no, sorted(missing))
        page.blocks = [b for b in page.blocks if b.id not in drop]
        n_drop += len(drop - missing)

        for i, b in enumerate(spec.get("add_blocks") or []):
            kw = {"id": f"p{page.page_no:03d}.m{i:02d}",          # m = manual
                  "polygon": polygon_from_box(*(b.get("bbox") or [0.05, 0.20, 0.95, 0.90])),
                  "provenance": Provenance.MANUAL}
            page.blocks.append(
                ParsedTable(cells=b["cells"], structure_provenance=Provenance.MANUAL, **kw)
                if "cells" in b else
                ParsedParagraph(role=b.get("role", "body"), content=b.get("text", "").strip(), **kw)
            )
            n_add += 1

        images = {b.id: b for b in page.blocks if isinstance(b, ParsedImage)}
        for bid, desc in (spec.get("fix_images") or {}).items():
            if (im := images.get(bid)) is None:
                log.warning("  patch: trang %s khong co anh %s", raw_no, bid)
                continue
            im.content = desc.strip() or None
            im.why_empty = None if desc.strip() else "decorative"
            im.provenance = Provenance.MANUAL
            n_fix += 1

        page.page_hash = page.compute_hash()   # vá xong hash đổi -> S4 biết trang này khác

    log.info("  patch: bo %d block, them %d block, sua %d mo ta anh", n_drop, n_add, n_fix)


def load_patch(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
