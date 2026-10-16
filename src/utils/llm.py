"""Gọi LLM qua API chat/completions, dùng chung cho kịch bản (S4), runtime và các bước VLM.

Để làm gì: một chỗ lo việc gửi request, thử lại khi lỗi, và ghi log prompt + phản hồi vào `logs/`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from utils.config import ROOT

log = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("LLM_MODEL", "gpt-5-mini")     # sửa trong .env, không sửa ở đây


def _loggable(messages: list[dict]) -> list[dict]:
    """Bản sao của tin nhắn để ghi log: thay ảnh base64 (rất dài) bằng một dòng mô tả."""
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            c = [{**x, "image_url": {"url": f"<anh base64, {len(x['image_url']['url'])} ky tu>"}}
                 if x.get("type") == "image_url" else x for x in c]
        out.append({**m, "content": c})
    return out


def _unfence(s: str) -> str:
    """Bỏ khung ```json ... ``` mà một số model bọc quanh JSON."""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    return s


class LLM:
    """Client gọi LLM: một object dùng cho nhiều lần gọi.

    Cách làm: chọn server (LLM_BASE_URL nếu có, không thì OPENAI_BASE_URL) và khoá API lúc tạo;
    `chat()` gửi request, `close()` đóng kết nối.
    """
    def __init__(self, model: str, log_dir: Path, *, retry: int = 4, timeout: float = 180.0,
                 backoff: float = 1.0, extra: dict[str, Any] | None = None,
                 base_url: str | None = None, api_key: str | None = None):
        """Chọn server + khoá API, tạo kết nối HTTP, tạo thư mục log.

        Cách làm: có server riêng thì KHÔNG gửi OPENAI_API_KEY sang đó; `retry`, `backoff` quyết định
        thử lại mấy lần và đợi bao lâu.
        """
        import httpx

        if base_url is None:              # LLM chữ (S4, runtime) đi cổng riêng nếu .env khai
            base_url = os.environ.get("LLM_BASE_URL") or None
            api_key = api_key or os.environ.get("LLM_API_KEY")
        if base_url:
            # Cổng riêng (vd Gemma local qua vLLM): KHÔNG gửi OPENAI_API_KEY sang host khác.
            # vLLM không bật --api-key thì nhận khoá nào cũng được.
            key = api_key or "EMPTY"
        else:
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise SystemExit("thieu OPENAI_API_KEY")
        base = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
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
        self.json_mode = True                      # cổng không có kênh JSON mode -> tắt, nhớ cho cả phiên

    async def chat(self, messages: list[dict], tag: str, *, raw: bool = False) -> Any:
        """Gửi tin nhắn cho LLM -> JSON đã đọc (hoặc chữ nguyên văn nếu `raw=True`).

        Cách làm: gửi request; 200 thì ghi log rồi trả kết quả; server không hỗ trợ JSON mode thì tắt
        JSON mode và gửi lại ngay; lỗi khác thì đợi (5s, 10s, 20s...) rồi thử lại; hết lượt thì báo lỗi.
        """
        payload = {"model": self.model, "messages": messages, **self.extra}
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        last = ""
        attempt = 0
        while attempt < self.retry:
            t0 = time.perf_counter()
            try:
                r = await self.client.post(self.url, json=payload)
                dt = time.perf_counter() - t0
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
                    self.n_calls += 1
                    (self.log_dir / f"{tag}.json").write_text(json.dumps(
                        {"model": self.model, "sec": round(dt, 1),
                         "messages": _loggable(messages), "response": content},
                        ensure_ascii=False, indent=2), encoding="utf-8")
                    if raw:
                        return content
                    try:
                        return json.loads(_unfence(content))
                    except json.JSONDecodeError:
                        # 2026-10-06 cổng đổi cách báo lần nữa: flash-lite + JSON mode -> HTTP 200 kèm
                        # CHỮ "Gemini 3.5 Flash is no longer available…" (~2/3 lần), tắt JSON mode thì
                        # 9/9 lần qua. Bật JSON mode mà nhận chữ không phải JSON -> bỏ JSON mode cho cả
                        # phiên, gửi lại NGAY (như nhánh 503 dưới). Đã tắt rồi mà vẫn hỏng -> lỗi thật.
                        if "response_format" not in payload:
                            raise
                        log.warning("  %s: %s bat JSON mode tra chu khong phai JSON -> bo response_format "
                                    "cho ca phien", tag, self.model)
                        self.json_mode = False
                        payload.pop("response_format")
                        continue
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                if "response_format" in payload and ("get_channel_failed" in r.text or r.status_code == 503):
                    # Cổng (key nhóm "starter") không có kênh nào chạy JSON mode cho model này —
                    # đo 2026-10-01: cùng request bỏ response_format thì qua. 2026-10-02 cổng đổi
                    # cách báo: flash-lite + JSON mode -> 503 "temporarily unavailable", bỏ JSON mode
                    # thì 200. Prompt vẫn đòi JSON, _unfence + json.loads vẫn kiểm.
                    # Gửi lại NGAY, không tính một lần thử: runtime chỉ có retry=1, tính vào là hết lượt.
                    if self.json_mode:                     # báo một lần mỗi phiên, không mỗi request
                        log.warning("  %s: cong khong chay JSON mode cho %s -> bo response_format cho ca phien",
                                    tag, self.model)
                    self.json_mode = False
                    payload.pop("response_format")
                    continue
            except Exception as e:                         # mạng, timeout, JSON hỏng
                last = f"{type(e).__name__}: {e}"
            attempt += 1
            if attempt < self.retry:
                wait = self.backoff * 2 ** (attempt - 1)
                log.warning("  %s loi (%d/%d) %s -> doi %.0fs", tag, attempt, self.retry, last, wait)
                await asyncio.sleep(wait)
        raise RuntimeError(last)

    async def close(self) -> None:
        """Đóng kết nối HTTP."""
        await self.client.aclose()
