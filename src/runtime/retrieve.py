"""Tìm các trang slide liên quan tới một câu hỏi.

Chạy thử:
    python src/runtime/retrieve.py src/output/chunker/<ten>/chunks.json "cau hoi" -k 5 --explain
    python src/runtime/retrieve.py src/output/chunker/<ten>/chunks.json "quay lai phan cham cong" --no-filter

Tìm bằng HAI cách cùng lúc, rồi gộp kết quả:
    - dense  (vector): hiểu Ý câu hỏi, nhưng hay bỏ sót từ hiếm như tên hàm, mã số
    - sparse (BM25):   khớp đúng CHỮ trong câu hỏi, nhưng không hiểu từ đồng nghĩa
Mỗi cách lấy 50 kết quả đầu, rồi gộp bằng RRF (xem hàm rrf_fuse), rồi gộp các đoạn cùng trang,
cuối cùng trả về 5 trang đầu.

Nếu API nhúng vector lỗi, file này KHÔNG tự xử lý mà báo lỗi ra (EmbedError).
Nơi gọi tự quyết: chat_engine.py thì chuyển sang chỉ dùng BM25; còn khi đo (evaluation/)
thì dừng luôn, để số đo không bị sai mà không ai biết.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Hashable, Literal, TypeVar

if __package__ in (None, ""):  # chạy thẳng file này: cho Python tìm được các thư mục trong src/
    sys.path[0] = str(Path(__file__).resolve().parents[1])

from utils.config import MODEL_ID, console_setup
from embedders.base import Embedder
from embedders.factory import create_embedder
from sparse.factory import create_sparse
from vectordb.factory import create_store
from utils.vectors import vectors_file
from models.kb_chunk import ChunkSet, KBChunk, SearchHit

log = logging.getLogger(__name__)

# Tham số gộp RRF. Các số này ĐO bằng scripts/tune.sh, không đoán.
# Đo ngày 2026-10-14 trên Onboarding 200 câu: K=10, vector nặng 1.5, BM25 nặng 1 là tốt nhất
# (top-1/3/5 = 153/186/194; bản cũ K=15 1:1 được 153/186/192).
RRF_K = 10
W_DENSE = 1.5       # độ nặng của kết quả tìm bằng vector
W_SPARSE = 1.0      # độ nặng của kết quả tìm bằng BM25
TOP_K = 5           # số trang trả về
POOL = 50           # mỗi cách tìm lấy 50 kết quả rồi mới gộp — lấy ít quá thì gộp mất thông tin

# Cách tìm: cả hai / chỉ vector / chỉ BM25
Mode = Literal["hybrid", "dense", "sparse"]
Key = TypeVar("Key", bound=Hashable)


def rrf_fuse(r_dense: dict[Key, int], r_sparse: dict[Key, int], *, k: int = RRF_K,
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
    out: dict[Key, float] = {}
    for ranks, w in ((r_dense, w_dense), (r_sparse, w_sparse)):
        for key, r in ranks.items():
            out[key] = out.get(key, 0.0) + w / (k + r)
    return out


class Retriever:
    """Bộ tìm kiếm cho MỘT bộ slide. Tạo một lần, tìm nhiều lần."""

    def __init__(self, chunks_path: str | Path, *, vectors_path: str | Path | None = None,
                 model_id: str = MODEL_ID, embed_retry: int = 4, rrf_k: int = RRF_K,
                 w_dense: float = W_DENSE, w_sparse: float = W_SPARSE):
        """Nạp các đoạn của bộ slide và chuẩn bị hai kho tìm kiếm.

        chunks_path   file chunks.json (các đoạn đã cắt từ slide)
        vectors_path  file vector của các đoạn; bỏ trống thì tự tìm theo tên model
        model_id      model nhúng vector
        embed_retry   gọi API nhúng lỗi thì thử lại mấy lần
        rrf_k, w_dense, w_sparse   tham số gộp RRF (xem rrf_fuse) — muốn thử số khác thì tạo bộ tìm mới
        """
        cp = Path(chunks_path)
        self.cs = ChunkSet.model_validate(json.loads(cp.read_text(encoding="utf-8")))
        self.chunks: list[KBChunk] = self.cs.chunks
        # mã đoạn -> đoạn (hai kho tìm trả về mã đoạn, cần tra lại nội dung)
        self._by_id = {c.chunk_id: c for c in self.chunks}

        # Kho vector: nếu đang lệch với chunks.json thì nạp lại từ file vector
        self.vectors_path = Path(vectors_path) if vectors_path else vectors_file(self.cs.doc_id, model_id)
        self.store = create_store(model_id=model_id)
        n_sync = self.store.sync(self.cs, self.vectors_path)
        # Kho từ khoá BM25: nếu lệch với chunks.json thì dựng lại
        self.sparse = create_sparse()
        self.sparse.sync(self.cs)

        self.model_id = model_id
        self.embed_retry = embed_retry
        self.rrf_k = rrf_k
        self.w_dense = w_dense
        self.w_sparse = w_sparse
        self._emb: Embedder | None = None
        log.info("nap %d chunk | kho %s%s | tu khoa %s | model %s", len(self.chunks),
                 self.store.kind, f" (nap lai {n_sync} vector)" if n_sync else "",
                 self.sparse.kind, model_id)

    @property
    def embedder(self) -> Embedder:
        """Công cụ biến câu hỏi thành vector.

        Chỉ tạo khi cần lần đầu — nhờ vậy chạy chỉ BM25 thì không cần khoá API.
        """
        if self._emb is None:
            self._emb = create_embedder(model_id=self.model_id, retry=self.embed_retry)
        return self._emb

    # ------------------------------------------------------------------ tìm

    def where(self, filter_dividers: bool = True) -> dict[str, object]:
        """Điều kiện lọc dùng cho cả hai cách tìm.

        Luôn chỉ tìm trong bộ slide này. Nếu filter_dividers=True thì bỏ các trang chỉ có
        tên chương (trang chuyển chương), vì chúng không có nội dung để trả lời.
        """
        return {"doc_id": self.cs.doc_id, **({"is_searchable": True} if filter_dividers else {})}

    def search(self, query: str, *, k: int = TOP_K, mode: Mode = "hybrid",
               filter_dividers: bool = True, group_by_page: bool = True) -> list[SearchHit]:
        """Tìm câu hỏi -> k trang liên quan nhất (mặc định 5), trang liên quan nhất đứng đầu.

        mode="hybrid" tìm bằng cả hai cách, "dense" chỉ vector, "sparse" chỉ BM25.
        Các bước:
            1. tìm bằng vector: biến câu hỏi thành vector (gọi API), hỏi kho vector 50 đoạn gần nhất
            2. tìm bằng BM25: hỏi kho từ khoá 50 đoạn khớp chữ nhất (không gọi API)
            3. gộp hai kết quả bằng RRF, xếp điểm từ cao xuống thấp
            4. gộp các đoạn cùng trang (nếu group_by_page), lấy k cái đầu
        Điểm `score` chỉ để xếp thứ tự, KHÔNG nói lên mức độ chắc chắn.
        """
        if not any(c.is_searchable or not filter_dividers for c in self.chunks):
            return []
        where = self.where(filter_dividers)

        # Mỗi kho trả về [(mã đoạn, điểm)], đoạn tốt nhất đứng đầu
        dense = []
        if mode in ("hybrid", "dense"):
            qv = self.embedder.embed([query])[0]
            dense = self.store.retrieve_context(qv, POOL, where)
        sparse = self.sparse.retrieve_context(query, POOL, where) if mode in ("hybrid", "sparse") else []

        # Hạng = vị trí trong danh sách kết quả, đếm từ 1
        dense_rank = {cid: r for r, (cid, _) in enumerate(dense, 1)}
        sparse_rank = {cid: r for r, (cid, _) in enumerate(sparse, 1)}
        dense_score, sparse_score = dict(dense), dict(sparse)
        fused = rrf_fuse(dense_rank, sparse_rank, k=self.rrf_k,
                         w_dense=self.w_dense, w_sparse=self.w_sparse)

        hits = [
            SearchHit(
                chunk_id=cid,
                page_no=self._by_id[cid].page_no,
                score=sc,
                rank_dense=dense_rank.get(cid),
                rank_sparse=sparse_rank.get(cid),
                score_dense=float(dense_score.get(cid, 0.0)),
                score_sparse=float(sparse_score.get(cid, 0.0)),
                text_enriched=self._by_id[cid].text_enriched,
                image_ratio=self._by_id[cid].image_ratio,
            )
            for cid, sc in sorted(fused.items(), key=lambda kv: -kv[1])
            if cid in self._by_id                 # phòng kho còn sót đoạn cũ không có trong chunks.json
        ]
        if group_by_page:
            hits = _group_by_page(hits)
        return hits[:k]


def _group_by_page(hits: list[SearchHit]) -> list[SearchHit]:
    """Gộp các đoạn cùng trang thành một kết quả.

    Một trang dài bị cắt thành nhiều đoạn; không gộp thì một trang chiếm nhiều chỗ trong top 5.
    Mỗi trang giữ đoạn có điểm cao nhất (không cộng dồn điểm), và đếm số đoạn đã gộp vào `n_chunks`.
    """
    out: dict[int, SearchHit] = {}
    for h in hits:                       # hits đã xếp từ cao xuống thấp -> đoạn đầu tiên của trang là đoạn mạnh nhất
        if (cur := out.get(h.page_no)) is None:
            out[h.page_no] = h.model_copy()
        else:
            cur.n_chunks += 1
    return sorted(out.values(), key=lambda h: -h.score)


def main(argv: list[str] | None = None) -> int:
    """Lệnh tìm thử trong terminal: in các trang tìm được cho một câu hỏi.

    --explain       in thêm hạng và điểm của từng cách tìm
    --dense-only    chỉ tìm bằng vector
    --sparse-only   chỉ tìm bằng BM25 (không cần khoá API)
    --no-filter     giữ cả trang chuyển chương
    --no-group      không gộp các đoạn cùng trang
    """
    ap = argparse.ArgumentParser(prog="retrieve")
    ap.add_argument("chunks", help="src/output/chunker/<ten>/chunks.json")
    ap.add_argument("query", nargs="+", help="cau hoi")
    ap.add_argument("-k", type=int, default=TOP_K)
    ap.add_argument("--vectors", default=None)
    ap.add_argument("--model", default=MODEL_ID)
    ap.add_argument("--dense-only", action="store_true")
    ap.add_argument("--sparse-only", action="store_true")
    ap.add_argument("--no-filter", action="store_true", help="giu ca trang phan muc")
    ap.add_argument("--no-group", action="store_true", help="khong gop chunk cung trang")
    ap.add_argument("--explain", action="store_true", help="in hang + diem tho tung nhanh")
    args = ap.parse_args(argv)

    console_setup()

    if args.dense_only and args.sparse_only:
        raise SystemExit("chon mot trong hai, khong the ca hai")
    mode: Mode = "dense" if args.dense_only else "sparse" if args.sparse_only else "hybrid"

    rt = Retriever(args.chunks, vectors_path=args.vectors, model_id=args.model)
    q = " ".join(args.query)
    hits = rt.search(q, k=args.k, mode=mode,
                     filter_dividers=not args.no_filter, group_by_page=not args.no_group)

    log.info("")
    log.info("HOI (%s%s): %s", mode, "" if not args.no_filter else ", khong loc", q)
    if not hits:
        log.info("   khong co ket qua")
        return 1
    for n, h in enumerate(hits, 1):
        log.info("%2d. trang %-3d %.4f  %s", n, h.page_no, h.score,
                 h.text_enriched[:62].replace("\n", " "))
        if args.explain:
            log.info("      dense hang %-4s cos=%.3f  |  bm25 hang %-4s diem=%.2f"
                     "  |  gop %d chunk  |  anh %.0f%%",
                     h.rank_dense or "-", h.score_dense,
                     h.rank_sparse or "-", h.score_sparse, h.n_chunks, h.image_ratio * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
