from datetime import UTC, datetime
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject
from rendering_service.export_pdf import (
    ExportHistoryEntry,
    FolderExportEntry,
    build_document_export,
    build_folder_export,
    is_tagged_pdf,
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


def _add_struct_tree_root(data: bytes) -> bytes:
    """Test-only helper (no `reportlab`/`pypdf` writer feature produces a
    real tagged PDF short of a full structure tree) - adds a minimal,
    otherwise-empty `/StructTreeRoot` dictionary directly onto the writer's
    document catalog. Enough for `is_tagged_pdf`'s presence check, not a
    real, navigable tag tree."""
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    struct_tree_root = writer._add_object(DictionaryObject())
    writer._root_object[NameObject("/StructTreeRoot")] = struct_tree_root
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _add_real_struct_tree(data: bytes, *, tag: str = "/P") -> bytes:
    """Post-Roadmap Phase 42 Session 4: unlike `_add_struct_tree_root`
    (bare presence marker, no navigable content - sufficient for
    `is_tagged_pdf`'s own tests), this builds a REAL, minimal-but-genuine
    one-element structure tree: one `/StructElem` of type `tag` under
    `/StructTreeRoot`, linked to page 0 via a real `/ParentTree` entry keyed
    off that page's `/StructParents` - exactly the shape needed to test
    genuine cross-document structure-tree MERGING (element identity,
    `/Pg` back-references, `/ParentTree` key resolution), not just
    `/StructTreeRoot` presence. The page's content stream is deliberately
    NOT made to actually contain a matching marked-content operator for the
    declared MCID - the merge logic under test operates purely on the
    object graph (`/K`/`/Pg`/`/ParentTree`), never on content-stream bytes,
    so this simplification does not weaken what these tests verify."""
    writer = PdfWriter(clone_from=PdfReader(BytesIO(data)))
    page = writer.pages[0]

    struct_elem = DictionaryObject()
    struct_elem[NameObject("/Type")] = NameObject("/StructElem")
    struct_elem[NameObject("/S")] = NameObject(tag)
    struct_elem[NameObject("/Pg")] = page.indirect_reference
    struct_elem[NameObject("/K")] = NumberObject(0)
    struct_elem_ref = writer._add_object(struct_elem)

    struct_tree_root = DictionaryObject()
    struct_tree_root[NameObject("/Type")] = NameObject("/StructTreeRoot")
    struct_tree_root[NameObject("/K")] = ArrayObject([struct_elem_ref])
    struct_tree_root_ref = writer._add_object(struct_tree_root)
    struct_elem[NameObject("/P")] = struct_tree_root_ref

    parent_tree = DictionaryObject()
    parent_tree[NameObject("/Nums")] = ArrayObject(
        [NumberObject(0), ArrayObject([struct_elem_ref])]
    )
    parent_tree_ref = writer._add_object(parent_tree)
    struct_tree_root[NameObject("/ParentTree")] = parent_tree_ref
    struct_tree_root[NameObject("/ParentTreeNextKey")] = NumberObject(1)

    page[NameObject("/StructParents")] = NumberObject(0)
    writer._root_object[NameObject("/StructTreeRoot")] = struct_tree_root_ref

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _resolve_struct_tag_for_page(reader: PdfReader, page_index: int) -> str | None:
    """Test helper: follows a real reader's page `/StructParents` through
    its `/ParentTree` to the owning `/StructElem`'s `/S` tag name - the same
    resolution path a real assistive-technology consumer (or a PDF/UA
    validator) would perform, used here to verify the MERGED document's
    structure tree is not just present but actually internally consistent
    and correctly attributes each page to the right source document."""
    struct_parents = reader.pages[page_index].get("/StructParents")
    if struct_parents is None:
        return None
    struct_root = reader.trailer["/Root"]["/StructTreeRoot"]
    parent_tree = struct_root["/ParentTree"]
    nums = parent_tree["/Nums"]
    for i in range(0, len(nums), 2):
        if int(nums[i]) == int(struct_parents):
            entry = nums[i + 1].get_object()
            elem = entry[0].get_object()
            return elem.get("/S")
    return None


def test_is_tagged_pdf_false_for_untagged_pdf():
    assert is_tagged_pdf(_real_pdf(pages=1)) is False


def test_is_tagged_pdf_true_when_struct_tree_root_present():
    assert is_tagged_pdf(_add_struct_tree_root(_real_pdf(pages=1))) is True


def test_is_tagged_pdf_false_for_garbage_bytes():
    """Malformed/unparseable input is reported as untagged, not raised -
    see `is_tagged_pdf`'s own docstring."""
    assert is_tagged_pdf(b"not a pdf at all") is False


def test_is_tagged_pdf_false_for_empty_bytes():
    assert is_tagged_pdf(b"") is False


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


def test_build_document_export_preserves_struct_tree_history_after():
    """PDF/UA tag preservation (post-roadmap phase 33 session 2, ADR 0136):
    a tagged source document keeps its `/StructTreeRoot` through the export
    merge - the writer is now constructed via `clone_from` (preserves an
    existing struct tree) instead of `.append()` (never does, confirmed
    empirically for this session)."""
    document_pdf = _add_struct_tree_root(_real_pdf(pages=2, text="Doc"))
    history_pdf = render_history_pdf("Doc.pdf", [])

    result = build_document_export(
        document_pdf=document_pdf, history_pdf=history_pdf, history_position="after"
    )

    assert is_tagged_pdf(result) is True
    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 3
    assert "Doc 1" in reader.pages[0].extract_text()
    assert "No export history" in reader.pages[2].extract_text()


def test_build_document_export_preserves_struct_tree_history_before():
    """Same guarantee regardless of `history_position` - the history page is
    prepended via `.merge(0, ...)`, not by making it the writer's own
    `clone_from` source, so the document's struct tree survives either
    way."""
    document_pdf = _add_struct_tree_root(_real_pdf(pages=1, text="Doc"))
    history_pdf = render_history_pdf("Doc.pdf", [])

    result = build_document_export(
        document_pdf=document_pdf, history_pdf=history_pdf, history_position="before"
    )

    assert is_tagged_pdf(result) is True
    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 2
    assert "No export history" in reader.pages[0].extract_text()
    assert "Doc 1" in reader.pages[1].extract_text()


def test_build_document_export_leaves_untagged_source_untagged():
    """Regression guard: an untagged source stays untagged (no tags are
    fabricated), same behavior as before this session."""
    document_pdf = _real_pdf(pages=1, text="Doc")
    history_pdf = render_history_pdf("Doc.pdf", [])

    result = build_document_export(
        document_pdf=document_pdf, history_pdf=history_pdf, history_position="after"
    )

    assert is_tagged_pdf(result) is False


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


def test_build_folder_export_preserves_struct_tree_for_a_single_tagged_document():
    """Post-Roadmap Phase 42 Session 4 (ADR 0158): the single-document case
    (one tagged document, no other tagged sources) now correctly comes out
    tagged too - previously dropped unconditionally regardless of source
    count (the old, now-replaced "known limitation" test locked in exactly
    that unconditional drop)."""
    doc_a = build_document_export(
        document_pdf=_add_real_struct_tree(_real_pdf(pages=1, text="A"), tag="/P"),
        history_pdf=_real_pdf(pages=1, text="AHist"),
        history_position="after",
    )
    assert is_tagged_pdf(doc_a) is True

    result = build_folder_export([FolderExportEntry(title="A.pdf", export_pdf=doc_a)])

    assert is_tagged_pdf(result) is True


def test_build_folder_export_merges_struct_trees_from_multiple_tagged_documents():
    """The actual multi-document merge (ADR 0158): two independently tagged
    source documents, each numbering its own `/StructParents` starting at 0
    (as real producers typically do) - without renumbering, document B's
    key would collide with and shadow document A's `/ParentTree` entry.
    Verifies not just `is_tagged_pdf()` (mere presence) but that each merged
    page's structure tag can genuinely be resolved back to its OWN source's
    tag type, via a real `/StructParents` -> `/ParentTree` -> `/StructElem`
    walk (`_resolve_struct_tag_for_page`) - the same path a screen reader or
    a PDF/UA validator would take."""
    doc_a = build_document_export(
        document_pdf=_add_real_struct_tree(_real_pdf(pages=1, text="A"), tag="/P"),
        history_pdf=_real_pdf(pages=1, text="AHist"),
        history_position="after",
    )
    doc_b = build_document_export(
        document_pdf=_add_real_struct_tree(_real_pdf(pages=1, text="B"), tag="/H1"),
        history_pdf=_real_pdf(pages=1, text="BHist"),
        history_position="after",
    )

    result = build_folder_export(
        [
            FolderExportEntry(title="A.pdf", export_pdf=doc_a),
            FolderExportEntry(title="B.pdf", export_pdf=doc_b),
        ]
    )

    assert is_tagged_pdf(result) is True
    reader = PdfReader(BytesIO(result))
    # 1 TOC page + 2 pages for A (doc + history) + 2 pages for B (doc + history).
    assert len(reader.pages) == 5

    # TOC page (index 0) and both untagged history pages (indices 2, 4) have
    # no /StructParents at all - nothing fabricated for untagged content.
    assert reader.pages[0].get("/StructParents") is None
    assert reader.pages[2].get("/StructParents") is None
    assert reader.pages[4].get("/StructParents") is None

    # Each document's own tagged page (index 1 for A, index 3 for B)
    # resolves to its OWN, distinct tag type - not swapped, not collided.
    assert _resolve_struct_tag_for_page(reader, 1) == "/P"
    assert _resolve_struct_tag_for_page(reader, 3) == "/H1"

    # No /StructParents collision: both source documents used key 0
    # internally, but the merged document's two tagged pages must have been
    # renumbered to two DIFFERENT keys.
    assert reader.pages[1].get("/StructParents") != reader.pages[3].get("/StructParents")


def test_build_folder_export_leaves_untagged_folder_untagged():
    """Regression guard: when no source is tagged at all, no empty/
    fabricated `/StructTreeRoot` is added - unchanged, pre-existing
    behavior."""
    doc_a = build_document_export(
        document_pdf=_real_pdf(pages=1, text="A"),
        history_pdf=_real_pdf(pages=1, text="AHist"),
        history_position="after",
    )

    result = build_folder_export([FolderExportEntry(title="A.pdf", export_pdf=doc_a)])

    assert is_tagged_pdf(result) is False


def test_build_folder_export_preserves_tags_for_the_tagged_document_in_a_mixed_folder():
    """A folder with one tagged and one untagged document: the tagged
    document's page correctly keeps its tag, the untagged document's page
    correctly gets none - a real, partial-by-CONTENT (not partial-by-bug)
    result, since there is genuinely nothing to preserve for the untagged
    one."""
    doc_a = build_document_export(
        document_pdf=_add_real_struct_tree(_real_pdf(pages=1, text="A"), tag="/P"),
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

    assert is_tagged_pdf(result) is True
    reader = PdfReader(BytesIO(result))
    assert _resolve_struct_tag_for_page(reader, 1) == "/P"
    assert reader.pages[3].get("/StructParents") is None
