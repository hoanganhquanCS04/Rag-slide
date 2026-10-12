"""Schema của ParsedDocument — MỘT định dạng, vừa để người đọc vừa để pipeline chạy.

    ParsedDocument --*--> ParsedPage --*--> Block
                                              |-- ParsedParagraph   (chữ)
                                              |-- ParsedTable       (bảng)
                                              `-- ParsedImage       (ảnh)

Mỗi block trả lời hai câu, loại nào cũng vậy:

    content      nói gì           chữ · mô tả VLM · markdown bảng
    polygon      nằm đâu          4 góc, [0,1], gốc TRÊN-TRÁI

Mọi chữ do VLM đọc từ ảnh trang (CÓ THỂ SAI); khối người vá tay có id `pNNN.mNN` (parser/patch.py).

Ghi ra JSON: trường rỗng (None / [] / {}) KHÔNG ghi — trừ `content`, để nhìn là thấy
block nào không có nội dung. Nạp lại thì trường vắng lấy giá trị mặc định.

Back-reference (KHÔNG ghi JSON, gán lại mỗi lần nạp bằng `link()`): `block.page`,
`block.page_no`, `block.section`, `page.document`, `page.section`.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

from models.backref import BackRef

# Một dòng chỉ gồm một URL — "https://moso.vn/" · "www.abc.com/x"
URL_LINE = re.compile(r"(?:https?://|www\.)\S+")

# slide_type — luật, không gọi model. Đo trên 3_datavisualization, tách bạch không vùng xám:
#   phân mục  tiêu đề cx=0.50 cy=0.47 (7 trang)   ·   nội dung  cx=0.20 cy=0.11
DIVIDER_MIN_CY = 0.30
DIVIDER_CX_RANGE = (0.35, 0.65)
# CHỈ tiêu đề mở đầu bằng "Bài tập" — dò "yêu cầu" trong cả trang là bắt nhầm deck nhân sự
# (Onboarding p26 "Chấm công: Quản lý yêu cầu của CBNV")
EXERCISE_TITLE = re.compile(r"^\s*bài tập", re.I)

Point = tuple[float, float]
Box = tuple[float, float, float, float]   # (trái, trên, phải, dưới), [0,1], gốc trên-trái


def norm_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def slugify_doc_id(stem: str) -> str:
    """Tên file gốc -> doc_id sạch: "3_DataVisualization (1)" -> "3_datavisualization"."""
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)         # bỏ "(1)" của bản tải trùng
    # bỏ dấu TRƯỚC khi lọc ký tự: không thì "Thời gian" -> "th_i_gian"
    stem = unicodedata.normalize("NFD", stem.replace("đ", "d").replace("Đ", "D"))
    stem = "".join(c for c in stem if not unicodedata.combining(c))
    stem = re.sub(r"[^0-9A-Za-z]+", "_", stem).strip("_")
    return stem.lower() or "doc"


class _Base(BaseModel):
    """Ghi JSON gọn: bỏ trường rỗng, bỏ trường suy ra được (`_DERIVED`), xếp theo `_ORDER`."""

    _KEEP_EMPTY: ClassVar[set[str]] = set()
    _DERIVED: ClassVar[set[str]] = set()        # tính lại lúc nạp -> không ghi ra file
    _ORDER: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _compact(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        data = {k: v for k, v in data.items()
                if k not in self._DERIVED and (k in self._KEEP_EMPTY or v not in (None, [], {}))}
        rank = {k: i for i, k in enumerate(self._ORDER)}
        return dict(sorted(data.items(), key=lambda kv: rank.get(kv[0], len(rank))))


# --------------------------------------------------------------------------- Block


class Href(_Base):
    """Một link ẩn: `text` = dòng chữ VLM mà vùng link phủ gần hết ("Cẩm nang phân quyền"); rỗng =
    link chỉ phủ một cụm trong câu (dòng đó không tính là dòng link — links.py). `url` = đích.

    Một block list có thể mang nhiều link (p3 Onboarding: 7) — `text` mới chọn được đúng cái.
    """

    text: str
    url: str


class Block(_Base):
    """Lớp cha của mọi mẩu nội dung trên trang.

    Không LƯU `page_no` / `reading_order`: block nằm trong `page.blocks` là đã nói lên trang
    nào, thứ tự đọc là thứ tự trong mảng. Muốn biết trang / chương thì hỏi back-reference
    `.page` / `.page_no` / `.section` (gán bởi `ParsedPage.link()`, không ghi JSON).
    Header/footer lặp của docling bị bỏ ở parser — không phải nội dung, chương lấy từ mục lục.
    """

    _KEEP_EMPTY: ClassVar[set[str]] = {"content"}
    _ORDER: ClassVar[tuple[str, ...]] = (
        "id", "kind", "role", "content", "urls", "hrefs", "cells", "caption",
        "polygon", "why_empty",
    )

    id: str                     # "p010.b03" — ổn định, chunk và câu kịch bản trỏ vào
    content: str | None = None
    polygon: list[Point]
    # Link ẨN sau chữ (annotation PDF, parser/links.py) — khác `urls` là URL IN RA thành chữ.
    # Không vào hash / KB: robot không đọc URL, runtime chỉ hiện ra khi được hỏi.
    hrefs: list[Href] = Field(default_factory=list)

    _page_ref: BackRef = PrivateAttr(default_factory=BackRef)

    @field_validator("polygon")
    @classmethod
    def _check_polygon(cls, v: list[Point]) -> list[Point]:
        """4 góc theo chiều kim đồng hồ từ trên-trái, làm tròn 3 chữ số (~1/1000 trang)."""
        if len(v) != 4:
            raise ValueError(f"polygon phai co 4 goc, nhan {len(v)}")
        return [(round(min(max(x, 0.0), 1.0), 3), round(min(max(y, 0.0), 1.0), 3)) for x, y in v]

    @property
    def box(self) -> tuple[float, float, float, float]:
        """-> (trái, trên, phải, dưới)."""
        xs = [x for x, _ in self.polygon]
        ys = [y for _, y in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)

    @property
    def center(self) -> Point:
        l, t, r, b = self.box
        return (l + r) / 2, (t + b) / 2

    @property
    def area(self) -> float:
        """Chiếm bao nhiêu phần diện tích trang. Ngưỡng gọi VLM dựa vào đây."""
        l, t, r, b = self.box
        return max(0.0, r - l) * max(0.0, b - t)

    # -------- back-reference

    @property
    def page(self) -> ParsedPage | None:
        """Trang chứa block này."""
        return self._page_ref.target

    @property
    def page_no(self) -> int | None:
        return self.page.page_no if self.page else None

    @property
    def section(self) -> SectionSpan | None:
        """Chương chứa block này — đi qua trang để hỏi deck."""
        return self.page.section if self.page else None


def polygon_from_box(l: float, t: float, r: float, b: float) -> list[Point]:
    return [(l, t), (r, t), (r, b), (l, b)]


def _is_anchor(line: str, hrefs: list[Href]) -> bool:
    """Dòng này là chữ của một link ẩn? So sau khi bỏ khoảng trắng: docling chèn cách lung tung
    ("q u y chế"), chữ dưới vùng link lại lẫn mảnh chữ bên cạnh ("quy định, h, quy chế")."""
    a = re.sub(r"\s+", "", line).lower()
    for h in hrefs:
        b = re.sub(r"\s+", "", h.text).lower()
        if b and (b in a and 2 * len(b) >= len(a) or len(a) >= 4 and a in b
                  or difflib.SequenceMatcher(None, a, b).ratio() >= 0.8):
            return True
    return False


class ParsedParagraph(Block):
    """Mọi thứ là CHỮ: tiêu đề, đoạn văn, bó gạch đầu dòng, danh sách link.

    `kind` = mẩu này LÀ GÌ (vẫn là chữ). `role` = robot ĐỐI XỬ với nó thế nào:

        body      đoạn văn thường
        title     tiêu đề trang
        list      bó gạch đầu dòng, một block, các dòng ngăn bằng xuống dòng
                  -> S4 diễn đạt lại, không đọc bullet nguyên văn (§5 S4)
        links     >= nửa số dòng là LINK — URL in ra chữ, hoặc chữ có link ẩn (`hrefs`)
                  -> KHÔNG đọc URL thành tiếng. Luật, tự gán.
        caption   chú thích

    Chỉ body/list mới đổi sang `links`. Nửa số dòng chứ không phải "có link là đổi": p18/p19
    Onboarding là list quy định > 1000 chữ, link chỉ 1 dòng — đổi là robot bỏ qua nội dung.
    Title có link vẫn là title (tên trang lấy từ title).
    """

    kind: Literal["paragraph"] = "paragraph"
    role: Literal["body", "title", "list", "links", "caption"] = "body"
    content: str
    urls: list[str] = Field(default_factory=list)   # chỉ có khi role="links"

    @model_validator(mode="after")
    def _links(self) -> ParsedParagraph:
        """Suy `role="links"` + tách `urls` từ chữ. Chạy cả lúc nạp lại -> không lệch được."""
        self.refresh_role()
        return self

    def refresh_role(self) -> None:
        """Gọi lại sau khi gắn `hrefs` (parser/links.py) — validator chạy lúc tạo, trước khi có link."""
        found = [ln for ln in self.lines if URL_LINE.fullmatch(ln)]
        n_link = sum(1 for ln in self.lines if URL_LINE.fullmatch(ln) or _is_anchor(ln, self.hrefs))
        if self.role in ("body", "list") and n_link and 2 * n_link >= len(self.lines):
            self.role = "links"
        self.urls = found if self.role == "links" else []

    @property
    def lines(self) -> list[str]:
        return [ln.strip() for ln in self.content.split("\n") if ln.strip()]


def table_markdown(cells: list[list[str]]) -> str | None:
    if not cells:
        return None

    def row(r: list[str]) -> str:
        return "| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in r) + " |"

    return "\n".join([row(cells[0]), "|" + "---|" * len(cells[0])] + [row(r) for r in cells[1:]])


def fill_down(cells: list[list[str]]) -> list[list[str]]:
    """Ô đầu hàng trống mà hàng vẫn có chữ -> chép từ hàng trên xuống.

    Ô gộp dọc (Onboarding p22 "Các trường hợp lỗi công" phủ 4 hàng) chỉ ghi ở hàng đầu, 3 hàng
    dưới trống — cắt chunk theo hàng là mảnh sau không biết mình thuộc nhóm nào. Chỉ cột đầu, chỉ
    hàng thân (hàng 0 là header). Đo 2 deck: 5 hàng ở p21–23, đều đúng là ô gộp.
    """
    out = [list(r) for r in cells]
    for i in range(2, len(out)):
        if out[i] and not out[i][0].strip() and any(c.strip() for c in out[i][1:]) and out[i - 1]:
            out[i][0] = out[i - 1][0]
    return out


class ParsedTable(Block):
    """Bảng. `cells` là bản gốc, hàng đầu là header; `content` là markdown SINH từ `cells`.

    Cả chữ trong ô lẫn lưới hàng/cột đều do VLM đọc — lưới CÓ THỂ SAI (số đặt nhầm hàng).

    `content` tính lại mỗi lần NẠP và KHÔNG ghi ra file (trùng với `cells`). Sửa `cells` trong bộ
    nhớ thì phải dựng lại object. Nạp vào là chép tên ô gộp dọc xuống hàng trống (`fill_down`).
    """

    _DERIVED: ClassVar[set[str]] = {"content"}

    kind: Literal["table"] = "table"
    cells: list[list[str]] = Field(default_factory=list)
    caption: str | None = None

    @model_validator(mode="after")
    def _markdown(self) -> ParsedTable:
        self.cells = fill_down(self.cells)
        self.content = table_markdown(self.cells)
        return self


class ParsedImage(Block):
    """Ảnh. `content` = mô tả của VLM (hoặc người sửa trong data/patches/).

    `content: null` thì `why_empty` nói VÌ SAO — *chưa gọi* hay *gọi mà không có* xử lý
    khác hẳn nhau:

        decorative             logo, hoạ tiết — VLM xem rồi, không có nội dung. Hợp lệ.
        area_below_threshold   ảnh nhỏ quá, không gọi VLM. Hợp lệ.
        not_described          ảnh ĐỦ TO mà không có mô tả -> mất nội dung thật, CẦN XEM.
        api_error              gọi VLM mà lỗi -> chạy lại là có thể được, CẦN XEM.
    """

    kind: Literal["image"] = "image"
    why_empty: Literal["decorative", "area_below_threshold", "not_described", "api_error"] | None = None
    # Bước ② (parser/pictures.py, src/prompts/s0_vlm_picture_prompt.md): VLM xét ảnh trên ảnh CẢ trang
    # -> loại ảnh + lý do ngắn, giữ lại để người duyệt (S7) soi phân loại.
    picture_type: Literal["TRANG_TRI", "SO_DO", "ANH_CO_CHU", "MINH_HOA"] | None = None
    reason: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.why_empty in ("not_described", "api_error")


AnyBlock = Annotated[
    ParsedParagraph | ParsedTable | ParsedImage,
    Field(discriminator="kind"),
]


# ------------------------------------------------------------------ tầng trang


class ParsedPage(_Base):
    _ORDER: ClassVar[tuple[str, ...]] = (
        "page_no", "title", "section_id", "page_hash", "layout_error", "blocks",
    )

    page_no: int
    title: str | None = None
    section_id: str | None = None         # trỏ lên ParsedDocument.sections
    page_hash: str = ""                   # đổi nội dung / vị trí -> đổi hash -> S4 viết lại (§8)
    # VLM sắp bố cục trang mà trượt kiểm tra -> trang dùng block docling, ghi lý do ở đây
    layout_error: str | None = None
    blocks: list[AnyBlock] = Field(default_factory=list)

    _document_ref: BackRef = PrivateAttr(default_factory=BackRef)

    @model_validator(mode="after")
    def _link(self) -> ParsedPage:
        return self.link()

    def link(self) -> ParsedPage:
        """Gán `block.page` cho mọi block. Gọi lại sau khi thêm / thay block (vá tay, chép bảng)."""
        for b in self.blocks:
            b._page_ref = BackRef(self)
        return self

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> ParsedPage:
        return super().__deepcopy__(memo).link()

    @property
    def document(self) -> ParsedDocument | None:
        """Deck chứa trang này."""
        return self._document_ref.target

    @property
    def section(self) -> SectionSpan | None:
        """Chương chứa trang này — bản ĐÃ TRA của `section_id`, khỏi tự gọi `section_of()`."""
        return self.document.section_of(self.page_no) if self.document else None

    @property
    def images(self) -> list[ParsedImage]:
        return [b for b in self.blocks if isinstance(b, ParsedImage)]

    @property
    def tables(self) -> list[ParsedTable]:
        return [b for b in self.blocks if isinstance(b, ParsedTable)]

    @property
    def paragraphs(self) -> list[ParsedParagraph]:
        return [b for b in self.blocks if isinstance(b, ParsedParagraph)]

    @property
    def slide_type(self) -> Literal["section_divider", "exercise", "content"]:
        """Luật trên chính trang này — tính lại mỗi lần nạp, KHÔNG ghi ra JSON (deck mới gần như
        toàn `content`, ghi ra chỉ là nhiễu). Kịch bản, KB, cờ vẫn đọc qua property này.

        section_divider  đúng 1 mẩu chữ, là tiêu đề, nằm GIỮA trang -> S4 chỉ nói câu chuyển
        exercise         tiêu đề mở đầu bằng "Bài tập" -> robot ĐỌC yêu cầu, không giảng
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
    def is_text_starved(self) -> bool:
        """<=1 mẩu chữ -> trang sống chết nhờ mô tả ảnh."""
        return len(self.paragraphs) <= 1

    def compute_hash(self) -> str:
        """Hash nội dung + vị trí. Đổi chữ HOẶC đổi bố cục đều ra hash mới."""
        h = hashlib.sha256()
        for b in self.blocks:
            h.update(f"{b.kind}|{b.polygon}|{b.content or ''}".encode("utf-8"))
        return h.hexdigest()[:16]

    def cache_hash(self) -> str:
        """Khoá cache của ②b / ② — như `compute_hash` nhưng DỒN khoảng trắng trong chữ đoạn.

        Hai bước đó nhìn ẢNH trang, xuống dòng trong chữ không đổi kết quả. Parser từng dồn mọi
        `\\n` thành dấu cách; giữ lại xuống dòng mà dùng `compute_hash` thì mất sạch cache, gọi lại
        VLM cả deck. Chữ đã dồn sẵn -> trùng đúng `compute_hash` cũ.
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
    """Một chương. SUY RA chứ không parse ra — nên phải khai `source` + `confidence`."""

    id: str
    title: str
    pages: tuple[int, int]                # [trang đầu, trang cuối]
    source: Literal["outline_page", "manual"]
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def start_page(self) -> int:
        return self.pages[0]

    @property
    def end_page(self) -> int:
        return self.pages[1]

    def contains(self, page_no: int) -> bool:
        return self.start_page <= page_no <= self.end_page


class Flag(_Base):
    """Chỗ cần người xem. §10 cấm bắt duyệt cả deck — chỉ duyệt phần bị flag."""

    kind: Literal[
        "empty_page",
        "image_not_described",
        "table_empty",
        "layout_failed",
        "no_sections",
    ]
    severity: Literal["info", "warn", "error"] = "warn"
    page_no: int | None = None
    block_id: str | None = None
    detail: str = ""


class SourceInfo(_Base):
    path: str
    sha256: str = ""


class ParserInfo(_Base):
    """Parse bằng gì — để biết dữ liệu trong file đến từ đâu.

    `vlm_model` là model đọc trang (parser/variants/docling.py) — mọi khối `vlm` đến từ nó.
    """

    docling_version: str = ""
    vlm_model: str | None = None
    picture_area_threshold: float = 0.05


class ParsedDocument(_Base):
    _ORDER: ClassVar[tuple[str, ...]] = ("doc_id", "source", "parser", "sections", "flags", "pages")

    doc_id: str
    source: SourceInfo
    parser: ParserInfo
    sections: list[SectionSpan] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    pages: list[ParsedPage] = Field(default_factory=list)

    @model_validator(mode="after")
    def _link(self) -> ParsedDocument:
        return self.link()

    def link(self) -> ParsedDocument:
        """Gán back-reference cả cây: `page.document`, rồi `block.page`. Gọi lại sau khi sửa cây."""
        for p in self.pages:
            p._document_ref = BackRef(self)
            p.link()
        return self

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> ParsedDocument:
        return super().__deepcopy__(memo).link()

    # -------- truy vấn

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    @property
    def n_images(self) -> int:
        return sum(len(p.images) for p in self.pages)

    @property
    def n_described_images(self) -> int:
        return sum(1 for p in self.pages for im in p.images if im.content)

    def page(self, page_no: int) -> ParsedPage | None:
        return next((p for p in self.pages if p.page_no == page_no), None)

    def section_of(self, page_no: int) -> SectionSpan | None:
        return next((s for s in self.sections if s.contains(page_no)), None)

    # -------- đọc / ghi

    @classmethod
    def load(cls, path: str | Path) -> ParsedDocument:
        """Nạp file đã parse. File định dạng cũ -> báo MỘT câu, không đổ 100 lỗi validate."""
        try:
            return cls.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except ValidationError as e:
            raise SystemExit(
                f"{path}: khong nap duoc ({e.error_count()} loi) — co the la dinh dang cu.\n"
                "Dung lai (co docling_vlmpipeline.json thi khong ton API): python src/parser/cli.py <file raw>"
            ) from None

    def to_json(self) -> str:
        """indent=2, nhưng mảng ngắn (polygon, hàng bảng, footer) gập về một dòng."""
        return _fmt(self.model_dump(mode="json")) + "\n"


def _fmt(v: Any, ind: int = 0) -> str:
    """Mảng không chứa object và dài <= 100 ký tự -> một dòng; còn lại xuống dòng như indent=2."""
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
