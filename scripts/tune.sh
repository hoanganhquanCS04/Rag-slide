#!/usr/bin/env bash
# Quét K + trọng số dense/sparse của RRF trên MỘT bộ chunk -> HAI file đặt theo tên file vào:
#   <thư mục chunks>/audit/<tên chunks>_tune.md + <tên chunks>_tune.json
#   + <tên chunks>_misses.json (câu trượt top-5 của cấu hình tốt nhất, dạng file câu hỏi)
#
#   bash scripts/tune.sh out/kb/onboarding_kit/chunks.json
#        -> out/kb/onboarding_kit/audit/chunks_tune.md + chunks_tune.json
#   bash scripts/tune.sh out/kb/onboarding_kit/chunks_thu.json --k 5,10,20 --wd 1,1.5,2
#        -> out/kb/onboarding_kit/audit/chunks_thu_tune.md + .json
#
# Đổi cách chunk -> chỉ đổi file vào. Bộ chunk chưa nhúng thì tự nhúng (có cache).
# Một file vào = một bộ báo cáo: chạy lại thì ghi đè báo cáo của chính file đó.
# Câu hỏi: data/eval/<doc_id>.queries.json (đổi bằng --queries). Tham số sau file chunk chuyển cho
# src/kb/tune.py (xem --help). Thoát mã 1 khi kho vector trả pool lệch -> bảng không tin được, chạy lại.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -lt 1 ]; then
  echo "dung: bash scripts/tune.sh <chunks.json> [--k 1,5,10] [--wd 1,2] [--ws 1] [--queries q.json]" >&2
  exit 2
fi

if   [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe   # Windows
elif [ -x .venv/bin/python ];         then PY=.venv/bin/python
else                                       PY=python
fi

PYTHONIOENCODING=utf-8 "$PY" src/kb/tune.py "$@"
