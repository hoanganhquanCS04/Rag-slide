"""Lớp cách ly giữa docling và pipeline.

`DoclingDocument` là đồ thị con trỏ `$ref` theo schema riêng của docling. Dính trực
tiếp vào nó ở mọi stage thì đổi version docling là vỡ cả pipeline. `ParsedDocument`
là bản chuẩn hoá: cây chứa lồng nhau, toạ độ đã quy về [0,1] gốc trên-trái, và mọi
mẩu nội dung đều khai rõ `provenance` theo NT2.

Lớp này chỉ BIỂU DIỄN — không chunk, không enrich, không embed. Đó là việc của S5.
"""

from parsing.models import (
    Block,
    Flag,
    ParsedDocument,
    ParsedImage,
    ParsedPage,
    ParsedParagraph,
    ParsedTable,
    ParserInfo,
    Provenance,
    SectionSpan,
    SourceInfo,
)

__all__ = [
    "Block",
    "Flag",
    "ParsedDocument",
    "ParsedImage",
    "ParsedPage",
    "ParsedParagraph",
    "ParsedTable",
    "ParserInfo",
    "Provenance",
    "SectionSpan",
    "SourceInfo",
]
