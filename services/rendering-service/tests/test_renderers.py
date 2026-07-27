from io import BytesIO

import pytest
from docx import Document
from PIL import Image
from pptx import Presentation
from pypdf import PdfReader
from rendering_service.renderers import select_renderers
from rendering_service.renderers.docx_text import DocxTextExtractionRenderer
from rendering_service.renderers.pdf_archive import PdfArchiveRenderer
from rendering_service.renderers.pptx_text import PptxTextExtractionRenderer
from rendering_service.renderers.thumbnail import ThumbnailRenderer
from reportlab.pdfgen import canvas


def _real_png(size=(800, 600)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color=(120, 20, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _real_docx(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _real_pptx(title: str) -> bytes:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = title
    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _real_pdf(pages: int = 2) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    for i in range(pages):
        c.drawString(10, 100, f"Seite {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_thumbnail_renderer_downsizes_and_supports_image():
    renderer = ThumbnailRenderer()
    assert renderer.supports(content_type="image/png", filename="foo.png")
    assert renderer.supports(content_type=None, filename="foo.PNG")
    assert not renderer.supports(content_type="application/pdf", filename="foo.pdf")

    output = await renderer.render(_real_png(), filename="foto.png", content_type="image/png")
    assert output.target_content_type == "image/png"
    assert output.target_filename == "foto_thumbnail.png"
    thumb = Image.open(BytesIO(output.data))
    assert thumb.width <= 256
    assert thumb.height <= 256


@pytest.mark.asyncio
async def test_docx_text_extraction_renderer():
    renderer = DocxTextExtractionRenderer()
    assert renderer.supports(content_type=None, filename="brief.docx")
    assert not renderer.supports(content_type=None, filename="brief.txt")

    data = _real_docx("Hallo Welt, dies ist ein Test.")
    output = await renderer.render(data, filename="brief.docx", content_type=None)
    assert output.target_filename == "brief.txt"
    assert output.target_content_type == "text/plain; charset=utf-8"
    assert b"Hallo Welt, dies ist ein Test." in output.data


@pytest.mark.asyncio
async def test_pptx_text_extraction_renderer():
    renderer = PptxTextExtractionRenderer()
    assert renderer.supports(content_type=None, filename="folien.pptx")

    data = _real_pptx("Quartalsbericht")
    output = await renderer.render(data, filename="folien.pptx", content_type=None)
    assert output.target_filename == "folien.txt"
    assert b"Quartalsbericht" in output.data
    assert b"Folie 1" in output.data


@pytest.mark.asyncio
async def test_pdf_archive_renderer_preserves_pages():
    renderer = PdfArchiveRenderer()
    assert renderer.supports(content_type="application/pdf", filename="akte.pdf")

    data = _real_pdf(pages=3)
    output = await renderer.render(data, filename="akte.pdf", content_type="application/pdf")
    assert output.target_filename == "akte_archiv.pdf"
    assert output.target_content_type == "application/pdf"
    reader = PdfReader(BytesIO(output.data))
    assert len(reader.pages) == 3


def test_select_renderers_matches_expected_rules():
    image_renderers = select_renderers(content_type="image/jpeg", filename="a.jpg")
    assert {r.rendition_type for r in image_renderers} == {"thumbnail"}

    docx_renderers = select_renderers(content_type=None, filename="a.docx")
    assert {r.rendition_type for r in docx_renderers} == {"substitute_text"}

    assert select_renderers(content_type="text/csv", filename="a.csv") == []
