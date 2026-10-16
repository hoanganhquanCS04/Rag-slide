"""Sinh kịch bản từng trang bằng LLM (S4).

Để làm gì: viết lời robot nói cho mỗi trang — MỘT đoạn văn liền + danh sách khối nguồn (`refs`).
Cách làm:
    - Mỗi trang chỉ đưa nội dung CỦA TRANG ĐÓ vào prompt (không nhồi cả deck).
    - Các chương chạy song song; trong một chương thì chạy tuần tự, trang sau đọc kịch bản các
      trang trước cùng chương để không mở đầu giống nhau.
    - LLM chỉ viết chữ; số âm tiết do code đếm theo kho phát âm.
    - Viết xong code kiểm; có lỗi thì cho LLM sửa một lần (pass 2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from utils.config import PROMPTS_DIR
from utils.llm import LLM
from models.parsed_document import (ParsedDocument, ParsedImage, ParsedPage, ParsedParagraph,
                            ParsedTable)
from offline.scenario import validate
from models.scenario import SlideScript
from utils.syllables import Pronunciation, count

log = logging.getLogger(__name__)

PROMPT_PATH = PROMPTS_DIR / "s4_scenario.md"

# Kịch bản các trang trước cùng section: N trang gần nhất đưa NGUYÊN VĂN, trang xa hơn chỉ
# đưa câu mở đầu (đủ để không mở đầu giống nhau). Không giới hạn thì trang cuối sec_01 của
# Onboarding (23 trang) mang theo kịch bản 22 trang — nhồi cả chương vào prompt.
PREV_FULL = 3
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

TYPE_RULES = {
    "section_divider": (
        "Đây là TRANG CHUYỂN CHƯƠNG: trên trang chỉ có tên chương, không có nội dung.\n"
        "- Viết 1–2 câu: báo hiệu chuyển sang chương mới và NÊU TÊN CHƯƠNG (lấy đúng tiêu đề trang).\n"
        "- Có thể thêm một câu hỏi tu từ gợi tò mò.\n"
        "- CẤM giảng giải chương này là gì — trên trang không có thông tin đó.\n"
        "- `refs` có thể để rỗng."
    ),
    "closing": (
        "Đây là TRANG KẾT THÚC bài thuyết trình (lời cảm ơn), KHÔNG phải chương mới.\n"
        "- Viết 1–2 câu: cảm ơn người nghe, có thể mời đặt câu hỏi.\n"
        "- CẤM tóm tắt lại nội dung — trên trang không có thông tin đó.\n"
        "- Không cần đọc lại tiêu đề trang. `refs` có thể để rỗng."
    ),
    "content": (
        "Đây là TRANG NỘI DUNG.\n"
        "- NÓI ĐỦ Ý: MỌI thông tin trên trang phải được nói ra — số liệu, đối tượng, điều kiện,\n"
        "  ngoại lệ, mốc thời gian. Người nghe phải hình dung được hết thông tin trên trang.\n"
        "- KHÔNG giới hạn độ dài: trang ít chữ thì nói ngắn, trang nhiều chữ thì nói dài.\n"
        "- Đủ ý KHÔNG phải đọc nguyên văn: gộp các mục cùng loại thành câu nói tự nhiên.\n"
        "- Không tả lại hình ảnh từng chi tiết."
    ),
    "exercise": (
        "Đây là TRANG BÀI TẬP: người nghe cần biết PHẢI LÀM GÌ, không cần nghe giảng.\n"
        "- Báo đến phần bài tập, rồi ĐỌC LẠI yêu cầu, hạn nộp, thứ cần nộp — đúng như trên trang.\n"
        "- CẤM giảng lại kiến thức, CẤM gợi ý cách làm — trên trang không có thông tin đó.\n"
        "- Danh sách link: CHỈ nói có mấy trang và là trang gì, KHÔNG đọc địa chỉ."
    ),
}


def load_prompt() -> tuple[str, str]:
    """Đọc prompt `s4_scenario.md` -> (nội dung, hash 12 ký tự). Hash đổi = prompt đổi = viết lại."""
    t = PROMPT_PATH.read_text(encoding="utf-8")
    return t, hashlib.sha1(t.encode()).hexdigest()[:12]


# ------------------------------------------------------------------ dựng input


def is_closing(doc: ParsedDocument, page: ParsedPage) -> bool:
    """Trang có phải lời kết không: trang chuyển chương nằm CUỐI deck ("THANK YOU!")."""
    return page.slide_type == "section_divider" and page.page_no == doc.pages[-1].page_no


def rules_key(doc: ParsedDocument, page: ParsedPage) -> str:
    """Chọn bộ luật cho trang: "closing" nếu là lời kết, không thì theo loại trang (slide_type)."""
    return "closing" if is_closing(doc, page) else page.slide_type


def page_blocks(page: ParsedPage) -> list[tuple[str, str]]:
    """Các khối CÓ nội dung của trang -> [(id, chữ)], để LLM trỏ `ref` vào."""
    return [(b.id, b.content.strip())
            for b in page.blocks if b.content and b.content.strip()]


def must_cover(b: object) -> bool:
    """Khối có bắt buộc phải được nói tới không.

    Cách làm: chỉ đoạn văn / danh sách / bảng có >= 3 chữ; không tính tiêu đề, khối link, chú thích, ảnh.
    """
    if isinstance(b, ParsedParagraph) and b.role not in ("body", "list"):
        return False
    return (isinstance(b, (ParsedParagraph, ParsedTable))
            and len((b.content or "").split()) >= 3)


def block_info(page: ParsedPage) -> dict[str, validate.BlockInfo]:
    """Thông tin các khối của trang cho bộ kiểm -> {id: (chữ, có phải nói không)}."""
    cover = {b.id for b in page.blocks if must_cover(b)}
    return {i: validate.BlockInfo(text=t, must_cover=i in cover) for i, t in page_blocks(page)}


def check(ss: SlideScript, page: ParsedPage) -> list[validate.Issue]:
    """Kiểm kịch bản một trang (gọi `validate.check` với các khối của trang)."""
    return validate.check(ss, block_info(page))


def render_prompt(template: str, doc: ParsedDocument, page: ParsedPage, stype: str,
                  previous: list[SlideScript]) -> str:
    """Dựng prompt cho một trang từ template.

    Cách làm: thay các chỗ {{...}} bằng — tên deck, số trang, tên chương, loại trang, tiêu đề trang
    trước / sau, các khối của trang (ghi rõ khối nào là ẢNH, khối nào là LINK), kịch bản các trang
    trước cùng chương (3 trang gần nhất đầy đủ, xa hơn chỉ câu đầu tiên), và luật riêng của loại trang.
    """
    prev_p = doc.page(page.page_no - 1)
    next_p = doc.page(page.page_no + 1)
    sec = doc.section_of(page.page_no)
    key = rules_key(doc, page)

    blocks = page_blocks(page)
    # Ghi rõ khối nào là ẢNH: không ghi thì LLM coi mô tả ảnh như chữ trên slide và đem ra đọc.
    # Khối link cũng ghi rõ: không biết thì LLM đọc "nhatot chấm com gạch chéo…" ra miệng.
    label = {b.id: "ẢNH" for b in page.blocks if isinstance(b, ParsedImage)}
    label |= {b.id: "DANH SÁCH LINK — không đọc địa chỉ" for b in page.paragraphs
              if b.role == "links"}
    btxt = "\n\n".join(
        f"[{i}] ({label.get(i, 'chữ trên slide')})\n{t}"
        for i, t in blocks) or "(trang không có chữ)"

    if previous:
        older, recent = previous[:-PREV_FULL], previous[-PREV_FULL:]
        lines = [f"- Trang {s.page_no} (chỉ câu mở đầu): {SENTENCE_END.split(s.text, 1)[0]}"
                 for s in older if s.text]
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


def to_script(raw: dict, page: ParsedPage, stype: str, pron: Pronunciation,
              model: str, prompt_hash: str) -> SlideScript:
    """Đổi JSON LLM trả về ({"text", "refs"}) thành SlideScript.

    Cách làm: dồn khoảng trắng / xuống dòng trong `text` thành một dấu cách; `refs` nhận list (hoặc
    một chuỗi lẻ), bỏ trùng; đếm âm tiết và từ lạ bằng code.
    """
    text = " ".join(str(raw.get("text") or "").split()) if isinstance(raw, dict) else ""
    refs = raw.get("refs") if isinstance(raw, dict) else None
    refs = [refs] if isinstance(refs, str) else refs if isinstance(refs, list) else []
    refs = list(dict.fromkeys(str(r).strip().strip("[]") for r in refs if str(r).strip()))
    n, unknown = count(text, pron)
    return SlideScript(page_no=page.page_no, page_hash=page.page_hash, slide_type=stype,
                       section_id=page.section_id, text=text, refs=refs, syllables=n,
                       unknown_terms=sorted(set(unknown)), model=model, prompt_hash=prompt_hash)


def _badness(issues: list[validate.Issue]) -> tuple[int, int]:
    """Độ tệ của một bộ lỗi -> (số lỗi đỏ, số lỗi "sửa"), để so pass 1 với pass 2."""
    return (sum(i.level == "red" for i in issues), sum(i.level == "fix" for i in issues))


async def write_page(llm: LLM, sem: asyncio.Semaphore, template: str, prompt_hash: str,
                     doc: ParsedDocument, page: ParsedPage, previous: list[SlideScript],
                     pron: Pronunciation) -> SlideScript:
    """Viết kịch bản một trang -> SlideScript.

    Cách làm: pass 1 gọi LLM; LLM lỗi thì trả kịch bản rỗng có cờ `llm_failed`; kiểm bằng code; có lỗi
    thì pass 2 gửi kèm danh sách lỗi để LLM sửa, giữ bản ít lỗi hơn; cuối cùng gắn cờ.
    """
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
    issues = check(ss, page)

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
            issues2 = check(ss2, page)
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
    log.info("  p%-3d %-15s %d ref %3d am tiet %4.1fs  pass%d  %s",
             ss.page_no, rules_key(doc, page), len(ss.refs), ss.syllables, ss.seconds,
             ss.passes, ",".join(ss.flags) or "ok")
    return ss


# ------------------------------------------------------------------ cả deck


def groups(doc: ParsedDocument) -> list[list[ParsedPage]]:
    """Chia các trang thành nhóm theo chương (trang trước chương đầu thành một nhóm riêng)."""
    by: dict[str, list[ParsedPage]] = {}
    for p in doc.pages:
        by.setdefault(p.section_id or "_intro", []).append(p)
    return [sorted(v, key=lambda p: p.page_no) for _, v in sorted(
        by.items(), key=lambda kv: min(p.page_no for p in kv[1]))]


def recount(ss: SlideScript, pron: Pronunciation) -> SlideScript:
    """Đếm lại âm tiết và từ lạ của kịch bản cũ theo kho phát âm hiện tại (không gọi LLM)."""
    ss.syllables, unk = count(ss.text, pron)
    ss.unknown_terms = sorted(set(unk))
    return ss


async def run_deck(doc: ParsedDocument, pron: Pronunciation, *, model: str,
                   existing: dict[int, SlideScript], only: set[int] | None,
                   force: bool, workers: int, log_dir: Path,
                   on_page: Callable[[dict[int, SlideScript]], None] | None = None,
                   ) -> list[SlideScript]:
    """Viết kịch bản cả deck -> danh sách SlideScript theo thứ tự trang.

    Cách làm: các chương chạy song song, trong chương chạy tuần tự. Mỗi trang:
        - kịch bản cũ còn dùng được (cùng page_hash, prompt, model, loại trang) -> đếm lại + kiểm lại,
          chỉ viết lại nếu có lỗi MỚI hoặc lần trước LLM lỗi;
        - không được chọn (`only`) -> giữ chữ, đếm lại + kiểm lại;
        - còn lại -> viết mới.
    Viết lại một trang thì các trang sau cùng chương cũng viết lại. `on_page` gọi sau mỗi trang để lưu dần.
    """
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
        if "llm_failed" in ss.flags and not ss.text and old is not None:
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
            if old is not None and fresh and want:
                # Dùng lại được — nhưng KIỂM LẠI: bộ kiểm có thể vừa thêm luật mới. Chỉ viết
                # lại khi trượt lỗi MỚI (chưa có trong cờ lần trước) hoặc lần trước API hỏng.
                # Lỗi cũ mà pass 2 đã không sửa được thì giữ + gắn cờ — không thế thì MỖI lần
                # chạy lại (vd chỉ đổi kho phát âm) là trả tiền viết lại mọi trang còn cờ vàng.
                prev = set(old.flags)                # chụp TRƯỚC: recount sửa `old` tại chỗ
                ss = recount(old, pron)
                issues = check(ss, page)
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
                ss.flags = validate.to_flags(check(ss, page))
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
