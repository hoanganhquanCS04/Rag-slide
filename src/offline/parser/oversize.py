"""Bước ①b — đọc lại trang QUÁ KHỔ (poster, sơ đồ khổ lớn) bằng cách cắt thành nhiều mảnh -> `oversize.json`.

Để làm gì: VLM co MỌI ảnh về cùng một cỡ (Gemma 4: tối đa 1120 token ảnh × 48×48 px ≈ 2,58 triệu px).
Trang càng to thì mỗi pt càng ít pixel: sdtc.pdf (3263 × 2531 pt) chỉ còn ~0,56 px/pt, chữ 7 pt cao ~4 px
-> không đọc được, VLM lặp vòng tới hết max_tokens. Trang thường (Onboarding 1440 × 810 pt) ~1,5 px/pt.
Cách làm, CHỈ với trang quá khổ (trang thường không đụng tới, không gọi VLM, không ghi file):
    1. cắt trang thành lưới mảnh, mỗi mảnh cỡ một slide thường, chồng mép nhau một chút
    2. mỗi mảnh 1 request, gửi 2 ảnh: cả trang thu nhỏ có khung đỏ quanh mảnh (để định hướng)
       + mảnh vẽ nét (để chép chữ); prompt `src/prompts/s0_vlm_tile_prompt.md`
    3. đổi toạ độ mảnh về toạ độ trang, gộp khối thấy ở hai mảnh chồng mép, giữ khối tiêu đề của bước ①
Cache theo từng mảnh; mảnh lỗi thì lần chạy sau tự gọi lại. Phản hồi thô: `logs/vlm_tiles/<doc_id>/`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from models.parsed_document import (
    AnyBlock,
    ParsedDocument,
    ParsedImage,
    ParsedPage,
    ParsedParagraph,
    ParsedTable,
    norm_lines,
    norm_text,
    polygon_from_box,
)
from offline.parser.vlm import Box, create_vlm_client, render_region, to_data_url, user_message
from utils.config import PROMPTS_DIR, ROOT
from utils.llm import LLM

log = logging.getLogger(__name__)

TILE_PROMPT = PROMPTS_DIR / "s0_vlm_tile_prompt.md"
LOG_DIR = ROOT / "logs" / "vlm_tiles"

# Số pixel tối đa VLM nhìn được trong MỘT ảnh: Gemma 4, max_soft_tokens 1120, mỗi token = 3×3 ô 16 px
PIXEL_BUDGET = 1120 * 48 * 48
MIN_PX_PER_PT = 1.0          # VLM nhìn trang < 1 px mỗi pt -> chữ nhỏ nhoè -> quá khổ
TILE_WIDTH_PT = 1000.0       # mảnh tối đa ~1000 × 560 pt (cỡ một slide 16:9) -> VLM nhìn ~2 px/pt
TILE_HEIGHT_PT = 560.0
OVERLAP = 0.08               # mỗi mảnh nới ra 8% về mỗi phía, để dòng ở đường cắt có bản đầy đủ ở mảnh bên
CONTEXT_LONG_SIDE_PX = 1600  # ảnh cả trang để định hướng — chỉ cần thấy bố cục
MAX_TOKENS = 8192
REPETITION_PENALTY = 1.05    # như bước ①: phạt nhẹ để VLM thoát vòng lặp
CONCURRENCY = 8

# Gộp khối thấy ở hai mảnh chồng mép
SAME_PLACE = 0.5             # hai hộp chồng nhau hơn nửa hộp nhỏ -> cùng một chỗ trên trang
SAME_TEXT = 0.85             # 85% chữ của bản ngắn có trong bản dài -> cùng một chữ (bản cụt / đọc lệch vài ký tự)
EDGE = 10                    # hộp cách mép mảnh < 10/1000 -> bị đường cắt chạm vào, chữ có thể cụt

# loại khối sau normalize_elements -> vai trò khối chữ (tiêu đề nhỏ của mảnh vẫn là "title" như bước ①)
_ROLE = {"Title": "title", "Section-header": "title", "Caption": "caption"}


# ------------------------------------------------------------------ trang nào quá khổ, cắt ra sao

def px_per_pt(width_pt: float, height_pt: float) -> float:
    """VLM sẽ nhìn trang này nét cỡ nào: bao nhiêu pixel cho mỗi pt, sau khi ảnh bị co về PIXEL_BUDGET."""
    return math.sqrt(PIXEL_BUDGET / (width_pt * height_pt))


def is_oversized(width_pt: float, height_pt: float) -> bool:
    """Trang có quá khổ không: VLM nhìn cả trang mà mỗi pt chưa tới MIN_PX_PER_PT pixel."""
    return px_per_pt(width_pt, height_pt) < MIN_PX_PER_PT


def tile_boxes(width_pt: float, height_pt: float) -> list[Box]:
    """Chia trang thành lưới mảnh -> các hộp theo thang [0,1], xếp theo hàng (trên xuống, trái sang).

    Cách làm: số cột / hàng sao cho mỗi ô ≤ TILE_WIDTH_PT × TILE_HEIGHT_PT; mỗi ô nới thêm OVERLAP về mỗi phía.
    """
    columns = math.ceil(width_pt / TILE_WIDTH_PT)
    rows = math.ceil(height_pt / TILE_HEIGHT_PT)
    boxes = []
    for row in range(rows):
        for column in range(columns):
            l, r = column / columns, (column + 1) / columns
            t, b = row / rows, (row + 1) / rows
            pad_x, pad_y = (r - l) * OVERLAP, (b - t) * OVERLAP
            boxes.append((max(l - pad_x, 0.0), max(t - pad_y, 0.0), min(r + pad_x, 1.0), min(b + pad_y, 1.0)))
    return boxes


# ------------------------------------------------------------------ gọi VLM từng mảnh (có cache)

def _context_image(page: Any, box: Box) -> str:
    """Vẽ cả trang thu nhỏ, khoanh khung đỏ quanh vùng của mảnh -> data URL."""
    from PIL import ImageDraw

    img = render_region(page, (0.0, 0.0, 1.0, 1.0), CONTEXT_LONG_SIDE_PX / max(page.get_size()))
    W, H = img.size
    l, t, r, b = box
    ImageDraw.Draw(img).rectangle([l * W, t * H, r * W, b * H], outline=(255, 0, 0), width=max(4, W // 250))
    return to_data_url(img)


def _tile_image(page: Any, box: Box) -> str:
    """Vẽ riêng vùng của mảnh, nét nhất trong giới hạn VLM nhìn được (PIXEL_BUDGET) -> data URL."""
    w_pt, h_pt = page.get_size()
    l, t, r, b = box
    return to_data_url(render_region(page, box, px_per_pt((r - l) * w_pt, (b - t) * h_pt)))


def _key(pdf_sha: str, page_no: int, box: Box, prompt_sha: str, model: str) -> str:
    """Khoá cache của một mảnh: hash(file PDF + số trang + vùng mảnh + prompt + model)."""
    raw = f"{pdf_sha}|{page_no}|{[round(v, 4) for v in box]}|{prompt_sha}|{model}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _ask(llm: LLM, sem: asyncio.Semaphore, prompt: str, tag: str,
               context_url: str, tile_url: str) -> dict[str, Any]:
    """Gửi một mảnh cho VLM -> {"elements": [...]} (đã sửa đúng dạng), lỗi thì {"error"}."""
    from offline.parser.variants.docling import normalize_elements   # import trong hàm: docling.py gọi file này

    async with sem:
        try:
            content = await llm.chat(user_message([context_url, tile_url], prompt), tag=tag, raw=True)
        except Exception as e:                                   # mạng / server -> lần sau gọi lại
            log.warning("  manh %s: %s", tag, e)
            return {"error": str(e)[:200]}
    elements, notes = normalize_elements(content)
    if elements is None:
        log.warning("  manh %s: JSON hong", tag)
        return {"error": "JSON hong"}
    if notes:
        log.info("  manh %s: %s", tag, "; ".join(notes))
    return {"elements": elements}


async def _run(doc: ParsedDocument, pdf_path: Path, out_json: Path, model: str, redo: bool) -> dict | None:
    """Đọc mọi mảnh của các trang quá khổ rồi ghi `oversize.json`; không có trang quá khổ -> None.

    Cách làm: mảnh có trong cache và khoá không đổi thì dùng lại; còn lại vẽ ảnh trước (pypdfium2 không
    chạy song song được), rồi gọi VLM song song.
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        sizes = {page.page_no: pdf[page.page_no - 1].get_size() for page in doc.pages}
        oversized = [no for no, (w, h) in sizes.items() if is_oversized(w, h)]
        if not oversized:
            log.info("①b trang qua kho: khong co")
            return None

        prompt = TILE_PROMPT.read_text(encoding="utf-8")
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        pdf_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:16]
        old = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() and not redo else {}
        old_tiles = {(t["page"], t["index"]): t for t in old.get("tiles", [])}

        tiles: dict[tuple[int, int], dict[str, Any]] = {}
        todo: list[tuple[int, int, Box, str, str, str]] = []
        for no in oversized:
            w, h = sizes[no]
            boxes = tile_boxes(w, h)
            log.info("①b trang qua kho: p%d (%.0fx%.0f pt, %.2f px/pt) -> %d manh",
                     no, w, h, px_per_pt(w, h), len(boxes))
            for index, box in enumerate(boxes, 1):
                key = _key(pdf_sha, no, box, prompt_sha, model)
                prev = old_tiles.get((no, index))
                if prev and prev.get("key") == key and not prev.get("error"):
                    tiles[(no, index)] = prev                    # cache: mảnh không đổi
                    continue
                # pypdfium2 không an toàn đa luồng -> vẽ hết ảnh TRƯỚC, rồi mới gọi song song
                page = pdf[no - 1]
                todo.append((no, index, box, key, _context_image(page, box), _tile_image(page, box)))
    finally:
        pdf.close()

    if todo:
        llm = create_vlm_client(model, LOG_DIR / out_json.parent.name, max_tokens=MAX_TOKENS,
                                repetition_penalty=REPETITION_PENALTY)
        sem = asyncio.Semaphore(CONCURRENCY)
        log.info("①b doc manh: %d manh goi %s, %d manh dung cache", len(todo), model, len(tiles))
        try:
            results = await asyncio.gather(*(
                _ask(llm, sem, prompt, f"p{no:03d}_t{index:02d}", context_url, tile_url)
                for no, index, _, _, context_url, tile_url in todo))
        finally:
            await llm.close()
        for (no, index, box, key, _, _), res in zip(todo, results):
            tiles[(no, index)] = {"page": no, "index": index, "box": list(box), "key": key, **res}
    else:
        log.info("①b doc manh: %d manh deu dung cache (khong goi VLM)", len(tiles))

    data = {"doc_id": doc.doc_id, "model": model, "prompt": TILE_PROMPT.name, "prompt_sha256": prompt_sha,
            "tiles": [tiles[k] for k in sorted(tiles)]}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    errors = [f"p{t['page']}_t{t['index']}" for t in data["tiles"] if t.get("error")]
    log.info("  manh: %d doc duoc, loi %s -> %s", len(data["tiles"]) - len(errors), errors or "khong", out_json)
    return data


