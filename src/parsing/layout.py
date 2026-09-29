"""② VLM nhìn CẢ trang, sắp mẩu chữ docling thành khối -> `out/parsed/<doc_id>/layout.json`.

Vì sao: docling xử lý từng vùng riêng lẻ nên băm vụn chữ trong sơ đồ (Onboarding p5: 21 mẩu
1–2 chữ), trộn dòng hai cột (p7 DO/DON'T; p10 đảo nghĩa quy định xe xăng), và không biết ảnh
nào chỉ để trang trí. Nhìn cả trang thì thấy.

Chia việc (NT2):

    docling  CHỮ     mẩu chữ đánh số T1, T2… (text layer, đúng 100%) + vùng ảnh P / bảng B
    VLM      BỐ CỤC  nhìn ảnh cả trang, trả JSON TRỎ ID — không chép lại chữ
    code     GHÉP    id -> chữ nguyên văn, rồi KIỂM: sót id, id bịa, vùng bị bỏ quên

Chữ VLM tự viết chỉ có ở chỗ docling không có chữ (ảnh chụp bảng, mô tả sơ đồ) -> `vlm`.
Đo trên 7 trang khó của Onboarding: vụn 77 -> 1, phủ chữ 100%, 2 lần chạy ra giống nhau;
bảng OT (p20) và mã ưu đãi (p33) đọc từ ảnh khớp từng số.

`layout.json` chỉ lưu phản hồi thô của VLM, mỗi trang một mục:

    {"pages": {"7": {"key": "<sha>", "model": "...", "sec": 4.1, "response": {...}}}}

`key` = hash(model + prompt + mẩu chữ của trang). Chạy lại docling / sửa prompt / đổi model
-> khoá lệch -> trang đó không dùng bản cũ nữa. Ghép + kiểm (`apply_layout`) không gọi API.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import time
from pathlib import Path
from typing import Any

from parsing.from_docling import _box, _norm, _resolve
from parsing.models import (
    AnyBlock,
    ParsedDocument,
    ParsedImage,
    ParsedParagraph,
    ParsedTable,
    Provenance,
    polygon_from_box,
    table_markdown,
)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
PROMPT = ROOT / "prompts" / "s0_page_layout.md"

Items = dict[str, dict[str, Any]]      # {"T1": {"kind": "T", "box": [...], "text": ...}, "P1": ...}
_FURNITURE = {"page_header", "page_footer"}


# ------------------------------------------------------------------ mẩu docling của một trang


def page_items(raw: dict[str, Any], page_no: int) -> Items:
    """Mẩu chữ / ảnh / bảng của trang theo thứ tự đọc, đánh số T / P / B.

    Đi vào MỌI group, kể cả list: cần từng gạch đầu dòng riêng thì VLM mới chia lại được cột
    (from_docling gộp cả list thành một block — p10 trộn hai nhóm quy định vào một list).
    """
    size = raw["pages"][str(page_no)]["size"]
    is_pptx = "presentationml" in ((raw.get("origin") or {}).get("mimetype") or "")
    out: Items = {}
    count = {"T": 0, "P": 0, "B": 0}
    seen: set[str] = set()

    def add(kind: str, **kw: Any) -> None:
        count[kind] += 1
        out[f"{kind}{count[kind]}"] = {"kind": kind, **kw}

    def walk(node: dict[str, Any]) -> None:
        for ref in node.get("children", []):
            item = _resolve(raw, ref["$ref"])
            if item is None or item.get("self_ref") in seen:
                continue
            seen.add(item.get("self_ref", ""))
            prov = item.get("prov") or []
            sref = item.get("self_ref", "")
            if (prov and prov[0]["page_no"] == page_no and item.get("label") not in _FURNITURE
                    and item.get("content_layer") != "furniture"):
                box = [round(x, 3) for x in _box(prov[0], size["width"], size["height"], is_pptx=is_pptx)]
                if sref.startswith("#/pictures/"):
                    add("P", box=box)
                elif sref.startswith("#/tables/"):
                    grid = (item.get("data") or {}).get("grid") or []
                    cells = [[_norm(c.get("text")) for c in row] for row in grid]
                    add("B", box=box, cells=cells,
                        text=table_markdown(cells) if any(any(r) for r in cells) else None)
                elif text := _norm(item.get("text")):
                    add("T", box=box, text=text)
            walk(item)

    walk(raw.get("body") or {})
    return out


def items_prompt(page_no: int, items: Items) -> str:
    """Phần chữ gửi kèm ảnh trang. Cũng là đầu vào của khoá cache."""
    lines = [f"Trang {page_no}.", "", "MẨU CHỮ:"]
    lines += [f"{k} {v['box']} {v['text']}" for k, v in items.items() if v["kind"] == "T"]
    if ps := [k for k, v in items.items() if v["kind"] == "P"]:
        lines += ["", "VÙNG ẢNH:"]
        for k in ps:
            b = items[k]["box"]
            lines.append(f"{k} {b} chiếm {(b[2] - b[0]) * (b[3] - b[1]):.0%} trang")
    if bs := [k for k, v in items.items() if v["kind"] == "B"]:
        lines += ["", "VÙNG BẢNG:"]
        for k in bs:
            v = items[k]
            body = v["text"].replace("\n", " ⏎ ") if v["text"] else "(rỗng — không đọc được chữ nào)"
            lines.append(f"{k} {v['box']} {body}")
    return "\n".join(lines)


def page_pdf(src: Path) -> Path | None:
    """PDF để vẽ ảnh trang (và đọc link ẩn). `.pptx`: docling không vẽ slide ra ảnh -> dùng PDF
    CÙNG TÊN xuất từ PowerPoint (`scripts/pptx2pdf.ps1`)."""
    if src.suffix.lower() == ".pdf":
        return src
    pdf = src.with_suffix(".pdf")
    return pdf if pdf.exists() else None


def _jpeg_b64(page: Any, width_px: int = 1600) -> str:
    img = page.render(scale=width_px / page.get_size()[0]).to_pil().convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _key(model: str, prompt: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{prompt}\n{text}".encode()).hexdigest()[:16]


def load_layout(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"pages": {}}


# ------------------------------------------------------------------ gọi VLM


async def run_layout(raw: dict[str, Any], pdf: Path, layout_path: Path, *, tag: str, model: str,
                     pages: list[int] | None = None, concurrency: int = 4) -> int:
    """Gọi VLM cho `pages` (ép gọi lại), hoặc mọi trang chưa có / đã cũ trong cache. -> số lần gọi.

    Trang gọi lỗi (hết retry) thì bỏ qua, không ghi — bước ③ dùng docling cho trang đó.
    """
    import pypdfium2 as pdfium

    from llm import LLM

    layout = load_layout(layout_path)
    prompt = PROMPT.read_text(encoding="utf-8")
    todo: list[tuple[int, str, str]] = []
    for no in pages or sorted(int(k) for k in raw["pages"]):
        text = items_prompt(no, page_items(raw, no))
        key = _key(model, prompt, text)
        if pages is None and layout["pages"].get(str(no), {}).get("key") == key:
            continue
        todo.append((no, text, key))
    if not todo:
        log.info("② layout: moi trang da co san, khong goi VLM")
        return 0

    pdf_doc = pdfium.PdfDocument(str(pdf))
    try:
        if len(pdf_doc) != len(raw["pages"]):
            # pptx có slide ẩn thì PDF xuất ra ít trang hơn -> ảnh trang lệch chữ
            log.warning("  layout: %s co %d trang, docling %d trang -> bo qua buoc ②",
                        pdf.name, len(pdf_doc), len(raw["pages"]))
            return 0
        images = {no: _jpeg_b64(pdf_doc[no - 1]) for no, _, _ in todo}
    finally:
        pdf_doc.close()

    log.info("② layout: goi %s cho %d trang ...", model, len(todo))
    llm = LLM(model, ROOT / "logs" / "layout", retry=3, timeout=180, extra={"temperature": 0})
    sem = asyncio.Semaphore(concurrency)

    async def one(no: int, text: str, key: str) -> None:
        msgs = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{images[no]}"}},
            ]},
        ]
        async with sem:
            t0 = time.perf_counter()
            resp = await llm.chat(msgs, tag=f"{tag}_p{no:03d}")
        layout["pages"][str(no)] = {"key": key, "model": model,
                                    "sec": round(time.perf_counter() - t0, 1), "response": resp}

    t0 = time.perf_counter()
    try:
        results = await asyncio.gather(*(one(*t) for t in todo), return_exceptions=True)
    finally:
        await llm.close()
    for (no, _, _), r in zip(todo, results):
        if isinstance(r, Exception):
            log.warning("  layout p%d: goi VLM loi, trang nay dung docling — %s", no, r)

    layout["pages"] = dict(sorted(layout["pages"].items(), key=lambda kv: int(kv[0])))
    layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if not isinstance(r, Exception))
    log.info("  %d/%d trang xong, %.0fs -> %s", ok, len(todo), time.perf_counter() - t0, layout_path)
    return len(todo)


# ------------------------------------------------------------------ ghép + kiểm


def _flat(x: Any) -> list[Any]:
    """VLM ghi id nhiều kiểu: "T4" · ["T4","T5"] · {"ids": [...]} · lồng nhau -> phẳng.
    `{"read": "..."}` (chữ VLM đọc từ ảnh) giữ nguyên để `take` nhận ra."""
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        return [i for y in x for i in _flat(y)]
    if isinstance(x, dict) and "read" in x:
        return [x]
    if isinstance(x, dict) and "ids" in x:
        return _flat(x["ids"])
    return [json.dumps(x, ensure_ascii=False)[:40]] if x else []


def assemble(resp: dict[str, Any], items: Items) -> tuple[list[dict[str, Any]], str | None]:
    """Phản hồi VLM -> (khối có chữ thật, lỗi). Không tin VLM: mọi id đều kiểm.

    Lỗi (-> trang dùng docling): mẩu chữ bị sót, id không tồn tại, vùng ảnh/bảng không được
    nhắc tới (p20 từng bỏ luôn bảng hệ số OT mà chữ vẫn phủ 100%).
    """
    used: set[str] = set()
    unknown: list[str] = []
    cur: list[str] = []                 # id T của khối đang ghép -> polygon
    read = False                        # khối đang ghép có chữ VLM tự đọc từ ảnh -> `vlm`

    def take(ids: Any) -> str:
        """id T -> chữ docling nguyên văn. `{"read": ...}` -> chữ VLM đọc từ ảnh, ở bất kỳ khối
        nào (Thời gian làm việc p7: 2 dòng lưu ý chỉ có trong ảnh, VLM đặt thành list)."""
        nonlocal read
        parts = []
        for i in _flat(ids):
            if isinstance(i, dict):
                parts.append(str(i["read"]))
                read = True
            elif items.get(i, {}).get("kind") == "T":
                used.add(i)
                cur.append(i)
                parts.append(items[i]["text"])
            else:
                unknown.append(i)
        return " ".join(p for p in parts if p)

    def cell(c: Any) -> str:
        """Ô bảng. VLM hay lồng list vào ô (Onboarding p7: cột DON'T 3 gạch)."""
        if isinstance(c, dict) and c.get("kind") == "list":
            return " ".join(f"• {take(it)}" for it in c.get("items") or [])
        return take(c)

    blocks: list[dict[str, Any]] = []
    for b in resp.get("blocks") or []:
        kind, source = b.get("kind"), b.get("source")
        cur.clear()
        read = False
        if kind in ("title", "text"):
            blk = {"kind": kind, "content": take(b.get("ids"))}
        elif kind == "list":
            lines = [x for it in b.get("items") or [] if (x := take(it))]
            blk = {"kind": "list", "content": "\n".join(lines)}
        elif kind == "table" and not b.get("rows"):            # giữ nguyên bảng docling
            if items.get(source, {}).get("kind") != "B":
                unknown.append(str(source))
                continue
            blk = {"kind": "table", "cells": items[source]["cells"]}
        elif kind == "table":
            blk = {"kind": "table", "cells": [[cell(c) for c in row] for row in b.get("rows") or []]}
        elif kind == "figure":
            take(b.get("ids"))
            read = True                                         # mô tả hình luôn do VLM viết
            blk = {"kind": "figure", "content": b.get("describe")}
        else:
            unknown.append(f"kind={kind}")
            continue
        if blk.get("content", True) or blk.get("cells"):       # bỏ khối rỗng ({"ids": []})
            blocks.append({**blk, "provenance": "vlm" if read else "text_layer",
                           "source": source, "ids": list(cur)})

    skip = set(resp.get("skip") or [])
    missing = [k for k, v in items.items() if v["kind"] == "T" and k not in used | skip]
    cited = set(resp.get("decorative") or []) | {b["source"] for b in blocks}
    forgotten = [k for k, v in items.items() if v["kind"] in ("P", "B") and k not in cited]

    err = []
    if missing:
        err.append(f"sot {len(missing)} manh chu ({', '.join(missing[:6])})")
    if unknown:
        err.append(f"id la {unknown[:6]}")
    if forgotten:
        err.append(f"bo quen vung {', '.join(forgotten)}")
    return blocks, "; ".join(err) or None


def _to_blocks(page_no: int, blocks: list[dict[str, Any]], items: Items) -> list[AnyBlock]:
    """Khối đã ghép -> Block. polygon = khung bao các mẩu T đã dùng + vùng nguồn P/B.
    Id `pNNN.vNN` (v = VLM sắp) — khác `bNN` của docling để patch không trỏ nhầm."""
    out: list[AnyBlock] = []
    for n, b in enumerate(blocks):
        boxes = [items[i]["box"] for i in b["ids"]]
        if b["source"] in items:
            boxes.append(items[b["source"]]["box"])
        box = (min(x[0] for x in boxes), min(x[1] for x in boxes),
               max(x[2] for x in boxes), max(x[3] for x in boxes)) if boxes else (0, 0, 1, 1)
        kw = {"id": f"p{page_no:03d}.v{n:02d}", "polygon": polygon_from_box(*box),
              "provenance": Provenance(b["provenance"])}
        if b["kind"] == "table":
            out.append(ParsedTable(cells=b["cells"], structure_provenance=Provenance.VLM, **kw))
        elif b["kind"] == "figure":
            out.append(ParsedImage(content=b["content"], **kw))
        else:
            role = {"title": "title", "text": "body", "list": "list"}[b["kind"]]
            out.append(ParsedParagraph(role=role, content=b["content"], **kw))
    return out


def apply_layout(doc: ParsedDocument, raw: dict[str, Any], layout: dict[str, Any]) -> None:
    """Thay block docling bằng khối VLM ở trang có bố cục hợp lệ.

    Trượt kiểm tra -> giữ block docling, ghi `page.layout_error` (-> cờ `layout_failed`).
    Bố cục cũ (khoá lệch) -> giữ docling, không cờ: chưa ai nhìn trang đó với dữ liệu mới.
    """
    prompt = PROMPT.read_text(encoding="utf-8")
    used, failed, stale = 0, [], []
    for page in doc.pages:
        entry = layout["pages"].get(str(page.page_no))
        if not entry:
            continue
        items = page_items(raw, page.page_no)
        if entry["key"] != _key(entry["model"], prompt, items_prompt(page.page_no, items)):
            stale.append(page.page_no)
            continue
        blocks, err = assemble(entry["response"], items)
        if err:
            page.layout_error = err
            failed.append(page.page_no)
            continue
        page.blocks = _to_blocks(page.page_no, blocks, items)
        page.title = next((b.content for b in page.paragraphs if b.role == "title"), None)
        page.page_hash = page.compute_hash()
        used += 1

    log.info("  bo cuc VLM: %d trang | truot kiem tra %s | cu, bo qua %s | con lai dung docling",
             used, failed or "-", stale or "-")
