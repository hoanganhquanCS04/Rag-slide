"""Soi kho vector (Chroma) của MỘT bài: in hết các bản ghi đang có trong kho + kiểm kho có toàn vẹn không.

Chỉ đọc kho, không ghi gì.

    python scripts/inspect_db.py onboarding_kit                # số liệu + cảnh báo + MỌI bản ghi
    python scripts/inspect_db.py onboarding_kit --show 5       # chỉ in 5 bản ghi đầu (0 = không in)
    python scripts/inspect_db.py onboarding_kit --page 18      # chỉ in các bản ghi của trang 18
    python scripts/inspect_db.py onboarding_kit --full-vector  # in nguyên vector (mặc định chỉ vài số đầu)

Mỗi bản ghi trong kho có đúng 4 trường, in ra đủ cả 4:
    id         chunk_id, vd "onboarding_kit#p018.2" (trang 18, mẩu thứ 2)
    embedding  vector của chữ (1536 số với text-embedding-3-small)
    document   chữ đã đem nhúng = tiền tố [chương · tiêu đề · trang N/M] + chữ của trang
    metadata   doc_id · page_no · section_id · content_type — chỉ dùng để lọc khi tìm

Kiểm kho có toàn vẹn không (in CANH BAO, không có thì in OK):
    - chunk_id lặp
    - page_no trong metadata lệch số trang trong chunk_id ("...#p018.2" phải là trang 18)
    - chunk rỗng (không có chữ)
    - metadata thiếu khoá bắt buộc, hoặc có khoá lạ ngoài 4 khoá chuẩn (kho cũ, chưa ghi lại)
    - số vector khác số bản ghi, vector có NaN, hoặc số chiều lệch nhau
"""

import argparse
import logging
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent       # chỉ để nạp src/ — sau đó dùng utils.config
sys.path.insert(0, str(REPO / "src"))

import numpy as np

from components.vectordb.factory import create_store
from utils.config import MODEL_ID, console_setup

log = logging.getLogger(__name__)

# 4 khoá metadata chuẩn của mỗi bản ghi trong kho
METADATA_KEYS = {"doc_id", "page_no", "section_id", "content_type"}
# khoá bắt buộc phải có — section_id được thiếu (trang không thuộc chương nào thì không ghi)
REQUIRED_KEYS = {"doc_id", "page_no", "content_type"}

# chunk_id "onboarding_kit#p022.3" -> trang 22
PAGE_OF_CHUNK = re.compile(r"#p(\d+)")

# mặc định in mấy số đầu của vector
VECTOR_HEAD = 8


def show_records(doc_id: str, *, show: int | None = None, page: int | None = None,
                 full_vector: bool = False) -> None:
    """In đủ 4 trường (id · embedding · document · metadata) của các bản ghi trong kho, xếp theo trang.

    show         chỉ in N bản ghi đầu; None = in hết
    page         chỉ in các bản ghi của trang này
    full_vector  in nguyên vector; mặc định chỉ vài số đầu + số chiều + độ dài vector
    """
    store = create_store(model_id=MODEL_ID)
    records = sorted(store.get_document(doc_id), key=lambda r: (r.page_no, r.chunk_id))
    if page is not None:
        records = [r for r in records if r.page_no == page]
        if not records:
            log.info("trang %d khong co ban ghi nao trong kho", page)
            return
    if show is not None:
        records = records[:show]
    if not records:
        return

    chunk_ids, vectors = store.get_vectors(doc_id)
    vector_of = dict(zip(chunk_ids, vectors))
    for record in records:
        vector = vector_of.get(record.chunk_id)
        log.info("")
        log.info("=" * 80)
        log.info("id        : %s", record.chunk_id)
        log.info("metadata  : %s", dict(sorted(record.metadata.items())))
        if vector is None:
            log.info("embedding : (khong co vector)")
        else:
            numbers = vector if full_vector else vector[:VECTOR_HEAD]
            tail = "" if full_vector else " ..."
            log.info("embedding : %d chieu · do dai %.4f · [%s%s]", len(vector), float(np.linalg.norm(vector)),
                     " ".join(f"{x:.4f}" for x in numbers), tail)
        log.info("document  : %d ky tu", len(record.document))
        log.info("%s", record.document)


