"""R1 — lệnh TƯỜNG MINH bằng regex: ~0ms, không gọi model, không tốn tiền.

CẤM bắt câu hỏi nội dung (§10). Chỉ khớp khi CẢ CÂU là lệnh (fullmatch):

    "sang trang 12"        -> nhảy          "trang 12 nói gì"   -> KHÔNG khớp, để LLM
    "trang tiếp đi"        -> nhảy          "quay lại phần 3D"  -> KHÔNG khớp (không có số)
    "nhắc lại"             -> nói lại trang "dừng"              -> kết thúc
"""

from __future__ import annotations

import re

from runtime.models import Reply

_TAIL = r"(?:\s+(?:đi|nhé|nha|ạ|với|giúp|giùm|luôn))*\s*[.!?]*"
_WHO = r"(?:cho (?:tôi|mình|em) )?"
_MOVE = (r"(?:xem |mở |sang |qua |tới |đến |về |lật (?:sang |tới |đến )?"
         r"|chuyển (?:sang |tới |đến )?|quay (?:lại |về )(?:trang |slide )?)?")
_PAGE = r"(?:trang|slide)\s*(?:số\s*)?"

GOTO = re.compile(rf"{_WHO}{_MOVE}{_PAGE}(\d+){_TAIL}")
NEXT = re.compile(rf"{_WHO}(?:sang |qua |tới )?(?:trang|slide) (?:tiếp(?: theo)?|sau|kế(?: tiếp)?){_TAIL}")
PREV = re.compile(rf"{_WHO}(?:quay (?:lại|về) |lùi (?:lại )?|về )(?:trang|slide) (?:trước(?: đó)?|vừa rồi){_TAIL}")
FIRST = re.compile(rf"{_WHO}{_MOVE}(?:trang|slide) đầu(?: tiên)?{_TAIL}")
LAST = re.compile(rf"{_WHO}{_MOVE}(?:trang|slide) cuối(?: cùng)?{_TAIL}")
REPEAT = re.compile(rf"(?:nói|nhắc|đọc|giảng) lại(?: trang này| lần nữa)?{_TAIL}")
STOP = re.compile(rf"(?:dừng|thoát|kết thúc|nghỉ)(?: lại| thôi| ở đây)?{_TAIL}|q|quit|exit")


def match(text: str, current: int, n_pages: int) -> Reply | None:
    """-> Reply nếu câu là lệnh tường minh, None nếu phải để LLM hiểu."""
    t = " ".join(text.lower().split())
    target: int | None = None
    if m := GOTO.fullmatch(t):
        target = int(m.group(1))
    elif NEXT.fullmatch(t):
        target = current + 1
    elif PREV.fullmatch(t):
        target = current - 1
    elif FIRST.fullmatch(t):
        target = 1
    elif LAST.fullmatch(t):
        target = n_pages
    elif REPEAT.fullmatch(t):
        return Reply(action="meta", command="repeat", via="fastpath")
    elif STOP.fullmatch(t):
        return Reply(action="meta", command="stop", via="fastpath")
    else:
        return None

    if not 1 <= target <= n_pages:
        return Reply(action="ask_back", via="fastpath",
                     text=f"Bộ slide chỉ có {n_pages} trang, bạn muốn xem trang nào?")
    return Reply(action="goto", page=target, via="fastpath")
