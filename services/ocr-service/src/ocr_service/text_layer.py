import logging

import fitz  # PyMuPDF
from ocr_service.engines.interface import OcrPageResult

logger = logging.getLogger(__name__)

# PDF-Standard-Textrendermodus 3 = unsichtbar (Tr 3 im Content-Stream) - exakt
# das Prinzip, mit dem auch ocrmypdf seinen OCR-Textlayer einbettet: der Text
# ist durchsuch-/markierbar, aber nie sichtbar gezeichnet.
_INVISIBLE_RENDER_MODE = 3


def embed_text_layer(pdf_bytes: bytes, pages: list[OcrPageResult], raster_dpi: int) -> bytes:
    """Fügt `pages` (Tesseract-Wortboxen, in Pixel-Koordinaten bei `raster_dpi`
    rasterisiert, siehe engines/tesseract_ocr.py) als unsichtbaren Textlayer
    in eine Kopie von `pdf_bytes` ein - macht aus einem reinen Scan eine
    durchsuchbare/markierbare PDF, ohne das sichtbare Erscheinungsbild zu
    verändern (Nutzer-Feedback: "PDF ohne Textlayer wird PDF mit Textlayer").

    Pixel→Punkt-Umrechnung: die Rasterung erfolgte bei `raster_dpi` DPI,
    PDF-Koordinaten sind in Punkten (72 pro Zoll) - derselbe Ursprung
    (oben links, y wächst nach unten) in Pixmap und PDF-Seite, keine
    Achsenspiegelung nötig."""
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
                    # Einzelnes Wort (z. B. nicht darstellbares Zeichen) darf
                    # den gesamten Textlayer nicht zum Scheitern bringen.
                    logger.warning("Konnte Wort %r nicht in Textlayer einbetten", text)
        return doc.tobytes()
    finally:
        doc.close()
