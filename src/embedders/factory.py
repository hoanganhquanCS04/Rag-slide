"""Chọn embedder theo `EMBEDDER` trong .env (mặc định openai), bọc thêm cache."""

from __future__ import annotations

import importlib
import os

from utils.config import MODEL_ID
from embedders.base import RETRY, Embedder
from embedders.cache import CachedEmbedder

# import lúc chọn — biến thể này hỏng không kéo biến thể kia theo
EMBEDDERS = {
    "openai": "embedders.variants.openai:OpenAIEmbedder",
}


def create_embedder(kind: str | None = None, *, model_id: str = MODEL_ID, retry: int = RETRY,
                    cache: bool = True) -> Embedder:
    """Tạo embedder theo tên -> object embedder (mặc định có bọc cache).

    Cách làm: tra tên trong `EMBEDDERS` ("module:Class"), chỉ import module khi được chọn;
    `cache=False` thì không bọc cache.
    """
    kind = (kind or os.environ.get("EMBEDDER") or "openai").lower()
    if kind not in EMBEDDERS:
        raise SystemExit(f"EMBEDDER='{kind}' khong hop le — chon {' | '.join(EMBEDDERS)}")
    module, cls = EMBEDDERS[kind].split(":")
    emb: Embedder = getattr(importlib.import_module(module), cls)(model_id, retry=retry)
    return CachedEmbedder(emb) if cache else emb
