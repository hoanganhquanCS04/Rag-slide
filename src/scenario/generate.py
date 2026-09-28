"""Sinh kịch bản từng trang bằng LLM. Xem docs/spec/scenario.md.

Ba điểm dễ sai:

  1. **Chỉ đưa nội dung CHÍNH TRANG ĐÓ.** Không nhồi cả deck (§10). Nguồn sự thật là các
     khối của `ParsedPage`, mỗi khối kèm `id` + `provenance` để LLM trỏ `ref` vào được.
  2. **Song song theo SECTION, tuần tự TRONG section.** 7 trang liền cùng tên "Đồ thị dạng
     đường" — viết độc lập thì robot mở đầu y hệt bảy lần. Trang sau đọc kịch bản trang
     trước cùng section. Đây là thứ thay cho `message` đã bỏ.
  3. **`syllables` do code tính.** LLM chỉ viết chữ; đếm âm tiết theo pronunciation.json.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from llm import LLM, ROOT
from parsing.models import ParsedDocument, ParsedImage, ParsedPage, ParsedParagraph
from scenario import validate
from scenario.models import Grounding, Prosody, Sentence, SlideScript
from scenario.syllables import Pronunciation, count

log = logging.getLogger(__name__)

PROMPT_PATH = ROOT / "prompts" / "s4_scenario.md"

TYPE_RULES = {
    "section_divider": (
        "Đây là TRANG CHUYỂN CHƯƠNG: trên trang chỉ có tên chương, không có nội dung.\n"
        "- Viết 1–2 câu, TẤT CẢ là `delivery`. CẤM câu `content`.\n"
        "- Báo hiệu chuyển sang chương mới và NÊU TÊN CHƯƠNG (lấy đúng tiêu đề trang).\n"
        "- Có thể thêm một câu hỏi tu từ gợi tò mò.\n"
        "- CẤM giảng giải chương này là gì — trên trang không có thông tin đó."
    ),
    "content": (
        "Đây là TRANG NỘI DUNG.\n"
        "- Viết 3–6 câu. Mở bằng một câu `delivery` dẫn dắt, rồi các câu `content`.\n"
        "- Giảng điều người học cần nắm. Không tả lại hình ảnh từng chi tiết."
    ),
    "exercise": (
        "Đây là TRANG BÀI TẬP: người học cần biết PHẢI LÀM GÌ, không cần nghe giảng.\n"
        "- Viết 2–4 câu. Một câu `delivery` báo đến phần bài tập, còn lại là `content`.\n"
        "- ĐỌC LẠI yêu cầu, hạn nộp, thứ cần nộp — đúng như trên trang, không thêm bớt.\n"
        "- CẤM giảng lại kiến thức, CẤM gợi ý cách làm — trên trang không có thông tin đó.\n"
        "- Danh sách link: CHỈ nói có mấy trang và là trang gì, KHÔNG đọc địa chỉ."
    ),
}


def load_prompt() -> tuple[str, str]:
    t = PROMPT_PATH.read_text(encoding="utf-8")
    return t, hashlib.sha1(t.encode()).hexdigest()[:12]


# ------------------------------------------------------------------ dựng input


def page_blocks(page: ParsedPage) -> list[tuple[str, str, str]]:
    """-> [(id, provenance, text)] — chỉ khối CÓ nội dung, để LLM trỏ ref vào."""
    return [(b.id, b.provenance.value, b.content.strip())
            for b in page.blocks if b.content and b.content.strip()]


def block_info(page: ParsedPage, pron: Pronunciation) -> dict[str, validate.BlockInfo]:
    """id -> provenance / có phải tiêu đề / độ dài — cho bộ kiểm tra grounding."""
    titles = {b.id for b in page.blocks if isinstance(b, ParsedParagraph) and b.role == "title"}
    return {i: validate.BlockInfo(provenance=p, is_title=i in titles,
                                  syllables=count(t, pron)[0])
            for i, p, t in page_blocks(page)}


def render_prompt(template: str, doc: ParsedDocument, page: ParsedPage, stype: str,
                  previous: list[SlideScript], pron: Pronunciation) -> str:
    prev_p = doc.page(page.page_no - 1)
    next_p = doc.page(page.page_no + 1)
    sec = doc.section_of(page.page_no)

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
        prev_txt = "\n".join(f"- Trang {s.page_no}: {s.text}" for s in previous)
    else:
        prev_txt = "(chưa có — đây là trang đầu của chương)"

    fill = {
        "deck_title": (doc.pages[0].title or doc.doc_id) if doc.pages else doc.doc_id,
        "page_no": str(page.page_no),
        "n_pages": str(doc.n_pages),
        "section_title": sec.title if sec else "(mở đầu)",
        "slide_type": stype,
        "prev_title": (prev_p.title if prev_p else None) or "(không có)",
        "next_title": (next_p.title if next_p else None) or "(không có)",
        "blocks": btxt,
        "previous_script": prev_txt,
        "terms": ", ".join(pron.keys),
        "type_rules": TYPE_RULES[stype],
    }
    out = template
    for k, v in fill.items():
        out = out.replace("{{" + k + "}}", v)
    return out


# ------------------------------------------------------------------- gọi LLM


# ------------------------------------------------------------------- một trang


def to_script(raw: dict, page: ParsedPage, stype: str, pron: Pronunciation,
              model: str, prompt_hash: str) -> SlideScript:
    sents: list[Sentence] = []
    unknown: list[str] = []
    for s in raw.get("sentences", []):
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        kind = "content" if s.get("kind") == "content" else "delivery"
        ref = s.get("ref") or None
        n, unk = count(text, pron)
        unknown += unk
        sents.append(Sentence(
            kind=kind, text=text, syllables=n,
            grounding=Grounding(type="kb_chunk", ref=ref) if kind == "content"
            else Grounding(type="structure"),
            prosody=Prosody(
                emphasis=[str(x) for x in (s.get("emphasis") or [])][:3],
                pause_before_ms=int(s.get("pause_before_ms") or 0),
                speed=float(s.get("speed") or 1.0),
            ),
        ))
    sec = page.section_id
    return SlideScript(page_no=page.page_no, page_hash=page.page_hash, slide_type=stype,
                       section_id=sec, sentences=sents, unknown_terms=sorted(set(unknown)),
                       model=model, prompt_hash=prompt_hash)


async def write_page(llm: LLM, sem: asyncio.Semaphore, template: str, prompt_hash: str,
                     doc: ParsedDocument, page: ParsedPage, previous: list[SlideScript],
                     pron: Pronunciation) -> SlideScript:
    stype = page.slide_type
    prompt = render_prompt(template, doc, page, stype, previous, pron)
    refs = block_info(page, pron)
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
    issues = validate.check(ss, refs, pron, page.title)

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
            issues2 = validate.check(ss2, refs, pron, page.title)
            ss2.passes = 2
            ss, issues = ss2, issues2
        except RuntimeError as e:
            log.warning("  p%d pass 2 that bai, giu pass 1: %s", page.page_no, e)

    ss.flags = validate.to_flags(issues)
    log.info("  p%-3d %-15s %d cau %3d am tiet %4.1fs  pass%d  %s",
             ss.page_no, stype, len(ss.sentences), ss.syllables, ss.seconds, ss.passes,
             ",".join(ss.flags) or "ok")
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
                   force: bool, workers: int, log_dir: Path) -> list[SlideScript]:
    template, prompt_hash = load_prompt()
    llm = LLM(model, log_dir)
    sem = asyncio.Semaphore(workers)
    results: dict[int, SlideScript] = {}

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
                ss.flags = validate.to_flags(
                    validate.check(ss, block_info(page, pron), pron, page.title))
                if old.page_hash != page.page_hash:
                    ss.flags.append("stale_manual")
            elif old is not None and fresh and want:
                # Dùng lại được — nhưng KIỂM LẠI: bộ kiểm có thể vừa thêm luật mới. Trượt
                # thì viết lại, không để kịch bản cũ lọt qua luật mới chỉ vì prompt không đổi.
                ss = recount(old, pron)
                issues = validate.check(ss, block_info(page, pron), pron, page.title)
                ss.flags = validate.to_flags(issues)
                if validate.needs_fix(issues):
                    log.info("  p%-3d dung lai KHONG dat luat moi (%s) -> viet lai",
                             page.page_no, ",".join(ss.flags))
                    ss = await write_page(llm, sem, template, prompt_hash, doc, page, done, pron)
                    dirty = True
            elif old is not None and not want:
                ss = recount(old, pron)
            elif want:
                ss = await write_page(llm, sem, template, prompt_hash, doc, page, done, pron)
                dirty = True
            else:
                continue                             # không chọn, chưa có -> bỏ qua
            results[page.page_no] = ss
            done.append(ss)

    try:
        await asyncio.gather(*(run_group(g) for g in groups(doc)))
    finally:
        await llm.close()
    log.info("  %d lan goi LLM", llm.n_calls)
    return [results[k] for k in sorted(results)]
