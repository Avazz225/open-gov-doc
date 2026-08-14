from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OcrWordResult:
    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float  # 0-100; 100.0 for words from the native text layer (exact, not OCR'd)


@dataclass
class OcrPageResult:
    page_number: int
    width: int
    height: int
    words: list[OcrWordResult]


@dataclass
class OcrExtractionResult:
    engine: str
    average_confidence: float
    full_text: str
    pages: list[OcrPageResult]
    # One page image per entry in `pages` (same index, 1:1), only set for
    # PDFs (see engines/__init__.py) - empty for raster images, which don't
    # need their own OCR page-image rendition (see rendering-service
    # thumbnail). Used to be a single `bytes | None` field exclusively for
    # the first page (bugfix: multi-page PDF preview always showed only
    # page 1, regardless of which version/excerpt was selected).
    page_images: list[bytes]
    page_image_content_type: str | None


class TextLayerExtractor(ABC):
    """An OCR engine (3.3/3.8, same plugin principle as the storage backends
    and `rendering_service.renderers.Renderer`) - unlike there, however,
    exactly one engine runs per version instead of several independent
    rules, selected by `select_engine()` based on the two-stage decision
    "is there a usable text layer?"."""

    engine_name: str

    @abstractmethod
    def supports(
        self, *, content_type: str | None, filename: str, native_text_available: bool | None
    ) -> bool: ...

    @abstractmethod
    async def extract(
        self, data: bytes, *, filename: str, content_type: str | None
    ) -> OcrExtractionResult: ...


class UnreadableDocumentError(Exception):
    """File claims to be a PDF (content type/extension), but is corrupt/
    cannot be opened - unlike a simply unsupported format (e.g. .docx), an
    explicit error case, not a silent skip."""
