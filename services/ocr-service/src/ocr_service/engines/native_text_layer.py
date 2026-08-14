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
    """Uses the PDF text layer that already exists (3.9: "automatic
    detection of whether OCR is needed at all") instead of expensive image
    recognition - fast, exact, confidence always 100.0."""

    engine_name = "native_text_layer"

    def supports(
        self, *, content_type: str | None, filename: str, native_text_available: bool | None
    ) -> bool:
        return native_text_available is True

    async def extract(
        self, data: bytes, *, filename: str, content_type: str | None
    ) -> OcrExtractionResult:
        doc = fitz.open(stream=data, filetype="pdf")
        # get_text("words") returns coordinates in PDF points (1/72"); the
        # factor scales them into the same pixel grid as the page image
        # generated simultaneously via get_pixmap(dpi=raster_dpi), so the
        # overlay and the image line up exactly.
        scale = _settings.raster_dpi / 72

        pages: list[OcrPageResult] = []
        page_images: list[bytes] = []
        full_text_parts: list[str] = []
        # Iterate over all pages, not just the first (bugfix: multi-page
        # PDF preview used to always show only page 1).
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
