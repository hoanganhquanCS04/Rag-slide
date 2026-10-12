"""Chọn embedder theo `EMBEDDER` trong .env (mặc định openai), bọc cache trừ khi tắt."""

from __future__ import annotations

import importlib
import os

from index.config import MODEL_ID
from index.embedder.base import RETRY, Embedder
from index.embedder.cache import CachedEmbedder

# import lúc chọn — biến thể này hỏng không kéo biến thể kia theo
EMBEDDERS = {
    "openai": "index.embedder.variants.openai:OpenAIEmbedder",
}


def create_embedder(kind: str | None = None, *, model_id: str = MODEL_ID, retry: int = RETRY,
                    cache: bool = True) -> Embedder:
    kind = (kind or os.environ.get("EMBEDDER") or "openai").lower()
    if kind not in EMBEDDERS:
        raise SystemExit(f"EMBEDDER='{kind}' khong hop le — chon {' | '.join(EMBEDDERS)}")
    module, cls = EMBEDDERS[kind].split(":")
    emb: Embedder = getattr(importlib.import_module(module), cls)(model_id, retry=retry)
    return CachedEmbedder(emb) if cache else emb
