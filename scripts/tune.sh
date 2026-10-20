#!/usr/bin/env bash
# Quét K + trọng số dense/sparse của RRF trên MỘT deck (đọc từ kho vector) -> báo cáo:
#   src/output/evaluation/<doc_id>/tune.md + tune.json
#   + tune_misses.json (câu trượt top-5 của cấu hình tốt nhất, dạng file câu hỏi)
#
#   bash scripts/tune.sh onboarding_kit
#   bash scripts/tune.sh onboarding_kit --k 5,10,20 --wd 1,1.5,2
#
# Chạy `src/offline/chunker/cli.py <document.json>` trước để kho có deck. Đo cách chunk khác mà không
# đè kho thật: CHROMA_PATH=<thư mục khác> cho cả lệnh chunk lẫn lệnh này. Chạy lại thì ghi đè báo cáo.
# Câu hỏi: data/eval/<doc_id>.queries.json (đổi bằng --queries). Tham số sau doc_id chuyển cho
# src/evaluation/tune.py (xem --help). Thoát mã 1 khi kho vector trả pool lệch -> bảng không tin được, chạy lại.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
  echo "dung: bash scripts/tune.sh <doc_id> [--k 1,5,10] [--wd 1,2] [--ws 1] [--queries q.json]" >&2
  exit 2
fi

if   [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe   # Windows
elif [ -x .venv/bin/python ];         then PY=.venv/bin/python
else                                       PY=python
fi

export PYTHONIOENCODING=utf-8

"$PY" src/evaluation/tune.py "$@"
