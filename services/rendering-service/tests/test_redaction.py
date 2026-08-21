from io import BytesIO

import fitz
import pytest
from rendering_service.redaction import (
    InvalidRedactionRegionError,
    apply_redactions,
    get_page_count,
    render_page_image,
)
from reportlab.pdfgen import canvas


def _pdf_with_top_and_bottom_text(pages: int = 1) -> bytes:
    """200x200pt pages, "TOPTEXT" near the top and "BOTTOMTEXT" near the
    bottom of every page (reportlab is bottom-left-origin; PyMuPDF, which
    `redaction.py` uses, is top-left-origin - verified empirically that
    reportlab y=180 lands in PyMuPDF's y in [~5,~25] (top) and y=10 lands in
    [~175,~195] (bottom))."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    for _ in range(pages):
        c.drawString(10, 180, "TOPTEXT")
        c.drawString(10, 10, "BOTTOMTEXT")
        c.showPage()
    c.save()
    return buf.getvalue()


def _extracted_text(data: bytes) -> str:
    with fitz.open(stream=data, filetype="pdf") as doc:
        return "".join(page.get_text() for page in doc)


def test_get_page_count():
    assert get_page_count(_pdf_with_top_and_bottom_text(pages=3)) == 3


def test_render_page_image_returns_png_bytes():
    image = render_page_image(_pdf_with_top_and_bottom_text(), 1)
    assert image.startswith(b"\x89PNG")


def test_render_page_image_rejects_out_of_range_page():
    with pytest.raises(InvalidRedactionRegionError):
        render_page_image(_pdf_with_top_and_bottom_text(pages=1), 2)


def test_apply_redactions_actually_removes_covered_text():
    """The core correctness property this session relies on (ADR 0115): the
    redacted region's text must be genuinely gone from the content stream,
    not merely covered - confirmed here by re-extracting text, not by
    inspecting rendered pixels."""
    data = _pdf_with_top_and_bottom_text()
    assert "TOPTEXT" in _extracted_text(data)
    assert "BOTTOMTEXT" in _extracted_text(data)

    redacted = apply_redactions(
        data, [{"page_number": 1, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.2}]
    )

    extracted = _extracted_text(redacted)
    assert "TOPTEXT" not in extracted
    assert "BOTTOMTEXT" in extracted


def test_apply_redactions_leaves_other_pages_untouched():
    data = _pdf_with_top_and_bottom_text(pages=2)

    redacted = apply_redactions(
        data, [{"page_number": 1, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.2}]
    )

    with fitz.open(stream=redacted, filetype="pdf") as doc:
        assert "TOPTEXT" not in doc[0].get_text()
        assert "TOPTEXT" in doc[1].get_text()


def test_apply_redactions_supports_multiple_regions():
    data = _pdf_with_top_and_bottom_text()

    redacted = apply_redactions(
        data,
        [
            {"page_number": 1, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.2},
            {"page_number": 1, "x": 0.0, "y": 0.8, "width": 1.0, "height": 0.2},
        ],
    )

    extracted = _extracted_text(redacted)
    assert "TOPTEXT" not in extracted
    assert "BOTTOMTEXT" not in extracted


def test_apply_redactions_rejects_out_of_range_page():
    with pytest.raises(InvalidRedactionRegionError):
        apply_redactions(
            _pdf_with_top_and_bottom_text(pages=1),
            [{"page_number": 5, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.2}],
        )


def test_apply_redactions_returns_valid_pdf_bytes():
    redacted = apply_redactions(
        _pdf_with_top_and_bottom_text(),
        [{"page_number": 1, "x": 0.0, "y": 0.0, "width": 1.0, "height": 0.2}],
    )
    assert redacted.startswith(b"%PDF")
