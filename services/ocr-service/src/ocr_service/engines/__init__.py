import fitz  # PyMuPDF
from ocr_service.engines.interface import TextLayerExtractor, UnreadableDocumentError
from ocr_service.engines.native_text_layer import NativeTextLayerEngine
from ocr_service.engines.tesseract_ocr import TesseractEngine
from ocr_service.settings import Settings

_settings = Settings()

_ENGINES: list[TextLayerExtractor] = [NativeTextLayerEngine(), TesseractEngine()]


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
    """Anders als `select_renderers()` (liefert eine Liste unabhängig
    anwendbarer Regeln) liefert dies genau eine Engine oder `None` - OCR
    erzeugt ein autoritatives Ergebnis je Version, nicht mehrere. `.docx`/
    `.pptx`/Video/etc. bekommen keine Engine (kein Rasterbild, kein OCR-Bedarf
    - deren Textextraktion übernimmt bereits P5-S2s `DocxTextExtractionRenderer`/
    `PptxTextExtractionRenderer`)."""
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


__all__ = ["NativeTextLayerEngine", "TesseractEngine", "UnreadableDocumentError", "select_engine"]