# ------------------------------------------------------------------ ghép các mảnh thành khối của trang

@dataclass
class TileBlock:
    """Một khối đọc được từ một mảnh, kèm mảnh nào đọc ra và nó có bị đường cắt chạm vào không."""
    block: AnyBlock
    tile_index: int
    at_cut: bool                  # chạm đường cắt giữa hai mảnh -> chữ có thể cụt

    @property
    def text(self) -> str:
        """Chữ để so trùng: chữ thường, dồn khoảng trắng (ảnh thì rỗng)."""
        return norm_text(getattr(self.block, "content", "") or "").lower()


def _to_page_box(tile: Box, bbox: list[float]) -> Box:
    """Đổi hộp theo thang 0–1000 của mảnh -> hộp theo thang [0,1] của trang."""
    l, t, r, b = tile
    x1, y1, x2, y2 = (min(max(v / 1000, 0.0), 1.0) for v in bbox)
    return l + x1 * (r - l), t + y1 * (b - t), l + x2 * (r - l), t + y2 * (b - t)


def _touches_cut(tile: Box, bbox: list[float]) -> bool:
    """Khối có chạm đường cắt giữa hai mảnh không (chạm mép của TRANG thì không tính — đó là mép thật)."""
    l, t, r, b = tile
    x1, y1, x2, y2 = bbox
    return ((x1 <= EDGE and l > 0.0) or (y1 <= EDGE and t > 0.0)
            or (x2 >= 1000 - EDGE and r < 1.0) or (y2 >= 1000 - EDGE and b < 1.0))


