"""Chọn chunker theo tên (hiện chỉ có `page`)."""

from __future__ import annotations

import importlib
from pathlib import Path

from chunker.base import BaseChunker

# import lúc chọn — biến thể này hỏng không kéo biến thể kia theo
CHUNKERS = {
    "page": "chunker.variants.page:PageChunker",
}


def create_chunker(kind: str, out_dir: Path, max_tokens: int) -> BaseChunker:
    """Tạo chunker theo tên -> object chunker.

    Cách làm: tra tên trong `CHUNKERS` ("module:Class"), chỉ import module khi được chọn.
    """
    if kind not in CHUNKERS:
        raise SystemExit(f"chunker '{kind}' khong hop le — chon {' | '.join(CHUNKERS)}")
    module, cls = CHUNKERS[kind].split(":")
    return getattr(importlib.import_module(module), cls)(out_dir, max_tokens)
