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

    {"pages": {"7": {"key": "<sha>", "model": "...", "prompt_sha": "<sha>", "sec": 4.1, "response": {...}}}}

`key` = hash(model + mẩu chữ của trang). Chạy lại docling / đổi model -> khoá lệch -> trang đó
không dùng bản cũ nữa (id T1, T2… đã trỏ vào chữ khác). Sửa prompt thì KHÔNG: bố cục cũ vẫn
đúng, chỉ chưa hưởng luật mới — `prompt_sha` ghi lại bản prompt, log báo "prompt cũ", muốn áp
luật mới cho trang nào thì `cli.py run <file> --pages N`. Không thế thì sửa một dòng prompt là
cả deck rơi về docling cho tới khi gọi lại VLM hết.

Cache ghi ngay sau MỖI trang: chạy cả deck mà đứt giữa chừng vẫn giữ trang đã xong (bản cũ ghi
một lần cuối lượt -> Onboarding mất 43/51 trang). Ghép + kiểm (`apply_layout`) không gọi API.

②b BẢNG — cắt ảnh từng bảng, VLM chép ra `cells` (prompts/s0_table.md), chữ nắn về text layer.
Bảng ô gộp (Onboarding p21–23) mà để VLM dựng trong lượt cả trang thì hỏng: nó chép theo chữ
docling (đã gán nhầm ô: "vi phạm" sang ô "Không lỗi"), không có luật ô gộp nên ô cao chỉ ghi ở
hàng đầu. Cache `layout.json["tables"]`, khoá = model + id + khung bảng (sửa prompt KHÔNG gọi
lại, như trang). `run_tables` gọi API, `apply_tables` ghép — không gọi API.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
import time
from difflib import SequenceMatcher
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
TABLE_PROMPT = ROOT / "prompts" / "s0_table.md"

Items = dict[str, dict[str, Any]]      # {"T1": {"kind": "T", "box": [...], "text": ...}, "P1": ...}
# `read` chỉ gồm dấu đầu dòng / dấu câu: không phải chữ, để lại thì cả khối bị gắn `vlm` oan
BULLET_ONLY = re.compile(r"[\s·•●○▪■◦‣∙⁃.\-–—*✓✔❖➢>:;,]*")
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


