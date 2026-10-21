"""Các kiểu dữ liệu dùng khi hỏi đáp.

    RuntimeConfig  cấu hình (đọc từ config/runtime.json)
    Reply          câu robot sẽ nói
    Answer         kết quả đầy đủ của một câu hỏi: Reply + thông tin để xem lại

Hiện chỉ có hỏi đáp. Chưa có: chuyển slide, tự thuyết trình, giọng nói.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.kb_chunk import SearchHit


class RuntimeConfig(BaseModel):
    """Cấu hình hỏi đáp, đọc từ file config/runtime.json."""

    top_k: int = 5                      # tìm lấy mấy trang
    max_contexts: int = 5               # đưa mấy trang vào prompt cho LLM đọc
    max_answer_sentences: int = 4       # câu trả lời dài tối đa mấy câu
    llm_timeout_s: float = 30.0         # chờ LLM tối đa bao nhiêu giây
    llm_retry: int = 1                  # LLM lỗi thì thử lại mấy lần (người nghe đang chờ, nên ít thôi)
    llm_extra: dict[str, Any] = Field(default_factory=dict)   # tham số thêm gửi kèm cho LLM
    json_mode: bool = True              # có bắt LLM trả JSON không (một số model không hỗ trợ -> để false)

    @classmethod
    def load(cls, path: str | Path) -> RuntimeConfig:
        """Đọc file cấu hình. Các khoá bắt đầu bằng "_" là ghi chú, bỏ qua."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})


class Reply(BaseModel):
    """Câu robot sẽ nói, sau khi code đã kiểm."""

    action: Literal["answer", "escalate"]       # answer = trả lời · escalate = từ chối, để người thật trả lời
    text: str = ""                              # câu robot nói ra
    sources: list[str] = Field(default_factory=list)     # trả lời: lấy từ đoạn nào (mã đoạn)
    via: Literal["llm", "fallback"] = "llm"     # llm = LLM quyết · fallback = LLM lỗi, code tự trả lời
    note: str = ""                              # ghi chú nội bộ để xem lại, robot KHÔNG nói ra


class Answer(BaseModel):
    """Kết quả đầy đủ của một câu hỏi: câu robot nói + mọi thứ để xem lại vì sao nó nói vậy."""

    reply: Reply                                # câu robot nói
    ms: dict[str, int] = Field(default_factory=dict)     # mất bao lâu (mili giây): search · llm · total
    search_mode: Literal["hybrid", "sparse"] = "hybrid"  # sparse = API nhúng lỗi, câu này chỉ tìm bằng BM25
    hits: list[SearchHit] = Field(default_factory=list)  # các trang tìm được (vài trang đầu được đưa vào prompt)
    prompt: str = ""                            # prompt đã gửi cho LLM
    raw: dict[str, Any] | None = None           # LLM trả về gì (None = gọi LLM bị lỗi)
    log_path: Path | None = None                # file ghi lại prompt và câu LLM trả về
