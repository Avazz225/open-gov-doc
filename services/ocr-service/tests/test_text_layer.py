from io import BytesIO

import fitz
import pytest
from ocr_service.engines.interface import OcrPageResult, OcrWordResult
from ocr_service.text_layer import embed_text_layer
from reportlab.pdfgen import canvas


def _blank_pdf(width: int = 400, height: int = 300) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    c.showPage()
    c.save()
    return buf.getvalue()


def test_embed_text_layer_adds_extractable_invisible_text():
    pdf_bytes = _blank_pdf()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc[0].get_text("text").strip() == ""
    doc.close()

    raster_dpi = 150
    pages = [
        OcrPageResult(
            page_number=1,
            width=int(400 * raster_dpi / 72),
            height=int(300 * raster_dpi / 72),
            words=[
                OcrWordResult(
                    text="TESTWORT", left=50.0, top=50.0, width=150.0, height=30.0, confidence=95.0
                )
            ],
        )
    ]

    result_bytes = embed_text_layer(pdf_bytes, pages, raster_dpi)

    result_doc = fitz.open(stream=result_bytes, filetype="pdf")
    try:
        extracted = result_doc[0].get_text("text")
    finally:
        result_doc.close()
    assert "TESTWORT" in extracted


def test_embed_text_layer_skips_whitespace_only_words():
    pdf_bytes = _blank_pdf()
    pages = [
        OcrPageResult(
            page_number=1,
            width=800,
            height=600,
            words=[OcrWordResult(text="   ", left=10, top=10, width=5, height=5, confidence=0.0)],
        )
    ]

    result_bytes = embed_text_layer(pdf_bytes, pages, 150)

    doc = fitz.open(stream=result_bytes, filetype="pdf")
    try:
        assert doc[0].get_text("text").strip() == ""
    finally:
        doc.close()


def test_embed_text_layer_ignores_page_number_beyond_document():
    """Ein OCR-Ergebnis mit mehr Seiten als das Original (sollte nicht
    vorkommen, aber defensiv) darf die Einbettung nicht zum Absturz bringen."""
    pdf_bytes = _blank_pdf()
    pages = [
        OcrPageResult(
            page_number=5,
            width=800,
            height=600,
            words=[
                OcrWordResult(
                    text="AUSSERHALB", left=10, top=10, width=100, height=20, confidence=90.0
                )
            ],
        )
    ]

    result_bytes = embed_text_layer(pdf_bytes, pages, 150)

    doc = fitz.open(stream=result_bytes, filetype="pdf")
    try:
        assert doc[0].get_text("text").strip() == ""
    finally:
        doc.close()


def test_embed_text_layer_raises_for_corrupt_pdf():
    """Dokumentiert die Fehlerweitergabe, auf die sich pipeline.process_version()s
    Try/Except verlässt (ein Fehlschlag hier darf den OCR-Befund selbst nicht
    zunichtemachen, siehe pipeline.py)."""
    with pytest.raises(fitz.FileDataError):
        embed_text_layer(b"das ist kein echtes PDF", [], 150)
