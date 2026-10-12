"""Cấu hình chung: gốc repo, thư mục prompt, thư mục output, model nhúng.

Chỗ DUY NHẤT nạp `.env` và tính đường dẫn gốc — module nào cần thì import từ đây, không tự tính
`Path(__file__).parents[...]` (dời file là lệch, như `llm.py` từng trỏ nhầm sang `src/`).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:    # phải nạp TRƯỚC khi đọc EMBED_MODEL / LLM_MODEL
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

MODEL_ID = os.environ.get("EMBED_MODEL", "text-embedding-3-small")   # sửa trong .env, không sửa ở đây

PROMPTS_DIR = ROOT / "src" / "prompts"

# Mọi artifact pipeline sinh ra. Mỗi thư mục con TRÙNG TÊN thư mục code trong src/ sinh ra nó.
OUTPUT_DIR = ROOT / "src" / "output"
PARSER_OUTPUT = OUTPUT_DIR / "parser"          # <doc_id>/document.json + cache VLM (S0)
CHUNKER_OUTPUT = OUTPUT_DIR / "chunker"        # <doc_id>/chunks.json (S5)
EMBEDDERS_OUTPUT = OUTPUT_DIR / "embedders"    # <doc_id>/vectors__<model>.{npy,json} · cache/<model>/
VECTORDB_OUTPUT = OUTPUT_DIR / "vectordb"      # chroma/ (chỉ khi VECTOR_DB=chroma)
RETRIEVAL_OUTPUT = OUTPUT_DIR / "retrieval"    # <doc_id>/deck_map.txt + kết quả đo (eval, tune, audit)
SCENARIO_OUTPUT = OUTPUT_DIR / "scenario"      # <doc_id>/scenario.{json,md} (S4)
