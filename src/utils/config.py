"""Cấu hình chung: đường dẫn gốc repo, thư mục prompt, thư mục output, tên model nhúng.

Để làm gì: mọi file khác lấy đường dẫn và cấu hình từ ĐÂY, không tự tính.
Cách làm: import file này là nạp luôn `.env`; các hằng số bên dưới tính một lần.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:    # phải nạp TRƯỚC khi đọc EMBED_MODEL / LLM_MODEL
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

MODEL_ID = os.environ.get("EMBED_MODEL", "text-embedding-3-small")   # sửa trong .env, không sửa ở đây

PROMPTS_DIR = ROOT / "src" / "prompts"
EVAL_QUERIES_DIR = ROOT / "data" / "eval"      # <doc_id>.queries.json — câu hỏi có nhãn để đo tìm kiếm

# Mọi artifact pipeline sinh ra. Mỗi thư mục con TRÙNG TÊN thư mục code trong src/ sinh ra nó.
OUTPUT_DIR = ROOT / "src" / "output"
PARSER_OUTPUT = OUTPUT_DIR / "parser"          # <doc_id>/document.json + cache VLM (S0)
EMBEDDERS_OUTPUT = OUTPUT_DIR / "embedders"    # cache/<model>/<sha1>.npy — cache nhúng theo hash chữ
VECTORDB_OUTPUT = OUTPUT_DIR / "vectordb"      # chroma/ — kho vector (vector + chữ + metadata)
EVALUATION_OUTPUT = OUTPUT_DIR / "evaluation"  # <doc_id>/eval.json · <tên chunks>_tune.{md,json} · _misses.json
SCENARIO_OUTPUT = OUTPUT_DIR / "scenario"      # <doc_id>/scenario.{json,md} (S4)


def console_setup() -> None:
    """Cho CLI: in tiếng Việt không vỡ (stdout/stderr utf-8) + log mức INFO chỉ in nội dung."""
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
