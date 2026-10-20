"""Lớp cha của embedder: danh sách chữ -> ma trận vector (đã chuẩn hoá độ dài về 1).

Để làm gì: phần dùng chung — chia lô, thử lại khi lỗi, chuẩn hoá. Embedder cụ thể chỉ phải viết
`_request` (gọi API cho một lô) và `dim` (số chiều). Cache không ở đây mà ở `cache.py`.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import ClassVar

import numpy as np

from models.kb_chunk import ChunkSet

log = logging.getLogger(__name__)

BATCH = 64          # API nhận cả lô — đây là chỗ offline lãi so với chạy local
RETRY = 4


class EmbedError(SystemExit):
    """Lỗi khi gọi nhúng hỏng hết số lần thử (runtime bắt lỗi này để chỉ dùng BM25)."""


class Embedder(ABC):
    """Khung của embedder. `retry`: offline để 4 (đợi 1+2+4+8s), runtime để 1 (khán giả đang chờ)."""

    kind: ClassVar[str]
    n_api_calls: int = 0

    def __init__(self, model_id: str, *, retry: int = RETRY):
        """Nhớ tên model và số lần thử."""
        self.model_id = model_id
        self.retry = retry

    @property
    @abstractmethod
    def dim(self) -> int:
        """Số chiều của vector (lớp con tự khai)."""
        ...

    @abstractmethod
    def _request(self, batch: list[str]) -> np.ndarray:
        """Gọi API một lần cho một lô chữ -> ma trận (số chữ, số chiều); lỗi thì báo lỗi (lớp con tự viết)."""

    @property
    def backend(self) -> str:
        """Tên embedder thật sự nhúng (ghi vào file .json kèm vector)."""
        return self.kind

    def _call(self, batch: list[str]) -> np.ndarray:
        """Gọi `_request` cho một lô, lỗi thì đợi 1s, 2s, 4s... rồi thử lại; hết lượt thì báo EmbedError."""
        last = ""
        for attempt in range(self.retry):
            try:
                v = self._request(batch)
                self.n_api_calls += 1
                return v
            except Exception as e:                       # HTTP lỗi, mạng rớt, timeout
                last = f"{type(e).__name__}: {e}"
            if attempt + 1 < self.retry:                 # lần cuối thì khỏi đợi
                wait = 2 ** attempt                      # backoff luỹ thừa (§9)
                log.warning("  goi API hong (%d/%d) %s -> doi %ds",
                            attempt + 1, self.retry, last, wait)
                time.sleep(wait)
        raise EmbedError(f"API embedding that bai sau {self.retry} lan: {last}")

    def embed(self, texts: list[str]) -> np.ndarray:
        """Nhúng danh sách chữ -> ma trận vector.

        Cách làm: chia lô 64 chữ, gọi API từng lô, chuẩn hoá mỗi vector về độ dài 1 (để cosine = tích vô hướng).
        """
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for s in range(0, len(texts), BATCH):
            v = np.asarray(self._call(texts[s:s + BATCH]), dtype=np.float32)
            out[s:s + len(v)] = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
            log.debug("  nhung %d/%d", min(s + BATCH, len(texts)), len(texts))
        return out


def embed_chunkset(cs: ChunkSet, embedder: Embedder) -> np.ndarray:
    """Nhúng mọi chunk (kể cả trang chuyển chương) -> ma trận vector, hàng i = chunk i.

    Cách làm: nhúng `text_enriched` (chữ có tiền tố), không phải `text_raw`.
    """
    texts = [c.text_enriched for c in cs.chunks]      # KHÔNG dùng text_raw (§10)
    t0 = time.perf_counter()
    mat = embedder.embed(texts)
    dt = time.perf_counter() - t0
    log.info("nhung %d vector trong %.1fs (%.0fms/chunk, %d lan goi API)",
             len(texts), dt, dt / max(len(texts), 1) * 1000, embedder.n_api_calls)
    return mat