def _to_block(element: dict[str, Any], box: Box, page_no: int, picture_area_threshold: float) -> AnyBlock | None:
    """Một phần tử VLM trả -> khối của trang (chữ / bảng / ảnh); chữ rỗng -> None.

    Cách làm: giống bước ① (`DoclingParser._to_parsed`): ảnh -> ParsedImage, bảng HTML -> ParsedTable,
    còn lại -> ParsedParagraph; thanh đầu / chân trang của MẢNH vẫn là chữ thường (không bỏ như trang thường).
    """
    polygon = polygon_from_box(*box)
    category, text = element["category"], element.get("text") or ""
    if category == "Picture":
        image = ParsedImage(id="", page_no=page_no, polygon=polygon)
        image.why_empty = "area_below_threshold" if image.area < picture_area_threshold else "not_described"
        return image
    if category == "Table" and "<table" in text.lower():
        from docling.utils.chandra_utils import _parse_table_html
        try:
            cells = [[norm_text(cell.text) for cell in row] for row in _parse_table_html(text).grid]
            if cells:
                return ParsedTable(id="", page_no=page_no, polygon=polygon, cells=cells)
        except Exception as e:                                   # HTML hỏng -> giữ thành chữ thường
            log.info("  bang manh p%d: %s", page_no, e)
    if not (content := norm_lines(text)):
        return None
    return ParsedParagraph(id="", page_no=page_no, polygon=polygon, content=content,
                           role=_ROLE.get(category, "body"))  # type: ignore[arg-type]


