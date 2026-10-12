"""Đếm âm tiết theo kho phát âm CHUNG `data/pronunciation.json`. Xem docs/spec/pronunciation.md.

CODE đếm, không để LLM tự khai: LLM đếm âm tiết tiếng Việt sai có hệ thống, và nó không
biết `savefig` đọc thành "sếp phích" là 2 âm tiết.

    "Gọi savefig kèm tên file"
      Gọi(1) savefig->"sếp phích"(2) kèm(1) tên(1) file->"phai"(1)

Từ lạ không có trong kho -> đếm ước lượng VÀ báo `unknown_pronunciation`. Không được
lặng lẽ đếm bừa — đó là cách timing lệch mà không ai thấy. S4 chạy TRƯỚC khi kho có từ
đó là bình thường: `scripts/extract_terms.py` gom từ lạ trong kịch bản vào kho, người
chốt cách đọc, chạy lại S4 thì chỉ đếm lại (không gọi LLM).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

STORE = Path(__file__).resolve().parents[2] / "data" / "pronunciation.json"

VN = "àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ"

# Số có dấu phân cách ("2,7", "230.000") tách TRƯỚC, thành MỘT token — không thì "1.5" bị
# coi là từ lạ kiểu `plt.savefig`, lọt vào kho phát âm (đo được: 1.5, 2.7, 230.000, 3.9).
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_TOKEN = re.compile(r"\d+(?:[.,]\d+)+(?!\w)|\w+(?:\.\w+)*")

# Một âm tiết tiếng Việt, đã bỏ dấu: (phụ âm đầu)? + nguyên âm + (phụ âm cuối)?
# Dùng để không báo oan "ba", "hai", "cho", "theo", "trong" là từ tiếng Anh.
_VI_PLAIN = re.compile(
    r"^(?:ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|b|c|d|g|h|k|l|m|n|p|r|s|t|v|x)?"
    r"[aeiouy]{1,3}"
    r"(?:ng|nh|ch|c|m|n|p|t)?$"
)

# Đọc từng chữ cái theo tên chữ tiếng Việt — cách đoán mặc định cho viết tắt
LETTER_VI = {
    "a": "a", "b": "bê", "c": "xê", "d": "đê", "đ": "đê", "e": "e", "f": "ép", "g": "giê",
    "h": "hát", "i": "i", "j": "gi", "k": "ca", "l": "e lờ", "m": "em", "n": "en",
    "o": "o", "p": "pê", "q": "quy", "r": "e rờ", "s": "ét", "t": "tê", "u": "u",
    "v": "vê", "w": "vê kép", "x": "ích", "y": "i dài", "z": "dét",
    "0": "không", "1": "một", "2": "hai", "3": "ba", "4": "bốn", "5": "năm",
    "6": "sáu", "7": "bảy", "8": "tám", "9": "chín",
}


def spell(word: str) -> str:
    """CBNV -> 'xê bê en vê', T7 -> 'tê bảy'."""
    return " ".join(LETTER_VI.get(c.lower(), c) for c in word if c.isalnum())


def _base(tok: str) -> str:
    """Bỏ dấu: THỜI -> thoi, CBLĐ -> cbld."""
    t = unicodedata.normalize("NFD", tok.lower()).replace("đ", "d")
    return "".join(c for c in t if not unicodedata.combining(c))


def _has_mark(tok: str) -> bool:
    return any(c in VN for c in tok.lower())


def _vi_shape(tok: str) -> bool:
    return bool(_VI_PLAIN.match(_base(tok)))


def is_abbrev(tok: str) -> bool:
    """Toàn chữ hoa = viết tắt, KỂ CẢ khi có chữ Việt: CBLĐ, HĐLĐ, VNĐ (bản cũ thấy "Đ" là
    tưởng tiếng Việt, đếm 1 âm tiết). Trừ chữ Việt viết hoa: có dấu + đúng dạng âm tiết
    (THỜI, LƯƠNG). Chữ hoa KHÔNG dấu dạng âm tiết (GIAN, IT) thì không phân biệt được một
    mình — `words()` gỡ bằng ngữ cảnh."""
    return len(tok) >= 2 and tok.isupper() and not (_has_mark(tok) and _vi_shape(tok))


def _is_vietnamese(tok: str) -> bool:
    return _has_mark(tok) or bool(_VI_PLAIN.match(tok.lower()))


def is_foreign(tok: str) -> bool:
    """Từ tiếng Anh hoặc viết tắt — thứ phải có cách đọc trong kho."""
    return not _NUMBER.fullmatch(tok) and (is_abbrev(tok) or not _is_vietnamese(tok))


def words(text: str) -> list[str]:
    """Tách từ (giữ nguyên `plt.savefig`). Dải chữ HOA mà có chữ mang dấu — "THỜI GIAN LÀM
    VIỆC" chép từ tiêu đề — là tiếng Việt viết hoa: hạ về chữ thường, để GIAN không bị
    đánh vần "giê i a en". Viết tắt thật trong dải (HĐLĐ) giữ nguyên."""
    ms = list(_TOKEN.finditer(text))
    out = [m.group() for m in ms]
    i = 0
    while i < len(ms):
        j = i
        while (j + 1 < len(ms) and out[j].isupper() and out[j + 1].isupper()
               and not text[ms[j].end():ms[j + 1].start()].strip()):
            j += 1
        if j > i and any(_has_mark(out[k]) for k in range(i, j + 1)):
            for k in range(i, j + 1):
                if _vi_shape(out[k]):
                    out[k] = out[k].lower()
        i = j + 1
    return out


def _read_group(g: int, padded: bool) -> int:
    """Số âm tiết của nhóm 1–999: 25 = hai mươi lăm (3), 105 = một trăm linh năm (4).
    `padded`: có nhóm lớn hơn đứng trước -> 25 đọc "không trăm hai mươi lăm" (+2)."""
    h, r = divmod(g, 100)
    t, u = divmod(r, 10)
    s = 2 if h else (2 if padded and r else 0)       # "hai trăm" / "không trăm"
    if t >= 2:
        s += 2                                       # "ba mươi"
    elif t == 1:
        s += 1                                       # "mười"
    elif (h or padded) and u:
        s += 1                                       # "linh"
    return s + (1 if u else 0)


def _read_int(n: int) -> int:
    """230000 = hai trăm ba mươi nghìn (5), 2025 = hai nghìn không trăm hai mươi lăm (7)."""
    if n == 0:
        return 1
    s, higher = 0, False
    for scale in (10**9, 10**6, 10**3, 1):          # tỷ, triệu, nghìn
        g, n = divmod(n, scale)
        if g:
            s += (_read_int(g) if g >= 1000 else _read_group(g, higher)) + (scale > 1)
            higher = True
    return s


def _number_syllables(tok: str) -> int:
    """Đọc số kiểu Việt. Nhóm sau dấu phân cách đều 3 chữ số = hàng nghìn ("230.000",
    "4.400.000"); còn lại là số thập phân ("1.5", "2,7" = hai phẩy bảy)."""
    parts = re.split(r"[.,]", tok)
    if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]):
        return _read_int(int("".join(parts)))
    if len(parts) > 1:
        return _read_int(int(parts[0])) + 1 + _read_int(int(parts[1]))   # "phẩy"
    return _read_int(int(tok))


def _guess(tok: str) -> int:
    """Từ lạ: viết tắt đếm theo cách đánh vần, từ Anh đếm theo cụm nguyên âm."""
    if is_abbrev(tok):
        return len(spell(tok).split())
    return max(1, len(re.findall(r"[aeiouy]+", tok.lower())))


def syllables_of(say: str) -> int:
    """Số âm tiết của một cách đọc — TÍNH, không đọc từ file: lưu trong file là mở cửa
    cho sửa `say` mà quên sửa số."""
    n = 0
    for tok in words(say):
        if _NUMBER.fullmatch(tok):
            n += _number_syllables(tok)
        elif _is_vietnamese(tok):
            n += 1
        else:
            n += _guess(tok)
    return n


class Pronunciation:
    def __init__(self, terms: dict[str, dict]):
        self.say = {k: str(v.get("say") or k) for k, v in terms.items()}
        self.syllables = {k: syllables_of(s) for k, s in self.say.items()}
        # Viết tắt khớp ĐÚNG hoa-thường: "MAY" trong kho không được bắt "may" tiếng Việt.
        # Từ thường khớp không phân biệt: "Python" = "python".
        self._exact = {k: k for k in terms if is_abbrev(k)}
        self._lower = {k.lower(): k for k in terms if not is_abbrev(k)}

    @classmethod
    def load(cls, path: str | Path = STORE) -> "Pronunciation":
        p = Path(path)
        if not p.exists():                  # S4 chạy trước khi có kho: mọi từ Anh là từ lạ
            return cls({})
        return cls(json.loads(p.read_text(encoding="utf-8")).get("terms", {}))

    def lookup(self, word: str) -> str | None:
        """-> key trong kho, hoặc None."""
        cands = [word, word.split(".")[-1]] if "." in word else [word]   # plt.savefig -> savefig
        for w in cands:
            if w in self._exact:
                return self._exact[w]
            if not is_abbrev(w) and w.lower() in self._lower:
                return self._lower[w.lower()]
        return None

    def terms_in(self, text: str) -> list[str]:
        """Thuật ngữ trong câu: từ Anh / viết tắt, hoặc từ có trong kho (`sin` trông như
        tiếng Việt nhưng kho biết nó là thuật ngữ). `plt.savefig` tách thành plt, savefig."""
        return [p for w in words(text) for p in w.split(".")
                if is_foreign(p) or self.lookup(p)]

    def hash_for(self, texts: Iterable[str]) -> str:
        """Hash CHỈ các mục mà kịch bản thật sự nói. Kho dùng chung nhiều deck — sửa một từ
        deck này không nói thì kịch bản deck này KHÔNG bị coi là lệch."""
        used = {k: self.say[k] for t in texts for w in words(t) if (k := self.lookup(w))}
        return hashlib.sha1(json.dumps(used, ensure_ascii=False, sort_keys=True).encode()
                            ).hexdigest()[:16]


def count(text: str, pron: Pronunciation) -> tuple[int, list[str]]:
    """-> (số âm tiết, các từ tiếng Anh / viết tắt KHÔNG có trong kho)."""
    n = 0
    unknown: list[str] = []
    for tok in words(text):
        if _NUMBER.fullmatch(tok):
            n += _number_syllables(tok)
            continue
        key = pron.lookup(tok)
        if key:
            n += pron.syllables[key]
        elif is_foreign(tok):
            # chưa ai quyết cách đọc: ước lượng, và BÁO
            n += _guess(tok)
            unknown.append(tok)
        else:
            n += 1
    return n, unknown
