from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OcrWordResult:
    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float  # 0-100; 100.0 für Wörter aus dem nativen Textlayer (exakt, nicht OCR'd)


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
    # Ein Seitenbild je Eintrag in `pages` (gleicher Index, 1:1), nur für PDFs
    # gesetzt (siehe engines/__init__.py) - leer für Rasterbilder, die keine
    # eigene OCR-Seitenbild-Rendition brauchen (siehe rendering-service-
    # Thumbnail). War früher ein einzelnes `bytes | None`-Feld ausschließlich
    # für die erste Seite (Bugfix: mehrseitige PDF-Vorschau zeigte immer nur
    # Seite 1, egal welche Version/welcher Ausschnitt ausgewählt war).
    page_images: list[bytes]
    page_image_content_type: str | None


class TextLayerExtractor(ABC):
    """Eine OCR-Engine (3.3/3.8, gleiches Plugin-Prinzip wie die
    Storage-Backends und `rendering_service.renderers.Renderer`) - anders als
    dort läuft aber pro Version genau eine Engine statt mehrerer unabhängiger
    Regeln, ausgewählt von `select_engine()` anhand der zweistufigen
    Entscheidung "gibt es einen nutzbaren Textlayer?"."""

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
    """Datei gibt sich als PDF aus (Content-Type/Endung), ist aber korrupt/
    nicht öffnenbar - anders als ein schlicht nicht unterstütztes Format
    (z. B. .docx) ein expliziter Fehlerfall, kein stiller Skip."""
