"""② Xét ảnh: layout ① (ParsedDocument) + PDF -> pictures.json; `apply_pictures` gắn vào doc.

MỖI TRANG CÓ ẢNH một request (Prompt B, `prompts/s0_vlm_picture_prompt.md`): ảnh CẢ trang có KHUNG ĐỎ
đánh số [1], [2]… quanh từng ảnh + danh sách `[k] [x1, y1, x2, y2]` (thang 0–1000, cùng thang Prompt A).
VLM trả mỗi ảnh một mục {id, picture_type, reason, text}:

    TRANG_TRI   -> BỎ khỏi document.json (apply_pictures), chỉ còn trong pictures.json
    SO_DO       -> content = 2–4 câu mô tả
    ANH_CO_CHU  -> content = chữ chép trong ảnh
    MINH_HOA    -> content = 1 câu: màn hình / ảnh minh hoạ bước nào

Thấy CẢ trang (không cắt rời từng ảnh) để xét được logo, icon cạnh chữ, ô số thứ tự — ảnh cắt rời
từng tả ảnh Tết thành Trung Thu (CLAUDE.md §5 S0).

Cache THEO TRANG trong pictures.json: khoá = sha(page.cache_hash() của layout + sha prompt + model). Không đổi
-> không gọi lại (trừ `redo`). Trang gọi lỗi ghi `error`, lần chạy sau tự gọi lại. Prompt + phản hồi
ở `logs/vlm_pictures/<doc_id>/` (src/llm.py, ảnh không ghi vào log).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from llm import LLM
from parser.vlm import FENCE, load_json_array, vlm_endpoint
from parsing.models import ParsedDocument, ParsedImage

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
PICTURE_PROMPT = ROOT / "prompts" / "s0_vlm_picture_prompt.md"
LOG_DIR = ROOT / "logs" / "vlm_pictures"

RENDER_SCALE = 2.0       # ảnh trang gửi VLM = 2 × 72dpi (Gemma 4 tự co theo VLM_SOFT_TOKENS)
MAX_TOKENS = 8192        # ANH_CO_CHU chép nguyên văn nhiều ảnh chụp (Onboarding p31: 13 ảnh)
CONCURRENCY = 8
TYPES = ("TRANG_TRI", "SO_DO", "ANH_CO_CHU", "MINH_HOA")
# code / công thức trong ảnh chụp -> "\y", "\{" không phải escape JSON hợp lệ -> hỏng cả trang
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def _box1000(im: ParsedImage) -> list[int]:
    return [round(v * 1000) for v in im.box]


def _page_image(pdf: Any, page_no: int, images: list[ParsedImage]) -> str:
    """Ảnh cả trang, mỗi ảnh cần xét khoanh KHUNG ĐỎ + nhãn [k] -> data URL PNG."""
    from PIL import ImageDraw, ImageFont

    img = pdf[page_no - 1].render(scale=RENDER_SCALE).to_pil().convert("RGB")
    W, H = img.size
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=max(18, W // 60))
    width = max(3, W // 400)
    for k, im in enumerate(images, 1):
        l, t, r, b = im.box
        draw.rectangle([l * W, t * H, r * W, b * H], outline=(255, 0, 0), width=width)
        label = f"[{k}]"
        lw, lh = font.getlength(label) + 8, font.size + 6
        y = t * H - lh if t * H >= lh else t * H          # nhãn ngay trên khung, hết chỗ thì trong khung
        draw.rectangle([l * W, y, l * W + lw, y + lh], fill=(255, 0, 0))
        draw.text((l * W + 4, y + 2), label, fill=(255, 255, 255), font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _parse(content: str, n: int) -> dict[int, dict[str, Any]]:
    """Phản hồi -> {id: mục}. Mảng / object bọc mảng / một object lẻ (trang 1 ảnh); vá escape nếu
    hỏng; bị cụt (hết max_tokens) thì `load_json_array` cứu phần đầu."""
    body = FENCE.sub("", content).strip()
    data: Any = None
    for text in (body, _BAD_ESCAPE.sub(r"\\\\", body)):
        try:
            data = json.loads(text)
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        data = load_json_array(content) or load_json_array(_BAD_ESCAPE.sub(r"\\\\", content))
    if isinstance(data, dict):
        data = [data] if "id" in data else next((v for v in data.values() if isinstance(v, list)), None)
    if not isinstance(data, list):
        raise ValueError("JSON hong")
    items = data
    out: dict[int, dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            k = int(str(it.get("id")).strip("[] "))
        except ValueError:
            continue
        kind = str(it.get("picture_type", "")).strip().upper()
        if 1 <= k <= n and kind in TYPES:
            out[k] = {"picture_type": kind, "reason": str(it.get("reason") or "").strip(),
                      "text": str(it.get("text") or "").strip()}
    return out


def _key(page_hash: str, prompt_sha: str, model: str) -> str:
    return hashlib.sha256(f"{page_hash}|{prompt_sha}|{model}".encode()).hexdigest()[:16]


async def _ask(llm: LLM, sem: asyncio.Semaphore, prompt: str, page_no: int,
               images: list[ParsedImage], data_url: str) -> dict[str, Any]:
    listing = "\n".join(f"[{k}] {_box1000(im)}" for k, im in enumerate(images, 1))
    text = prompt.replace("{{pictures}}", listing)
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": text}]}]
    async with sem:
        try:
            content = await llm.chat(messages, tag=f"p{page_no:03d}", raw=True)
            got = _parse(content, len(images))
        except Exception as e:                                   # mạng / JSON hỏng -> chạy sau gọi lại
            log.warning("  anh p%d: %s", page_no, e)
            return {"error": str(e)[:200], "items": []}
    missing = [im.id for k, im in enumerate(images, 1) if k not in got]
    if missing:
        log.warning("  anh p%d: VLM bo sot %s", page_no, missing)
    return {"items": [{"block_id": im.id, **got[k]} for k, im in enumerate(images, 1) if k in got]}


async def _run(layout: ParsedDocument, pdf_path: Path, out_json: Path, model: str, redo: bool) -> dict:
    import pypdfium2 as pdfium

    prompt = PICTURE_PROMPT.read_text(encoding="utf-8")
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    old = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() and not redo else {}
    old_pages = old.get("pages", {})

    pages: dict[str, Any] = {}
    todo: list[tuple[int, list[ParsedImage], str]] = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for page in layout.pages:
            images = page.images
            if not images:
                continue
            key = _key(page.cache_hash(), prompt_sha, model)
            prev = old_pages.get(str(page.page_no))
            if prev and prev.get("key") == key and not prev.get("error"):
                pages[str(page.page_no)] = prev                   # cache: trang không đổi
                continue
            todo.append((page.page_no, images, key))
        # pypdfium2 không an toàn đa luồng -> vẽ hết ảnh trang TRƯỚC, rồi mới gọi song song
        rendered = [(no, images, key, _page_image(pdf, no, images)) for no, images, key in todo]
    finally:
        pdf.close()

    if rendered:
        base, api_key, soft = vlm_endpoint()
        extra: dict[str, Any] = {"temperature": 0, "max_tokens": MAX_TOKENS}
        if soft:
            extra["mm_processor_kwargs"] = {"max_soft_tokens": soft}
        llm = LLM(model, LOG_DIR / out_json.parent.name, retry=3, timeout=300.0, backoff=5.0,
                  extra=extra, base_url=base, api_key=api_key)
        llm.json_mode = False                 # Prompt B trả MẢNG; JSON mode ép object -> mất mục
        sem = asyncio.Semaphore(CONCURRENCY)
        log.info("② xet anh: %d trang goi %s, %d trang dung cache", len(rendered), model,
                 len(pages))
        try:
            results = await asyncio.gather(*(_ask(llm, sem, prompt, no, images, url)
                                             for no, images, _, url in rendered))
        finally:
            await llm.close()
        for (no, _, key, _), res in zip(rendered, results):
            pages[str(no)] = {"key": key, **res}
    else:
        log.info("② xet anh: %d trang deu dung cache (khong goi VLM)", len(pages))

    data = {"doc_id": layout.doc_id, "model": model, "prompt": PICTURE_PROMPT.name,
            "prompt_sha256": prompt_sha,
            "pages": dict(sorted(pages.items(), key=lambda kv: int(kv[0])))}
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = Counter(it["picture_type"] for p in pages.values() for it in p.get("items", []))
    errors = [no for no, p in pages.items() if p.get("error")]
    log.info("  anh: %s | trang loi %s -> %s", dict(kinds), errors or "khong", out_json)
    return data


def run_pictures(layout: ParsedDocument, pdf_path: Path, out_json: Path, model: str, *,
                 redo: bool = False) -> dict:
    """Bước ②: xét mọi ảnh trong `layout` -> `out_json` (pictures.json)."""
    return asyncio.run(_run(layout, pdf_path, out_json, model, redo))


def apply_pictures(doc: ParsedDocument, pictures_json: Path) -> Counter[str]:
    """Gắn kết quả bước ② vào ảnh của `doc` theo `block_id` (id layout ① = id document.json).

    TRANG_TRI -> BỎ khỏi `page.blocks` (không ghi vào document.json; phân loại + lý do vẫn ở pictures.json
    cho người duyệt). Id khối còn lại KHÔNG đánh lại — patch vẫn trỏ đúng. SO_DO / ANH_CO_CHU / MINH_HOA có
    chữ -> content; loại mang thông tin mà text rỗng -> not_described (cờ cho người duyệt). Ảnh không có
    kết quả giữ nguyên.
    """
    data = json.loads(pictures_json.read_text(encoding="utf-8"))
    by_id = {it["block_id"]: it for p in data.get("pages", {}).values() for it in p.get("items", [])}
    out: Counter[str] = Counter()
    for page in doc.pages:
        touched = False
        decorative: set[str] = set()
        for im in page.images:
            it = by_id.get(im.id)
            if it is None:
                continue
            im.picture_type, im.reason = it["picture_type"], it.get("reason") or None
            text = (it.get("text") or "").strip()
            if it["picture_type"] == "TRANG_TRI":
                decorative.add(im.id)
            elif text:
                im.content, im.why_empty = text, None
            else:
                im.content, im.why_empty = None, "not_described"
            out[it["picture_type"]] += 1
            touched = True
        if decorative:
            page.blocks = [b for b in page.blocks if b.id not in decorative]
        if touched:
            page.page_hash = page.compute_hash()
    return out
