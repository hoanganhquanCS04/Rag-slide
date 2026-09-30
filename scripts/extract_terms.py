"""Gom thuật ngữ robot SẼ NÓI vào kho phát âm CHUNG `data/pronunciation.json`.

Xem docs/spec/pronunciation.md. Chạy SAU S4, đọc KỊCH BẢN, không đọc chunk: chỉ từ nằm
trong kịch bản mới được đọc thành tiếng, nên chỉ chúng cần cách đọc. Bản cũ quét
chunks.json — đo trên 3_datavisualization: 10/35 mục người duyệt xong mà kịch bản không
bao giờ nói, còn 16 từ kịch bản CÓ nói (CSV, Google Maps...) thì bảng không có.

MỘT kho cho mọi deck: CBNV duyệt một lần là xong cho mọi deck nhân sự. Chữ đọc khác nhau
tùy ngữ cảnh (T7 = thứ bảy / cấp T7) thì chấp nhận MỘT cách đọc.

Máy chỉ ĐOÁN `say`, người chốt:
  viết tắt (toàn hoa)  -> đánh vần:        CBNV -> "xê bê en vê"
  từ tiếng Anh         -> để nguyên chữ:   Google -> "Google"   (người sửa thành "gu gồ")

Mục người đã chốt (`by: "nguoi"`) KHÔNG BAO GIỜ bị ghi đè. Mục cũ không còn kịch bản nào
nói thì vẫn giữ — kho dùng chung, deck khác có thể cần.

    python scripts/extract_terms.py out/deck/<doc_id>/scenario.json [out/deck/<khac>/scenario.json ...]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scenario.models import Scenario
from scenario.syllables import STORE, Pronunciation, count, is_abbrev, spell

log = logging.getLogger("terms")

GUIDE = [
    "KHO CHUNG cho mọi deck. Máy chỉ ĐỀ XUẤT, người CHỐT.",
    "1. Sửa 'say' chỗ đọc chưa đúng — viết bằng chữ Việt, cách nhau bởi khoảng trắng.",
    "2. Đổi 'by' từ 'auto' thành 'nguoi' ở mục đã duyệt — chạy lại script không đè mục 'nguoi'.",
    "3. Số âm tiết TÍNH từ 'say' lúc chạy, không ghi ở đây.",
    "4. Sửa xong chạy lại S4 (src/scenario/cli.py): chỉ đếm lại âm tiết, không gọi LLM.",
    "mode: spell = đánh vần từng chữ · phonetic_vi = phiên âm Việt · as_english = để nguyên chữ Anh",
]


def guess(word: str) -> dict:
    if is_abbrev(word):
        return {"say": spell(word), "mode": "spell", "by": "auto"}
    return {"say": word, "mode": "as_english", "by": "auto"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="extract_terms")
    ap.add_argument("scenarios", nargs="+", help="out/deck/<doc_id>/scenario.json")
    ap.add_argument("--store", default=str(STORE), help="mặc định data/pronunciation.json")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    store = Path(args.store)
    terms: dict[str, dict] = (json.loads(store.read_text(encoding="utf-8")).get("terms", {})
                              if store.exists() else {})
    pron = Pronunciation(terms)

    # Đếm lại bằng kho HIỆN TẠI, không tin `unknown_terms` lưu trong scenario.json (có thể cũ)
    seen: Counter = Counter()
    for path in args.scenarios:
        sc = Scenario.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
        for ss in sc.slides:
            for s in ss.sentences:
                seen.update(w.split(".")[-1] for w in count(s.text, pron)[1])  # plt.savefig -> savefig

    added: list[str] = []
    have = {k.lower() if not is_abbrev(k) else k for k in terms}
    for w, _ in seen.most_common():
        key = w if is_abbrev(w) else w.lower()
        if key in have:                     # "Google" và "google" là một mục
            continue
        have.add(key)
        terms[w] = guess(w)
        added.append(w)

    store.parent.mkdir(parents=True, exist_ok=True)
    body = {"_huong_dan": GUIDE, "terms": dict(sorted(terms.items(), key=lambda kv: kv[0].lower()))}
    store.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    n_auto = sum(1 for v in terms.values() if v.get("by") != "nguoi")
    log.info("ghi -> %s", store)
    log.info("   %d mục mới: %s", len(added),
             ", ".join(f"{w} ({seen[w]})" for w in added) or "không")
    log.info("   %d mục, %d còn 'auto' (chưa ai duyệt)", len(terms), n_auto)
    if n_auto:
        log.info("   MỞ FILE RA SỬA 'say', rồi đổi by 'auto' -> 'nguoi'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
