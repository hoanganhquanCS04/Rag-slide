"""Gộp kết quả hai cách tìm (vector + từ khoá BM25) thành một danh sách trang.

Để làm gì: mỗi cách tìm giỏi một kiểu — vector hiểu Ý câu hỏi, BM25 khớp đúng CHỮ. Gộp lại thì
tốt hơn từng cách riêng (đo bằng src/evaluation/eval.py).
Cách làm: RRF — chỉ dùng HẠNG của từng cách, không dùng điểm gốc (xem `rrf_fuse`), rồi gộp các
đoạn cùng trang, lấy k trang đầu.
"""

from __future__ import annotations

from typing import Hashable, TypeVar

from models.kb_chunk import SearchHit
from utils.filters import Hit

# Tham số gộp RRF. Các số này ĐO bằng scripts/tune.sh, không đoán.
# Đo ngày 2026-10-14 trên Onboarding 200 câu: K=10, vector nặng 1.5, BM25 nặng 1 là tốt nhất
# (top-1/3/5 = 153/186/194; bản cũ K=15 1:1 được 153/186/192).
RRF_K = 10
W_DENSE = 1.5       # độ nặng của kết quả tìm bằng vector
W_SPARSE = 1.0      # độ nặng của kết quả tìm bằng BM25
TOP_K = 5           # số trang trả về
POOL = 50           # mỗi cách tìm lấy 50 kết quả rồi mới gộp — lấy ít quá thì gộp mất thông tin

Key = TypeVar("Key", bound=Hashable)


def rrf_fuse(rank_dense: dict[Key, int], rank_sparse: dict[Key, int], *, k: int = RRF_K,
             w_dense: float = W_DENSE, w_sparse: float = W_SPARSE) -> dict[Key, float]:
    """Gộp hai bảng xếp hạng thành một điểm chung cho mỗi đoạn.

    Vào: hạng của mỗi đoạn ở cách tìm vector, và ở cách tìm BM25, vd {đoạn 3: hạng 1, đoạn 7: hạng 2}.
    Ra:  điểm gộp của mỗi đoạn — điểm càng cao càng liên quan.

    Công thức: điểm = w_dense / (k + hạng vector) + w_sparse / (k + hạng BM25).
    Đoạn không có mặt ở một cách tìm thì phần đó không cộng gì.
    Chỉ dùng HẠNG, không dùng điểm gốc, vì điểm vector (0–1) và điểm BM25 (0–30) không so được với nhau.
    Ví dụ (K=10, vector nặng 1.5, BM25 nặng 1): đoạn hạng 1 ở vector, hạng 4 ở BM25
    -> 1.5/(10+1) + 1/(10+4) ≈ 0.208.
    """
    output: dict[Key, float] = {}
    for ranks, weight in ((rank_dense, w_dense), (rank_sparse, w_sparse)):
        for key, rank in ranks.items():
            output[key] = output.get(key, 0.0) + weight / (k + rank)
    return output


def group_by_page(hits: list[SearchHit]) -> list[SearchHit]:
    """Gộp các đoạn cùng trang thành một kết quả.

    Một trang dài bị cắt thành nhiều đoạn; không gộp thì một trang chiếm nhiều chỗ trong top 5.
    Mỗi trang giữ đoạn có điểm cao nhất (không cộng dồn điểm), và đếm số đoạn đã gộp vào `n_chunks`.
    """
    output: dict[int, SearchHit] = {}
    for hit in hits:                     # hits đã xếp từ cao xuống thấp -> đoạn đầu tiên của trang là đoạn mạnh nhất
        if (current := output.get(hit.page_no)) is None:
            output[hit.page_no] = hit.model_copy()
        else:
            current.n_chunks += 1
    return sorted(output.values(), key=lambda h: -h.score)


def ensemble_search(dense: list[Hit], sparse: list[Hit], *,
                    k: int = TOP_K, group_pages: bool = True, rrf_k: int = RRF_K,
                    w_dense: float = W_DENSE, w_sparse: float = W_SPARSE) -> list[SearchHit]:
    """Gộp kết quả tìm bằng vector và tìm bằng BM25 -> k trang liên quan nhất.

    dense, sparse   kết quả từng cách tìm, dạng [(bản ghi, điểm)], tốt nhất đứng đầu.
                    Bản ghi mang sẵn chữ và số trang. Muốn chỉ dùng một cách thì đưa danh sách rỗng cho cách kia.
    group_pages     gộp các đoạn cùng trang (mặc định có)

    Điểm `score` của kết quả chỉ để xếp thứ tự, KHÔNG nói lên mức độ chắc chắn.
    """
    # Bản ghi theo mã đoạn, lấy từ CẢ hai nhánh — đoạn chỉ một nhánh tìm ra vẫn có chữ
    records = {record.chunk_id: record for record, _ in [*dense, *sparse]}
    # Hạng = vị trí trong danh sách kết quả, đếm từ 1
    dense_rank = {record.chunk_id: rank for rank, (record, _) in enumerate(dense, 1)}
    sparse_rank = {record.chunk_id: rank for rank, (record, _) in enumerate(sparse, 1)}
    dense_score = {record.chunk_id: score for record, score in dense}
    sparse_score = {record.chunk_id: score for record, score in sparse}
    fused = rrf_fuse(dense_rank, sparse_rank, k=rrf_k, w_dense=w_dense, w_sparse=w_sparse)

    hits = [
        SearchHit(
            chunk_id=chunk_id,
            page_no=records[chunk_id].page_no,
            score=score,
            rank_dense=dense_rank.get(chunk_id),
            rank_sparse=sparse_rank.get(chunk_id),
            score_dense=float(dense_score.get(chunk_id, 0.0)),
            score_sparse=float(sparse_score.get(chunk_id, 0.0)),
            text_enriched=records[chunk_id].document,
        )
        for chunk_id, score in sorted(fused.items(), key=lambda kv: -kv[1])
    ]
    if group_pages:
        hits = group_by_page(hits)
    return hits[:k]
