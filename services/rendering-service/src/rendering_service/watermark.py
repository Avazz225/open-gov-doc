from io import BytesIO

import qrcode
from barcode import Code128
from barcode.writer import ImageWriter
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# Output stamping (3.7, extended Post-Roadmap Phase 31 Session 6, ADR 0117):
# originally a fixed diagonal text-only overlay only, now also QR/Code128
# barcode stamps, and configurable positioning for all three stamp types.
# Position/stamp-type validity is the CALLER's responsibility (main.py's
# endpoint, and document_service's export-pipeline integration) - this
# module stays a lenient, pure rendering function, same division of
# responsibility already established by export_pdf.py's `history_position`
# handling (validated once at the API boundary, not re-validated here).
_CORNER_POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right"}
_MARGIN = 24.0
_QR_SIZE = 72.0
_BARCODE_SIZE = (120.0, 40.0)


def _corner_origin(
    position: str, *, page_width: float, page_height: float, stamp_width: float, stamp_height: float
) -> tuple[float, float]:
    if position == "top-left":
        return _MARGIN, page_height - _MARGIN - stamp_height
    if position == "top-right":
        return page_width - _MARGIN - stamp_width, page_height - _MARGIN - stamp_height
    if position == "bottom-left":
        return _MARGIN, _MARGIN
    if position == "bottom-right":
        return page_width - _MARGIN - stamp_width, _MARGIN
    raise ValueError(f"unbekannte Eck-Position {position!r}")


def _qr_image_reader(value: str) -> ImageReader:
    buffer = BytesIO()
    qrcode.make(value).save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def _barcode_image_reader(value: str) -> ImageReader:
    buffer = BytesIO()
    # `write_text=False`: the human-readable digits/characters Code128
    # normally renders below the bars would otherwise compete for the same
    # small corner footprint as the bars themselves - the encoded value is
    # already fully recoverable by scanning, a printed label isn't needed.
    Code128(value, writer=ImageWriter()).write(buffer, options={"write_text": False})
    buffer.seek(0)
    return ImageReader(buffer)


def add_stamp(
    data: bytes, *, stamp_type: str = "text", value: str, position: str = "diagonal-center"
) -> bytes:
    """On-demand stamp (3.7). `stamp_type`: `"text"` (default, backward
    compatible with the original `add_text_watermark`), `"qr"`, or
    `"barcode"` (Code128). `position`: `"diagonal-center"` (the original
    rotated, semi-transparent, page-centered text stamp - text only) or one
    of the four page corners, drawn upright/unrotated (all three stamp
    types)."""
    reader = PdfReader(BytesIO(data))
    if not reader.pages:
        raise ValueError("PDF enthält keine Seiten")

    first_page = reader.pages[0]
    page_width = float(first_page.mediabox.width)
    page_height = float(first_page.mediabox.height)

    overlay_buffer = BytesIO()
    stamp = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))

    if stamp_type == "text" and position == "diagonal-center":
        stamp.saveState()
        stamp.setFont("Helvetica-Bold", 40)
        stamp.setFillColorRGB(0.6, 0.6, 0.6)
        stamp.setFillAlpha(0.35)
        stamp.translate(page_width / 2, page_height / 2)
        stamp.rotate(45)
        stamp.drawCentredString(0, 0, value)
        stamp.restoreState()
    elif stamp_type == "text":
        stamp.setFont("Helvetica-Bold", 12)
        stamp.setFillColorRGB(0.3, 0.3, 0.3)
        text_width = stamp.stringWidth(value, "Helvetica-Bold", 12)
        x, y = _corner_origin(
            position,
            page_width=page_width,
            page_height=page_height,
            stamp_width=text_width,
            stamp_height=12,
        )
        stamp.drawString(x, y, value)
    else:
        if stamp_type == "qr":
            image_reader = _qr_image_reader(value)
            stamp_width = stamp_height = _QR_SIZE
        else:
            image_reader = _barcode_image_reader(value)
            stamp_width, stamp_height = _BARCODE_SIZE
        x, y = _corner_origin(
            position,
            page_width=page_width,
            page_height=page_height,
            stamp_width=stamp_width,
            stamp_height=stamp_height,
        )
        stamp.drawImage(image_reader, x, y, width=stamp_width, height=stamp_height, mask="auto")

    stamp.save()
    overlay_buffer.seek(0)
    overlay_page = PdfReader(overlay_buffer).pages[0]

    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        page.merge_page(overlay_page)

    output_buffer = BytesIO()
    writer.write(output_buffer)
    return output_buffer.getvalue()
