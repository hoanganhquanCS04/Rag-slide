"""Cấu hình chung của `index/`: gốc repo, thư mục ra, model nhúng.

Chỗ DUY NHẤT trong `index/` nạp `.env`. Factory nào cần biến môi trường thì import từ đây
(bản cũ: hai factory phải import `kb.embed` chỉ để nạp `.env` — kho dính vào embedder).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

try:    # cùng cách src/llm.py lấy khoá — phải nạp TRƯỚC khi đọc EMBED_MODEL
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

MODEL_ID = os.environ.get("EMBED_MODEL", "text-embedding-3-small")   # sửa trong .env, không sửa ở đây

# out/index/<doc_id>/vectors__<model>.{npy,json} · out/index/chroma/ · out/index/.embed_cache/
OUT = ROOT / "out" / "index"
