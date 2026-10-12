"""Chọn chunker theo tên (mặc định page)."""

from __future__ import annotations

import importlib
from pathlib import Path

from chunker.base import BaseChunker

# import lúc chọn — biến thể này hỏng không kéo biến thể kia theo
CHUNKERS = {
    "page": "chunker.variants.page:PageChunker",
}


def create_chunker(kind: str, out_dir: Path, max_tokens: int) -> BaseChunker:
    if kind not in CHUNKERS:
        raise SystemExit(f"chunker '{kind}' khong hop le — chon {' | '.join(CHUNKERS)}")
    module, cls = CHUNKERS[kind].split(":")
    return getattr(importlib.import_module(module), cls)(out_dir, max_tokens)