def _covered(text: str, other: str) -> bool:
    """Chữ ngắn hơn trong hai chữ có nằm (gần) trọn trong chữ dài hơn không.

    Cách làm: đếm bao nhiêu ký tự của chữ ngắn khớp được vào chữ dài (giữ thứ tự), chia cho độ dài chữ ngắn;
    ≥ SAME_TEXT là trùng. Bắt được cả bản cụt ("I TRIỂN KHAI / RỘNG QUY MÔ" trong "KHỐI TRIỂN KHAI / VÀ MỞ
    RỘNG QUY MÔ / Giám đốc…") lẫn bản đọc lệch vài ký tự ("Ngô Quốc Hùng" / "Ngô Quốc Hưng").
    """
    short, long = sorted((text, other), key=len)
    if not short:
        return False
    matched = sum(m.size for m in SequenceMatcher(None, short, long, autojunk=False).get_matching_blocks())
    return matched / len(short) >= SAME_TEXT


def _is_same(a: TileBlock, b: TileBlock) -> bool:
    """Hai khối từ HAI mảnh khác nhau có phải cùng một khối trên trang không.

    Cách làm: phải CÙNG CHỖ (hộp chồng nhau hơn nửa hộp nhỏ) VÀ CÙNG NỘI DUNG (ảnh với ảnh; chữ thì
    `_covered`). Phải xét chỗ: sơ đồ có hàng chục ô "Trưởng phòng (T4)" thật ở các chỗ khác nhau, so chữ
    không thôi là xoá nhầm.
    """
    if a.tile_index == b.tile_index:
        return False
    l, t, r, bottom = a.block.box
    ol, ot, or_, ob = b.block.box
    inter = max(0.0, min(r, or_) - max(l, ol)) * max(0.0, min(bottom, ob) - max(t, ot))
    if inter <= SAME_PLACE * max(min(a.block.area, b.block.area), 1e-9):
        return False
    if isinstance(a.block, ParsedImage) or isinstance(b.block, ParsedImage):
        return isinstance(a.block, ParsedImage) and isinstance(b.block, ParsedImage)
    return _covered(a.text, b.text)


