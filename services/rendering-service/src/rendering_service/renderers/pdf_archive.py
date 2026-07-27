import os
from io import BytesIO

from pypdf import PdfReader, PdfWriter

from rendering_service.renderers.interface import Renderer, RenderOutput


class PdfArchiveRenderer(Renderer):
    """PDF/A-Konvertierung (3.7) als eigene Ersatzdarstellung (2.4): kopiert
    alle Seiten in einen neu geschriebenen PDF-Strukturbaum und markiert ihn
    per Info-Dictionary als Archivkopie.

    **Bewusste Einschränkung, wie bei ADR 0010 (ClamdEngine vs. EICAR)**: dies
    ist eine *Best-Effort*-Tagging-Konvertierung, keine ISO-19005-validierte
    PDF/A-Erzeugung - echte PDF/A-Konformität (Font-Einbettung,
    Farbraum-Validierung) bräuchte Ghostscript/veraPDF, die in dieser Umgebung
    nicht verfügbar sind. Siehe Offene Punkte in docs/services/
    rendering-service.md."""

    rendition_type = "pdf_archive"

    def supports(self, *, content_type: str | None, filename: str) -> bool:
        if content_type == "application/pdf":
            return True
        return filename.lower().endswith(".pdf")

    async def render(self, data: bytes, *, filename: str, content_type: str | None) -> RenderOutput:
        reader = PdfReader(BytesIO(data))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata(
            {
                "/Producer": "DMS Rendering Service - PDF-Archivkopie "
                "(Best-Effort, nicht ISO-19005-validiert)",
                "/Title": filename,
            }
        )
        buffer = BytesIO()
        writer.write(buffer)
        stem = os.path.splitext(filename)[0] or "dokument"
        return RenderOutput(
            rendition_type=self.rendition_type,
            target_filename=f"{stem}_archiv.pdf",
            target_content_type="application/pdf",
            data=buffer.getvalue(),
        )
