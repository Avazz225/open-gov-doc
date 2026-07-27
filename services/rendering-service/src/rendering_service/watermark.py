from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas


def add_text_watermark(data: bytes, text: str) -> bytes:
    """On-Demand-Wasserzeichen (3.7) - anders als die Ersatzdarstellungen
    (2.4) kein automatisch bei jedem Upload erzeugtes, persistiertes Objekt:
    ein Wasserzeichen (z. B. "VERTRAULICH", Empfängername bei einem Export)
    ist typischerweise eine bewusste Einzelaktion für einen konkreten Anlass,
    kein Standardschritt für jedes hochgeladene PDF. Bewusst einfach gehalten:
    ein diagonaler, halbtransparenter Textstempel über jede Seite, keine
    Positions-/Farb-/Wiederholungs-Konfiguration - siehe Offene Punkte in
    docs/services/rendering-service.md."""
    reader = PdfReader(BytesIO(data))
    if not reader.pages:
        raise ValueError("PDF enthält keine Seiten")

    first_page = reader.pages[0]
    page_width = float(first_page.mediabox.width)
    page_height = float(first_page.mediabox.height)

    overlay_buffer = BytesIO()
    stamp = canvas.Canvas(overlay_buffer, pagesize=(page_width, page_height))
    stamp.saveState()
    stamp.setFont("Helvetica-Bold", 40)
    stamp.setFillColorRGB(0.6, 0.6, 0.6)
    stamp.setFillAlpha(0.35)
    stamp.translate(page_width / 2, page_height / 2)
    stamp.rotate(45)
    stamp.drawCentredString(0, 0, text)
    stamp.restoreState()
    stamp.save()
    overlay_buffer.seek(0)
    overlay_page = PdfReader(overlay_buffer).pages[0]

    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        page.merge_page(overlay_page)

    output_buffer = BytesIO()
    writer.write(output_buffer)
    return output_buffer.getvalue()
