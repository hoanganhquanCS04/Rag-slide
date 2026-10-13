"""Lớp cha của mọi embedder: danh sách chữ -> ma trận vector đã chuẩn hoá L2.

Biến thể chỉ cần viết `_request` (MỘT lượt gọi cho một lô) và `dim`. Cắt lô, thử lại có
backoff (§9), chuẩn hoá dùng chung ở đây. Cache KHÔNG ở đây — `cache.py` bọc ngoài.

Xem docs/spec/embedding.md. Bốn điểm dễ sai:

  1. Embed `text_enriched`, KHÔNG phải `text_raw` (§10 cấm).
  2. Nhúng CẢ bộ chunk, kể cả trang phân mục. Lọc là việc của lúc TRUY VẤN.
     Lọc sớm thì audit không đo được hiện tượng trang phân mục cướp kết quả.
  3. `model_id` nằm trong TÊN FILE (`utils/vectors.py`). Đổi model mà không sinh lại thì truy
     vấn index cũ bằng vector mới -> trả rác mà không báo lỗi (§5 S6a).
  4. API KHÔNG trả sparse vector. Nhánh sparse là BM25 dựng riêng ở `retrieval/sparse/`, gộp với
     dense ở `search.py` — KHÔNG có sẵn như bge-m3 trước đây.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import ClassVar

import numpy as np

log = logging.getLogger(__name__)

BATCH = 64          # API nhận cả lô — đây là chỗ offline lãi so với chạy local
RETRY = 4


class EmbedError(SystemExit):
    """Gọi nhúng hỏng hết số lần thử. `runtime/retrieve.py` bắt cái này để lùi về chỉ BM25."""


class Embedder(ABC):
    """`retry`: offline để 4 (đợi 1+2+4+8s); runtime để 1 — khán giả đang chờ."""

    kind: ClassVar[str]
    n_api_calls: int = 0

    def __init__(self, model_id: str, *, retry: int = RETRY):
        self.model_id = model_id
        self.retry = retry

    @property
    @abstractmethod
    def dim(self) -> int: ...

    @abstractmethod
    def _request(self, batch: list[str]) -> np.ndarray:
        """MỘT lượt gọi cho một lô -> (len(batch), dim). Hỏng thì raise — `_call` lo thử lại."""

    @property
    def backend(self) -> str:
        """Tên biến thể THẬT nhúng — ghi vào sidecar .json. Lớp bọc trả tên lớp bên trong."""
        return self.kind

    def _call(self, batch: list[str]) -> np.ndarray:
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
        """-> ma trận (len(texts), dim), đã chuẩn hoá L2: cosine = tích vô hướng."""
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for s in range(0, len(texts), BATCH):
            v = np.asarray(self._call(texts[s:s + BATCH]), dtype=np.float32)
            out[s:s + len(v)] = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
            log.debug("  nhung %d/%d", min(s + BATCH, len(texts)), len(texts))
        return out
