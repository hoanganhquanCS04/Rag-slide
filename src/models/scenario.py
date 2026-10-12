"""Schema của kịch bản (S4). Xem docs/spec/scenario.md §12.

Lưu ở file RIÊNG `src/output/scenario/<doc_id>/scenario.json`, không nhét vào `ParsedDocument`:
ParsedDocument suy ra được từ PDF (parse lại là có), kịch bản thì LLM sinh + người duyệt
(mất là phải trả tiền + duyệt lại).

Back-reference (KHÔNG ghi JSON, gán lại mỗi lần nạp): `sentence.slide`, `sentence.page_no`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, model_validator

from models.backref import BackRef

SYL_PER_MIN = 200          # tiếng Việt nói 190–210 âm tiết/phút (§5 S4)

# Cờ đỏ: KHÔNG đóng gói được cho tới khi người duyệt xử lý
RED_FLAGS = {
    "bad_grounding",            # content không trỏ đúng khối / delivery mang số, thuật ngữ
    "empty_script",
    "llm_failed",
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

    _slide_ref: BackRef = PrivateAttr(default_factory=BackRef)

    @property
    def slide(self) -> SlideScript | None:
        """Kịch bản trang chứa câu này."""
        return self._slide_ref.target

    @property
    def page_no(self) -> int | None:
        return self.slide.page_no if self.slide else None


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

    @model_validator(mode="after")
    def _link(self) -> SlideScript:
        return self.link()

    def link(self) -> SlideScript:
        """Gán `sentence.slide` cho mọi câu."""
        for s in self.sentences:
            s._slide_ref = BackRef(self)
        return self

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> SlideScript:
        return super().__deepcopy__(memo).link()

    @property
    def syllables(self) -> int:
        return sum(s.syllables for s in self.sentences)

    @property
    def seconds(self) -> float:
        return self.syllables / SYL_PER_MIN * 60

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

    @model_validator(mode="after")
    def _link(self) -> Scenario:
        return self.link()

    def link(self) -> Scenario:
        """Gán lại cả cây — slide dựng bằng `model_copy` không chạy validator nên back-ref cũ."""
        for s in self.slides:
            s.link()
        return self

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.slides)
