"""Con trỏ ngược lên object cha: block -> trang -> deck, chunk -> bộ chunk, câu -> trang kịch bản.

Giữ trong `PrivateAttr` nên KHÔNG ghi ra JSON, gán lại mỗi lần nạp bằng `link()` của object cha.

Luôn `==` nhau: pydantic so cả attribute ẩn trong `__eq__`. So con trỏ ngược là so ngược lên cha,
cha lại so các con, con lại so cha... -> `RecursionError` khi so hai cây khác nhau (đã chạy thử,
pydantic 2.13). Phép so model chỉ được so DỮ LIỆU, không so vị trí trong cây.

Deep copy KHÔNG chép theo object cha (chép theo là nhân đôi cả cây, con trỏ vào bản thứ ba): bản
chép ra rỗng, object cha tự `link()` lại trong `__deepcopy__` của nó.
"""

from __future__ import annotations

from typing import Any


class BackRef:
    __slots__ = ("target",)

    def __init__(self, target: Any = None) -> None:
        self.target = target

    def __eq__(self, other: object) -> bool:
        return isinstance(other, BackRef)

    def __deepcopy__(self, memo: dict[int, Any]) -> BackRef:
        return BackRef()

    __hash__ = None  # type: ignore[assignment]
