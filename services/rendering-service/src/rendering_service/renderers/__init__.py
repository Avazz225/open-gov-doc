from rendering_service.renderers.docx_text import DocxTextExtractionRenderer
from rendering_service.renderers.interface import Renderer, RenderOutput
from rendering_service.renderers.pdf_archive import PdfArchiveRenderer
from rendering_service.renderers.pptx_text import PptxTextExtractionRenderer
from rendering_service.renderers.thumbnail import ThumbnailRenderer

# Regeltabelle "Quellformat -> Ersatzdarstellung(en)" (2.4/3.7). Bewusst auf
# Formate beschränkt, die ohne OCR auskommen (Office-Dokumente, Rasterbilder,
# PDF) - bildbasierte/gescannte Dokumente folgen als Nachzieheffekt von P5-S3
# (siehe IMPLEMENTATION_PLAN.md). Ein Video-Transkriptions-Plugin (2.4) ist
# laut Konzept selbst optional ("sofern verfügbar") und bewusst nicht Teil
# dieser Session - es existiert (noch) keine Transkriptions-Engine.
RENDERERS: list[Renderer] = [
    ThumbnailRenderer(),
    DocxTextExtractionRenderer(),
    PptxTextExtractionRenderer(),
    PdfArchiveRenderer(),
]


def select_renderers(*, content_type: str | None, filename: str) -> list[Renderer]:
    return [r for r in RENDERERS if r.supports(content_type=content_type, filename=filename)]


__all__ = ["RENDERERS", "RenderOutput", "Renderer", "select_renderers"]
