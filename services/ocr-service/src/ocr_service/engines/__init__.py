import fitz  # PyMuPDF
from ocr_service.engines.interface import TextLayerExtractor, UnreadableDocumentError
from ocr_service.engines.native_text_layer import NativeTextLayerEngine
from ocr_service.engines.tesseract_ocr import TesseractEngine, is_raster_image
from ocr_service.settings import Settings

_settings = Settings()

_ENGINES: list[TextLayerExtractor] = [NativeTextLayerEngine(), TesseractEngine()]

# Kosten-/Performance-Schutzventil (3.9, P5b-S5): "geschätzt anhand
# Seiten-/Bildgröße" statt exakter Analyse - eine echte Wortzählung würde die
# teure OCR-Arbeit, die die Obergrenze gerade vermeiden soll, selbst schon
# vorwegnehmen. Grobe, aber dokumentierte Annahme für eine typische
# Geschäftsdokument-Seite (konkrete Schätzmethode ist laut Konzept
# ausdrücklich Implementierungsdetail, keine Konzeptentscheidung).
ASSUMED_WORDS_PER_PAGE = 250


def estimate_word_count(data: bytes, *, content_type: str | None, filename: str) -> int:
    """Günstige Vorab-Schätzung ohne die Engine laufen zu lassen, um gegen
    `OcrConfig.max_word_count` zu prüfen. Rasterbilder gelten immer als eine
    Seite (kein Seitenzahl-Konzept); PDFs nutzen die ohnehin kostenlos
    verfügbare `page_count` - kein Rendern/Rastern nötig, um sie zu ermitteln."""
    if is_raster_image(content_type=content_type, filename=filename):
        return ASSUMED_WORDS_PER_PAGE
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        # Nicht lesbar - select_engine() wirft in diesem Fall ohnehin UnreadableDocumentError.
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


__all__ = [
    "NativeTextLayerEngine",
    "TesseractEngine",
    "UnreadableDocumentError",
    "estimate_word_count",
    "select_engine",
]
