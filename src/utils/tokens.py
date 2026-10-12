"""Đếm token bằng tokenizer THẬT của model nhúng — hàm dùng chung cho mọi chunker."""

from __future__ import annotations

from functools import lru_cache

TOKENIZER_ID = "cl100k_base"   # tokenizer của text-embedding-3-*


@lru_cache(maxsize=1)
def _encoding():
    import tiktoken

    return tiktoken.get_encoding(TOKENIZER_ID)


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))
