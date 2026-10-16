"""Kiểu dữ liệu của kết quả parse (S0): một deck gồm các trang, mỗi trang gồm các khối.

    ParsedDocument -> ParsedPage -> Block (ParsedParagraph = chữ · ParsedTable = bảng · ParsedImage = ảnh)

Để làm gì: một định dạng duy nhất, vừa để người mở ra đọc vừa để các bước sau chạy.
Cách làm: ghi ra JSON gọn — trường rỗng không ghi (trừ `content`), nạp lại thì lấy giá trị mặc định.
Mọi chữ do VLM đọc từ ảnh trang nên CÓ THỂ SAI. Id khối `pNNN.bNN` = trang NNN, khối thứ NN.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

# Một dòng chỉ gồm một URL — "https://moso.vn/" · "www.abc.com/x"
URL_LINE = re.compile(r"(?:https?://|www\.)\S+")

# slide_type — luật, không gọi model. Đo trên 3_datavisualization, tách bạch không vùng xám:
#   phân mục  tiêu đề cx=0.50 cy=0.47 (7 trang)   ·   nội dung  cx=0.20 cy=0.11
DIVIDER_MIN_CY = 0.30
DIVIDER_CX_RANGE = (0.35, 0.65)
# CHỈ tiêu đề mở đầu bằng "Bài tập" — dò "yêu cầu" trong cả trang là bắt nhầm deck nhân sự
# (Onboarding p26 "Chấm công: Quản lý yêu cầu của CBNV")
EXERCISE_TITLE = re.compile(r"^\s*bài tập", re.I)
LABEL_CHARS = 48         # ParsedPage.label: cắt dòng chữ đầu khi trang không có tiêu đề

Point = tuple[float, float]
Box = tuple[float, float, float, float]   # (trái, trên, phải, dưới), [0,1], gốc trên-trái


def norm_text(s: str | None) -> str:
    """Dồn mọi khoảng trắng liền nhau thành một dấu cách và bỏ khoảng trắng hai đầu."""
    return re.sub(r"\s+", " ", s or "").strip()


def slugify_doc_id(stem: str) -> str:
    """Đổi tên file gốc thành doc_id sạch: "3_DataVisualization (1)" -> "3_datavisualization".

    Cách làm: bỏ đuôi " (1)" của file tải trùng -> bỏ dấu tiếng Việt -> ký tự lạ thành "_" -> viết thường.
    """
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)         # bỏ "(1)" của bản tải trùng
    # bỏ dấu TRƯỚC khi lọc ký tự: không thì "Thời gian" -> "th_i_gian"
    stem = unicodedata.normalize("NFD", stem.replace("đ", "d").replace("Đ", "D"))
    stem = "".join(c for c in stem if not unicodedata.combining(c))
    stem = re.sub(r"[^0-9A-Za-z]+", "_", stem).strip("_")
    return stem.lower() or "doc"


class _Base(BaseModel):
    """Lớp cha của mọi model ở đây: ghi JSON gọn.

    Cách làm: bỏ trường rỗng (trừ `_KEEP_EMPTY`), bỏ trường tính lại được (`_DERIVED`), xếp khoá theo `_ORDER`.
    """

    _KEEP_EMPTY: ClassVar[set[str]] = set()
    _DERIVED: ClassVar[set[str]] = set()        # tính lại lúc nạp -> không ghi ra file
    _ORDER: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _compact(self, handler: Any) -> dict[str, Any]:
        """Lọc và sắp xếp các khoá trước khi ghi JSON (theo luật của `_Base`)."""
        data = handler(self)
        data = {k: v for k, v in data.items()
                if k not in self._DERIVED and (k in self._KEEP_EMPTY or v not in (None, [], {}))}
        rank = {k: i for i, k in enumerate(self._ORDER)}
        return dict(sorted(data.items(), key=lambda kv: rank.get(kv[0], len(rank))))


# --------------------------------------------------------------------------- Block

class Block(_Base):
    """Lớp cha của mọi khối trên trang: id, trang, nội dung, vị trí.

    Để làm gì: chữ, bảng, ảnh đều có chung 4 trường này để các bước sau xử lý như nhau.
    Cách làm: thứ tự đọc là thứ tự trong `page.blocks`; chương của khối thì hỏi `doc.section_of(block.page_no)`.
    """

    _KEEP_EMPTY: ClassVar[set[str]] = {"content"}
    _ORDER: ClassVar[tuple[str, ...]] = (
        "id", "page_no", "kind", "role", "content", "urls", "cells", "caption",
        "polygon", "why_empty",
    )

    id: str                     # "p010.b03" — ổn định, chunk và câu kịch bản trỏ vào
    page_no: int                # trang chứa block
    content: str | None = None
    polygon: list[Point]

    @field_validator("polygon")
    @classmethod
    def _check_polygon(cls, v: list[Point]) -> list[Point]:
        """Kiểm polygon đủ 4 góc, kẹp toạ độ vào [0, 1] và làm tròn 3 chữ số."""
        if len(v) != 4:
            raise ValueError(f"polygon phai co 4 goc, nhan {len(v)}")
        return [(round(min(max(x, 0.0), 1.0), 3), round(min(max(y, 0.0), 1.0), 3)) for x, y in v]

    @property
    def box(self) -> tuple[float, float, float, float]:
        """Hộp bao của khối -> (trái, trên, phải, dưới)."""
        xs = [x for x, _ in self.polygon]
        ys = [y for _, y in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def center(self) -> Point:
        """Tâm của khối -> (x, y)."""
        l, t, r, b = self.box
        return (l + r) / 2, (t + b) / 2

    @property
    def area(self) -> float:
        """Khối chiếm bao nhiêu phần diện tích trang (0 đến 1)."""
        l, t, r, b = self.box
        return max(0.0, r - l) * max(0.0, b - t)


def polygon_from_box(l: float, t: float, r: float, b: float) -> list[Point]:
    """Đổi hộp (trái, trên, phải, dưới) thành 4 góc theo chiều kim đồng hồ."""
    return [(l, t), (r, t), (r, b), (l, b)]


class ParsedParagraph(Block):
    """Khối chữ: tiêu đề, đoạn văn, danh sách gạch đầu dòng, danh sách link.

    Cách làm: `role` cho biết robot đối xử với nó thế nào —
        body      đoạn văn thường
        title     tiêu đề
        list      gạch đầu dòng (kịch bản diễn đạt lại, không đọc từng dòng)
        links     >= nửa số dòng là URL -> robot KHÔNG đọc địa chỉ (tự gán, chỉ đổi từ body / list)
        caption   chú thích
    """

    kind: Literal["paragraph"] = "paragraph"
    role: Literal["body", "title", "list", "links", "caption"] = "body"
    content: str
    urls: list[str] = Field(default_factory=list)   # chỉ có khi role="links"

    @model_validator(mode="after")
    def _links(self) -> ParsedParagraph:
        """Tự gán `role = "links"` khi >= nửa số dòng là URL, và tách các URL ra `urls`.

        Cách làm: chạy mỗi lần tạo hoặc nạp lại khối, nên không bao giờ lệch với chữ.
        """
        found = [ln for ln in self.lines if URL_LINE.fullmatch(ln)]
        if self.role in ("body", "list") and found and 2 * len(found) >= len(self.lines):
            self.role = "links"
        self.urls = found if self.role == "links" else []
        return self

    @property
    def lines(self) -> list[str]:
        """Các dòng chữ của khối (bỏ dòng trống)."""
        return [ln.strip() for ln in self.content.split("\n") if ln.strip()]


def table_markdown(cells: list[list[str]]) -> str | None:
    """Đổi lưới ô của bảng thành markdown `| a | b |`; bảng rỗng thì None."""
    if not cells:
        return None

    def row(r: list[str]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in r) + " |"

    return "\n".join([row(cells[0]), "|" + "---|" * len(cells[0])] + [row(r) for r in cells[1:]])


def fill_down(cells: list[list[str]]) -> list[list[str]]:
    """Chép tên ô gộp dọc xuống các hàng trống bên dưới.

    Để làm gì: cắt chunk theo hàng thì hàng nào cũng biết nó thuộc nhóm nào.
    Cách làm: chỉ xét cột đầu, bỏ qua hàng tiêu đề; ô đầu trống mà hàng còn chữ -> lấy ô hàng trên.
    """
    out = [list(r) for r in cells]
    for i in range(2, len(out)):
        if out[i] and not out[i][0].strip() and any(c.strip() for c in out[i][1:]) and out[i - 1]:
            out[i][0] = out[i - 1][0]
    return out


class ParsedTable(Block):
    """Khối bảng: `cells` là lưới ô (hàng đầu là tiêu đề), `content` là markdown sinh từ `cells`.

    Cách làm: `content` tính lại mỗi lần nạp và không ghi ra file; sửa `cells` thì phải dựng lại object.
    """

    _DERIVED: ClassVar[set[str]] = {"content"}

    kind: Literal["table"] = "table"
    cells: list[list[str]] = Field(default_factory=list)
    caption: str | None = None

    @model_validator(mode="after")
    def _markdown(self) -> ParsedTable:
        """Chép ô gộp xuống (`fill_down`) rồi sinh `content` dạng markdown từ `cells`."""
        self.cells = fill_down(self.cells)
        self.content = table_markdown(self.cells)
        return self


class ParsedImage(Block):
    """Khối ảnh: `content` = mô tả ảnh do VLM viết.

    Cách làm: ảnh không có mô tả thì `why_empty` nói vì sao —
        decorative             ảnh trang trí, không cần mô tả
        area_below_threshold   ảnh quá nhỏ, không gọi VLM
        not_described          ảnh đủ to mà không có mô tả -> cần người xem
        api_error              gọi VLM bị lỗi -> chạy lại
    `picture_type` + `reason` là kết quả VLM phân loại ảnh ở bước ②.
    """

    kind: Literal["image"] = "image"
    why_empty: Literal["decorative", "area_below_threshold", "not_described", "api_error"] | None = None
    # Bước ② (parser/pictures.py, src/prompts/s0_vlm_picture_prompt.md): VLM xét ảnh trên ảnh CẢ trang
    # -> loại ảnh + lý do ngắn, giữ lại để người duyệt (S7) soi phân loại.
    picture_type: Literal["TRANG_TRI", "SO_DO", "ANH_CO_CHU", "MINH_HOA"] | None = None
    reason: str | None = None

    @property
    def needs_review(self) -> bool:
        """Ảnh có cần người xem không (not_described hoặc api_error)."""
        return self.why_empty in ("not_described", "api_error")


AnyBlock = Annotated[
    ParsedParagraph | ParsedTable | ParsedImage,
    Field(discriminator="kind"),
]


# ------------------------------------------------------------------ tầng trang


class ParsedPage(_Base):
    """Một trang: số trang, tiêu đề, chương, hash nội dung và danh sách khối theo thứ tự đọc."""
    _ORDER: ClassVar[tuple[str, ...]] = (
        "page_no", "title", "section_id", "page_hash", "blocks",
    )

    page_no: int
    title: str | None = None
    section_id: str | None = None         # trỏ lên ParsedDocument.sections
    page_hash: str = ""                   # đổi nội dung / vị trí -> đổi hash -> S4 viết lại (§8)
    blocks: list[AnyBlock] = Field(default_factory=list)

    @property
    def images(self) -> list[ParsedImage]:
        """Các khối ảnh của trang."""
        return [b for b in self.blocks if isinstance(b, ParsedImage)]

    @property
    def tables(self) -> list[ParsedTable]:
        """Các khối bảng của trang."""
        return [b for b in self.blocks if isinstance(b, ParsedTable)]

    @property
    def paragraphs(self) -> list[ParsedParagraph]:
        """Các khối chữ của trang."""
        return [b for b in self.blocks if isinstance(b, ParsedParagraph)]

    @property
    def slide_type(self) -> Literal["section_divider", "exercise", "content"]:
        """Loại trang, tính bằng luật (không gọi model, không ghi ra file).

        Cách làm:
            section_divider  đúng 1 khối chữ, là tiêu đề, nằm giữa trang, không có ảnh / bảng có nội dung
            exercise         tiêu đề bắt đầu bằng "Bài tập"
            content          còn lại
        """
        paras = self.paragraphs
        # Trang có ảnh được mô tả / bảng có chữ thì KHÔNG phải trang phân mục, dù chỉ 1 tiêu đề:
        # Onboarding p4 (sơ đồ tổ chức) — khối VLM đọc hoàn toàn bằng `read` không có toạ độ
        # thật, nhận khung cả trang -> tâm (0.5, 0.5) -> từng bị xếp nhầm là phân mục, lọc khỏi tìm.
        has_media = any(im.content for im in self.images) or any(t.content for t in self.tables)
        if len(paras) == 1 and paras[0].role == "title" and not has_media:
            cx, cy = paras[0].center
            if cy >= DIVIDER_MIN_CY and DIVIDER_CX_RANGE[0] <= cx <= DIVIDER_CX_RANGE[1]:
                return "section_divider"
        if self.title and EXERCISE_TITLE.match(self.title):
            return "exercise"
        return "content"

    @property
    def label(self) -> str:
        """Tên ngắn của trang: tiêu đề; không có thì dòng chữ đầu tiên (cắt 48 ký tự)."""
        if self.title:
            return self.title
        first = next((b.content for b in self.paragraphs if b.content), "") or \
            next((b.content for b in self.blocks if b.content), "")
        first = first.split("\n")[0].strip()
        return first[:LABEL_CHARS] + ("…" if len(first) > LABEL_CHARS else "") or "(không có chữ)"

    @property
    def is_text_starved(self) -> bool:
        """Trang có <= 1 khối chữ không (trang sống nhờ mô tả ảnh)."""
        return len(self.paragraphs) <= 1

    def compute_hash(self) -> str:
        """Hash nội dung + vị trí mọi khối của trang.

        Để làm gì: hash đổi thì các bước sau biết trang đã đổi (vd kịch bản phải viết lại trang đó).
        """
        h = hashlib.sha256()
        for b in self.blocks:
            h.update(f"{b.kind}|{b.polygon}|{b.content or ''}".encode("utf-8"))
        return h.hexdigest()[:16]

    def cache_hash(self) -> str:
        """Giống `compute_hash` nhưng dồn khoảng trắng trong chữ trước khi hash.

        Để làm gì: làm khoá cache cho bước ②b / ② — xuống dòng trong chữ đổi thì không phải gọi lại VLM.
        """
        h = hashlib.sha256()
        for b in self.blocks:
            c = b.content or ""
            if b.kind == "paragraph":
                c = " ".join(c.split())
            h.update(f"{b.kind}|{b.polygon}|{c}".encode("utf-8"))
        return h.hexdigest()[:16]


# ------------------------------------------------------------------ tầng tài liệu


class SectionSpan(_Base):
    """Một chương: id, tên, trang đầu – trang cuối, nguồn (dò từ mục lục) và độ tin cậy."""

    id: str
    title: str
    pages: tuple[int, int]                # [trang đầu, trang cuối]
    source: Literal["outline_page", "manual"]
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def start_page(self) -> int:
        """Trang đầu của chương."""
        return self.pages[0]

    @property
    def end_page(self) -> int:
        """Trang cuối của chương."""
        return self.pages[1]

    def contains(self, page_no: int) -> bool:
        """Trang `page_no` có thuộc chương này không."""
        return self.start_page <= page_no <= self.end_page


class Flag(_Base):
    """Một chỗ cần người xem: loại cờ, mức độ, trang, khối và mô tả ngắn."""

    kind: Literal[
        "empty_page",
        "image_not_described",
        "table_empty",
        "no_sections",
    ]
    severity: Literal["info", "warn", "error"] = "warn"
    page_no: int | None = None
    block_id: str | None = None
    detail: str = ""


class SourceInfo(_Base):
    """File gốc: đường dẫn + hash."""
    path: str
    sha256: str = ""


class ParserInfo(_Base):
    """Parse bằng gì: phiên bản docling, model VLM, ngưỡng diện tích ảnh."""

    docling_version: str = ""
    vlm_model: str | None = None
    picture_area_threshold: float = 0.05


class ParsedDocument(_Base):
    """Cả deck: doc_id, file gốc, thông tin parser, chương, cờ và các trang.

    Cách làm: `document.json` chỉ chứa các trang; chương và cờ do `parser.enrich` tính lúc nạp.
    """
    _ORDER: ClassVar[tuple[str, ...]] = ("doc_id", "source", "parser", "sections", "flags", "pages")

    doc_id: str
    source: SourceInfo
    parser: ParserInfo
    sections: list[SectionSpan] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    pages: list[ParsedPage] = Field(default_factory=list)

    # -------- truy vấn

    @property
    def n_pages(self) -> int:
        """Số trang."""
        return len(self.pages)

    @property
    def n_images(self) -> int:
        """Tổng số khối ảnh."""
        return sum(len(p.images) for p in self.pages)

    @property
    def n_described_images(self) -> int:
        """Số khối ảnh có mô tả."""
        return sum(1 for p in self.pages for im in p.images if im.content)

    def page(self, page_no: int) -> ParsedPage | None:
        """Lấy trang theo số trang; không có thì None."""
        return next((p for p in self.pages if p.page_no == page_no), None)

    def section_of(self, page_no: int) -> SectionSpan | None:
        """Chương chứa trang `page_no`; không thuộc chương nào thì None."""
        return next((s for s in self.sections if s.contains(page_no)), None)

    # -------- đọc / ghi

    @classmethod
    def load(cls, path: str | Path) -> ParsedDocument:
        """Nạp `document.json` thành ParsedDocument.

        Cách làm: file định dạng cũ, nạp không được -> báo một câu và bảo parse lại.
        """
        try:
            return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except ValidationError as e:
            raise SystemExit(
                f"{path}: khong nap duoc ({e.error_count()} loi) — co the la dinh dang cu.\n"
                "Dung lai (co docling_vlmpipeline.json thi khong ton API): python src/offline/parser/cli.py <file raw>"
            ) from None

    def to_json(self) -> str:
        """Ghi ra chuỗi JSON thụt lề 2, mảng ngắn gập về một dòng cho dễ đọc."""
        return _fmt(self.model_dump(mode="json")) + "\n"


def _fmt(v: Any, ind: int = 0) -> str:
    """Định dạng JSON đẹp: mảng ngắn (<= 100 ký tự, không chứa object) viết một dòng, còn lại xuống dòng."""
    pad = "  " * ind
    if isinstance(v, dict) and v:
        items = [f"{pad}  {json.dumps(k, ensure_ascii=False)}: {_fmt(x, ind + 1)}"
                 for k, x in v.items()]
        return "{\n" + ",\n".join(items) + f"\n{pad}}}"
    if isinstance(v, list) and v:
        flat = json.dumps(v, ensure_ascii=False, separators=(", ", ": "))
        if len(flat) <= 100 and not any(isinstance(x, dict) for x in v):
            return flat
        return "[\n" + ",\n".join(f"{pad}  {_fmt(x, ind + 1)}" for x in v) + f"\n{pad}]"
    return json.dumps(v, ensure_ascii=False)
