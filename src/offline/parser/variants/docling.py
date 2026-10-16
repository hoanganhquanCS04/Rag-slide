"""Bước ① qua docling VlmPipeline: mỗi trang một request VLM đọc ảnh cả trang.

Để làm gì: lấy bố cục trang — các khối chữ / bảng / ảnh, vị trí và chữ của từng khối.
Cách làm: prompt ở `src/prompts/s0_vlm_layout_prompt.md`; phản hồi được sửa cho đúng dạng dots_ocr
rồi docling dựng DoclingDocument; phản hồi thô từng trang lưu ở `logs/vlm_pipeline/<doc_id>/`.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")            # phải đặt TRƯỚC khi import docling
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from docling.datamodel.base_models import InputFormat  # noqa: E402
from docling.datamodel.document import ConversionResult  # noqa: E402
from docling.datamodel.pipeline_options import VlmConvertOptions, VlmPipelineOptions  # noqa: E402
from docling.datamodel.settings import settings  # noqa: E402
from docling.datamodel.vlm_engine_options import ApiVlmEngineOptions, VlmEngineType  # noqa: E402
from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: E402
from docling.pipeline.vlm_pipeline import VlmPipeline  # noqa: E402
from docling.utils.dots_utils import parse_dots_json  # noqa: E402
from docling_core.types.doc import (  # noqa: E402
    DocItemLabel,
    DoclingDocument,
    ImageRefMode,
    PictureItem,
    TableItem,
    TextItem,
)
from docling_core.types.doc.base import Size  # noqa: E402

from offline.parser.base import BaseParser  # noqa: E402
from offline.parser.vlm import load_json_array, vlm_endpoint  # noqa: E402
from utils.config import PROMPTS_DIR, ROOT  # noqa: E402
from models.parsed_document import (  # noqa: E402
    AnyBlock,
    ParsedDocument,
    ParsedImage,
    ParsedPage,
    ParsedParagraph,
    ParsedTable,
    ParserInfo,
    SourceInfo,
    norm_text,
    polygon_from_box,
    slugify_doc_id,
)

log = logging.getLogger(__name__)

LAYOUT_PROMPT = PROMPTS_DIR / "s0_vlm_layout_prompt.md"
RAW_DIR = ROOT / "logs" / "vlm_pipeline"

_NOISY = (
    "httpx", "urllib3", "huggingface_hub",
    "docling.models.utils.hf_model_download",
    "docling.pipeline.base_pipeline",
    "docling.document_converter",
    "docling.models.factories",
    "docling.models.factories.base_factory",
    "docling.utils.accelerator_utils",
)

# ------------------------------------------------------------------ phản hồi VLM -> dạng dots_ocr

# loại khối của prompt -> loại parse_dots_json nhận; loại lạ thì nó bỏ IM LẶNG
_CATEGORY = {
    "title": "Title",
    "section-header": "Section-header",
    "paragraph": "Text",
    "table": "Table",
    "image": "Picture",
    "page_header": "Page-header",
    "page_footer": "Page-footer",
}
_PLAIN = {"Title", "Section-header", "Text", "Page-header", "Page-footer"}

_NESTED_BBOX = re.compile(r'"bbox"\s*:\s*\[\s*\[([^\[\]]*)\]\s*\]')
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
# ký hiệu Markdown đầu dòng ("# ", "- ") + cặp sao bọc chữ; "#Liệt kê" (code) và dấu * lẻ giữ nguyên
_MD_LEAD = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|[-*+•][ \t]+)", re.MULTILINE)
_MD_STARS = re.compile(r"\*{1,3}(\S(?:[^*\n]*?\S)?)\*{1,3}")


def _plain(text: str) -> str:
    """Bỏ ký hiệu Markdown (#, gạch đầu dòng, **đậm**) mà VLM tự thêm vào chữ."""
    return _MD_STARS.sub(r"\1", _MD_LEAD.sub("", text))


def normalize_elements(content: str) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """Sửa phản hồi VLM của một trang cho đúng dạng dots_ocr -> (danh sách phần tử, ghi chú).

    Cách làm: đọc mảng JSON; bbox nhiều khung / 2 điểm -> gộp thành 1 hộp bao; đổi tên loại khối về
    dạng docling hiểu; bỏ Markdown trong chữ; bỏ phần tử lặp; JSON hỏng -> None (mất cả trang).
    """
    data = load_json_array(_NESTED_BBOX.sub(r'"bbox": [\1]', content))
    if data is None:
        return None, ["JSON hong -> mat ca trang"]
    out: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    boxed = dropped = dup = 0
    for el in data:
        if not isinstance(el, dict):
            continue
        nums = [float(v) for v in _NUM.findall(json.dumps(el.get("bbox")))]
        if len(nums) < 4 or len(nums) % 2:
            dropped += 1
            continue
        boxed += len(nums) > 4                    # nhiều khung / 2 điểm -> hộp bao (từng mất trang bìa)
        xs, ys = nums[0::2], nums[1::2]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        raw = str(el.get("category", ""))
        category = _CATEGORY.get(raw.lower(), raw)
        text = next((el[k] for k in ("paragraph", "text", "content") if el.get(k) is not None), "")
        text = "\n".join(map(str, text)) if isinstance(text, list) else str(text)
        if category in _PLAIN:
            text = _plain(text)
        sig = (category, tuple(round(v) for v in bbox), text)
        if sig in seen:                           # VLM lặp vòng: một khung sinh lại hàng chục lần
            dup += 1
            continue
        seen.add(sig)
        out.append({"category": category, "bbox": bbox, "text": text})
    notes = [f"{boxed} khung nhieu diem -> hop bao"] if boxed else []
    notes += [f"bo {dropped} khoi khong co bbox"] if dropped else []
    notes += [f"bo {dup} phan tu lap"] if dup else []
    return out, notes


class GemmaVlmPipeline(VlmPipeline):
    """VlmPipeline của docling, sửa lại để đọc được phản hồi của Gemma (toạ độ thang 0–1000)."""

    def _dots_page_document(self, conv_res, page, predicted_text, page_image) -> DoclingDocument:
        """Dựng DoclingDocument cho một trang từ phản hồi VLM đã sửa (`normalize_elements`)."""
        assert page.size is not None
        elements, notes = normalize_elements(predicted_text)
        if notes:
            log.warning("  vlm p%d: %s", page.page_no, "; ".join(notes))
        return parse_dots_json(
            content=json.dumps(elements or [], ensure_ascii=False),
            original_page_size=page.size,
            page_no=page.page_no,
            filename=conv_res.input.file.name or "file",
            page_image=page_image,
            model_image_size=Size(width=1000, height=1000),   # docling tưởng pixel Qwen2-VL
        )


# ------------------------------------------------------------------ parser

_ROLE = {
    DocItemLabel.TITLE: "title",
    DocItemLabel.SECTION_HEADER: "title",
    DocItemLabel.CAPTION: "caption",
    DocItemLabel.LIST_ITEM: "list",
}
_FURNITURE = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}


