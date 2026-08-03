import fitz  # PyMuPDF
from ocr_service.engines.interface import (
    OcrExtractionResult,
    OcrPageResult,
    OcrWordResult,
    TextLayerExtractor,
)
from ocr_service.settings import Settings

_settings = Settings()


class NativeTextLayerEngine(TextLayerExtractor):
    """Nutzt den bereits vorhandenen PDF-Textlayer (3.9: "automatische
    Erkennung, ob OCR überhaupt nötig ist") statt teurer Bilderkennung -
    schnell, exakt, Konfidenz immer 100.0."""

    engine_name = "native_text_layer"

    def supports(
        self, *, content_type: str | None, filename: str, native_text_available: bool | None
    ) -> bool:
        return native_text_available is True

    async def extract(
        self, data: bytes, *, filename: str, content_type: str | None
    ) -> OcrExtractionResult:
        doc = fitz.open(stream=data, filetype="pdf")
        # get_text("words") liefert Koordinaten in PDF-Punkten (1/72"); der
        # Faktor skaliert sie in dasselbe Pixelraster wie das gleichzeitig per
        # get_pixmap(dpi=raster_dpi) erzeugte Seitenbild, damit Overlay und
        # Bild exakt übereinanderliegen.
        scale = _settings.raster_dpi / 72

        pages: list[OcrPageResult] = []
        page_images: list[bytes] = []
        full_text_parts: list[str] = []
        # Alle Seiten durchlaufen, nicht nur die erste (Bugfix: mehrseitige
        # PDF-Vorschau zeigte bislang immer nur Seite 1).
        for page in doc:
            pixmap = page.get_pixmap(dpi=_settings.raster_dpi)
            raw_words = page.get_text("words")  # (x0,y0,x1,y1,text,block_no,line_no,word_no)
            words = [
                OcrWordResult(
                    text=w[4],
                    left=w[0] * scale,
                    top=w[1] * scale,
                    width=(w[2] - w[0]) * scale,
                    height=(w[3] - w[1]) * scale,
                    confidence=100.0,
                )
                for w in raw_words
            ]
            pages.append(
                OcrPageResult(
                    page_number=page.number + 1,
                    width=pixmap.width,
                    height=pixmap.height,
                    words=words,
                )
            )
            page_images.append(pixmap.tobytes("png"))
            full_text_parts.extend(w[4] for w in raw_words)

        return OcrExtractionResult(
            engine=self.engine_name,
            average_confidence=100.0,
            full_text=" ".join(full_text_parts),
            pages=pages,
            page_images=page_images,
            page_image_content_type="image/png",
        )
