import fitz  # PyMuPDF
from ocr_service.engines.interface import TextLayerExtractor, UnreadableDocumentError
from ocr_service.engines.native_text_layer import NativeTextLayerEngine
from ocr_service.engines.tesseract_ocr import TesseractEngine, is_raster_image
from ocr_service.settings import Settings

_settings = Settings()

_ENGINES: list[TextLayerExtractor] = [NativeTextLayerEngine(), TesseractEngine()]

# Cost/performance safety valve (3.9, P5b-S5): "estimated from page/image
# size" instead of exact analysis - a real word count would itself already
# preempt the expensive OCR work that the upper limit is meant to avoid in
# the first place. A rough but documented assumption for a typical business
# document page (the concrete estimation method is explicitly an
# implementation detail per the concept, not a concept decision).
ASSUMED_WORDS_PER_PAGE = 250


def estimate_word_count(data: bytes, *, content_type: str | None, filename: str) -> int:
    """Cheap upfront estimate without running the engine, to check against
    `OcrConfig.max_word_count`. Raster images always count as one page (no
    page-count concept); PDFs use the `page_count` that is available for
    free anyway - no rendering/rasterizing needed to determine it."""
    if is_raster_image(content_type=content_type, filename=filename):
        return ASSUMED_WORDS_PER_PAGE
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        # Not readable - select_engine() raises UnreadableDocumentError in this case anyway.
        return 0
    return doc.page_count * ASSUMED_WORDS_PER_PAGE


def _is_pdf(*, content_type: str | None, filename: str) -> bool:
    if content_type == "application/pdf":
        return True
    return filename.lower().endswith(".pdf")


def _native_text_available(data: bytes) -> bool:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        char_count = len(doc[0].get_text("text").strip())
    except Exception as exc:
        raise UnreadableDocumentError(f"PDF nicht lesbar: {exc}") from exc
    return char_count >= _settings.min_native_text_chars


def select_engine(
    *, content_type: str | None, filename: str, data: bytes
) -> TextLayerExtractor | None:
    """Unlike `select_renderers()` (returns a list of independently
    applicable rules), this returns exactly one engine or `None` - OCR
    produces one authoritative result per version, not several. `.docx`/
    `.pptx`/video/etc. get no engine (not a raster image, no OCR need -
    their text extraction is already handled by P5-S2's
    `DocxTextExtractionRenderer`/`PptxTextExtractionRenderer`)."""
    native_text_available: bool | None = None
    if _is_pdf(content_type=content_type, filename=filename):
        native_text_available = _native_text_available(data)

    for engine in _ENGINES:
        if engine.supports(
            content_type=content_type,
            filename=filename,
            native_text_available=native_text_available,
        ):
            return engine
    return None


__all__ = [
    "NativeTextLayerEngine",
    "TesseractEngine",
    "UnreadableDocumentError",
    "estimate_word_count",
    "select_engine",
]
