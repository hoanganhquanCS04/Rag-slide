"""Biến thể `openai` — gọi /v1/embeddings (cổng OPENAI_BASE_URL, khoá OPENAI_API_KEY).

Vì sao bỏ bge-m3 local (đo được, ghi lại để sau khỏi tranh cãi lại):

    bge-m3 CPU      182ms/câu hỏi · 819ms/chunk · 4.3 GB đĩa · nạp nguội ~10 phút
    API 3-small     719ms/câu hỏi · 0 GB đĩa    · không phải nạp

API CHẬM GẤP 4 ở runtime (mất round-trip mạng) và ăn ~29% budget 2.5s của §2.
Đổi lại máy không phải giữ model. Offline thì API nhanh hơn vì gửi cả lô một lần.
Muốn quay lại local: thêm `variants/bge_m3.py` + một dòng trong `embedders/factory.py`.
"""

from __future__ import annotations

import logging
import os

import numpy as np

from embedders.base import BATCH, RETRY, Embedder

log = logging.getLogger(__name__)

DIM = {"text-embedding-3-small": 1536,
       "text-embedding-3-large": 3072,
       "text-embedding-ada-002": 1536}
TIMEOUT = 60.0


class OpenAIEmbedder(Embedder):
    kind = "openai"

    def __init__(self, model_id: str, *, retry: int = RETRY):
        super().__init__(model_id, retry=retry)
        import httpx   # KHÔNG dùng urllib: Cloudflare chặn User-Agent Python-urllib (lỗi 1010)

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise SystemExit("thieu OPENAI_API_KEY")
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")

        self.url = f"{base}/embeddings"
        self.client = httpx.Client(
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        log.info("embed qua API: %s | %d chieu | lo %d", model_id, self.dim, BATCH)

    @property
    def dim(self) -> int:
        return DIM.get(self.model_id, 1536)

    def _request(self, batch: list[str]) -> np.ndarray:
        r = self.client.post(self.url, json={"model": self.model_id, "input": batch})
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return np.array([d["embedding"] for d in data], dtype=np.float32)
