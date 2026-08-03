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
    """Rasterbild-OCR (3.9) für gescannte PDFs (kein nutzbarer Textlayer) und
    für Rasterbilder direkt. Tatsächlich verdrahtete Standard-Engine anstelle
    des im Konzept genannten PaddleOCR (siehe ADR 0011 für die Abwägung -
    paddlepaddle ist als ML-Framework in dieser Umgebung nicht praktikabel)."""

    engine_name = "tesseract"

    def supports(
        self, *, content_type: str | None, filename: str, native_text_available: bool | None
    ) -> bool:
        if native_text_available is False:
            return True  # gescanntes PDF ohne nutzbaren Textlayer
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
                page_images=[],  # Rasterbild - kein eigenständiges Seitenbild nötig
                page_image_content_type=None,
            )

        # Mehrseitiges PDF: jede Seite einzeln rastern und OCR'en (nicht nur
        # Seite 1, siehe `OcrExtractionResult.page_images`-Kommentar).
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
