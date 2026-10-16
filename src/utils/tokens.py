"""Đếm token bằng đúng tokenizer của model nhúng (cl100k_base)."""

from __future__ import annotations

from functools import lru_cache

TOKENIZER_ID = "cl100k_base"   # tokenizer của text-embedding-3-*


@lru_cache(maxsize=1)
def _encoding():
    """Nạp tokenizer một lần rồi giữ lại (lru_cache)."""
    import tiktoken

    return tiktoken.get_encoding(TOKENIZER_ID)


def count_tokens(text: str) -> int:
    """Đếm số token của một đoạn chữ."""
    return len(_encoding().encode(text))
