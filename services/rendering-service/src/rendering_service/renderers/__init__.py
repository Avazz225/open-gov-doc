from rendering_service.renderers.docx_text import DocxTextExtractionRenderer
from rendering_service.renderers.interface import Renderer, RenderOutput
from rendering_service.renderers.ods_text import OdsTextExtractionRenderer
from rendering_service.renderers.pdf_archive import PdfArchiveRenderer
from rendering_service.renderers.pptx_text import PptxTextExtractionRenderer
from rendering_service.renderers.thumbnail import ThumbnailRenderer

# Rule table "source format -> rendition(s)" (2.4/3.7). Deliberately limited
# to formats that work without OCR (Office documents, raster images, PDF) -
# image-based/scanned documents follow as a follow-up effect of P5-S3
# (see IMPLEMENTATION_PLAN.md). A video transcription plugin (2.4) is
# itself optional per the concept ("if available") and deliberately not
# part of this session - no transcription engine exists (yet).
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
    """For the retry poll loop (post-roadmap phase 20 session 4, ADR 0080):
    a failed rendition row only knows its `rendition_type` (no longer the
    original content-type/filename mapping that led to the renderer
    selection) - a retry must specifically call ONLY the one affected
    renderer again, not run `select_renderers()` again over all rules
    (which would also unnecessarily regenerate already successful
    renditions)."""
    for renderer in RENDERERS:
        if renderer.rendition_type == rendition_type:
            return renderer
    return None


__all__ = ["RENDERERS", "RenderOutput", "Renderer", "get_renderer_by_type", "select_renderers"]
