"""Sinh kịch bản từng trang bằng LLM. Xem docs/spec/scenario.md.

Ba điểm dễ sai:

  1. **Chỉ đưa nội dung CHÍNH TRANG ĐÓ.** Không nhồi cả deck (§10). Nguồn sự thật là các
     khối của `ParsedPage`, mỗi khối kèm `id` + `provenance` để LLM trỏ `ref` vào được.
  2. **Song song theo SECTION, tuần tự TRONG section.** 7 trang liền cùng tên "Đồ thị dạng
     đường" — viết độc lập thì robot mở đầu y hệt bảy lần. Trang sau đọc kịch bản trang
     trước cùng section. Đây là thứ thay cho `message` đã bỏ.
  3. **`syllables` do code tính.** LLM chỉ viết chữ; đếm âm tiết theo kho phát âm chung
     `data/pronunciation.json`. Kho KHÔNG vào prompt: thuật ngữ lấy từ chính trang, kiểm
     bằng code (`term_not_on_page`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path

from llm import LLM, ROOT
from parsing.models import (ParsedDocument, ParsedImage, ParsedPage, ParsedParagraph,
                            ParsedTable)
from scenario import validate
from scenario.models import Grounding, Prosody, Sentence, SlideScript
from scenario.syllables import Pronunciation, count

log = logging.getLogger(__name__)

PROMPT_PATH = ROOT / "prompts" / "s4_scenario.md"

# Kịch bản các trang trước cùng section: N trang gần nhất đưa NGUYÊN VĂN, trang xa hơn chỉ
# đưa câu mở đầu (đủ để không mở đầu giống nhau). Không giới hạn thì trang cuối sec_01 của
# Onboarding (23 trang) mang theo kịch bản 22 trang — nhồi cả chương vào prompt.
PREV_FULL = 3

TYPE_RULES = {
    "section_divider": (
        "Đây là TRANG CHUYỂN CHƯƠNG: trên trang chỉ có tên chương, không có nội dung.\n"
        "- Viết 1–2 câu, TẤT CẢ là `delivery`. CẤM câu `content`.\n"
        "- Báo hiệu chuyển sang chương mới và NÊU TÊN CHƯƠNG (lấy đúng tiêu đề trang).\n"
        "- Có thể thêm một câu hỏi tu từ gợi tò mò.\n"
        "- CẤM giảng giải chương này là gì — trên trang không có thông tin đó."
    ),
    "closing": (
        "Đây là TRANG KẾT THÚC bài thuyết trình (lời cảm ơn), KHÔNG phải chương mới.\n"
        "- Viết 1–2 câu, TẤT CẢ là `delivery`. CẤM câu `content`.\n"
        "- Cảm ơn người nghe, có thể mời đặt câu hỏi.\n"
        "- CẤM tóm tắt lại nội dung — trên trang không có thông tin đó.\n"
        "- Không cần đọc lại tiêu đề trang."
    ),
    "content": (
        "Đây là TRANG NỘI DUNG.\n"
        "- Mở bằng một câu `delivery` dẫn dắt, rồi các câu `content`.\n"
        "- NÓI ĐỦ Ý: MỌI thông tin trên trang phải được nói ra — số liệu, đối tượng, điều kiện,\n"
        "  ngoại lệ, mốc thời gian. Người nghe phải hình dung được hết thông tin trên trang.\n"
        "- KHÔNG có giới hạn số câu: trang ít chữ thì nói ngắn, trang nhiều chữ thì nói dài.\n"
        "  Nhiều ý thì viết NHIỀU CÂU, không nhồi vào một câu dài.\n"
        "- Đủ ý KHÔNG phải đọc nguyên văn: gộp các mục cùng loại vào một câu nói tự nhiên.\n"
        "- Không tả lại hình ảnh từng chi tiết."
    ),
    "exercise": (
        "Đây là TRANG BÀI TẬP: người nghe cần biết PHẢI LÀM GÌ, không cần nghe giảng.\n"
        "- Một câu `delivery` báo đến phần bài tập, còn lại là `content`.\n"
        "- ĐỌC LẠI yêu cầu, hạn nộp, thứ cần nộp — đúng như trên trang, không thêm bớt.\n"
        "- CẤM giảng lại kiến thức, CẤM gợi ý cách làm — trên trang không có thông tin đó.\n"
        "- Danh sách link: CHỈ nói có mấy trang và là trang gì, KHÔNG đọc địa chỉ."
    ),
}


def load_prompt() -> tuple[str, str]:
    t = PROMPT_PATH.read_text(encoding="utf-8")
    return t, hashlib.sha1(t.encode()).hexdigest()[:12]


# ------------------------------------------------------------------ dựng input


def is_closing(doc: ParsedDocument, page: ParsedPage) -> bool:
    """Trang "chuyển chương" nằm CUỐI deck là lời kết ("THANK YOU!"), không phải chương
    mới — luật chuyển chương sẽ bắt robot nói "sang chương THANK YOU"."""
    return page.slide_type == "section_divider" and page.page_no == doc.pages[-1].page_no


def rules_key(doc: ParsedDocument, page: ParsedPage) -> str:
    return "closing" if is_closing(doc, page) else page.slide_type


def page_blocks(page: ParsedPage) -> list[tuple[str, str, str]]:
    """-> [(id, provenance, text)] — chỉ khối CÓ nội dung, để LLM trỏ ref vào."""
    return [(b.id, b.provenance.value, b.content.strip())
            for b in page.blocks if b.content and b.content.strip()]


def must_cover(b: object) -> bool:
    """Khối mang thông tin, kịch bản PHẢI nói tới: đoạn văn, danh sách, bảng. Không tính
    tiêu đề, khối link (không đọc địa chỉ), chú thích, ảnh (ảnh minh hoạ không cần nói), và
    nhãn < 3 chữ ("NỘI DUNG", "CÒN LẠI")."""
    if isinstance(b, ParsedParagraph) and b.role not in ("body", "list"):
        return False
    return (isinstance(b, (ParsedParagraph, ParsedTable))
            and len((b.content or "").split()) >= 3)


def block_info(page: ParsedPage, pron: Pronunciation) -> dict[str, validate.BlockInfo]:
    """id -> provenance / có phải tiêu đề / độ dài / phải nói — cho bộ kiểm tra."""
    titles = {b.id for b in page.blocks if isinstance(b, ParsedParagraph) and b.role == "title"}
    cover = {b.id for b in page.blocks if must_cover(b)}
    return {i: validate.BlockInfo(provenance=p, is_title=i in titles,
                                  syllables=count(t, pron)[0], text=t, must_cover=i in cover)
            for i, p, t in page_blocks(page)}


def check(ss: SlideScript, doc: ParsedDocument, page: ParsedPage,
          pron: Pronunciation) -> list[validate.Issue]:
    # trang lời kết không bị bắt nêu tiêu đề
    title = None if is_closing(doc, page) else page.title
    return validate.check(ss, block_info(page, pron), pron, title)


def render_prompt(template: str, doc: ParsedDocument, page: ParsedPage, stype: str,
                  previous: list[SlideScript]) -> str:
    prev_p = doc.page(page.page_no - 1)
    next_p = doc.page(page.page_no + 1)
    sec = doc.section_of(page.page_no)
    key = rules_key(doc, page)

    blocks = page_blocks(page)
    # Ghi rõ khối nào là ẢNH: ảnh người đã sửa mô tả mang provenance=manual, nhìn provenance
    # không còn phân biệt được với chữ trên slide -> LLM lại đem mô tả ảnh ra đọc.
    # Khối link cũng ghi rõ: không biết thì LLM đọc "nhatot chấm com gạch chéo…" ra miệng.
    label = {b.id: "ẢNH" for b in page.blocks if isinstance(b, ParsedImage)}
    label |= {b.id: "DANH SÁCH LINK — không đọc địa chỉ" for b in page.paragraphs
              if b.role == "links"}
    btxt = "\n\n".join(
        f"[{i}] ({label.get(i, 'chữ trên slide')}, provenance={p})\n{t}"
        for i, p, t in blocks) or "(trang không có chữ)"

    if previous:
        older, recent = previous[:-PREV_FULL], previous[-PREV_FULL:]
        lines = [f"- Trang {s.page_no} (chỉ câu mở đầu): {s.sentences[0].text}"
                 for s in older if s.sentences]
        lines += [f"- Trang {s.page_no}: {s.text}" for s in recent]
        prev_txt = "\n".join(lines)
    else:
        prev_txt = "(chưa có — đây là trang đầu của chương)"

    fill = {
        "deck_title": (doc.pages[0].title or doc.doc_id) if doc.pages else doc.doc_id,
        "page_no": str(page.page_no),
        "n_pages": str(doc.n_pages),
        "section_title": sec.title if sec else "(mở đầu)",
        "slide_type": key,
        "prev_title": (prev_p.title if prev_p else None) or "(không có)",
        "next_title": (next_p.title if next_p else None) or "(không có)",
        "blocks": btxt,
        "previous_script": prev_txt,
        "type_rules": TYPE_RULES[key],
    }
    out = template
    for k, v in fill.items():
        out = out.replace("{{" + k + "}}", v)
    return out


# ------------------------------------------------------------------- một trang


def _num(v: object, default: float, lo: float, hi: float) -> float:
    """LLM trả "300ms", "chậm", null... -> không được làm sập cả lần chạy. Hỏng thì lấy
    mặc định, lệch thì kẹp về khoảng hợp lý."""
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return min(hi, max(lo, x))


def to_script(raw: dict, page: ParsedPage, stype: str, pron: Pronunciation,
              model: str, prompt_hash: str) -> SlideScript:
    sents: list[Sentence] = []
    unknown: list[str] = []
    items = raw.get("sentences") if isinstance(raw, dict) else None
    for s in items if isinstance(items, list) else []:
        if not isinstance(s, dict):
            continue
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        kind = "content" if s.get("kind") == "content" else "delivery"
        ref = str(s.get("ref")).strip() if s.get("ref") else None
        n, unk = count(text, pron)
        unknown += unk
        emph = s.get("emphasis") if isinstance(s.get("emphasis"), list) else []
        sents.append(Sentence(
            kind=kind, text=text, syllables=n,
            grounding=Grounding(type="kb_chunk", ref=ref) if kind == "content"
            else Grounding(type="structure"),
            prosody=Prosody(
                # từ nhấn không nằm trong câu thì TTS không có chỗ nhấn -> bỏ
                emphasis=[str(x) for x in emph if str(x).lower() in text.lower()][:3],
                pause_before_ms=int(_num(s.get("pause_before_ms"), 0, 0, 1500)),
                speed=_num(s.get("speed"), 1.0, 0.8, 1.2),
            ),
        ))
    return SlideScript(page_no=page.page_no, page_hash=page.page_hash, slide_type=stype,
                       section_id=page.section_id, sentences=sents,
                       unknown_terms=sorted(set(unknown)), model=model, prompt_hash=prompt_hash)


def _badness(issues: list[validate.Issue]) -> tuple[int, int]:
    return (sum(i.level == "red" for i in issues), sum(i.level == "fix" for i in issues))


async def write_page(llm: LLM, sem: asyncio.Semaphore, template: str, prompt_hash: str,
                     doc: ParsedDocument, page: ParsedPage, previous: list[SlideScript],
                     pron: Pronunciation) -> SlideScript:
    stype = page.slide_type
    prompt = render_prompt(template, doc, page, stype, previous)
    msgs = [{"role": "user", "content": prompt}]
    tag = f"p{page.page_no:03d}"

    try:
        async with sem:
            raw = await llm.chat(msgs, f"{tag}_pass1")
    except RuntimeError as e:
        log.error("  p%d LLM that bai: %s", page.page_no, e)
        return SlideScript(page_no=page.page_no, page_hash=page.page_hash, slide_type=stype,
                           section_id=page.section_id, flags=["llm_failed"],
                           model=llm.model, prompt_hash=prompt_hash)

    ss = to_script(raw, page, stype, pron, llm.model, prompt_hash)
    issues = check(ss, doc, page, pron)

    # Pass 2: CHỈ cho trang trượt, kèm danh sách lỗi cụ thể. Không thử lần 3.
    if validate.needs_fix(issues):
        fixes = "\n".join(f"- {i}" for i in issues if i.level in ("red", "fix"))
        msgs2 = msgs + [
            {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)},
            {"role": "user", "content":
                "Kịch bản trên vi phạm các luật sau:\n" + fixes +
                "\n\nSửa lại. Giữ nguyên các câu không lỗi. Trả về đúng định dạng JSON như trước."},
        ]
        try:
            async with sem:
                raw2 = await llm.chat(msgs2, f"{tag}_pass2")
            ss2 = to_script(raw2, page, stype, pron, llm.model, prompt_hash)
            issues2 = check(ss2, doc, page, pron)
            # Pass 2 KHÔNG mặc nhiên tốt hơn: sửa lỗi này có thể đẻ lỗi khác, kể cả cờ đỏ.
            # Giữ bản ít lỗi hơn (cờ đỏ trước, lỗi thường sau); bằng nhau thì lấy pass 2.
            if _badness(issues2) <= _badness(issues):
                ss, issues = ss2, issues2
            else:
                log.info("  p%d pass 2 te hon (%s > %s) -> giu pass 1", page.page_no,
                         _badness(issues2), _badness(issues))
            ss.passes = 2
        except RuntimeError as e:
            log.warning("  p%d pass 2 that bai, giu pass 1: %s", page.page_no, e)

    ss.flags = validate.to_flags(issues)
    log.info("  p%-3d %-15s %d cau %3d am tiet %4.1fs  pass%d  %s",
             ss.page_no, rules_key(doc, page), len(ss.sentences), ss.syllables, ss.seconds,
             ss.passes, ",".join(ss.flags) or "ok")
    return ss


# ------------------------------------------------------------------ cả deck


def groups(doc: ParsedDocument) -> list[list[ParsedPage]]:
    """Mỗi section một nhóm; trang chưa thuộc section nào (mở đầu) thành một nhóm riêng."""
    by: dict[str, list[ParsedPage]] = {}
    for p in doc.pages:
        by.setdefault(p.section_id or "_intro", []).append(p)
    return [sorted(v, key=lambda p: p.page_no) for _, v in sorted(
        by.items(), key=lambda kv: min(p.page_no for p in kv[1]))]


def recount(ss: SlideScript, pron: Pronunciation) -> SlideScript:
    """pronunciation đổi -> KHÔNG gọi LLM, chỉ đếm lại âm tiết."""
    unk: list[str] = []
    for s in ss.sentences:
        s.syllables, u = count(s.text, pron)
        unk += u
    ss.unknown_terms = sorted(set(unk))
    return ss


async def run_deck(doc: ParsedDocument, pron: Pronunciation, *, model: str,
                   existing: dict[int, SlideScript], only: set[int] | None,
                   force: bool, workers: int, log_dir: Path,
                   on_page: Callable[[dict[int, SlideScript]], None] | None = None,
                   ) -> list[SlideScript]:
    """`on_page(results)` gọi sau MỖI trang xong — để lưu dần. Chạy hỏng giữa chừng (mạng,
    Ctrl+C) thì các trang đã trả tiền vẫn còn trên đĩa."""
    template, prompt_hash = load_prompt()
    llm = LLM(model, log_dir, backoff=5.0)             # offline: kiên nhẫn, 5+10+20s
    sem = asyncio.Semaphore(workers)
    results: dict[int, SlideScript] = {}

    async def fresh_write(page: ParsedPage, done: list[SlideScript],
                          old: SlideScript | None) -> SlideScript:
        try:
            ss = await write_page(llm, sem, template, prompt_hash, doc, page, done, pron)
        except Exception:                            # một trang hỏng không được kéo sập cả deck
            log.exception("  p%d loi khi viet", page.page_no)
            ss = SlideScript(page_no=page.page_no, page_hash=page.page_hash,
                             slide_type=page.slide_type, section_id=page.section_id,
                             flags=["llm_failed"], model=llm.model, prompt_hash=prompt_hash)
        if "llm_failed" in ss.flags and not ss.sentences and old is not None:
            # API sập (đo được: 503 "temporarily unavailable" cả loạt) -> GIỮ bản cũ, gắn cờ.
            # Ghi đè bằng bản rỗng là mất kịch bản đã trả tiền. prompt_hash cũ giữ nguyên nên
            # lần chạy sau vẫn thấy trang này cần viết lại.
            ss = old.model_copy(update={"flags": sorted({*old.flags, "llm_failed"})})
        return ss

    async def run_group(pages: list[ParsedPage]) -> None:
        done: list[SlideScript] = []                 # kịch bản các trang trước cùng section
        dirty = False                                # một trang viết lại -> các trang SAU
        for page in pages:                           # cùng section có thể lặp ý (§13)
            old = existing.get(page.page_no)
            want = only is None or page.page_no in only
            fresh = (old is not None and not force and old.page_hash == page.page_hash
                     and old.prompt_hash == prompt_hash and old.model == model
                     and old.slide_type == page.slide_type
                     and not (dirty and only is None))
            if old is not None and old.edited_by == "nguoi":
                # Người sửa tay -> giữ nguyên. Vẫn đếm lại + KIỂM, trượt thì chỉ gắn cờ để
                # người thấy, không để LLM viết đè. Không bật `dirty`: trang sau không phải
                # viết lại vì trang này.
                ss = recount(old, pron)
                ss.flags = validate.to_flags(check(ss, doc, page, pron))
                if old.page_hash != page.page_hash:
                    ss.flags.append("stale_manual")
            elif old is not None and fresh and want:
                # Dùng lại được — nhưng KIỂM LẠI: bộ kiểm có thể vừa thêm luật mới. Chỉ viết
                # lại khi trượt lỗi MỚI (chưa có trong cờ lần trước) hoặc lần trước API hỏng.
                # Lỗi cũ mà pass 2 đã không sửa được thì giữ + gắn cờ — không thế thì MỖI lần
                # chạy lại (vd chỉ đổi kho phát âm) là trả tiền viết lại mọi trang còn cờ vàng.
                prev = set(old.flags)                # chụp TRƯỚC: recount sửa `old` tại chỗ
                ss = recount(old, pron)
                issues = check(ss, doc, page, pron)
                ss.flags = validate.to_flags(issues)
                new = {i.code for i in issues if i.level in ("red", "fix")} - prev
                if new or "llm_failed" in prev:
                    log.info("  p%-3d dung lai KHONG dat luat moi (%s) -> viet lai",
                             page.page_no, ",".join(sorted(new) or ["llm_failed"]))
                    ss = await fresh_write(page, done, old)
                    dirty = True
            elif old is not None and not want:
                # không chọn -> giữ chữ, nhưng đếm lại + kiểm lại (rẻ) để cờ không cũ
                ss = recount(old, pron)
                ss.flags = validate.to_flags(check(ss, doc, page, pron))
            elif want:
                ss = await fresh_write(page, done, old)
                dirty = True
            else:
                continue                             # không chọn, chưa có -> bỏ qua
            results[page.page_no] = ss
            done.append(ss)
            if on_page:
                on_page(results)

    try:
        await asyncio.gather(*(run_group(g) for g in groups(doc)))
    finally:
        await llm.close()
    log.info("  %d lan goi LLM", llm.n_calls)
    return [results[k] for k in sorted(results)]
