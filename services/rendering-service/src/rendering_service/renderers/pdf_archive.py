import os
from io import BytesIO

from PIL import Image
from pypdf import PdfReader, PdfWriter

from rendering_service.renderers._libreoffice import convert_via_libreoffice
from rendering_service.renderers.interface import Renderer, RenderOutput

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}
# Office-/Textformate, die LibreOffice headless konvertieren kann (5.6, seit
# P7-S3) - Voraussetzung fuer "alle Dokumententypen aussonderungsfaehig"
# (Nutzervorgabe), nicht nur bereits-PDF-Dokumente wie zuvor.
_LIBREOFFICE_EXTENSIONS = {
    ".doc",
    ".docx",
    ".rtf",
    ".odt",
    ".ppt",
    ".pptx",
    ".odp",
    ".xls",
    ".xlsx",
    ".ods",
    ".csv",
    ".txt",
}


class PdfArchiveRenderer(Renderer):
    """PDF/A-Archivkopie (3.7/5.6) als eigene Ersatzdarstellung (2.4) - seit
    P7-S3 fuer drei Formatfamilien statt nur bereits-PDF-Dokumenten:

    - **PDF**: bestehende `pypdf`-Tagging-Logik (Seiten in neuen
      Strukturbaum kopiert, Info-Dictionary als Archivkopie markiert).
    - **Rasterbilder**: direkt ueber Pillow zu PDF gerendert (kein
      LibreOffice-Subprozess noetig fuer den einfachsten Fall).
    - **Office-/Textformate**: ueber LibreOffice headless konvertiert
      (`_libreoffice.py`), mit LibreOffices eigenem PDF/A-1b-Export-Filter
      (eingebettete Fonts/XMP-Metadaten) - technisch konformer als die reine
      `pypdf`-Nachbearbeitung, aber weiterhin **nicht unabhaengig
      veraPDF-validiert**.

    **Bewusste Einschraenkung, wie bei ADR 0010 (ClamdEngine vs. EICAR)**: in
    keinem der drei Pfade wird eine ISO-19005-Konformitaetspruefung
    durchgefuehrt - echte PDF/A-Validierung braeuchte veraPDF, das in dieser
    Umgebung nicht eingebunden ist. Siehe Offene Punkte in docs/services/
    rendering-service.md."""

    rendition_type = "pdf_archive"

    def supports(self, *, content_type: str | None, filename: str) -> bool:
        if content_type == "application/pdf":
            return True
        if content_type and content_type.startswith("image/"):
            return True
        ext = os.path.splitext(filename)[1].lower()
        return ext == ".pdf" or ext in _IMAGE_EXTENSIONS or ext in _LIBREOFFICE_EXTENSIONS

    async def render(self, data: bytes, *, filename: str, content_type: str | None) -> RenderOutput:
        ext = os.path.splitext(filename)[1].lower()
        stem = os.path.splitext(filename)[0] or "dokument"

        if content_type == "application/pdf" or ext == ".pdf":
            pdf_bytes = self._tag_pdf(data, filename)
        elif (content_type and content_type.startswith("image/")) or ext in _IMAGE_EXTENSIONS:
            pdf_bytes = self._image_to_pdf(data)
        else:
            pdf_bytes = await convert_via_libreoffice(data, filename=filename, pdf_a=True)

        return RenderOutput(
            rendition_type=self.rendition_type,
            target_filename=f"{stem}_archiv.pdf",
            target_content_type="application/pdf",
            data=pdf_bytes,
        )

    def _tag_pdf(self, data: bytes, filename: str) -> bytes:
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
        return buffer.getvalue()

    def _image_to_pdf(self, data: bytes) -> bytes:
        image = Image.open(BytesIO(data))
        image.load()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        buffer = BytesIO()
        image.save(buffer, format="PDF")
        return buffer.getvalue()
