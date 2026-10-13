#!/usr/bin/env bash
# Toàn bộ phần OFFLINE của MỘT deck, một lệnh: file raw -> parse -> chunk -> index -> deck_map -> kịch bản.
# Xong thì chỉ việc hỏi:  python scripts/try_ask.py <ten>
# Chạy lại bao nhiêu lần cũng được — bước nào có cache thì không tốn API.
#
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf   --redo       # gọi lại VLM cả file (mặc định dùng cache)
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf   --eval       # thêm bước đo bộ câu hỏi có nhãn
#   bash scripts/run_deck.sh data/raw/onboarding_kit.pdf   --no-scenario  # bỏ bước kịch bản (S4)
#
#   ①②      parse        -> src/output/parser/<ten>/document.json   src/parser/cli.py       [VLM: mỗi trang 1 lần]
#   ④a      chunk         -> src/output/chunker/<ten>/chunks.json         src/chunker/cli.py      [không API]
#   ④b      index         -> src/output/embedders/<ten>/vectors__* + kho src/retrieval/build_index.py[embedding: chunk đổi]
#   ⑤       deck_map      -> src/output/retrieval/<ten>/deck_map.txt      src/retrieval/deck_map.py[không API]
#   ⑥ S4    kịch bản      -> src/output/scenario/<ten>/scenario.json+.md src/scenario/cli.py     [LLM: trang đổi]
#   (--eval) eval         -> src/output/retrieval/<ten>/eval.json     src/retrieval/eval.py[nhúng câu hỏi]
#
# Tham số khác (--redo, --model, --table-pages) chuyển cho bước parse (`src/parser/cli.py --help`).
# Kịch bản có cờ đỏ (mã 3) thì vẫn chạy tiếp — file vẫn ghi, cờ để người duyệt.
# Kịch bản INCREMENTAL: chỉ trang có page_hash / prompt / LLM_MODEL đổi mới gọi LLM (~1–2 lần/trang).
# Mã khác 0 và 3 (không thấy file, thiếu khoá API…) -> DỪNG, không chạy tiếp trên dữ liệu cũ.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
  echo "dung: bash scripts/run_deck.sh <data/raw/file.pdf|.pptx> [--redo] [--eval] [--no-scenario]" >&2
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
from models.parsed_document import slugify_doc_id; print(slugify_doc_id(Path(sys.argv[1]).stem))" "$SRC")
PARSED="src/output/parser/$DOC_ID/document.json"
CHUNKS="src/output/chunker/$DOC_ID/chunks.json"

echo "=== [1/5] parse  $SRC -> $PARSED"
"$PY" src/parser/cli.py "$SRC" ${PARSE_ARGS[@]+"${PARSE_ARGS[@]}"}

echo "=== [2/5] chunk -> $CHUNKS"
"$PY" src/chunker/cli.py "$PARSED" --stats

echo "=== [3/5] index (nhung + nap kho) -> src/output/embedders/$DOC_ID/"
"$PY" src/retrieval/build_index.py "$CHUNKS"

echo "=== [4/5] deck_map -> src/output/retrieval/$DOC_ID/deck_map.txt"
"$PY" src/retrieval/deck_map.py "$PARSED"

if [ "$SCENARIO" -eq 1 ]; then
  echo "=== [5/5] kich ban (S4) -> src/output/scenario/$DOC_ID/scenario.json + scenario.md"
  rc=0; "$PY" src/scenario/cli.py "$PARSED" || rc=$?
  if [ "$rc" -eq 3 ]; then echo "    (co trang CO DO — xem bang tren / scenario.md, van chay tiep)"
  elif [ "$rc" -ne 0 ]; then echo "=== DUNG: buoc kich ban loi (ma $rc) — xem dong loi o tren" >&2; exit "$rc"
  fi
else
  echo "=== [5/5] kich ban: bo qua (--no-scenario)"
fi

if [ "$EVAL" -eq 1 ]; then
  # câu hỏi: data/eval/<ten>.queries.json — eval.py tự chọn, dòng "===" đầu tiên in tên file
  echo "=== [eval] -> src/output/retrieval/$DOC_ID/eval.json"
  # eval in từng câu ra log (stderr) — ở đây chỉ giữ dòng tổng; chi tiết từng câu nằm trong eval.json
  "$PY" src/retrieval/eval.py "$CHUNKS" -o "src/output/retrieval/$DOC_ID/eval.json" 2>&1 \
    | grep -E "^(===|---|ghi|CANH BAO|          )"
fi

echo
echo "=== XONG phan offline cua $DOC_ID. Hoi dap (Ctrl+C de thoat):"
echo "    $PY scripts/try_ask.py $DOC_ID"
