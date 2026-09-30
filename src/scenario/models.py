"""Schema của kịch bản (S4). Xem docs/spec/scenario.md §12.

Lưu ở file RIÊNG `out/deck/<doc_id>/scenario.json`, không nhét vào `ParsedDocument`:
ParsedDocument suy ra được từ PDF (parse lại là có), kịch bản thì LLM sinh + người duyệt
(mất là phải trả tiền + duyệt lại).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SYL_PER_MIN = 200          # tiếng Việt nói 190–210 âm tiết/phút (§5 S4)

# Cờ đỏ: KHÔNG đóng gói được cho tới khi người duyệt xử lý
RED_FLAGS = {
    "ungrounded_content_sentence",
    "bad_grounding_ref",
    "divider_has_content",
    "title_grounded_claim",     # trỏ vào tiêu đề mà nói nhiều hơn tên chủ đề = bơm kiến thức
    "empty_script",
    "llm_failed",
    "stale_manual",             # người sửa tay, rồi nội dung trang đổi -> câu có thể sai
}


class Prosody(BaseModel):
    emphasis: list[str] = Field(default_factory=list)
    pause_before_ms: int = 0
    speed: float = 1.0


class Grounding(BaseModel):
    type: Literal["kb_chunk", "structure", "style"]
    ref: str | None = None          # block_id, vd "p011.b02"


class Sentence(BaseModel):
    kind: Literal["content", "delivery"]
    text: str
    grounding: Grounding | None = None   # content mà None -> CỜ ĐỎ
    syllables: int = 0                   # CODE tính theo kho phát âm, không để LLM khai
    prosody: Prosody = Field(default_factory=Prosody)


class SlideScript(BaseModel):
    page_no: int
    page_hash: str
    slide_type: Literal["section_divider", "exercise", "content"]
    section_id: str | None = None
    sentences: list[Sentence] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    unknown_terms: list[str] = Field(default_factory=list)
    passes: int = 1                      # 2 = phải sửa một lần -> đáng soi
    model: str = ""
    prompt_hash: str = ""
    # "nguoi" = đã sửa tay trong scenario.json -> máy KHÔNG BAO GIỜ viết đè, kể cả
    # đổi prompt hay --force. Muốn máy viết lại thì đổi về "llm".
    edited_by: Literal["llm", "nguoi"] = "llm"

    @property
    def syllables(self) -> int:
        return sum(s.syllables for s in self.sentences)

    @property
    def seconds(self) -> float:
        return self.syllables / SYL_PER_MIN * 60

    @property
    def delivery_ratio(self) -> float:
        tot = self.syllables
        return sum(s.syllables for s in self.sentences if s.kind == "delivery") / tot if tot else 0.0

    @property
    def red_flags(self) -> list[str]:
        return [f for f in self.flags if f.split(":")[0] in RED_FLAGS]

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)


class Scenario(BaseModel):
    doc_id: str
    model: str
    prompt_hash: str
    # hash CÁC MỤC KỊCH BẢN NÀY DÙNG trong kho chung (không phải cả kho) — S6b so trước
    # khi synth, lệch là DỪNG
    pronunciation_hash: str
    slides: list[SlideScript] = Field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.slides)