def _key(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()[:16]


def _prompt_sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def load_layout(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"pages": {}}


def save_layout(layout: dict[str, Any], path: Path) -> None:
    """Ghi qua file tạm rồi đổi tên — đứt giữa lúc ghi cũng không hỏng cache đang có."""
    layout["pages"] = dict(sorted(layout["pages"].items(), key=lambda kv: int(kv[0])))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ gọi VLM


async def run_layout(raw: dict[str, Any], pdf: Path, layout_path: Path, *, tag: str, model: str,
                     pages: list[int] | None = None, concurrency: int = 4) -> int:
    """Gọi VLM cho `pages` (ép gọi lại), hoặc mọi trang chưa có / chữ đã đổi trong cache. -> số lần gọi.

    Trang gọi lỗi (hết retry) thì bỏ qua, không ghi — bước ③ dùng docling cho trang đó.
    Trang xong là ghi cache ngay, không đợi cả lượt.
    """
    import pypdfium2 as pdfium

    from llm import LLM

    layout = load_layout(layout_path)
    prompt = PROMPT.read_text(encoding="utf-8")
    psha = _prompt_sha(prompt)
    todo: list[tuple[int, str, str]] = []
    for no in pages or sorted(int(k) for k in raw["pages"]):
        text = items_prompt(no, page_items(raw, no))
        key = _key(model, text)
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
    lock = asyncio.Lock()                  # các luồng xong cùng lúc không ghi đè file của nhau

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
        async with lock:
            layout["pages"][str(no)] = {"key": key, "model": model, "prompt_sha": psha,
                                        "sec": round(time.perf_counter() - t0, 1), "response": resp}
            save_layout(layout, layout_path)

    t0 = time.perf_counter()
    try:
        results = await asyncio.gather(*(one(*t) for t in todo), return_exceptions=True)
    finally:
        await llm.close()
    for (no, _, _), r in zip(todo, results):
        if isinstance(r, Exception):
            keep = "giu bo cuc cu trong cache" if str(no) in layout["pages"] else "trang nay dung docling"
            log.warning("  layout p%d: goi VLM loi, %s — %s", no, keep, r)

    ok = sum(1 for r in results if not isinstance(r, Exception))
    log.info("  %d/%d trang xong, %.0fs -> %s", ok, len(todo), time.perf_counter() - t0, layout_path)
    return len(todo)


# ------------------------------------------------------------------ ghép + kiểm


def _same(s: str) -> str:
    """Chuẩn hoá để so chữ VLM chép lại với mẩu T: bỏ mọi khoảng trắng (docling hay chèn cách
    trước dấu câu: "Tần suất : 2 lần /tháng"), "..." = "…"."""
    return re.sub(r"\s+", "", s.replace("…", "..."))


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


def assemble(resp: dict[str, Any], items: Items,
             page_no: int | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Phản hồi VLM -> (khối có chữ thật, lỗi). Không tin VLM: mọi id đều kiểm.

    Lỗi (-> trang dùng docling): mẩu chữ bị sót, id không tồn tại, vùng ảnh/bảng không được
    nhắc tới (p20 từng bỏ luôn bảng hệ số OT mà chữ vẫn phủ 100%).

    Khối rỗng (`{"kind": "text", "ids": []}`) bỏ đi nhưng BÁO: Onboarding p20 trả đúng một khối
    như thế ở chỗ ô "LƯU Ý" nằm trong ảnh, ngoài khung bảng — VLM thấy chữ mà không có chỗ ghi,
    bỏ im lặng là mất 40 giờ/tháng · 200 giờ/năm · ca đêm 22h-6h mà không ai biết.

    `read` trùng chữ một mẩu T -> đổi về mẩu T (NT2: chữ có trong text layer thì lấy text layer).
    Onboarding p38: VLM đưa 4 tiêu đề mục T2/T8/T9/T11 vào `skip` rồi gõ lại bằng `read`, còn
    đổi "…" thành "..." — chữ do model chép lại đội lốt `vlm` dù bản đúng 100% nằm ngay đó.
    """
    used: set[str] = set()
    unknown: list[str] = []
    cur: list[str] = []                 # id T của khối đang ghép -> polygon
    read = False                        # khối đang ghép có chữ VLM tự đọc từ ảnh -> `vlm`
    skipped = set(resp.get("skip") or [])
    by_text: dict[str, list[str]] = {}  # chữ chuẩn hoá -> các mẩu T có đúng chữ đó
    for k, v in items.items():
        if v["kind"] == "T":
            by_text.setdefault(_same(v["text"]), []).append(k)
    repaired: list[str] = []

    def as_t(s: str) -> str | None:
        """Mẩu T chưa dùng có chữ trùng `s` — ưu tiên mẩu VLM đã đưa vào `skip`."""
        free = [k for k in by_text.get(_same(s), []) if k not in used]
        return next((k for k in free if k in skipped), free[0] if free else None)

    def take(ids: Any) -> str:
        """id T -> chữ docling nguyên văn. `{"read": ...}` -> chữ VLM đọc từ ảnh, ở bất kỳ khối
        nào (Thời gian làm việc p7: 2 dòng lưu ý chỉ có trong ảnh, VLM đặt thành list)."""
        nonlocal read
        parts = []
        for i in _flat(ids):
            if isinstance(i, dict) and BULLET_ONLY.fullmatch(str(i["read"])):
                continue        # VLM gõ lại dấu đầu dòng "·" "." (Onboarding p43/p44/p46) — không phải chữ
            if isinstance(i, dict) and (t := as_t(str(i["read"]))):
                repaired.append(t)
                i = t
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
    n_empty = 0
    for b in resp.get("blocks") or []:
        kind, source = b.get("kind"), b.get("source")
        cur.clear()
        read = False
        if kind in ("title", "text"):
            # VLM hay đặt `read` ngay ở khối thay vì trong ids (Onboarding p20: "LƯU Ý:")
            blk = {"kind": kind, "content": take(b.get("ids") or ([{"read": b["read"]}] if b.get("read") else []))}
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
        if blk.get("content", True) or blk.get("cells"):
            blocks.append({**blk, "provenance": "vlm" if read else "text_layer",
                           "source": source, "ids": list(cur)})
        else:
            n_empty += 1

    if n_empty:
        log.warning("  layout p%s: VLM tra %d khoi rong, bo qua — co the la chu trong anh chua ghi `read`",
                    page_no if page_no is not None else "?", n_empty)
    if repaired:
        log.info("  layout p%s: %d `read` trung chu text layer -> doi ve manh T (%s)",
                 page_no if page_no is not None else "?", len(repaired), ", ".join(repaired))
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
    Chữ trang đã đổi (khoá lệch) -> giữ docling, không cờ: chưa ai nhìn trang đó với dữ liệu mới.
    Làm bằng prompt cũ -> VẪN dùng, chỉ đếm vào log.
    """
    psha = _prompt_sha(PROMPT.read_text(encoding="utf-8"))
    used, failed, stale, old_prompt = 0, [], [], []
    for page in doc.pages:
        entry = layout["pages"].get(str(page.page_no))
        if not entry:
            continue
        items = page_items(raw, page.page_no)
        if entry["key"] != _key(entry["model"], items_prompt(page.page_no, items)):
            stale.append(page.page_no)
            continue
        if entry.get("prompt_sha") != psha:
            old_prompt.append(page.page_no)
        blocks, err = assemble(entry["response"], items, page.page_no)
        if err:
            page.layout_error = err
            failed.append(page.page_no)
            continue
        page.blocks = _to_blocks(page.page_no, blocks, items)
        page.title = next((b.content for b in page.paragraphs if b.role == "title"), None)
        page.page_hash = page.compute_hash()
        used += 1

    log.info("  bo cuc VLM: %d trang | truot kiem tra %s | chu doi, bo qua %s | prompt cu %s | con lai dung docling",
             used, failed or "-", stale or "-", old_prompt or "-")


# ------------------------------------------------------------------ ②b bảng: ảnh bảng -> cells


def _table_key(model: str, t: ParsedTable) -> str:
    return _key(model, f"{t.id}|{[round(v, 3) for v in t.box]}")


def _crop_b64(page: Any, box: tuple[float, float, float, float], width_px: int = 2000, margin: float = 0.012) -> str:
    img = page.render(scale=width_px / page.get_size()[0]).to_pil().convert("RGB")
    w, h = img.size
    crop = img.crop((int(max(0.0, box[0] - margin) * w), int(max(0.0, box[1] - margin) * h),
                     int(min(1.0, box[2] + margin) * w), int(min(1.0, box[3] + margin) * h)))
    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _page_text(pdf_doc: Any, page_no: int) -> str:
    return " ".join(pdf_doc[page_no - 1].get_textpage().get_text_range().split())


_SAME_CHAR = str.maketrans("’‘“”–—", "''\"\"--")


def _snap(frag: str, stream: str) -> str | None:
    """Mảnh chữ VLM đọc -> đoạn giống nhất (>= 0.85) trong text layer của trang, None nếu không có.

    VLM đọc ảnh có lúc nhầm (p22: "[ILVG]" -> "[ILV]", "YTCLCV của" -> "YTCLCVCủa"); text layer đúng
    100% (NT2). Khớp với chữ LIỀN MẠCH của trang, không với ô docling — docling cắt giữa câu. Chỉ cắt
    ở ranh giới từ, không thì ăn cả dấu câu của câu bên cạnh.
    """
    f = " ".join(frag.split())
    if not f:
        return f
    # So khớp bỏ qua nháy cong / gạch dài (p8: VLM "DON'T", PDF "DON’T") — thay 1 ký tự lấy 1 ký
    # tự nên chỉ số giữ nguyên, trả về đúng chữ gốc của PDF
    sn, fn = stream.translate(_SAME_CHAR), f.translate(_SAME_CHAR)
    if "…" in sn:                                   # VLM gõ "...", PDF có "…" (p21 "đột xuất…)")
        fn = fn.replace("...", "…")
    if (i := sn.find(fn)) >= 0:
        return stream[i:i + len(fn)]
    a, b, _ = SequenceMatcher(None, sn, fn, autojunk=False).find_longest_match(0, len(sn), 0, len(fn))
    base = a - b
    starts = [i for i in range(max(0, base - 15), min(len(sn), base + 16)) if i == 0 or sn[i - 1] == " "]
    ends = [j for j in range(max(0, base + len(fn) - 15), min(len(sn), base + len(fn) + 16) + 1)
            if j == len(sn) or sn[j] == " "]
    best, out = 0.0, None
    for s0 in starts:
        for s1 in ends:
            if s1 > s0 and (r := SequenceMatcher(None, sn[s0:s1], fn, autojunk=False).ratio()) > best:
                best, out = r, stream[s0:s1]
    return out if best >= 0.85 else None


def _table_cells(resp: dict[str, Any], stream: str) -> tuple[list[list[str]], int, int] | None:
    """Phản hồi VLM -> (cells đã nắn chữ, số mảnh khớp text layer, số mảnh không khớp).
    None khi lưới hỏng: thiếu hàng, hàng lệch số cột so với hàng tiêu đề."""
    rows = resp.get("cells")
    if not isinstance(rows, list) or len(rows) < 2 or not all(isinstance(r, list) for r in rows):
        return None
    if not rows[0] or any(len(r) != len(rows[0]) for r in rows):
        return None
    ok = bad = 0
    cells: list[list[str]] = []
    for r in rows:
        out = []
        for c in r:
            parts = []
            for line in str(c or "").split("\n"):
                # " — " là chỗ prompt bảo ghép "nhóm — mục con": hai mẩu chữ khác nhau trên ảnh,
                # nắn riêng từng mẩu (ghép chung thì không khớp, hoặc khớp mà nuốt mất dấu ngăn)
                pieces = []
                for frag in line.split(" — "):
                    if not frag.strip():
                        continue
                    s = _snap(frag, stream)
                    ok, bad = ok + (s is not None), bad + (s is None)
                    pieces.append(s if s is not None else " ".join(frag.split()))
                if not pieces:
                    continue
                # dòng mở bằng chữ thường = chữ tràn dòng trong ô hẹp ("Chốt công và\\nhướng dẫn hỗ"),
                # nối lại; dòng mới thật mở bằng chữ hoa / gạch đầu dòng / ngoặc thì giữ
                if parts and pieces[0][:1].islower():
                    parts[-1] += " " + " — ".join(pieces)
                else:
                    parts.append(" — ".join(pieces))
            out.append("\n".join(parts))
        cells.append(out)
    return cells, ok, bad


async def run_tables(doc: ParsedDocument, pdf: Path, layout_path: Path, *, tag: str, model: str,
                     pages: list[int] | None = None, concurrency: int = 4) -> int:
    """②b Gọi VLM cho bảng chưa có / khung đổi / nằm trong `pages` (ép gọi lại). -> số lần gọi.

    Cần khung bảng của bố cục cuối -> chạy trên `doc` đã dựng; xong thì dựng ③ lại. Bảng gọi lỗi
    thì bỏ qua — giữ `cells` cũ. Ghi cache ngay sau mỗi bảng.
    """
    import pypdfium2 as pdfium

    from llm import LLM

    layout = load_layout(layout_path)
    cache = layout.setdefault("tables", {})
    prompt = TABLE_PROMPT.read_text(encoding="utf-8")
    todo: list[tuple[ParsedTable, str, str]] = []
    pdf_doc = pdfium.PdfDocument(str(pdf))
    try:
        if len(pdf_doc) != doc.n_pages:
            return 0
        for page in doc.pages:
            for t in page.tables:
                key = _table_key(model, t)
                if t.provenance == Provenance.MANUAL or (
                        cache.get(t.id, {}).get("key") == key and not (pages and page.page_no in pages)):
                    continue
                todo.append((t, key, _crop_b64(pdf_doc[page.page_no - 1], t.box)))
    finally:
        pdf_doc.close()
    if not todo:
        log.info("②b bang: moi bang da co san, khong goi VLM")
        return 0

    log.info("②b bang: goi %s cho %d bang ...", model, len(todo))
    llm = LLM(model, ROOT / "logs" / "tables", retry=3, timeout=180, extra={"temperature": 0})
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()

    async def one(t: ParsedTable, key: str, b64: str) -> None:
        async with sem:
            t0 = time.perf_counter()
            resp = await llm.chat([
                {"role": "system", "content": prompt},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]},
            ], tag=f"{tag}_{t.id}")
        async with lock:
            cache[t.id] = {"key": key, "model": model, "prompt_sha": _prompt_sha(prompt),
                           "sec": round(time.perf_counter() - t0, 1), "response": resp}
            save_layout(layout, layout_path)

    try:
        results = await asyncio.gather(*(one(*j) for j in todo), return_exceptions=True)
    finally:
        await llm.close()
    for (t, _, _), r in zip(todo, results):
        if isinstance(r, Exception):
            log.warning("  bang %s: goi VLM loi, giu cells cu — %s", t.id, r)
    return len(todo)


