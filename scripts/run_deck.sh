#!/usr/bin/env bash
# Toàn bộ phần OFFLINE của MỘT deck, một lệnh: file raw -> parse -> chunk -> nhúng -> deck_map.
# Xong thì chỉ việc hỏi:  python scripts/try_ask.py <ten>
# Chạy lại bao nhiêu lần cũng được — bước nào có cache thì không tốn API.
#
#   bash scripts/run_deck.sh "data/raw/Onboarding Kit.pdf"
#   bash scripts/run_deck.sh "data/raw/Onboarding Kit.pdf" --no-vlm     # không gọi VLM, dùng cache
#   bash scripts/run_deck.sh "data/raw/Onboarding Kit.pdf" --pages 22   # gọi lại VLM riêng trang 22
#   bash scripts/run_deck.sh "data/raw/Onboarding Kit.pdf" --eval       # thêm bước đo bộ câu hỏi có nhãn
#
#   ①②②b③  parse        -> out/parsed/<ten>/document.json          src/parsing/cli.py run  [VLM: trang + bảng]
#   ④       chunk + nhúng -> out/kb/<ten>/chunks.json + vector + kho  src/kb/cli.py --embed   [embedding]
#   ⑤       deck_map      -> out/deck/<ten>/deck_map.txt             src/kb/deck_map.py      [không API]
#   (--eval) eval         -> out/kb/<ten>/audit/eval.json            src/kb/eval.py          [nhúng câu hỏi]
#
# Tham số khác (--no-vlm, --pages, --redo…) chuyển cho bước parse (`src/parsing/cli.py run --help`).
# Parse có cờ mức error (mã 1) thì vẫn chạy tiếp — file vẫn ghi, cờ để người duyệt.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
  echo "dung: bash scripts/run_deck.sh <data/raw/file.pdf|.pptx> [--no-vlm] [--pages 7,10] [--redo] [--eval]" >&2
  exit 2
fi
SRC="$1"; shift
EVAL=0
PARSE_ARGS=()
for a in "$@"; do
  if [ "$a" = "--eval" ]; then EVAL=1; else PARSE_ARGS+=("$a"); fi
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

echo "=== [1/3] parse  $SRC -> $PARSED"
rc=0; "$PY" src/parsing/cli.py run "$SRC" ${PARSE_ARGS[@]+"${PARSE_ARGS[@]}"} || rc=$?
if [ "$rc" -eq 1 ]; then echo "    (co co muc error — xem log tren, van chay tiep)"
elif [ "$rc" -ne 0 ]; then exit "$rc"
fi

echo "=== [2/3] chunk + nhung -> $CHUNKS"
"$PY" src/kb/cli.py "$PARSED" -o "$CHUNKS" --embed --stats

echo "=== [3/3] deck_map -> out/deck/$DOC_ID/deck_map.txt"
"$PY" src/kb/deck_map.py "$PARSED"

if [ "$EVAL" -eq 1 ]; then
  QUERIES="data/eval/$DOC_ID.queries.json"
  [ -f "$QUERIES" ] || QUERIES="data/eval/queries.json"
  echo "=== [eval] $QUERIES -> out/kb/$DOC_ID/audit/eval.json"
  # eval in từng câu ra log (stderr) — ở đây chỉ giữ dòng tổng; chi tiết từng câu nằm trong eval.json
  "$PY" src/kb/eval.py "$CHUNKS" --queries "$QUERIES" -o "out/kb/$DOC_ID/audit/eval.json" 2>&1 \
    | grep -E "^(===|---|ghi|CANH BAO|          )"
fi

echo
echo "=== XONG phan offline cua $DOC_ID. Hoi dap (Ctrl+C de thoat):"
echo "    $PY scripts/try_ask.py $DOC_ID"
