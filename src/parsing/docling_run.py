"""① Đọc file gốc (.pdf / .pptx) bằng docling -> `out/parsed/<doc_id>/docling.json`.

Chỉ lấy CHỮ + TOẠ ĐỘ + VÙNG ảnh/bảng. KHÔNG gọi VLM ở đây: ảnh được hiểu ở bước ②
(`layout.py`), khi VLM nhìn CẢ trang. Ảnh cắt rời khỏi trang thì VLM không phân biệt được
ảnh trang trí với ảnh có nội dung (Onboarding Kit: ảnh Tết tả thành "mâm cỗ Trung Thu",
ảnh người mẫu, icon… đều được tả dài).

PDF: pipeline mặc định của docling, TẮT OCR. PDF xuất từ PowerPoint đã có text layer; đo trên
7_XLA7 trang 11-16: bật OCR 73.1s / tắt 8.7s, markdown GIỐNG HỆT. OCR còn phá dấu tiếng Việt.

PPTX: docling đọc thẳng XML (SimplePipeline) — không layout model, không vẽ slide ra ảnh.
Đo trên Gen_gap.pptx: hình VECTOR bị bỏ qua hết, không có nhãn title/page_header, mỗi slide
là một group `chapter` (from_docling phải đi vào group).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

# Phải đặt TRƯỚC khi docling import huggingface_hub (layout / table model vẫn tải về máy).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

log = logging.getLogger(__name__)

_NOISY = (
    "httpx", "urllib3", "huggingface_hub",
    "docling.models.utils.hf_model_download",
    "docling.pipeline.base_pipeline",
    "docling.document_converter",
    "docling.models.factories",
    "docling.models.factories.base_factory",
    "docling.utils.accelerator_utils",
    "docling.models.inference_engines.object_detection.transformers_engine",
)


def run_docling(src: Path, out_json: Path) -> None:
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    kind = src.suffix.lower()
    if kind == ".pdf":
        opts = PdfPipelineOptions()
        opts.do_ocr = False
        conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    elif kind == ".pptx":
        conv = DocumentConverter()
    else:
        raise SystemExit(f"chi nhan .pdf / .pptx, nhan {src.name}")

    log.info("① docling: %s ...", src.name)
    t0 = time.perf_counter()
    doc = conv.convert(str(src)).document
    dt = time.perf_counter() - t0

    out_json.parent.mkdir(parents=True, exist_ok=True)
    doc.save_as_json(out_json)
    n = max(len(doc.pages), 1)
    log.info("  %d trang, %.0fs (%.1fs/trang) | %d manh chu, %d anh, %d bang -> %s",
             n, dt, dt / n, len(doc.texts), len(doc.pictures), len(doc.tables), out_json)
