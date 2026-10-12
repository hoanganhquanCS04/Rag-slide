"""Chọn parser theo tên (mặc định docling)."""

from __future__ import annotations

import importlib
from pathlib import Path

from parser.base import BaseParser

# import lúc chọn — biến thể này hỏng không kéo biến thể kia theo
PARSERS = {
    "docling": "parser.variants.docling:DoclingParser",
}


def create_parser(kind: str, model: str, out_dir: Path) -> BaseParser:
    if kind not in PARSERS:
        raise SystemExit(f"parser '{kind}' khong hop le — chon {' | '.join(PARSERS)}")
    module, cls = PARSERS[kind].split(":")
    return getattr(importlib.import_module(module), cls)(model, out_dir)
