import shutil
from io import BytesIO

import pytest
from ocr_service.engines import (
    ASSUMED_WORDS_PER_PAGE,
    UnreadableDocumentError,
    estimate_word_count,
    select_engine,
)
from ocr_service.engines.native_text_layer import NativeTextLayerEngine
from ocr_service.engines.tesseract_ocr import TesseractEngine
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas

_TESSERACT_AVAILABLE = shutil.which("tesseract") is not None


def _text_pdf(text: str) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    c.drawString(20, 250, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _blank_pdf() -> bytes:
    """PDF-Seite ohne jeglichen Text - simuliert ein gescanntes Dokument ohne
    nutzbaren Textlayer (nur Bildinhalt hätte denselben Effekt für get_text())."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    c.showPage()
    c.save()
    return buf.getvalue()


def _text_image(text: str) -> bytes:
    image = Image.new("RGB", (600, 200), color="white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 60), text, fill="black", font=font)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


async def test_native_text_layer_engine_extracts_words_with_bboxes():
    engine = NativeTextLayerEngine()
    data = _text_pdf("Hallo Welt")

    result = await engine.extract(data, filename="brief.pdf", content_type="application/pdf")

    assert result.engine == "native_text_layer"
    assert result.average_confidence == 100.0
    assert "Hallo" in result.full_text
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.page_number == 1
    assert page.width > 0 and page.height > 0
    assert any(w.text == "Hallo" for w in page.words)
    assert result.page_image is not None
    assert result.page_image_content_type == "image/png"
    # Bounding-Box-Koordinaten müssen innerhalb der Seitenbild-Pixelmaße liegen -
    # sonst würde das Overlay im Frontend über den Bildrand hinausragen.
    for word in page.words:
        assert 0 <= word.left <= page.width
        assert 0 <= word.top <= page.height


@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason="tesseract-ocr nicht auf diesem Host installiert - Verifikation erfolgt "
    "im echten Docker-Container (siehe Dockerfile: apt-get install tesseract-ocr)",
)
async def test_tesseract_engine_extracts_text_from_raster_image():
    engine = TesseractEngine()
    data = _text_image("TESTWORT")

    result = await engine.extract(data, filename="scan.png", content_type="image/png")

    assert result.engine == "tesseract"
    assert result.pages[0].width == 600
    assert result.pages[0].height == 200
    assert "TESTWORT" in result.full_text.upper()
    assert result.page_image is None  # Rasterbild - kein eigenständiges Seitenbild nötig
    assert 0.0 <= result.average_confidence <= 100.0


@pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason="tesseract-ocr nicht auf diesem Host installiert - Verifikation erfolgt "
    "im echten Docker-Container",
)
async def test_tesseract_engine_rasterizes_scanned_pdf():
    engine = TesseractEngine()
    data = _blank_pdf()

    result = await engine.extract(data, filename="scan.pdf", content_type="application/pdf")

    assert result.engine == "tesseract"
    assert result.page_image is not None  # PDF - eigenständiges Seitenbild wird erzeugt
    assert result.page_image_content_type == "image/png"


def test_select_engine_picks_native_for_text_pdf():
    engine = select_engine(
        content_type="application/pdf",
        filename="brief.pdf",
        data=_text_pdf("Hallo Welt, dies ist ein Test"),
    )
    assert engine is not None
    assert engine.engine_name == "native_text_layer"


def test_select_engine_picks_tesseract_for_scanned_pdf():
    engine = select_engine(content_type="application/pdf", filename="scan.pdf", data=_blank_pdf())
    assert engine is not None
    assert engine.engine_name == "tesseract"


def test_select_engine_picks_tesseract_for_raster_image():
    engine = select_engine(content_type="image/png", filename="foto.png", data=b"irrelevant")
    assert engine is not None
    assert engine.engine_name == "tesseract"


def test_select_engine_returns_none_for_unsupported_format():
    engine = select_engine(
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename="brief.docx",
        data=b"irrelevant",
    )
    assert engine is None


def _multi_page_pdf(page_count: int) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    for _ in range(page_count):
        c.drawString(20, 250, "Seite")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_estimate_word_count_scales_with_pdf_page_count():
    estimate = estimate_word_count(
        _multi_page_pdf(3), content_type="application/pdf", filename="brief.pdf"
    )
    assert estimate == 3 * ASSUMED_WORDS_PER_PAGE


def test_estimate_word_count_treats_raster_image_as_one_page():
    estimate = estimate_word_count(b"irrelevant", content_type="image/png", filename="scan.png")
    assert estimate == ASSUMED_WORDS_PER_PAGE


def test_estimate_word_count_zero_for_unreadable_pdf():
    estimate = estimate_word_count(
        b"kein echtes pdf", content_type="application/pdf", filename="kaputt.pdf"
    )
    assert estimate == 0


def test_select_engine_raises_for_corrupt_pdf():
    with pytest.raises(UnreadableDocumentError):
        select_engine(
            content_type="application/pdf", filename="kaputt.pdf", data=b"kein echtes pdf"
        )
