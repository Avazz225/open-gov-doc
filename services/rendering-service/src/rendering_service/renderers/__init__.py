from rendering_service.renderers.docx_text import DocxTextExtractionRenderer
from rendering_service.renderers.interface import Renderer, RenderOutput
from rendering_service.renderers.ods_text import OdsTextExtractionRenderer
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
    OdsTextExtractionRenderer(),
    PdfArchiveRenderer(),
]


def select_renderers(*, content_type: str | None, filename: str) -> list[Renderer]:
    return [r for r in RENDERERS if r.supports(content_type=content_type, filename=filename)]


def get_renderer_by_type(rendition_type: str) -> Renderer | None:
    """Für den Retry-Poll-Loop (Post-Roadmap Phase 20 Session 4, ADR 0080):
    eine fehlgeschlagene Rendition-Zeile kennt nur ihren `rendition_type`
    (nicht mehr die ursprüngliche Content-Type-/Dateinamen-Zuordnung, die zur
    Renderer-Auswahl führte) - ein Wiederholungsversuch muss gezielt NUR den
    einen betroffenen Renderer erneut aufrufen, nicht `select_renderers()`
    erneut über alle Regeln laufen lassen (würde auch bereits erfolgreiche
    Renditions unnötig neu erzeugen)."""
    for renderer in RENDERERS:
        if renderer.rendition_type == rendition_type:
            return renderer
    return None


__all__ = ["RENDERERS", "RenderOutput", "Renderer", "get_renderer_by_type", "select_renderers"]
