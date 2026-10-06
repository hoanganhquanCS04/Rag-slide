"""Chạy thử buổi thuyết trình trong terminal — bản chữ, hỏi đáp ở cuối mỗi trang.

    python src/runtime/cli.py onboarding_kit
    python src/runtime/cli.py onboarding_kit --start 11                 # bắt đầu từ trang 11
    python src/runtime/cli.py onboarding_kit --speed 1                  # đợi đúng thời lượng nói
    python src/runtime/cli.py onboarding_kit < cau_hoi.txt              # chạy thử bằng file

Cần có sẵn (offline): out/parsed/<ten>/document.json · out/kb/<ten>/chunks.json + vector
                      · out/deck/<ten>/scenario.json · out/deck/<ten>/deck_map.txt
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import DEFAULT_MODEL, ROOT
from runtime.models import RuntimeConfig
from runtime.session import Session


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="runtime")
    ap.add_argument("doc", help="doc_id, vd 3_datavisualization | tetnguyendan")
    ap.add_argument("--start", type=int, default=1, help="bắt đầu từ trang")
    ap.add_argument("--speed", type=float, default=0.0,
                    help="0 = in liền (mặc định) · 1 = đợi đúng thời lượng câu nói")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="mặc định LLM_MODEL trong .env")
    ap.add_argument("--config", default=str(ROOT / "config" / "runtime.json"))
    args = ap.parse_args(argv)

    for s in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    for noisy in ("httpx", "kb", "llm"):               # log nội bộ, không phải lời robot
        logging.getLogger(noisy).setLevel(logging.WARNING)

    session = Session(args.doc, RuntimeConfig.load(args.config), args.model, args.speed)
    try:
        asyncio.run(session.run(args.start))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
