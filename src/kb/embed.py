"""Nhúng chunk thành vector bằng API embedding của OpenAI.

Xem docs/spec/embedding.md. Bốn điểm dễ sai:

  1. Embed `text_enriched`, KHÔNG phải `text_raw` (§10 cấm).
  2. Nhúng CẢ 51 chunk, kể cả trang phân mục. Lọc là việc của lúc TRUY VẤN.
     Lọc sớm thì audit không đo được hiện tượng trang phân mục cướp kết quả.
  3. `model_id` nằm trong TÊN FILE. Đổi model mà không sinh lại thì truy vấn index cũ
     bằng vector mới -> trả rác mà không báo lỗi (§5 S6a).
  4. API KHÔNG trả sparse vector. §5 S5 đòi hybrid dense+sparse; phần sparse phải
     dựng riêng bằng BM25 ở `search.py`, KHÔNG có sẵn như bge-m3 trước đây.

Vì sao bỏ bge-m3 local (đo được, ghi lại để sau khỏi tranh cãi lại):

    bge-m3 CPU      182ms/câu hỏi · 819ms/chunk · 4.3 GB đĩa · nạp nguội ~10 phút
    API 3-small     719ms/câu hỏi · 0 GB đĩa    · không phải nạp

API CHẬM GẤP 4 ở runtime (mất round-trip mạng) và ăn ~29% budget 2.5s của §2.
Đổi lại máy không phải giữ model. Offline thì API nhanh hơn vì gửi cả lô một lần.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

import numpy as np

from kb.models import ChunkSet

log = logging.getLogger(__name__)

MODEL_ID = "text-embedding-3-small"
DIM = {"text-embedding-3-small": 1536,
       "text-embedding-3-large": 3072,
       "text-embedding-ada-002": 1536}
BATCH = 64          # API nhận cả lô — đây là chỗ offline lãi so với chạy local
TIMEOUT = 60.0
RETRY = 4


def model_slug(model_id: str) -> str:
    return model_id.split("/")[-1].lower()


def _key(text: str, model_id: str) -> str:
    return hashlib.sha1(f"{model_id}\x00{text}".encode()).hexdigest()


class EmbedError(SystemExit):
    """Gọi API nhúng thất bại. Là SystemExit để CLI thoát gọn như cũ; runtime bắt riêng lỗi
    này để lùi về chỉ BM25 thay vì làm khán giả chờ."""


class Embedder:
    """Gọi /v1/embeddings. Có cache trên đĩa theo hash nội dung.

    `retry`: offline để 4 (đợi 1+2+4+8s); runtime để 1 — khán giả đang chờ.
    """

    def __init__(self, model_id: str = MODEL_ID, *,
                 cache_dir: str | Path = "out/kb/.embed_cache", retry: int = RETRY):
        import httpx   # KHÔNG dùng urllib: Cloudflare chặn User-Agent Python-urllib (lỗi 1010)

        try:    # cùng cách scripts/parse_api.py lấy khoá
            from dotenv import load_dotenv

            load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        except ImportError:
            pass

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise SystemExit("thieu OPENAI_API_KEY")
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")

        self.model_id = model_id
        self.retry = retry
        self.url = f"{base}/embeddings"
        self.client = httpx.Client(
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
        self.cache_dir = Path(cache_dir) / model_slug(model_id)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.dim = DIM.get(model_id, 1536)
        self.n_api_calls = 0
        log.info("embed qua API: %s | %d chieu | lo %d", model_id, self.dim, BATCH)

    # ---------------- một lượt gọi API

    def _post(self, batch: list[str]) -> np.ndarray:
        payload = {"model": self.model_id, "input": batch}
        last = ""
        for attempt in range(self.retry):
            try:
                r = self.client.post(self.url, json=payload)
                if r.status_code == 200:
                    data = sorted(r.json()["data"], key=lambda d: d["index"])
                    v = np.array([d["embedding"] for d in data], dtype=np.float32)
                    self.n_api_calls += 1
                    # OpenAI trả sẵn vector chuẩn hoá, ép lại cho chắc: cosine = tích vô hướng
                    n = np.linalg.norm(v, axis=1, keepdims=True)
                    return v / np.maximum(n, 1e-12)
                last = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:                      # mạng rớt, timeout
                last = f"{type(e).__name__}: {e}"
            if attempt + 1 < self.retry:                 # lần cuối thì khỏi đợi
                wait = 2 ** attempt                      # backoff luỹ thừa (§9)
                log.warning("  goi API hong (%d/%d) %s -> doi %ds",
                            attempt + 1, self.retry, last, wait)
                time.sleep(wait)
        raise EmbedError(f"API embedding that bai sau {self.retry} lan: {last}")

    def embed(self, texts: list[str], *, use_cache: bool = True) -> np.ndarray:
        """-> ma trận (len(texts), dim), đã chuẩn hoá L2."""
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        todo: list[int] = []

        for i, t in enumerate(texts):
            f = self.cache_dir / f"{_key(t, self.model_id)}.npy"
            if use_cache and f.exists():
                out[i] = np.load(f)
            else:
                todo.append(i)

        hit = len(texts) - len(todo)
        if hit:
            log.info("  cache: dung lai %d/%d", hit, len(texts))

        for s in range(0, len(todo), BATCH):
            idx = todo[s:s + BATCH]
            vecs = self._post([texts[i] for i in idx])
            for i, v in zip(idx, vecs):
                out[i] = v
                if use_cache:
                    np.save(self.cache_dir / f"{_key(texts[i], self.model_id)}.npy", v)
            log.info("  nhung %d/%d", min(s + BATCH, len(todo)), len(todo))

        return out


# ---------------------------------------------------------------- chạy cả bộ


def embed_chunkset(
    cs: ChunkSet, out_dir: str | Path, *, model_id: str = MODEL_ID,
    use_cache: bool = True,
) -> tuple[Path, Path]:
    """Nhúng CẢ BỘ chunk -> ghi .npy + .json. Trả về đường dẫn hai file."""
    emb = Embedder(model_id)
    texts = [c.text_enriched for c in cs.chunks]      # KHÔNG dùng text_raw (§10)

    t0 = time.perf_counter()
    mat = emb.embed(texts, use_cache=use_cache)
    dt = time.perf_counter() - t0

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{cs.doc_id}__{model_slug(model_id)}"
    p_npy = out_dir / f"{stem}.vectors.npy"
    p_json = out_dir / f"{stem}.vectors.json"

    np.save(p_npy, mat)
    p_json.write_text(json.dumps({
        "doc_id": cs.doc_id,
        "model": model_id,
        "backend": "openai_api",
        "dim": emb.dim,
        "normalized": True,
        "n_vectors": len(texts),
        "n_api_calls": emb.n_api_calls,
        "embed_sec": round(dt, 2),
        "sparse": False,   # API khong tra sparse -> phai dung BM25 rieng (§5 S5)
        "rows": [c.chunk_id for c in cs.chunks],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("nhung %d vector trong %.1fs (%.0fms/chunk, %d lan goi API) -> %s",
             len(texts), dt, dt / max(len(texts), 1) * 1000, emb.n_api_calls, p_npy.name)
    log.warning("sparse CHUA co -> moi la dense, CHUA du hybrid nhu §5 S5 doi hoi")
    return p_npy, p_json
