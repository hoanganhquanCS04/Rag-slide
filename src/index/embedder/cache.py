"""Bọc một embedder khác bằng cache trên đĩa theo NỘI DUNG — chữ đã nhúng thì không gọi lại.

    out/index/.embed_cache/<model>/<text_key>.npy     một vector mỗi file, DÙNG CHUNG mọi deck

`.npy` của bài xếp theo VỊ TRÍ chunk nên không trả lời được "chữ này nhúng chưa". Cache trả lời
được — dùng lại khi: sửa vài trang rồi chạy lại (§8), eval / tune hỏi lại cùng bộ câu hỏi, thử
bộ chunk khác, runtime gặp lại câu hỏi cũ (~700ms -> ~0).

Tách khỏi `Embedder` vì là việc khác: muốn tắt thì factory không bọc (`cache=False`).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from index.config import OUT
from index.embedder.base import BATCH, Embedder
from index.vectors import model_slug, text_key

log = logging.getLogger(__name__)

CACHE_DIR = OUT / ".embed_cache"


class CachedEmbedder(Embedder):
    kind = "cache"

    def __init__(self, inner: Embedder, cache_dir: str | Path = CACHE_DIR):
        super().__init__(inner.model_id, retry=inner.retry)
        self.inner = inner
        self.cache_dir = Path(cache_dir) / model_slug(inner.model_id)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def dim(self) -> int:
        return self.inner.dim

    @property
    def backend(self) -> str:
        return self.inner.backend

    @property
    def n_api_calls(self) -> int:          # type: ignore[override]
        return self.inner.n_api_calls

    def _request(self, batch: list[str]) -> np.ndarray:
        return self.inner._request(batch)

    def _file(self, text: str) -> Path:
        return self.cache_dir / f"{text_key(text, self.model_id)}.npy"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        todo: list[int] = []
        for i, t in enumerate(texts):
            if (f := self._file(t)).exists():
                out[i] = np.load(f)
            else:
                todo.append(i)

        if hit := len(texts) - len(todo):
            log.info("  cache: dung lai %d/%d", hit, len(texts))

        # Ghi cache SAU MỖI LÔ: hỏng giữa chừng thì lô đã nhúng vẫn còn, chạy lại đi tiếp.
        for s in range(0, len(todo), BATCH):
            idx = todo[s:s + BATCH]
            for i, v in zip(idx, self.inner.embed([texts[i] for i in idx])):
                out[i] = v
                np.save(self._file(texts[i]), v)
            log.info("  nhung %d/%d", min(s + BATCH, len(todo)), len(todo))
        return out
