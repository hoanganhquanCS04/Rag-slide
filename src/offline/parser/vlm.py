"""Hàm dùng chung cho mọi lần gọi VLM: chọn server, vẽ ảnh gửi đi, dựng tin nhắn, đọc mảng JSON trả về.

Các bước gọi VLM riêng (②b chép bảng, ② xét ảnh, ①b đọc trang quá khổ) đều dùng chung file này.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any

from docling.utils.dots_utils import _clean_json

from utils.llm import LLM

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")

# Hộp (trái, trên, phải, dưới) theo thang [0,1] của trang
Box = tuple[float, float, float, float]


def vlm_endpoint() -> tuple[str, str | None, int | None]:
    """Server VLM -> (địa chỉ, khoá API, số token ảnh).

    Cách làm: có `VLM_BASE_URL` thì đi server riêng (không gửi OPENAI_API_KEY sang đó), không thì
    đi `OPENAI_BASE_URL`.
    """
    if base := os.environ.get("VLM_BASE_URL"):
        key = os.environ.get("VLM_API_KEY")       # không gửi OPENAI_API_KEY sang host khác
    else:
        base = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        key = os.environ.get("OPENAI_API_KEY")
    # Gemma 4 trên vLLM: mặc định 280 token ảnh đọc sai dấu ("Ánh xạ" -> "Ảnh xá"); 1120 thì đúng
    soft = os.environ.get("VLM_SOFT_TOKENS")
    return base, key, int(soft) if soft else None


def create_vlm_client(model: str, log_dir: Path, *, max_tokens: int, **params: Any) -> LLM:
    """Tạo kết nối tới VLM, cấu hình chung cho mọi bước gọi VLM riêng.

    Cách làm: temperature 0 (cùng ảnh ra cùng chữ), số token ảnh theo VLM_SOFT_TOKENS, thử lại 3 lần
    cách nhau 5s, chờ tối đa 300s; tắt JSON mode — phản hồi được đọc tay (vá JSON hỏng, bỏ ```).
    `params` = tham số thêm gửi kèm (vd repetition_penalty).
    """
    base, api_key, soft = vlm_endpoint()
    extra: dict[str, Any] = {"temperature": 0, "max_tokens": max_tokens, **params}
    if soft:
        extra["mm_processor_kwargs"] = {"max_soft_tokens": soft}
    llm = LLM(model, log_dir, retry=3, timeout=300.0, backoff=5.0, extra=extra, base_url=base, api_key=api_key)
    llm.json_mode = False
    return llm


def user_message(image_urls: list[str], text: str) -> list[dict[str, Any]]:
    """Dựng tin nhắn gửi VLM: các ảnh theo đúng thứ tự, rồi tới chữ prompt."""
    images = [{"type": "image_url", "image_url": {"url": url}} for url in image_urls]
    return [{"role": "user", "content": [*images, {"type": "text", "text": text}]}]


def to_data_url(img: Any) -> str:
    """Ảnh PIL -> PNG dạng data URL để gửi VLM."""
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def add_margin(box: Box, margin: float) -> Box:
    """Nới hộp thêm `margin` (theo bề trang) về mỗi phía, không vượt khỏi trang."""
    l, t, r, b = box
    return max(l - margin, 0.0), max(t - margin, 0.0), min(r + margin, 1.0), min(b + margin, 1.0)


def region_size_pt(page: Any, box: Box) -> tuple[float, float]:
    """Kích thước thật (pt) của một vùng trên trang PDF -> (rộng, cao)."""
    w_pt, h_pt = page.get_size()
    l, t, r, b = box
    return (r - l) * w_pt, (b - t) * h_pt


def render_region(page: Any, box: Box, scale: float) -> Any:
    """Vẽ RIÊNG một vùng của trang PDF (pypdfium2) ở độ phóng `scale` (× 72dpi) -> ảnh PIL RGB.

    Cách làm: chỉ vẽ đúng vùng cần (tham số crop = phần cắt bỏ ở lề trái, dưới, phải, trên, tính bằng pt),
    không vẽ cả trang rồi mới cắt — trang khổ lớn vẽ cả trang ở độ phóng cao rất tốn bộ nhớ.
    """
    w_pt, h_pt = page.get_size()
    l, t, r, b = box
    crop = (l * w_pt, (1 - b) * h_pt, (1 - r) * w_pt, t * h_pt)
    return page.render(scale=scale, crop=crop).to_pil().convert("RGB")


def escape_stray_quotes(text: str) -> str:
    """Sửa JSON hỏng vì VLM quên escape dấu `"` nằm giữa chuỗi.

    Cách làm: trong chuỗi, dấu `"` chỉ coi là đóng chuỗi khi ký tự kế tiếp là `, } ] :` hoặc hết
    văn bản; còn lại thì thêm dấu escape.
    """
    out: list[str] = []
    in_str = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if not in_str:
            in_str = c == '"'
        elif c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        elif c == '"':
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] not in ",}]:":
                c = '\\"'
            else:
                in_str = False
        out.append(c)
        i += 1
    return "".join(out)


def load_json_array(content: str) -> list[Any] | None:
    """Lấy mảng JSON trong phản hồi của VLM; mảng bọc trong object thì lấy mảng; hỏng hết thì None.

    Cách làm: thử lần lượt 3 cách đọc — nguyên văn (bỏ khung ```), cắt từ "[" đầu tiên, rồi sửa dấu
    `"` lạc; cách nào đọc được thì dừng.
    """
    # đọc NGUYÊN khối trước: _clean_json cắt từ "[" đầu tiên -> object bọc mảng thừa "}" -> hỏng cả trang
    # vá `"` lạc chỉ là đường lùi cuối — JSON đúng thì không đụng tới
    bare = FENCE.sub("", content)
    for text in (bare, _clean_json(content), escape_stray_quotes(bare)):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), None)
        if isinstance(data, list):
            return data
    return None
