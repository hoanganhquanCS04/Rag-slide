"""Bọc một embedder bằng cache trên đĩa: chữ đã nhúng rồi thì không gọi API lại.

    src/output/embedders/cache/<model>/<hash chữ>.npy    một vector mỗi file, dùng chung mọi deck

Để làm gì: sửa vài trang rồi chạy lại, chạy eval / tune nhiều lần, runtime gặp lại câu hỏi cũ —
đều không tốn API.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from utils.config import EMBEDDERS_OUTPUT
from embedders.base import BATCH, Embedder
from utils.vectors import model_slug, text_key

log = logging.getLogger(__name__)

CACHE_DIR = EMBEDDERS_OUTPUT / "cache"


class CachedEmbedder(Embedder):
    """Embedder có cache: nhìn cache trước, chỉ gọi embedder bên trong cho chữ chưa có."""
    kind = "cache"

    def __init__(self, inner: Embedder, cache_dir: str | Path = CACHE_DIR):
        """Nhớ embedder bên trong, tạo thư mục cache theo tên model."""
        super().__init__(inner.model_id, retry=inner.retry)
        self.inner = inner
        self.cache_dir = Path(cache_dir) / model_slug(inner.model_id)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def dim(self) -> int:
        """Số chiều — lấy của embedder bên trong."""
        return self.inner.dim

    @property
    def backend(self) -> str:
        """Tên embedder thật — lấy của embedder bên trong."""
        return self.inner.backend

    @property
    def n_api_calls(self) -> int:          # type: ignore[override]
        """Số lần gọi API — đếm ở embedder bên trong."""
        return self.inner.n_api_calls

    def _request(self, batch: list[str]) -> np.ndarray:
        """Chuyển thẳng cho embedder bên trong."""
        return self.inner._request(batch)

    def _file(self, text: str) -> Path:
        """Đường dẫn file cache của một đoạn chữ (tên file = hash của model + chữ)."""
        return self.cache_dir / f"{text_key(text, self.model_id)}.npy"

    def embed(self, texts: list[str]) -> np.ndarray:
        """Nhúng danh sách chữ, dùng cache.

        Cách làm: chữ có file cache thì đọc file; chữ chưa có thì nhúng theo lô và ghi cache ngay sau
        mỗi lô (hỏng giữa chừng thì lô đã xong vẫn còn).
        """
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