class DoclingParser(BaseParser):
    """Parser dùng docling VlmPipeline + VLM đọc ảnh cả trang."""

    kind = "docling"
    scale = 4.0                    # ảnh trang = 4 × 72dpi
    max_tokens = 4096
    timeout = 300.0                # trang VLM lặp từng tới 110s
    concurrency = 8
    picture_area_threshold = 0.05

    @property
    def raw_json(self) -> Path:
        """Đường dẫn file cache của bước ① (`docling_vlmpipeline.json`)."""
        return self.out_dir / "docling_vlmpipeline.json"

    def parse_layout(self, src: Path, pdf: Path, *, redo: bool = False) -> ParsedDocument:
        """Bước ①: có cache thì nạp lại, không có (hoặc `redo`) thì gọi VLM -> ParsedDocument."""
        if redo or not self.raw_json.exists():
            ddoc = self._convert(pdf)
        else:
            log.info("① %s co san (--redo de goi VLM lai)", self.raw_json.name)
            ddoc = DoclingDocument.load_from_json(self.raw_json)
        return self._to_parsed(ddoc, src)

    def _convert(self, pdf: Path) -> DoclingDocument:
        """Gọi VLM cho cả file -> DoclingDocument; ghi cache + phản hồi thô từng trang.

        Cách làm: bỏ ảnh trang khỏi cache (vẽ lại từ PDF khi cần) để file không phình.
        """
        for name in _NOISY:
            logging.getLogger(name).setLevel(logging.WARNING)
        if not self.model:
            raise SystemExit("thieu VLM_MODEL (.env)")
        log.info("① docling VlmPipeline: %s, %s doc ca trang ...", pdf.name, self.model)
        t0 = time.perf_counter()
        conv = self._converter()
        res = conv.convert(str(pdf), raises_on_error=False)
        del conv                   # giải phóng NGAY: để tới lúc thoát thì __del__ gọi logger đã bị dọn
        dt = time.perf_counter() - t0
        for e in res.errors:       # vd trang VLM lặp tới hết max_tokens
            log.warning("  docling p%s: %s", e.page_no, e.error_message)
        raw_dir = self._save_raw(res)

        ddoc = res.document
        for page in ddoc.pages.values():
            page.image = None      # ảnh cả trang base64 -> 10 MB / 17 trang; vẽ lại từ PDF khi cần
        self.out_dir.mkdir(parents=True, exist_ok=True)
        ddoc.save_as_json(self.raw_json, image_mode=ImageRefMode.PLACEHOLDER)
        n = max(len(ddoc.pages), 1)
        log.info("  %d trang, %.0fs (%.1fs/trang) | %d manh chu, %d anh, %d bang | raw -> %s",
                 n, dt, dt / n, len(ddoc.texts), len(ddoc.pictures), len(ddoc.tables), raw_dir)
        return ddoc

    def _converter(self) -> DocumentConverter:
        """Dựng DocumentConverter của docling: server VLM, prompt, độ phân giải, số trang chạy song song."""
        base, key, soft = vlm_endpoint()
        # `params` ghi đè HẲN tham số API của preset (model, max_tokens)
        params: dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens, "temperature": 0}
        if soft:
            params["mm_processor_kwargs"] = {"max_soft_tokens": soft}
        engine = ApiVlmEngineOptions(
            engine_type=VlmEngineType.API,  # KHÔNG phải runtime_type: field lạ bị bỏ im lặng, URL rơi về Ollama
            url=f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"} if key else {},
            params=params, timeout=self.timeout, concurrency=self.concurrency,
        )
        vlm = VlmConvertOptions.from_preset("dots_ocr", engine_options=engine, scale=self.scale)
        vlm.model_spec = vlm.model_spec.model_copy(update={
            "prompt": LAYOUT_PROMPT.read_text(encoding="utf-8"),
            "max_new_tokens": self.max_tokens,
        })
        opts = VlmPipelineOptions(vlm_options=vlm, enable_remote_services=True)  # thiếu -> OperationNotAllowed
        settings.perf.page_batch_size = self.concurrency   # mặc định 4 trang/lô -> nghẽn concurrency
        return DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_cls=GemmaVlmPipeline, pipeline_options=opts)})

    def _save_raw(self, res: ConversionResult) -> Path:
        """Ghi phản hồi thô của VLM từng trang ra `logs/vlm_pipeline/<doc_id>/pNNN.txt` (xoá bản cũ)."""
        d = RAW_DIR / self.out_dir.name
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("p*.txt"):
            old.unlink()
        for p in res.pages:
            r = p.predictions.vlm_response
            (d / f"p{p.page_no:03d}.txt").write_text(r.text if r else "", encoding="utf-8")
        return d

    def _to_parsed(self, ddoc: DoclingDocument, src: Path) -> ParsedDocument:
        """Đổi DoclingDocument thành ParsedDocument.

        Cách làm: đi theo thứ tự đọc của docling; bỏ header / footer; đổi toạ độ về [0,1] gốc trên-trái;
        đặt id `pNNN.bNN`; ảnh -> ParsedImage, bảng -> ParsedTable, chữ -> ParsedParagraph; cuối cùng gán
        tiêu đề trang (khối title đầu tiên) và page_hash.
        """
        pages = {no: ParsedPage(page_no=no) for no in sorted(ddoc.pages)}
        for item, _ in ddoc.iterate_items():
            if not isinstance(item, (TextItem, TableItem, PictureItem)) or not item.prov:
                continue
            if item.label in _FURNITURE:
                continue
            prov = item.prov[0]
            page = pages[prov.page_no]
            size = ddoc.pages[prov.page_no].size
            l, t, r, b = prov.bbox.to_top_left_origin(size.height).as_tuple()
            polygon = polygon_from_box(l / size.width, t / size.height, r / size.width, b / size.height)
            bid = f"p{page.page_no:03d}.b{len(page.blocks):02d}"
            block: AnyBlock
            if isinstance(item, PictureItem):
                block = ParsedImage(id=bid, page_no=page.page_no, polygon=polygon)
                block.why_empty = ("area_below_threshold" if block.area < self.picture_area_threshold
                                   else "not_described")
            elif isinstance(item, TableItem):
                block = ParsedTable(id=bid, page_no=page.page_no, polygon=polygon,
                                    cells=[[norm_text(c.text) for c in row] for row in item.data.grid])
            else:
                # chuẩn hoá TỪNG DÒNG, giữ `\n`: dồn cả khối làm mục lục p2 thành 1 dòng -> sections.py
                # bỏ qua p2, vớ mục lục con p3 -> một chương phủ p12–p51
                if not (text := "\n".join(ln for raw in item.text.splitlines() if (ln := norm_text(raw)))):
                    continue
                block = ParsedParagraph(id=bid, page_no=page.page_no, polygon=polygon, content=text,
                                        role=_ROLE.get(item.label, "body"))  # type: ignore[arg-type]
            page.blocks.append(block)

        for page in pages.values():
            page.title = next((b.content for b in page.paragraphs if b.role == "title"), None)
            page.page_hash = page.compute_hash()
        doc = ParsedDocument(
            doc_id=slugify_doc_id(src.stem),
            source=SourceInfo(path=str(src), sha256=str(ddoc.origin.binary_hash if ddoc.origin else "")),
            parser=ParserInfo(docling_version=importlib.metadata.version("docling"),
                              vlm_model=self.model, picture_area_threshold=self.picture_area_threshold),
            pages=list(pages.values()),
        )
        log.info("① %d trang, %d khoi, %d anh, %d bang", doc.n_pages,
                 sum(len(p.blocks) for p in doc.pages), doc.n_images, sum(len(p.tables) for p in doc.pages))
        return doc
