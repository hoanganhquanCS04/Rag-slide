"""Embedder `openai`: gọi API /v1/embeddings (server OPENAI_BASE_URL, khoá OPENAI_API_KEY).

Vì sao dùng API thay cho model local bge-m3 (đo được): máy không phải giữ model 4.3 GB; offline
thì API nhanh hơn vì gửi cả lô; đổi lại runtime chậm hơn (~719ms mỗi câu hỏi so với ~182ms).
"""

from __future__ import annotations

import logging
import os

import numpy as np

from components.embedders.base import BATCH, RETRY, Embedder

log = logging.getLogger(__name__)

DIM = {"text-embedding-3-small": 1536,
       "text-embedding-3-large": 3072,
       "text-embedding-ada-002": 1536}
TIMEOUT = 60.0


class OpenAIEmbedder(Embedder):
    """Embedder gọi API embeddings kiểu OpenAI."""
    kind = "openai"

    def __init__(self, model_id: str, *, retry: int = RETRY):
        """Đọc khoá + địa chỉ API từ .env, tạo kết nối HTTP."""
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
        """Số chiều theo tên model (không biết thì 1536)."""
        return DIM.get(self.model_id, 1536)

    def _request(self, batch: list[str]) -> np.ndarray:
        """Gửi một lô chữ lên API -> ma trận vector, xếp đúng thứ tự gửi đi."""
        r = self.client.post(self.url, json={"model": self.model_id, "input": batch})
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return np.array([d["embedding"] for d in data], dtype=np.float32)
