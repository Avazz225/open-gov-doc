from io import BytesIO

import pytest
from pypdf import PdfReader
from rendering_service.watermark import add_stamp
from reportlab.pdfgen import canvas


def _real_pdf(pages: int = 1) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    for i in range(pages):
        c.drawString(10, 100, f"Seite {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _has_embedded_image(page) -> bool:
    resources = page.get("/Resources")
    if resources is None or "/XObject" not in resources:
        return False
    xobjects = resources["/XObject"]
    return any(xobjects[name]["/Subtype"] == "/Image" for name in xobjects)


def test_add_stamp_text_diagonal_center_matches_original_watermark_behavior():
    """Post-Roadmap Phase 31 Session 6 (ADR 0117): the default stamp_type/
    position combo must reproduce the pre-existing diagonal watermark
    unchanged."""
    result = add_stamp(_real_pdf(pages=2), value="VERTRAULICH")
    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 2
    # A diagonal text overlay is drawn via `drawCentredString`, not an image
    # - no XObject expected for the text-only default.
    assert not _has_embedded_image(reader.pages[0])


def test_add_stamp_text_at_corner_is_not_rotated_diagonally():
    result = add_stamp(_real_pdf(pages=1), stamp_type="text", value="Entwurf", position="top-left")
    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 1
    assert not _has_embedded_image(reader.pages[0])


def test_add_stamp_qr_embeds_an_image_on_every_page():
    result = add_stamp(
        _real_pdf(pages=3), stamp_type="qr", value="DOC-123", position="bottom-right"
    )
    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 3
    for page in reader.pages:
        assert _has_embedded_image(page)


def test_add_stamp_barcode_embeds_an_image():
    result = add_stamp(
        _real_pdf(pages=1), stamp_type="barcode", value="DOC-123", position="top-right"
    )
    reader = PdfReader(BytesIO(result))
    assert _has_embedded_image(reader.pages[0])


@pytest.mark.parametrize("position", ["top-left", "top-right", "bottom-left", "bottom-right"])
def test_add_stamp_qr_at_every_corner(position):
    result = add_stamp(_real_pdf(pages=1), stamp_type="qr", value="DOC-123", position=position)
    reader = PdfReader(BytesIO(result))
    assert _has_embedded_image(reader.pages[0])


def test_add_stamp_rejects_pdf_without_pages():
    empty_pdf_buffer = BytesIO()
    from pypdf import PdfWriter

    PdfWriter().write(empty_pdf_buffer)
    with pytest.raises(ValueError, match="keine Seiten"):
        add_stamp(empty_pdf_buffer.getvalue(), value="x")