def inspect_store(doc_id: str) -> list[str]:
    """Đọc mọi chunk + vector của một bài trong kho, in số liệu tổng, trả danh sách cảnh báo."""
    store = create_store(model_id=MODEL_ID)
    records = store.get_document(doc_id)
    if not records:
        raise SystemExit(f"{doc_id}: kho {store.name} khong co bai nay — chay "
                         f"`python src/offline/chunker/cli.py src/output/parser/{doc_id}/document.json`")
    chunk_ids, vectors = store.get_vectors(doc_id)

    # Số liệu tổng
    pages = {r.page_no for r in records}
    content_types = Counter(r.metadata.get("content_type", "?") for r in records)
    log.info("kho: %s · collection %s", getattr(store, "path", "?"), store.name)
    log.info("%s: %d chunk · %d trang · %s", doc_id, len(records), len(pages),
             " · ".join(f"{kind} {n}" for kind, n in sorted(content_types.items())))

    warnings: list[str] = []

    # chunk_id lặp
    for chunk_id, n in Counter(r.chunk_id for r in records).items():
        if n > 1:
            warnings.append(f"chunk_id lap {n} lan: {chunk_id}")

    for record in records:
        # page_no lệch số trang trong chunk_id
        match = PAGE_OF_CHUNK.search(record.chunk_id)
        if not match:
            warnings.append(f"chunk_id khong co #pNNN: {record.chunk_id}")
        elif int(match.group(1)) != record.page_no:
            warnings.append(f"page_no lech chunk_id: {record.chunk_id} (metadata {record.page_no})")

        # chunk rỗng
        if not record.document.strip():
            warnings.append(f"chunk rong: {record.chunk_id}")

        # khoá metadata thiếu / lạ
        missing_keys = REQUIRED_KEYS - set(record.metadata)
        if missing_keys:
            warnings.append(f"metadata thieu khoa {sorted(missing_keys)}: {record.chunk_id}")
        extra_keys = set(record.metadata) - METADATA_KEYS
        if extra_keys:
            warnings.append(f"metadata khoa la {sorted(extra_keys)}: {record.chunk_id}")

    # vector: NaN / số chiều lệch
    if len(chunk_ids) != len(records):
        warnings.append(f"so vector ({len(chunk_ids)}) khac so chunk ({len(records)})")
    if vectors.ndim != 2:
        warnings.append(f"vector khong cung so chieu (mang {vectors.shape})")
    else:
        log.info("vector: %d chieu", vectors.shape[1])
        for chunk_id, vector in zip(chunk_ids, vectors):
            if np.isnan(vector).any():
                warnings.append(f"vector co NaN: {chunk_id}")

    return warnings


def main() -> int:
    """Đọc tên bài từ dòng lệnh, soi kho, in cảnh báo (không có thì in OK)."""
    parser = argparse.ArgumentParser(prog="inspect_db", description="Soi kho vector cua mot bai")
    parser.add_argument("doc_id", help="ten bai, vd onboarding_kit")
    parser.add_argument("--show", type=int, default=None, help="chi in N ban ghi dau (0 = khong in), mac dinh in het")
    parser.add_argument("--page", type=int, default=None, help="chi in cac ban ghi cua trang nay")
    parser.add_argument("--full-vector", action="store_true", help="in nguyen vector thay vi vai so dau")
    args = parser.parse_args()

    console_setup()
    warnings = inspect_store(args.doc_id)
    for warning in warnings:
        log.info("CANH BAO: %s", warning)
    if not warnings:
        log.info("OK")
    show_records(args.doc_id, show=args.show, page=args.page, full_vector=args.full_vector)
    return 1 if warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
