import os
from io import BytesIO

import fitz  # PyMuPDF
import pytesseract
from ocr_service.engines.interface import (
    OcrExtractionResult,
    OcrPageResult,
    OcrWordResult,
    TextLayerExtractor,
)
from ocr_service.settings import Settings
from PIL import Image

_settings = Settings()
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}


def is_raster_image(*, content_type: str | None, filename: str) -> bool:
    if content_type and content_type.startswith("image/"):
        return True
    return os.path.splitext(filename)[1].lower() in _IMAGE_EXTENSIONS


class TesseractEngine(TextLayerExtractor):
    """Raster-image OCR (3.9) for scanned PDFs (no usable text layer) and
    for raster images directly. The actually wired-up default engine
    instead of the PaddleOCR named in the concept (see ADR 0011 for the
    trade-off - paddlepaddle is not practical as an ML framework in this
    environment)."""

    engine_name = "tesseract"

    def supports(
        self, *, content_type: str | None, filename: str, native_text_available: bool | None
    ) -> bool:
        if native_text_available is False:
            return True  # scanned PDF without a usable text layer
        return native_text_available is None and is_raster_image(
            content_type=content_type, filename=filename
        )

    def _words_from_image(self, image: Image.Image) -> tuple[list[OcrWordResult], list[float]]:
        ocr_data = pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT, lang="deu+eng"
        )
        words: list[OcrWordResult] = []
        confidences: list[float] = []
        for i, text in enumerate(ocr_data["text"]):
            text = text.strip()
            confidence = float(ocr_data["conf"][i])
            if not text or confidence < 0:
                continue
            words.append(
                OcrWordResult(
                    text=text,
                    left=float(ocr_data["left"][i]),
                    top=float(ocr_data["top"][i]),
                    width=float(ocr_data["width"][i]),
                    height=float(ocr_data["height"][i]),
                    confidence=confidence,
                )
            )
            confidences.append(confidence)
        return words, confidences

    async def extract(
        self, data: bytes, *, filename: str, content_type: str | None
    ) -> OcrExtractionResult:
        if is_raster_image(content_type=content_type, filename=filename):
            image = Image.open(BytesIO(data))
            image.load()
            words, confidences = self._words_from_image(image)
            average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            return OcrExtractionResult(
                engine=self.engine_name,
                average_confidence=average_confidence,
                full_text=" ".join(w.text for w in words),
                pages=[
                    OcrPageResult(
                        page_number=1, width=image.width, height=image.height, words=words
                    )
                ],
                page_images=[],  # raster image - no separate page image needed
                page_image_content_type=None,
            )

        # Multi-page PDF: rasterize and OCR each page individually (not just
        # page 1, see the `OcrExtractionResult.page_images` comment).
        doc = fitz.open(stream=data, filetype="pdf")
        pages: list[OcrPageResult] = []
        page_images: list[bytes] = []
        all_words: list[OcrWordResult] = []
        all_confidences: list[float] = []
        for page_index in range(len(doc)):
            pixmap = doc[page_index].get_pixmap(dpi=_settings.raster_dpi)
            page_image_bytes = pixmap.tobytes("png")
            page_images.append(page_image_bytes)
            image = Image.open(BytesIO(page_image_bytes))
            words, confidences = self._words_from_image(image)
            pages.append(
                OcrPageResult(
                    page_number=page_index + 1, width=image.width, height=image.height, words=words
                )
            )
            all_words.extend(words)
            all_confidences.extend(confidences)

        average_confidence = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
        return OcrExtractionResult(
            engine=self.engine_name,
            average_confidence=average_confidence,
            full_text=" ".join(w.text for w in all_words),
            pages=pages,
            page_images=page_images,
            page_image_content_type="image/png",
        )
