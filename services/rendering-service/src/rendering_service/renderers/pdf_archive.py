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
        """The method name predates and is unrelated to PDF/UA accessibility
        tagging - "tag" here means marking the PDF as an archival copy
        (`/Producer`/`/Title` metadata), see class docstring.

        Post-roadmap phase 33 session 2 (ADR 0136, PDF/UA tag preservation):
        this was actually the real source of `export_pdf.py`'s originally
        assumed "pypdf's writer has no structure-tree-copying capability at
        all" (ADR 0119) - `document-service`'s export endpoint routes every
        already-PDF source through THIS function first (`main.py`'s
        `render_export_document`, before `export_pdf.build_document_export`
        ever runs), and the previous `PdfWriter()` + per-page `add_page()`
        loop dropped an existing `/StructTreeRoot` unconditionally (confirmed
        empirically: found live during this session's own end-to-end
        verification, after `build_document_export`'s own fix alone still
        produced an untagged export). `PdfWriter(clone_from=reader)`
        preserves an existing struct tree exactly like `export_pdf.py`'s own
        fix, and `add_metadata()` afterward does not disturb it (also
        confirmed empirically)."""
        reader = PdfReader(BytesIO(data))
        writer = PdfWriter(clone_from=reader)
        writer.add_metadata(
            {
                "/Producer": "OG Doc Rendering Service - PDF-Archivkopie "
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
