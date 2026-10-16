"""Kiểu dữ liệu của kịch bản (S4): cả deck -> từng trang, mỗi trang MỘT đoạn văn robot nói.

Để làm gì: lưu riêng ở `src/output/scenario/<doc_id>/scenario.json`, tách khỏi ParsedDocument
(parse lại không làm mất kịch bản đã trả tiền viết).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SYL_PER_MIN = 200          # tiếng Việt nói 190–210 âm tiết/phút (§5 S4)

# Cờ đỏ: KHÔNG đóng gói được cho tới khi người duyệt xử lý
RED_FLAGS = {
    "bad_grounding",            # không có ref, hoặc ref không phải khối của trang
    "empty_script",
    "llm_failed",
}


class SlideScript(BaseModel):
    """Kịch bản của một trang: MỘT đoạn văn robot nói + các khối nguồn của đoạn đó.

    Cách làm: `text` là đoạn văn liền (không chia câu dẫn / câu nội dung); `refs` là id các khối
    trên trang mà đoạn văn lấy thông tin; `syllables` do code đếm theo kho phát âm.
    `page_hash` + `prompt_hash` + `model` dùng để biết trang có phải viết lại không.
    """
    page_no: int
    page_hash: str
    slide_type: Literal["section_divider", "exercise", "content"]
    section_id: str | None = None
    text: str = ""
    refs: list[str] = Field(default_factory=list)   # id khối nguồn, vd ["p011.b02", "p011.b03"]
    syllables: int = 0                             # CODE đếm theo kho phát âm, không để LLM khai
    flags: list[str] = Field(default_factory=list)
    unknown_terms: list[str] = Field(default_factory=list)
    passes: int = 1                      # 2 = phải sửa một lần -> đáng soi
    model: str = ""
    prompt_hash: str = ""

    @property
    def seconds(self) -> float:
        """Thời lượng nói ước tính của trang (âm tiết / 200 mỗi phút)."""
        return self.syllables / SYL_PER_MIN * 60

    @property
    def red_flags(self) -> list[str]:
        """Các cờ đỏ của trang (phải người duyệt mới dùng được)."""
        return [f for f in self.flags if f.split(":")[0] in RED_FLAGS]


class Scenario(BaseModel):
    """Kịch bản cả deck: model, hash prompt, hash phát âm và kịch bản từng trang."""
    doc_id: str
    model: str
    prompt_hash: str
    # hash CÁC MỤC KỊCH BẢN NÀY DÙNG trong kho chung (không phải cả kho) — S6b so trước
    # khi synth, lệch là DỪNG
    pronunciation_hash: str
    slides: list[SlideScript] = Field(default_factory=list)

    @property
    def seconds(self) -> float:
        """Tổng thời lượng nói của cả deck."""
        return sum(s.seconds for s in self.slides)
