"""Kiểu dữ liệu của runtime v0 — bản chạy bằng CHỮ, hỏi đáp ở CUỐI MỖI TRANG.

Chưa có giọng nói, chưa ngắt lời giữa trang: robot "nói" hết trang (in từng câu kịch bản),
rồi mở phiên hỏi đáp. Đủ để làm đúng phần khó — hiểu câu hỏi, tìm, trả lời có nguồn,
điều hướng có cổng tin cậy — trước khi lắp TTS và R7.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class RuntimeConfig(BaseModel):
    """Đọc từ config/runtime.json — ngưỡng để trong config, không hardcode (§6)."""

    calibrated: bool = False            # biên RRF chưa fit trên bộ eval -> chỉ là phanh tạm
    gate_min_margin: float = 0.002      # rrf(top1) - rrf(top2) dưới mức này -> hỏi lại
    top_k: int = 5
    max_contexts: int = 5               # số trang tìm được đưa vào prompt = cả top-k
    max_answer_sentences: int = 4
    llm_timeout_s: float = 30.0
    llm_retry: int = 1                  # khán giả đang chờ: lỗi là escalate, không đợi
    llm_extra: dict[str, Any] = Field(default_factory=dict)
    json_mode: bool = True              # false: không gửi response_format (cổng không chạy JSON mode cho model)

    @classmethod
    def load(cls, path: str | Path) -> RuntimeConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})


class Context(BaseModel):
    """Một đoạn đưa vào prompt. LLM chỉ được trả lời bằng những đoạn này — `grounding` của
    câu trả lời phải trỏ vào `chunk_id` có trong danh sách, code kiểm lại."""

    chunk_id: str
    page_no: int
    text: str
    vlm_ratio: float = 0.0              # bao nhiêu phần do VLM tả — có thể sai (NT2)


class Reply(BaseModel):
    """Robot đáp lại một câu hỏi."""

    action: Literal["goto", "answer", "ask_back", "meta", "escalate"]
    text: str = ""                      # câu robot nói ra
    page: int | None = None             # goto: trang nhảy tới
    command: Literal["repeat", "stop"] | None = None     # meta
    sources: list[str] = Field(default_factory=list)     # answer: chunk_id đã dùng
    candidates: list[int] = Field(default_factory=list)  # ask_back / escalate: trang gợi ý
    via: Literal["fastpath", "llm", "fallback"] = "llm"  # để đo: ai quyết định
    note: str = ""                      # lý do nội bộ, KHÔNG nói ra (vd lỗi API)


class Turn(BaseModel):
    """Một lượt hỏi đáp — ghi ra logs/runtime/ để đo và làm bộ eval."""

    at_slide: int                       # chụp lúc NHẬN câu hỏi (§6), không phải lúc xử lý
    question: str
    reply: Reply
    search_mode: str = ""               # hybrid | sparse (lùi về khi API nhúng lỗi)
    ms: dict[str, int] = Field(default_factory=dict)
