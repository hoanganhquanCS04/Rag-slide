"""Tên model và mã hash dùng chung cho cache nhúng và kho vector.

Để làm gì: một chỗ duy nhất đặt tên model ngắn và tính hash chữ. Vector của chunk chỉ nằm trong kho
vector (Chroma) và cache nhúng (`src/output/embedders/cache/`) — không có file .npy riêng.
"""

from __future__ import annotations

import hashlib


def model_slug(model_id: str) -> str:
    """Tên model ngắn để đặt tên file: "openai/text-embedding-3-small" -> "text-embedding-3-small"."""
    return model_id.split("/")[-1].lower()


def text_key(text: str, model_id: str) -> str:
    """Mã hash của cặp (model, chữ).

    Để làm gì: tên file trong cache nhúng — chữ không đổi thì dùng lại vector, không gọi API.
    """
    return hashlib.sha1(f"{model_id}\x00{text}".encode()).hexdigest()