def apply_tables(doc: ParsedDocument, layout: dict[str, Any], pdf: Path | None) -> None:
    """Thay `cells` bảng bằng bản VLM chép từ ảnh bảng (②b), chữ nắn về text layer. Không gọi API.

    Mọi mảnh chữ khớp text layer -> `text_layer`; còn mảnh không khớp (VLM đọc nhầm mà không nắn
    được, hoặc bảng là ẢNH chụp — không có text layer) -> `vlm`. Lưới hỏng -> giữ `cells` cũ.
    """
    cache = layout.get("tables") or {}
    if not cache or pdf is None:
        return
    import pypdfium2 as pdfium

    used, part_vlm, image, broken = 0, [], [], []
    pdf_doc = pdfium.PdfDocument(str(pdf))
    try:
        if len(pdf_doc) != doc.n_pages:
            return
        for page in doc.pages:
            changed = False
            for i, b in enumerate(page.blocks):
                e = cache.get(b.id) if isinstance(b, ParsedTable) else None
                if not e or e["key"] != _table_key(e["model"], b):
                    continue
                if (res := _table_cells(e["response"], _page_text(pdf_doc, page.page_no))) is None:
                    broken.append(b.id)
                    continue
                cells, ok, bad = res
                if bad and ok:
                    part_vlm.append(f"{b.id} {bad}/{ok + bad}")
                elif bad:
                    image.append(b.id)
                page.blocks[i] = ParsedTable(
                    id=b.id, polygon=b.polygon, caption=b.caption, cells=cells,
                    provenance=Provenance.VLM if bad else Provenance.TEXT_LAYER,
                    structure_provenance=Provenance.VLM)
                used += 1
                changed = True
            if changed:
                page.page_hash = page.compute_hash()
    finally:
        pdf_doc.close()
    log.info("  bang doc tu anh: %d bang | anh chup (chu vlm): %s | manh chu khong khop text layer: %s"
             " | luoi hong, giu cu: %s", used, image or "-", part_vlm or "-", broken or "-")
