from datetime import UTC, datetime
from io import BytesIO

from pypdf import PdfReader
from rendering_service.export_pdf import (
    ExportHistoryEntry,
    FolderExportEntry,
    build_document_export,
    build_folder_export,
    render_history_pdf,
)
from reportlab.pdfgen import canvas


def _real_pdf(pages: int = 2, text: str = "Page") -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    for i in range(pages):
        c.drawString(10, 100, f"{text} {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _page_text(data: bytes, index: int) -> str:
    return PdfReader(BytesIO(data)).pages[index].extract_text()


def test_render_history_pdf_empty_history():
    result = render_history_pdf("Contract.pdf", [])
    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 1
    assert "No export history" in reader.pages[0].extract_text()


def test_render_history_pdf_with_entries():
    entries = [
        ExportHistoryEntry(datetime(2026, 1, 2, tzinfo=UTC), "alice", "exported"),
        ExportHistoryEntry(datetime(2026, 1, 3, tzinfo=UTC), "bob", "exported"),
    ]
    result = render_history_pdf("Contract.pdf", entries)
    reader = PdfReader(BytesIO(result))
    text = reader.pages[0].extract_text()
    assert "alice" in text
    assert "bob" in text


def test_build_document_export_history_after_appends_history_last():
    document_pdf = _real_pdf(pages=2, text="Doc")
    history_pdf = render_history_pdf("Doc.pdf", [])

    result = build_document_export(
        document_pdf=document_pdf, history_pdf=history_pdf, history_position="after"
    )

    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 3
    assert "Doc 1" in reader.pages[0].extract_text()
    assert "Doc 2" in reader.pages[1].extract_text()
    assert "No export history" in reader.pages[2].extract_text()


def test_build_document_export_history_before_prepends_history():
    document_pdf = _real_pdf(pages=1, text="Doc")
    history_pdf = render_history_pdf("Doc.pdf", [])

    result = build_document_export(
        document_pdf=document_pdf, history_pdf=history_pdf, history_position="before"
    )

    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 2
    assert "No export history" in reader.pages[0].extract_text()
    assert "Doc 1" in reader.pages[1].extract_text()


def test_build_document_export_stamps_stable_local_page_numbers():
    document_pdf = _real_pdf(pages=2, text="Doc")
    history_pdf = _real_pdf(pages=1, text="Hist")

    result = build_document_export(
        document_pdf=document_pdf, history_pdf=history_pdf, history_position="after"
    )

    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 3
    assert "Page 1/3" in reader.pages[0].extract_text()
    assert "Page 2/3" in reader.pages[1].extract_text()
    assert "Page 3/3" in reader.pages[2].extract_text()


def test_build_folder_export_has_toc_and_stable_local_numbers_plus_global_numbers():
    doc_a = build_document_export(
        document_pdf=_real_pdf(pages=2, text="A"),
        history_pdf=_real_pdf(pages=1, text="AHist"),
        history_position="after",
    )
    doc_b = build_document_export(
        document_pdf=_real_pdf(pages=1, text="B"),
        history_pdf=_real_pdf(pages=1, text="BHist"),
        history_position="after",
    )

    result = build_folder_export(
        [
            FolderExportEntry(title="A.pdf", export_pdf=doc_a),
            FolderExportEntry(title="B.pdf", export_pdf=doc_b),
        ]
    )

    reader = PdfReader(BytesIO(result))
    # 1 TOC page + 3 pages for A (2 doc + 1 history) + 2 pages for B (1 doc + 1 history).
    assert len(reader.pages) == 6

    toc_text = reader.pages[0].extract_text()
    assert "A.pdf" in toc_text
    assert "B.pdf" in toc_text

    # Local footers from Pass A survive untouched inside the combined PDF.
    assert "Page 1/3" in reader.pages[1].extract_text()
    assert "Page 3/3" in reader.pages[3].extract_text()
    assert "Page 1/2" in reader.pages[4].extract_text()

    # Global footer (Pass B) is additionally present on every page, at a
    # different vertical position than the local one, and reflects the
    # combined total.
    assert "Page 1/6 (overall)" in reader.pages[0].extract_text()
    assert "Page 6/6 (overall)" in reader.pages[5].extract_text()

    outline = reader.outline
    assert len(outline) == 2
    assert outline[0].title == "A.pdf"
    assert outline[1].title == "B.pdf"
    # A.pdf's bookmark points at page index 1 (right after the 1-page TOC).
    assert reader.get_destination_page_number(outline[0]) == 1
    # B.pdf's bookmark points at page index 4 (1 TOC + 3 pages for A).
    assert reader.get_destination_page_number(outline[1]) == 4