def _better(a: TileBlock, b: TileBlock) -> TileBlock:
    """Chọn bản tốt hơn của cùng một khối: bản KHÔNG chạm đường cắt (chữ không cụt), rồi tới bản chữ dài hơn."""
    if a.at_cut != b.at_cut:
        return b if a.at_cut else a
    return a if len(a.text) >= len(b.text) else b


def merge_tiles(tiles: list[dict[str, Any]], page_no: int, picture_area_threshold: float) -> tuple[list[AnyBlock], int]:
    """Ghép khối của các mảnh một trang -> (khối theo thứ tự mảnh, số khối trùng đã gộp).

    Cách làm: đi lần lượt từng mảnh; khối mới mà trùng một khối đã có (`_is_same`) thì giữ bản tốt hơn
    (`_better`) ngay tại chỗ của khối cũ, không thì thêm vào cuối.
    """
    kept: list[TileBlock] = []
    merged = 0
    for tile in sorted(tiles, key=lambda t: t["index"]):
        tile_box: Box = tuple(tile["box"])  # type: ignore[assignment]
        for element in tile.get("elements", []):
            block = _to_block(element, _to_page_box(tile_box, element["bbox"]), page_no, picture_area_threshold)
            if block is None:
                continue
            current = TileBlock(block, tile["index"], _touches_cut(tile_box, element["bbox"]))
            same = next((i for i, other in enumerate(kept) if _is_same(current, other)), None)
            if same is None:
                kept.append(current)
            else:
                kept[same] = _better(kept[same], current)
                merged += 1
    return [k.block for k in kept], merged


def apply_oversize(doc: ParsedDocument, oversize_json: Path, picture_area_threshold: float) -> Counter[str]:
    """Thay khối của các trang quá khổ bằng khối ghép từ các mảnh.

    Cách làm: giữ khối tiêu đề của bước ① (bước đó đọc cả trang, tiêu đề to nên đọc đúng), bỏ khối mảnh
    là tiêu đề hoặc một phần bị cắt của tiêu đề (chỉ so chữ — toạ độ tiêu đề của bước ① hay sai), rồi thêm
    khối các mảnh. Trang có mảnh lỗi vẫn ghép phần đọc được (báo số mảnh lỗi).
    """
    data = json.loads(oversize_json.read_text(encoding="utf-8"))
    by_page: dict[int, list[dict[str, Any]]] = {}
    for tile in data.get("tiles", []):
        by_page.setdefault(tile["page"], []).append(tile)
    out: Counter[str] = Counter()
    for page_no, tiles in by_page.items():
        page = doc.page(page_no)
        if page is None:
            continue
        out["manh_loi"] += sum(1 for t in tiles if t.get("error"))
        blocks, merged = merge_tiles([t for t in tiles if not t.get("error")], page_no, picture_area_threshold)
        title_blocks = [b for b in page.paragraphs if b.role == "title"][:1]
        title = norm_text(title_blocks[0].content).lower() if title_blocks else ""
        blocks = [b for b in blocks
                  if not (title and _covered(norm_text(getattr(b, "content", "") or "").lower(), title))]
        _replace_blocks(page, [*title_blocks, *blocks])
        out["gop_khoi_trung"] += merged
        out["khoi"] += len(page.blocks)
    return out


def _replace_blocks(page: ParsedPage, blocks: list[AnyBlock]) -> None:
    """Gắn danh sách khối mới vào trang: đánh lại id pNNN.bNN, đặt tiêu đề trang, tính lại page_hash."""
    for i, block in enumerate(blocks):
        block.id = f"p{page.page_no:03d}.b{i:02d}"
    page.blocks = blocks
    page.title = next((b.content for b in page.paragraphs if b.role == "title"), None)
    page.page_hash = page.compute_hash()


def read_oversized_pages(doc: ParsedDocument, pdf_path: Path, out_json: Path, model: str, *,
                         redo: bool = False, picture_area_threshold: float) -> ParsedDocument:
    """Bước ①b: trang quá khổ thì đọc lại theo mảnh rồi thay khối; không có trang quá khổ thì giữ nguyên doc."""
    if asyncio.run(_run(doc, pdf_path, out_json, model, redo)) is not None:
        log.info("  ap manh (①b): %s", dict(apply_oversize(doc, out_json, picture_area_threshold)))
    return doc
