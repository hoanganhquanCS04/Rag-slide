"""Đọc / ghi file vector trên đĩa: `src/output/embedders/<doc_id>/vectors__<model>.{npy,json}`.

Để làm gì: một chỗ duy nhất đặt tên, ghi và đọc file vector.
    .npy    ma trận vector, hàng i = chunk i trong chunks.json
    .json   thông tin kèm theo: model, chunk_id từng hàng, hash chữ từng hàng
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from utils.config import EMBEDDERS_OUTPUT
from models.kb_chunk import ChunkSet

REBUILD = "chay `python src/offline/embedder/cli.py src/output/chunker/<ten>/chunks.json` (trung cache, khong ton API)"


def model_slug(model_id: str) -> str:
    """Tên model ngắn để đặt tên file: "openai/text-embedding-3-small" -> "text-embedding-3-small"."""
    return model_id.split("/")[-1].lower()


def text_key(text: str, model_id: str) -> str:
    """Mã hash của cặp (model, chữ).

    Để làm gì: biết vector đang có còn khớp với chữ của chunk không (dùng cho cache và kiểm tra).
    """
    return hashlib.sha1(f"{model_id}\x00{text}".encode()).hexdigest()


def vectors_file(doc_id: str, model_id: str) -> Path:
    """Đường dẫn file .npy của một deck với một model (tên model nằm trong tên file)."""
    return EMBEDDERS_OUTPUT / doc_id / f"vectors__{model_slug(model_id)}.npy"


def save_vectors(cs: ChunkSet, mat: np.ndarray, *, model_id: str,
                 **info: Any) -> tuple[Path, Path]:
    """Ghi ma trận vector ra file .npy và thông tin kèm theo ra file .json.

    Cách làm: `info` (thời gian, số lần gọi API...) chỉ ghi thêm cho người đọc.
    """
    p_npy = vectors_file(cs.doc_id, model_id)
    p_npy.parent.mkdir(parents=True, exist_ok=True)
    p_json = p_npy.with_suffix(".json")

    np.save(p_npy, mat)
    p_json.write_text(json.dumps({
        "doc_id": cs.doc_id,
        "model": model_id,
        "dim": int(mat.shape[1]) if mat.ndim == 2 else 0,
        "normalized": True,
        "n_vectors": len(cs.chunks),
        **info,
        "sparse": False,   # API khong tra sparse — BM25 dung rieng o sparse/
        "rows": [c.chunk_id for c in cs.chunks],
        "keys": [text_key(c.text_enriched, model_id) for c in cs.chunks],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p_npy, p_json


def load_vectors(cs: ChunkSet, path: str | Path, model_id: str) -> np.ndarray:
    """Đọc file .npy và kiểm nó khớp với bộ chunk hiện tại.

    Cách làm: so model, so chunk_id từng hàng, so hash chữ từng hàng; lệch cái nào là dừng và
    bảo chạy lại bước nhúng.
    """
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"khong thay vector: {p}\n{REBUILD}")
    info = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
    if info.get("model") != model_id:
        raise SystemExit(f"{p.name}: nhung bang '{info.get('model')}', dang can '{model_id}'")
    if info.get("rows") != [c.chunk_id for c in cs.chunks]:
        raise SystemExit(f"{p.name}: vector khong khop bo chunk — chunk da doi ma chua nhung lai?\n"
                         f"{REBUILD}")
    if info.get("keys") != [text_key(c.text_enriched, model_id) for c in cs.chunks]:
        raise SystemExit(f"{p.name}: chu cua chunk da doi (hoac file cu chua co 'keys')\n{REBUILD}")
    return np.load(p)
