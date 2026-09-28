"""Cào một bài Wikipedia về làm tài liệu nguồn cho KB — chỉ lấy THÔ, chưa xử lý gì.

    python scripts/fetch_wiki.py "https://vi.wikipedia.org/wiki/Tết_Nguyên_Đán" --id tet_nguyen_dan
    python scripts/fetch_wiki.py <url> --id tet_nguyen_dan --revid 75594662   # lấy lại đúng bản cũ

Ghi ra data/sources/:
    <id>.<revid>.html        HTML do MediaWiki render — bản đủ nhất (còn bảng, chú thích ảnh)
    <id>.<revid>.txt         văn bản thuần của CHÍNH Wikipedia, mục đánh dấu `== ... ==`
    <id>.<revid>.meta.json   url, revid, lúc tải, giấy phép

Ghim `revid` vì Wikipedia bị sửa liên tục: không ghim thì hai lần build ra hai KB khác
nhau mà không ai biết. KHÔNG tải ảnh — v0 chỉ lấy chữ.

`.txt` lấy từ API TextExtracts, API này chỉ trả bản MỚI NHẤT. Hỏi `--revid` cũ mà bài đã
bị sửa thì bỏ `.txt`, chỉ còn `.html` — thà thiếu còn hơn ghép hai phiên bản vào một bộ.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

log = logging.getLogger("fetch_wiki")

# Wikimedia chặn User-Agent chung chung — phải tự xưng tên
UA = "Robot-slide-rag/0.1 (VinRobotics research; KB builder)"
LICENSE = "CC BY-SA 4.0"


def _title_of(url: str) -> tuple[str, str]:
    """'https://vi.wikipedia.org/wiki/T%E1%BA%BFt_...#muc' -> ('vi.wikipedia.org', 'Tết_...')"""
    u = urlparse(url)
    if "/wiki/" not in u.path:
        raise SystemExit(f"khong phai link bai Wikipedia: {url}")
    return u.netloc, unquote(u.path.split("/wiki/", 1)[1])


def _get(client: httpx.Client, host: str, params: dict) -> dict:
    r = client.get(f"https://{host}/w/api.php",
                   params={**params, "format": "json", "formatversion": 2})
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise SystemExit(f"API loi: {data['error']}")
    return data


def fetch(url: str, doc_id: str, out_dir: Path, revid: int | None) -> Path:
    host, title = _title_of(url)
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
        target = {"oldid": revid} if revid else {"page": title, "redirects": 1}
        p = _get(c, host, {"action": "parse", "prop": "text|revid|displaytitle|sections",
                           **target})["parse"]
        rev = p["revid"]
        log.info("%s | revid %d | %d muc | html %d ky tu",
                 p["title"], rev, len(p["sections"]), len(p["text"]))

        q = _get(c, host, {"action": "query", "prop": "extracts|info", "explaintext": 1,
                           "exsectionformat": "wiki", "titles": p["title"],
                           "redirects": 1})["query"]["pages"][0]
        latest = q.get("lastrevid")
        text = q.get("extract", "") if latest == rev else None
        if text is None:
            log.warning("bai da sua (moi nhat %s != %d) -> bo .txt, chi giu .html", latest, rev)

    out_dir.mkdir(parents=True, exist_ok=True)
    # KHÔNG dùng Path.with_suffix: nó coi ".75594662" là đuôi file và thay mất revid
    name = {ext: f"{doc_id}.{rev}.{ext}" for ext in ("html", "txt", "meta.json")}
    (out_dir / name["html"]).write_text(p["text"], encoding="utf-8")
    if text is not None:
        (out_dir / name["txt"]).write_text(text, encoding="utf-8")

    meta = {
        "doc_id": doc_id,
        "title": p["title"],
        "url": f"https://{host}/wiki/{p['title'].replace(' ', '_')}",
        "revid": rev,
        "permalink": f"https://{host}/w/index.php?oldid={rev}",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "license": LICENSE,
        "files": {"html": name["html"], "txt": name["txt"] if text is not None else None},
        "sections": [{"number": s["number"], "level": int(s["toclevel"]), "title": s["line"]}
                     for s in p["sections"]],
    }
    meta_path = out_dir / name["meta.json"]
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    for f in sorted(out_dir.glob(f"{doc_id}.{rev}.*")):
        log.info("ghi -> %s (%d KB)", f, f.stat().st_size // 1024)
    return meta_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_wiki")
    ap.add_argument("url", help="link bai Wikipedia (phan #muc bi bo qua)")
    ap.add_argument("--id", required=True, help="doc_id, vd tet_nguyen_dan")
    ap.add_argument("--revid", type=int, default=None, help="lay dung ban nay thay vi moi nhat")
    ap.add_argument("-o", "--out-dir", default="data/sources")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")

    fetch(args.url, args.id, Path(args.out_dir), args.revid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
