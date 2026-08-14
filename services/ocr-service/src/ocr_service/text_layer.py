import logging

import fitz  # PyMuPDF
from ocr_service.engines.interface import OcrPageResult

logger = logging.getLogger(__name__)

# PDF standard text render mode 3 = invisible (Tr 3 in the content stream) -
# exactly the principle ocrmypdf also uses to embed its OCR text layer: the
# text is searchable/selectable but never drawn visibly.
_INVISIBLE_RENDER_MODE = 3


def embed_text_layer(pdf_bytes: bytes, pages: list[OcrPageResult], raster_dpi: int) -> bytes:
    """Embeds `pages` (Tesseract word boxes, rasterized to pixel coordinates
    at `raster_dpi`, see engines/tesseract_ocr.py) as an invisible text layer
    into a copy of `pdf_bytes` - turns a plain scan into a searchable/
    selectable PDF without changing the visible appearance (user feedback:
    "PDF without a text layer becomes a PDF with a text layer").

    Pixel-to-point conversion: rasterization happened at `raster_dpi` DPI,
    PDF coordinates are in points (72 per inch) - same origin (top left, y
    grows downward) in the pixmap and the PDF page, no axis mirroring
    needed."""
    scale = 72.0 / raster_dpi
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page_result in pages:
            page_index = page_result.page_number - 1
            if page_index < 0 or page_index >= len(doc):
                continue
            page = doc[page_index]
            for word in page_result.words:
                text = word.text.strip()
                if not text:
                    continue
                x = word.left * scale
                y_baseline = (word.top + word.height) * scale
                fontsize = max(word.height * scale, 1.0)
                try:
                    page.insert_text(
                        (x, y_baseline),
                        text,
                        fontsize=fontsize,
                        render_mode=_INVISIBLE_RENDER_MODE,
                    )
                except Exception:
                    # A single word (e.g. an unrenderable character) must not
                    # fail the entire text layer.
                    logger.warning("Konnte Wort %r nicht in Textlayer einbetten", text)
        return doc.tobytes()
    finally:
        doc.close()
