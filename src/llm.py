"""Gọi LLM qua API chat/completions — dùng chung cho S4 (offline) và runtime.

JSON mode, retry luỹ thừa, log full prompt + response vào `logs/` (CLAUDE.md §9).
Offline và runtime khác nhau ở ĐỘ KIÊN NHẪN, không ở cách gọi:

    S4 (offline)   retry=4, backoff=5s     — không vội, lỗi thì đợi 5+10+20s rồi thử lại
                                             (đo được: 503 "temporarily unavailable" cả loạt,
                                              đợi 1+2+4s là hết lượt trước khi cổng hồi)
    runtime        retry=1, timeout ngắn   — khán giả đang chờ, lỗi thì escalate ngay
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "gpt-5-mini")     # sửa trong .env, không sửa ở đây


def _unfence(s: str) -> str:
    """Gemini qua cổng OpenAI vẫn bọc JSON trong ```json ... ``` dù đã bật JSON mode."""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    return s


class LLM:
    def __init__(self, model: str, log_dir: Path, *, retry: int = 4, timeout: float = 180.0,
                 backoff: float = 1.0, extra: dict[str, Any] | None = None):
        import httpx

        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise SystemExit("thieu OPENAI_API_KEY")
        base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.url = f"{base}/chat/completions"
        self.model = model
        self.retry = retry
        self.backoff = backoff                     # giây chờ lần đầu, sau đó nhân đôi
        self.extra = extra or {}                   # tham số riêng của model, vd reasoning_effort
        self.client = httpx.AsyncClient(timeout=timeout,
                                        headers={"Authorization": f"Bearer {key}"})
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.n_calls = 0

    async def chat(self, messages: list[dict], tag: str) -> dict:
        payload = {"model": self.model, "messages": messages,
                   "response_format": {"type": "json_object"}, **self.extra}
        last = ""
        for attempt in range(self.retry):
            t0 = time.perf_counter()
            try:
                r = await self.client.post(self.url, json=payload)
                dt = time.perf_counter() - t0
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
                    self.n_calls += 1
                    (self.log_dir / f"{tag}.json").write_text(json.dumps(
                        {"model": self.model, "sec": round(dt, 1),
                         "messages": messages, "response": content},
                        ensure_ascii=False, indent=2), encoding="utf-8")
                    return json.loads(_unfence(content))
                last = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:                         # mạng, timeout, JSON hỏng
                last = f"{type(e).__name__}: {e}"
            if attempt + 1 < self.retry:
                wait = self.backoff * 2 ** attempt
                log.warning("  %s loi (%d/%d) %s -> doi %.0fs", tag, attempt + 1, self.retry, last, wait)
                await asyncio.sleep(wait)
        raise RuntimeError(last)

    async def close(self) -> None:
        await self.client.aclose()
