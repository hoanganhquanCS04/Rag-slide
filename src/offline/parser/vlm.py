"""Hàm dùng chung cho mọi lần gọi VLM: chọn server và đọc mảng JSON trong phản hồi."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from docling.utils.dots_utils import _clean_json

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


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
