"""Bước ②b — chép lại từng bảng bằng VLM trên ảnh cắt nét cao -> `tables.json`.

Để làm gì: ở bước ① VLM nhìn cả trang ở độ phân giải thấp, hay đếm sai cột của bảng.
Cách làm: cắt đúng vùng bảng (thêm lề), vẽ lại ~2000px bề ngang, nhờ VLM chép thành JSON
{"columns", "rows"}. Cache theo từng bảng; bảng lỗi thì giữ bản của bước ①.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from utils.config import PROMPTS_DIR, ROOT
from utils.llm import LLM
from offline.parser.vlm import (
    FENCE,
    add_margin,
    create_vlm_client,
    region_size_pt,
    render_region,
    to_data_url,
    user_message,
)
from models.parsed_document import ParsedDocument, ParsedTable, norm_lines, norm_text

log = logging.getLogger(__name__)

TABLE_PROMPT = PROMPTS_DIR / "s0_vlm_table_prompt.md"
LOG_DIR = ROOT / "logs" / "vlm_tables"

TARGET_WIDTH_PX = 2000   # bề ngang ảnh cắt của bảng
MARGIN = 0.015           # lề quanh bbox, tính theo bề trang (bbox của Gemma hay hụt mép)
MAX_TOKENS = 8192
CONCURRENCY = 8
_TABLE = re.compile(r"<table\b.*</table>", re.S | re.I)
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def _crop_png(pdf: Any, page_no: int, box: tuple[float, float, float, float]) -> str:
    """Cắt vùng bảng (thêm lề) từ PDF, vẽ sao cho bề ngang bảng ~2000px -> ảnh PNG dạng data URL."""
    page = pdf[page_no - 1]
    region = add_margin(box, MARGIN)
    width_pt, _ = region_size_pt(page, region)
    scale = min(max(TARGET_WIDTH_PX / max(width_pt, 1.0), 2.0), 8.0)
    return to_data_url(render_region(page, region, scale))


def parse_table(content: str) -> list[list[str]]:
    """Đọc phản hồi của VLM thành lưới ô: hàng đầu = `columns`, sau đó là `rows`.

    Cách làm: đọc JSON (sửa escape lỗi nếu cần), mỗi hàng đệm / cắt cho đúng số cột; VLM lỡ trả
    HTML thì đọc HTML; đọc không được hoặc < 2 hàng thì báo lỗi.
    """
    body = FENCE.sub("", content).strip()
    data: Any = None
    for text in (body, _BAD_ESCAPE.sub(r"\\\\", body)):
        try:
            data = json.loads(text[text.find("{"): text.rfind("}") + 1])
            break
        except (json.JSONDecodeError, ValueError):
            continue
    if isinstance(data, dict) and isinstance(data.get("columns"), list) and isinstance(data.get("rows"), list):
        cols = [norm_lines(str(c)) for c in data["columns"]]
        n = len(cols)
        rows = [[norm_lines(str(c)) for c in r][:n] + [""] * (n - len(r))
                for r in data["rows"] if isinstance(r, list)]
        cells = [cols, *rows]
    elif m := _TABLE.search(content):
        from docling.utils.chandra_utils import _parse_table_html
        cells = [[norm_text(c.text) for c in row] for row in _parse_table_html(m.group(0)).grid]
    else:
        raise ValueError("khong doc duoc bang (JSON / HTML)")
    if len(cells) < 2 or max((len(r) for r in cells), default=0) < 2:
        raise ValueError(f"luoi qua nho {len(cells)} hang")
    return cells


def _key(page_hash: str, block_id: str, prompt_sha: str, model: str) -> str:
    """Khoá cache của một bảng: hash(nội dung trang + id bảng + prompt + model)."""
    return hashlib.sha256(f"{page_hash}|{block_id}|{prompt_sha}|{model}".encode()).hexdigest()[:16]


async def _ask(llm: LLM, sem: asyncio.Semaphore, prompt: str, block_id: str,
               data_url: str) -> dict[str, Any]:
    """Gửi ảnh một bảng cho VLM -> {"cells", "shape"}, lỗi thì {"error"}."""
    async with sem:
        try:
            content = await llm.chat(user_message([data_url], prompt), tag=block_id, raw=True)
            cells = parse_table(content)
        except Exception as e:                                   # mạng / JSON hỏng -> giữ bảng ①
            log.warning("  bang %s: %s", block_id, e)
            return {"error": str(e)[:200]}
    return {"cells": cells, "shape": f"{len(cells)}x{max(len(r) for r in cells)}"}


async def _run(layout: ParsedDocument, pdf_path: Path, out_json: Path, model: str, redo: bool,
               pages: set[int] | None) -> dict:
    """Chép lại mọi bảng (hoặc chỉ bảng ở `pages`) rồi ghi `tables.json`.

    Cách làm: bảng có trong cache và khoá không đổi thì dùng lại; còn lại cắt ảnh trước (pypdfium2
    không chạy song song được), rồi gọi VLM song song.
    """
    import pypdfium2 as pdfium

    prompt = TABLE_PROMPT.read_text(encoding="utf-8")
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    old = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else {}
    old_tables = old.get("tables", {})

    tables: dict[str, Any] = {}
    todo: list[tuple[str, int, str, str]] = []                   # (block_id, page_no, key, data_url)
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for page in layout.pages:
            for t in page.tables:
                key = _key(page.cache_hash(), t.id, prompt_sha, model)
                prev = old_tables.get(t.id)
                chosen = pages is None or page.page_no in pages
                if prev and (not chosen or (prev.get("key") == key and not prev.get("error") and not redo)):
                    tables[t.id] = prev                          # cache / trang không được chọn
                    continue
                if not chosen:
                    continue
                # pypdfium2 không an toàn đa luồng -> cắt hết ảnh TRƯỚC, rồi mới gọi song song
                todo.append((t.id, page.page_no, key, _crop_png(pdf, page.page_no, t.box)))
    finally:
        pdf.close()

    if todo:
        llm = create_vlm_client(model, LOG_DIR / out_json.parent.name, max_tokens=MAX_TOKENS)
        sem = asyncio.Semaphore(CONCURRENCY)
        log.info("②b chep bang: %d bang goi %s, %d bang dung cache", len(todo), model, len(tables))
        try:
            results = await asyncio.gather(*(_ask(llm, sem, prompt, bid, url) for bid, _, _, url in todo))
        finally:
            await llm.close()
        for (bid, no, key, _), res in zip(todo, results):
            tables[bid] = {"page": no, "key": key, **res}
    else:
        log.info("②b chep bang: %d bang deu dung cache (khong goi VLM)", len(tables))

    data = {"doc_id": layout.doc_id, "model": model, "prompt": TABLE_PROMPT.name,
            "prompt_sha256": prompt_sha, "tables": dict(sorted(tables.items()))}
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    errors = [bid for bid, t in tables.items() if t.get("error")]
    log.info("  bang: %d chep duoc, loi %s -> %s", len(tables) - len(errors), errors or "khong", out_json)
    return data


def run_tables(layout: ParsedDocument, pdf_path: Path, out_json: Path, model: str, *,
               redo: bool = False, pages: set[int] | None = None) -> dict:
    """Bước ②b: chép lại các bảng của doc -> nội dung `tables.json` (gọi `_run`)."""
    return asyncio.run(_run(layout, pdf_path, out_json, model, redo, pages))


def apply_tables(doc: ParsedDocument, tables_json: Path) -> Counter[str]:
    """Thay ô của các bảng trong doc bằng kết quả ②b, theo id bảng.

    Cách làm: dựng lại ParsedTable mới (để `content` tính lại); bảng ②b lỗi thì giữ bảng của bước ①.
    """
    data = json.loads(tables_json.read_text(encoding="utf-8")).get("tables", {})
    out: Counter[str] = Counter()
    for page in doc.pages:
        touched = False
        for i, b in enumerate(page.blocks):
            if not isinstance(b, ParsedTable):
                continue
            res = data.get(b.id)
            if not res or res.get("error") or not res.get("cells"):
                out["giu_bang_buoc_1"] += 1
                continue
            page.blocks[i] = ParsedTable(                      # dựng lại: fill_down + content tính lại
                id=b.id, page_no=b.page_no, polygon=b.polygon, caption=b.caption, cells=res["cells"])
            out["chep_lai_buoc_2b"] += 1
            touched = True
        if touched:
            page.page_hash = page.compute_hash()
    return out
