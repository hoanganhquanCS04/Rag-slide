#!/usr/bin/env bash
# Toàn bộ phần OFFLINE của MỘT deck, một lệnh: file raw -> parse -> chunk -> nhúng -> deck_map -> kịch bản.
# Xong thì chỉ việc hỏi:  python scripts/try_ask.py <ten>
# Chạy lại bao nhiêu lần cũng được — bước nào có cache thì không tốn API.
#
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf   --no-vlm     # không gọi VLM, dùng cache
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf   --pages 22   # gọi lại VLM riêng trang 22
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf   --eval       # thêm bước đo bộ câu hỏi có nhãn
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf   --no-scenario  # bỏ bước kịch bản (S4)
#
#   ①②②b③  parse        -> out/parsed/<ten>/document.json          src/parsing/cli.py run  [VLM: trang + bảng]
#   ④       chunk + nhúng -> out/kb/<ten>/chunks.json + vector + kho  src/kb/cli.py --embed   [embedding]
#   ⑤       deck_map      -> out/deck/<ten>/deck_map.txt             src/kb/deck_map.py      [không API]
#   ⑥ S4    kịch bản      -> out/deck/<ten>/scenario.json + .md      src/scenario/cli.py     [LLM: trang đổi]
#   (--eval) eval         -> out/kb/<ten>/audit/eval.json            src/kb/eval.py          [nhúng câu hỏi]
#
# Tham số khác (--no-vlm, --pages, --redo…) chuyển cho bước parse (`src/parsing/cli.py run --help`).
# Parse có cờ mức error / kịch bản có cờ đỏ (mã 3) thì vẫn chạy tiếp — file vẫn ghi, cờ để người duyệt.
# Kịch bản INCREMENTAL: chỉ trang có page_hash / prompt / LLM_MODEL đổi mới gọi LLM (~1–2 lần/trang).
# Mã khác 0 và 3 (không thấy file, thiếu khoá API…) -> DỪNG, không chạy tiếp trên dữ liệu cũ.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
  echo "dung: bash scripts/run_deck.sh <data/raw/file.pdf|.pptx> [--no-vlm] [--pages 7,10] [--redo] [--eval] [--no-scenario]" >&2
  exit 2
fi
SRC="$1"; shift
EVAL=0
SCENARIO=1
PARSE_ARGS=()
for a in "$@"; do
  case "$a" in
    --eval)        EVAL=1 ;;
    --no-scenario) SCENARIO=0 ;;
    *)             PARSE_ARGS+=("$a") ;;
  esac
done

if   [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe   # Windows
elif [ -x .venv/bin/python ];         then PY=.venv/bin/python
else                                       PY=python
fi
export PYTHONIOENCODING=utf-8

DOC_ID=$("$PY" -c "import sys; from pathlib import Path; sys.path.insert(0, 'src')
from parsing.from_docling import slugify_doc_id; print(slugify_doc_id(Path(sys.argv[1]).stem))" "$SRC")
PARSED="out/parsed/$DOC_ID/document.json"
CHUNKS="out/kb/$DOC_ID/chunks.json"

echo "=== [1/4] parse  $SRC -> $PARSED"
rc=0; "$PY" src/parsing/cli.py run "$SRC" ${PARSE_ARGS[@]+"${PARSE_ARGS[@]}"} || rc=$?
if [ "$rc" -eq 3 ]; then echo "    (co co muc error — xem log tren, van chay tiep)"
elif [ "$rc" -ne 0 ]; then echo "=== DUNG: buoc parse loi (ma $rc) — xem dong loi o tren" >&2; exit "$rc"
fi

echo "=== [2/4] chunk + nhung -> $CHUNKS"
"$PY" src/kb/cli.py "$PARSED" -o "$CHUNKS" --embed --stats

echo "=== [3/4] deck_map -> out/deck/$DOC_ID/deck_map.txt"
"$PY" src/kb/deck_map.py "$PARSED"

if [ "$SCENARIO" -eq 1 ]; then
  echo "=== [4/4] kich ban (S4) -> out/deck/$DOC_ID/scenario.json + scenario.md"
  rc=0; "$PY" src/scenario/cli.py "$PARSED" || rc=$?
  if [ "$rc" -eq 3 ]; then echo "    (co trang CO DO — xem bang tren / scenario.md, van chay tiep)"
  elif [ "$rc" -ne 0 ]; then echo "=== DUNG: buoc kich ban loi (ma $rc) — xem dong loi o tren" >&2; exit "$rc"
  fi
else
  echo "=== [4/4] kich ban: bo qua (--no-scenario)"
fi

if [ "$EVAL" -eq 1 ]; then
  # câu hỏi: data/eval/<ten>.queries.json — eval.py tự chọn, dòng "===" đầu tiên in tên file
  echo "=== [eval] -> out/kb/$DOC_ID/audit/eval.json"
  # eval in từng câu ra log (stderr) — ở đây chỉ giữ dòng tổng; chi tiết từng câu nằm trong eval.json
  "$PY" src/kb/eval.py "$CHUNKS" -o "out/kb/$DOC_ID/audit/eval.json" 2>&1 \
    | grep -E "^(===|---|ghi|CANH BAO|          )"
fi

echo
echo "=== XONG phan offline cua $DOC_ID. Hoi dap (Ctrl+C de thoat):"
echo "    $PY scripts/try_ask.py $DOC_ID"
