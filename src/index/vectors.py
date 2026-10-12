"""File vector trên đĩa: `out/index/<doc_id>/vectors__<model>.{npy,json}`.

MỘT chỗ đặt tên, ghi và đọc. Embedder và kho vector cùng dùng module này, KHÔNG import nhau.

    .npy    ma trận (n_chunk, dim) float32, đã chuẩn hoá L2 — hàng i là chunk i của chunks.json
    .json   sidecar: model, dim, rows[i] = chunk_id hàng i, keys[i] = text_key hàng i

`.npy` là NGUỒN; kho vector (chroma) chỉ là bản sao để tìm, xoá đi thì `sync` dựng lại từ đây.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from index.config import OUT
from kb.models import ChunkSet

REBUILD = "chay `python src/index/cli.py out/kb/<ten>/chunks.json` (trung cache, khong ton API)"


def model_slug(model_id: str) -> str:
    return model_id.split("/")[-1].lower()


def text_key(text: str, model_id: str) -> str:
    """Vân tay 'chữ này nhúng bằng model này'. Tên file cache, kho vector dùng để biết vector
    đang giữ còn khớp chunk không, và `load_vectors` dùng để bắt `.npy` cũ."""
    return hashlib.sha1(f"{model_id}\x00{text}".encode()).hexdigest()


def vectors_file(doc_id: str, model_id: str) -> Path:
    """out/index/<doc_id>/vectors__<model>.npy.

    Tên bài là tên thư mục; tên model PHẢI nằm trong tên file (§10) — đổi model mà trỏ vector
    cũ là trả rác không báo lỗi.
    """
    return OUT / doc_id / f"vectors__{model_slug(model_id)}.npy"


def save_vectors(cs: ChunkSet, mat: np.ndarray, *, model_id: str,
                 **info: Any) -> tuple[Path, Path]:
    """Ghi .npy + .json. `info` (backend, n_api_calls, embed_sec…) chỉ để người đọc."""
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
        "sparse": False,   # API khong tra sparse — BM25 dung rieng o index/sparse/
        "rows": [c.chunk_id for c in cs.chunks],
        "keys": [text_key(c.text_enriched, model_id) for c in cs.chunks],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return p_npy, p_json


def load_vectors(cs: ChunkSet, path: str | Path, model_id: str) -> np.ndarray:
    """Đọc .npy, kiểm hàng i ĐÚNG là chunk i của `cs` — cả chunk_id lẫn CHỮ.

    Chỉ so chunk_id là không đủ: chunker chạy lại đổi chữ mà giữ chunk_id thì kho nạp lại
    vector của chữ CŨ, metadata ghi chữ MỚI — tìm ra trang sai mà không báo lỗi.
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
