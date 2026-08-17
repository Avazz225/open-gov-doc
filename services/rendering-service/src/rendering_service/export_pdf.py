from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# PDF export with export history and combined folder export (post-roadmap
# Phase 28, ADR 0107). Two-pass pipeline:
#
# Pass A (build_document_export, per document, independent of single vs.
# folder export): document PDF + export-history PDF concatenated in the
# configured order, then a LOCAL "i/N" footer stamp, N = this document's own
# page count - final and stable regardless of whether/how this document is
# later embedded in a folder export.
#
# Pass B (build_folder_export, folder export only): a table-of-contents
# section rendered first, cumulative page offsets computed from each
# document's already-known page count, then a SECOND, independent GLOBAL
# "j/total" footer plus a pypdf outline item (bookmark) per document. The
# local footer from Pass A is untouched - both footers live at different
# vertical offsets so neither overlaps the other.
#
# Footer stamping reuses the exact reportlab-overlay-merged-via-pypdf idiom
# already established in watermark.py (add_text_watermark), just as a small
# corner label instead of a diagonal stamp.

_TABLE_STYLE = TableStyle(
    [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
)


@dataclass
class ExportHistoryEntry:
    happened_at: datetime
    actor: str | None
    action: str


def render_history_pdf(document_title: str, entries: list[ExportHistoryEntry]) -> bytes:
    """Renders a document's export history as a simple table (Phase 28) -
    same reportlab platypus idiom as reporting-service's `to_pdf`
    (Table+TableStyle). Fed from audit-service's existing `document.exported`
    event log (see document-service's export endpoint) - no dedicated export
    history storage of its own."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(f"Export history: {document_title}", styles["Heading1"]),
        Spacer(1, 12),
    ]
    if entries:
        rows = [["Timestamp", "Actor", "Action"]] + [
            [entry.happened_at.strftime("%Y-%m-%d %H:%M:%S UTC"), entry.actor or "-", entry.action]
            for entry in entries
        ]
        table = Table(rows, hAlign="LEFT")
        table.setStyle(_TABLE_STYLE)
        elements.append(table)
    else:
        elements.append(Paragraph("No export history recorded yet.", styles["Normal"]))
    doc.build(elements)
    return buffer.getvalue()


def _stamp_footer(data: bytes, *, label_for_page: Callable[[int, int], str], y: float) -> bytes:
    reader = PdfReader(BytesIO(data))
    writer = PdfWriter(clone_from=reader)
    page_count = len(writer.pages)
    for index, page in enumerate(writer.pages):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay_buffer = BytesIO()
        stamp = canvas.Canvas(overlay_buffer, pagesize=(width, height))
        stamp.setFont("Helvetica", 8)
        stamp.setFillColorRGB(0.3, 0.3, 0.3)
        stamp.drawRightString(width - 24, y, label_for_page(index, page_count))
        stamp.save()
        overlay_buffer.seek(0)
        overlay_page = PdfReader(overlay_buffer).pages[0]
        page.merge_page(overlay_page)
    output_buffer = BytesIO()
    writer.write(output_buffer)
    return output_buffer.getvalue()


def build_document_export(
    *, document_pdf: bytes, history_pdf: bytes, history_position: str
) -> bytes:
    """Pass A - see module docstring. `history_position` is `"before"` or
    `"after"` (document-service's `ExportConfig`/per-request override,
    ADR 0107)."""
    sections = (
        [history_pdf, document_pdf] if history_position == "before" else [document_pdf, history_pdf]
    )
    writer = PdfWriter()
    for section in sections:
        writer.append(BytesIO(section))
    buffer = BytesIO()
    writer.write(buffer)
    merged = buffer.getvalue()
    return _stamp_footer(merged, label_for_page=lambda i, n: f"Page {i + 1}/{n}", y=16)


@dataclass
class FolderExportEntry:
    title: str
    export_pdf: bytes  # output of build_document_export


def _render_toc_pdf(entries_with_offsets: list[tuple[str, int]]) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Table of contents", styles["Heading1"]), Spacer(1, 12)]
    rows = [["Document", "Page"]] + [[title, str(offset)] for title, offset in entries_with_offsets]
    table = Table(rows, hAlign="LEFT")
    table.setStyle(_TABLE_STYLE)
    elements.append(table)
    doc.build(elements)
    return buffer.getvalue()


def build_folder_export(entries: list[FolderExportEntry]) -> bytes:
    """Pass B - see module docstring. Each entry's `export_pdf` is expected
    to already be the output of `build_document_export` (Pass A already
    applied). Document order in `entries` is the caller's responsibility
    (document-service orders by `kennzeichen`, not title)."""
    page_counts = [len(PdfReader(BytesIO(entry.export_pdf)).pages) for entry in entries]

    # The TOC's own page count depends only on the entry count (title/page
    # columns), not on the offsets it will end up displaying - render a
    # throwaway probe first purely to learn how many pages the real TOC
    # will occupy before the first document.
    toc_probe = _render_toc_pdf([(entry.title, 0) for entry in entries])
    toc_page_count = len(PdfReader(BytesIO(toc_probe)).pages)

    offsets = []
    running = toc_page_count + 1
    for count in page_counts:
        offsets.append(running)
        running += count

    toc_pdf = _render_toc_pdf(
        [(entry.title, offset) for entry, offset in zip(entries, offsets, strict=True)]
    )

    writer = PdfWriter()
    writer.append(BytesIO(toc_pdf))
    for entry in entries:
        start_index = len(writer.pages)
        writer.append(BytesIO(entry.export_pdf))
        writer.add_outline_item(entry.title, start_index)

    buffer = BytesIO()
    writer.write(buffer)
    combined = buffer.getvalue()
    return _stamp_footer(combined, label_for_page=lambda i, n: f"Page {i + 1}/{n} (overall)", y=28)
