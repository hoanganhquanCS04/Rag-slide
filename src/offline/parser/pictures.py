"""Bước ② — xét từng ảnh bằng VLM: ảnh loại gì, có nội dung gì -> `pictures.json`.

Để làm gì: bỏ ảnh trang trí, ghi nội dung cho ảnh có ích.
Cách làm: mỗi trang có ảnh là một request, gửi nhiều ảnh:
    ảnh đầu      cả trang có KHUNG ĐỎ đánh số quanh từng ảnh — để VLM hiểu ngữ cảnh
    ảnh sau đó   từng ảnh [1], [2]… CẮT RIÊNG, vẽ nét — để VLM nhìn rõ hình nhỏ
VLM trả mỗi ảnh một loại:
    TRANG_TRI   bỏ khỏi document.json
    SO_DO       2–4 câu mô tả
    ANH_CO_CHU  chép chữ trong ảnh
    MINH_HOA    1 câu: ảnh minh hoạ cho bước nào
Cache theo trang; trang lỗi thì lần chạy sau tự gọi lại.
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
    load_json_array,
    region_size_pt,
    render_region,
    to_data_url,
    user_message,
)
from models.parsed_document import ParsedDocument, ParsedImage

log = logging.getLogger(__name__)

PICTURE_PROMPT = PROMPTS_DIR / "s0_vlm_picture_prompt.md"
LOG_DIR = ROOT / "logs" / "vlm_pictures"

RENDER_SCALE = 2.0       # ảnh trang gửi VLM = 2 × 72dpi (Gemma 4 tự co theo VLM_SOFT_TOKENS)
CROP_LONG_SIDE_PX = 1000 # ảnh cắt riêng: cạnh dài ~1000px — đủ nét, ít token (ảnh nhỏ hơn thì không co)
CROP_MAX_SCALE = 8.0     # icon bé tí cũng không phóng quá 8 × 72dpi
CROP_MARGIN = 0.01       # lề quanh hộp ảnh, theo bề trang (hộp của Gemma hay hụt mép)
# Tối đa mấy ảnh cắt trong 1 request. Onboarding p4 có 104 ảnh: gửi 105 ảnh một lần làm vLLM treo
# (2026-10-19) -> trang nhiều ảnh chia nhóm 8 ảnh, mỗi nhóm một request.
MAX_PICTURES_PER_REQUEST = 8
MAX_TOKENS = 8192        # ANH_CO_CHU chép nguyên văn nhiều ảnh chụp (Onboarding p31: 13 ảnh)
CONCURRENCY = 8
TYPES = ("TRANG_TRI", "SO_DO", "ANH_CO_CHU", "MINH_HOA")
# code / công thức trong ảnh chụp -> "\y", "\{" không phải escape JSON hợp lệ -> hỏng cả trang
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def _box1000(im: ParsedImage) -> list[int]:
    """Hộp của ảnh theo thang 0–1000 (cùng thang với prompt)."""
    return [round(v * 1000) for v in im.box]


def _page_image(pdf: Any, page_no: int, images: list[ParsedImage]) -> str:
    """Vẽ cả trang, khoanh khung đỏ + nhãn [k] quanh từng ảnh cần xét -> ảnh PNG dạng data URL."""
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
    return to_data_url(img)


def _crop_image(pdf: Any, page_no: int, image: ParsedImage) -> str:
    """Cắt riêng một ảnh (thêm lề nhỏ) từ PDF, vẽ sao cho cạnh dài ~1000px -> ảnh PNG dạng data URL."""
    page = pdf[page_no - 1]
    region = add_margin(image.box, CROP_MARGIN)
    long_side_pt = max(*region_size_pt(page, region), 1.0)
    return to_data_url(render_region(page, region, min(CROP_LONG_SIDE_PX / long_side_pt, CROP_MAX_SCALE)))


def _parse(content: str, n: int) -> dict[int, dict[str, Any]]:
    """Đọc phản hồi của VLM -> {số thứ tự ảnh: {loại, lý do, chữ}}.

    Cách làm: chấp nhận mảng, object bọc mảng, hoặc một object lẻ; sửa escape lỗi; bỏ mục có số
    thứ tự hoặc loại không hợp lệ.
    """
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
    """Khoá cache của một trang: hash(nội dung trang + prompt + model)."""
    return hashlib.sha256(f"{page_hash}|{prompt_sha}|{model}".encode()).hexdigest()[:16]


async def _ask(llm: LLM, sem: asyncio.Semaphore, prompt: str, page_no: int,
               images: list[ParsedImage], page_url: str, crop_urls: list[str]) -> dict[str, Any]:
    """Gửi một nhóm ảnh của trang cho VLM -> {"items": [kết quả từng ảnh]}, lỗi thì {"error"}.

    Thứ tự ảnh gửi: ảnh cả trang có khung đỏ, rồi ảnh cắt riêng của [1], [2]… đúng thứ tự danh sách.
    """
    listing = "\n".join(f"[{k}] {_box1000(im)}" for k, im in enumerate(images, 1))
    messages = user_message([page_url, *crop_urls], prompt.replace("{{pictures}}", listing))
    async with sem:
        try:
            # tên file log theo ảnh đầu nhóm: trang chia nhiều nhóm thì mỗi nhóm một file, không đè nhau
            content = await llm.chat(messages, tag=images[0].id, raw=True)
            got = _parse(content, len(images))
        except Exception as e:                                   # mạng / JSON hỏng -> chạy sau gọi lại
            log.warning("  anh p%d: %s", page_no, e)
            return {"error": str(e)[:200], "items": []}
    missing = [im.id for k, im in enumerate(images, 1) if k not in got]
    if missing:
        log.warning("  anh p%d: VLM bo sot %s", page_no, missing)
    return {"items": [{"block_id": im.id, **got[k]} for k, im in enumerate(images, 1) if k in got]}


async def _run(layout: ParsedDocument, pdf_path: Path, out_json: Path, model: str, redo: bool) -> dict:
    """Xét ảnh mọi trang có ảnh rồi ghi `pictures.json`.

    Cách làm: trang có trong cache và khoá không đổi thì dùng lại; còn lại vẽ ảnh trước (pypdfium2
    không chạy song song được), rồi gọi VLM song song.
    """
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
        # Trang nhiều ảnh chia nhóm, mỗi nhóm 1 request: ảnh trang chỉ khoanh nhóm đó + ảnh cắt của nhóm.
        # pypdfium2 không an toàn đa luồng -> vẽ hết ảnh trang + ảnh cắt TRƯỚC, rồi mới gọi song song
        rendered = [(no, group, key, _page_image(pdf, no, group), [_crop_image(pdf, no, im) for im in group])
                    for no, images, key in todo
                    for group in (images[i:i + MAX_PICTURES_PER_REQUEST]
                                  for i in range(0, len(images), MAX_PICTURES_PER_REQUEST))]
    finally:
        pdf.close()

    if rendered:
        # JSON mode tắt sẵn trong create_vlm_client: prompt trả MẢNG, JSON mode ép object -> mất mục
        llm = create_vlm_client(model, LOG_DIR / out_json.parent.name, max_tokens=MAX_TOKENS)
        sem = asyncio.Semaphore(CONCURRENCY)
        log.info("② xet anh: %d trang (%d request) goi %s, %d trang dung cache", len(todo), len(rendered),
                 model, len(pages))
        try:
            results = await asyncio.gather(*(_ask(llm, sem, prompt, no, group, page_url, crop_urls)
                                             for no, group, _, page_url, crop_urls in rendered))
        finally:
            await llm.close()
        # gộp kết quả các nhóm về từng trang; nhóm nào lỗi thì cả trang tính là lỗi (lần sau gọi lại)
        for (no, _, key, _, _), res in zip(rendered, results):
            page_result = pages.setdefault(str(no), {"key": key, "items": []})
            page_result["items"] += res.get("items", [])
            if res.get("error"):
                page_result["error"] = res["error"]
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
    """Bước ②: xét mọi ảnh của doc -> nội dung `pictures.json` (gọi `_run`)."""
    return asyncio.run(_run(layout, pdf_path, out_json, model, redo))


def apply_pictures(doc: ParsedDocument, pictures_json: Path) -> Counter[str]:
    """Gắn kết quả bước ② vào các khối ảnh của doc, theo id ảnh.

    Cách làm: TRANG_TRI thì bỏ khối; loại khác có chữ thì ghi vào `content`; có loại mà không có chữ
    thì đánh dấu `not_described`; không đánh lại id; tính lại page_hash.
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
