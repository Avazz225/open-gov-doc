from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    PdfObject,
)
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


def is_tagged_pdf(data: bytes) -> bool:
    """Accessibility pass (14.2, post-roadmap phase 31 session 8): whether a
    PDF carries a structure tree (`/StructTreeRoot` in its document catalog)
    - the actual technical basis of a "tagged"/accessibility-oriented PDF,
    not merely "is a PDF". Used to warn before export (see main.py's
    `/render/pdf-tag-check`), not to validate full PDF/UA conformance (no
    veraPDF integration here, same documented limitation as the existing
    PDF/A-without-ISO-19005-validation gap, docs/services/rendering-
    service.md "Open Points"). Malformed/unreadable input is reported as
    untagged rather than raising - content that can't even be parsed is
    certainly not a valid tagged PDF."""
    try:
        reader = PdfReader(BytesIO(data))
        return "/StructTreeRoot" in reader.root_object
    except Exception:
        return False


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
    ADR 0107).

    Post-roadmap phase 33 session 2 (ADR 0136, PDF/UA tag preservation): the
    writer is constructed via `PdfWriter(clone_from=<document reader>)`
    rather than `PdfWriter().append(document_pdf)` - confirmed empirically
    (this session's own probing, pypdf 6.14.2) that `clone_from` preserves
    an already-present `/StructTreeRoot`, while `.append()`/`.merge()` never
    contribute a source's own structure tree, regardless of merge order.
    Adding further pages to an already-`clone_from`-populated writer via
    `.merge()`/`.append()` does NOT clear its existing struct tree (also
    confirmed empirically) - so the history page(s) are merged in
    afterward, at the correct position for either `history_position`,
    without disturbing a tagged document's own tags. `history_pdf` itself
    is never tagged (rendered by `render_history_pdf`/reportlab, which
    cannot produce a structure tree) - only the document's own tags, if
    any, are ever at stake here."""
    reader = PdfReader(BytesIO(document_pdf))
    writer = PdfWriter(clone_from=reader)
    if history_position == "before":
        writer.merge(0, BytesIO(history_pdf))
    else:
        writer.append(BytesIO(history_pdf))
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


def _read_number_tree(node: DictionaryObject, out: dict[int, PdfObject]) -> None:
    """Flattens a PDF "number tree" (spec 7.9.7) into a plain dict - used
    for `/StructTreeRoot/ParentTree`. Handles both encodings the spec
    allows: a flat leaf (`/Nums` only) and an intermediate node (`/Kids`,
    each with its own `/Limits` range) - real-world tagged PDFs from
    different producers use either, so both must be read, even though the
    merged tree this module WRITES is always a single flat leaf (simpler,
    still spec-valid regardless of size, see `_merge_struct_trees`)."""
    nums = node.get("/Nums")
    if nums is not None:
        nums_obj = nums.get_object()
        for i in range(0, len(nums_obj), 2):
            out[int(nums_obj[i])] = nums_obj[i + 1]
    kids = node.get("/Kids")
    if kids is not None:
        for kid_ref in kids.get_object():
            _read_number_tree(kid_ref.get_object(), out)


def _merge_struct_trees(
    writer: PdfWriter, sources: list[tuple[PdfReader, list[PdfObject]]]
) -> None:
    """Real, structural PDF/UA tag preservation across MULTIPLE independent
    source documents merged into one `writer` (Post-Roadmap Phase 42
    Session 4 - see [ADR 0158](../../../docs/adr/0158-multi-document-struct-tree-merge.md)
    for the full spec background/rationale this docstring summarizes).
    `build_document_export`'s `clone_from` technique (ADR 0136, P33-S2) only
    ever preserves ONE source's entire struct tree at writer-construction
    time - it cannot be called again for a second source without discarding
    the first. This function instead builds a genuinely new, correctly
    merged `/StructTreeRoot` by hand, after all sources have already been
    `.append()`-ed onto `writer` in the normal way.

    `sources` pairs each already-appended `PdfReader` with the NEW page
    objects it produced in `writer`, in the same order as `reader.pages` -
    the caller must have appended them in that same order, immediately
    before calling this function, for the trick below to work.

    The key mechanism this relies on: `PdfObject.clone(writer)` (pypdf's own
    general-purpose object-graph clone, used internally by `.append()`
    itself) checks `writer._id_translated[id(source_reader)]` for an
    existing translation before creating a new object. Since each source's
    PAGES were already cloned into `writer` by the preceding `.append()`
    call, cloning anything else reachable from that SAME reader instance -
    in particular a structure element's `/Pg` back-reference to one of its
    own pages - transparently resolves to the page ALREADY appended, with
    no manual old-page-to-new-page remapping needed (confirmed empirically
    against pypdf 6.14.2, not merely assumed from its docs).

    What cloning does NOT handle, and this function must, is
    `/StructParents`/`/StructParent` - unlike `/Pg` these are plain integers,
    not indirect references, and they are keys into a single, PER-DOCUMENT
    `/ParentTree`. Each source numbered its own pages independently
    (typically starting at 0), so blindly keeping those numbers would let a
    later source's key silently collide with and overwrite an earlier
    source's `/ParentTree` entry once both live in the same merged document.
    This function renumbers every page's `/StructParents` with one running
    counter shared across all sources, and rebuilds `/ParentTree` to match.

    Untagged sources (no `/StructTreeRoot`) contribute nothing - their pages
    simply keep no `/StructParents` at all, exactly as before this session.
    If NO source is tagged, `writer`'s root is left untouched (no empty/
    fabricated `/StructTreeRoot` is ever added) - unchanged, pre-existing
    behavior for an all-untagged folder.

    Deliberately out of scope (see ADR 0158 "Consequences"): annotation-level
    `/StructParent` (singular, e.g. on tagged form-field widgets) is not
    remapped - this project's rendering pipeline (LibreOffice conversion,
    OCR, scans) does not produce annotation-level structure tags in
    practice, only page-content `/StructParents` (plural), so this is a
    documented, narrow, deliberate scope cut rather than an oversight."""
    merged_k = ArrayObject()
    merged_nums: list[tuple[int, PdfObject]] = []
    next_key = 0
    any_tagged = False

    for reader, new_pages in sources:
        struct_root_entry = reader.trailer["/Root"].get("/StructTreeRoot")
        if struct_root_entry is None:
            continue
        any_tagged = True
        struct_root = struct_root_entry.get_object()

        old_k = struct_root.get("/K")
        if old_k is not None:
            old_k_obj = old_k.get_object()
            children = old_k_obj if isinstance(old_k_obj, ArrayObject) else [old_k]
            for child in children:
                merged_k.append(child.clone(writer))

        parent_tree_entry = struct_root.get("/ParentTree")
        if parent_tree_entry is None:
            continue
        old_nums: dict[int, PdfObject] = {}
        _read_number_tree(parent_tree_entry.get_object(), old_nums)
        for page_index, new_page in enumerate(new_pages):
            old_struct_parents = reader.pages[page_index].get("/StructParents")
            if old_struct_parents is None:
                continue
            old_value = old_nums.get(int(old_struct_parents))
            if old_value is None:
                continue
            cloned_value = old_value.clone(writer) if hasattr(old_value, "clone") else old_value
            new_page[NameObject("/StructParents")] = NumberObject(next_key)
            merged_nums.append((next_key, cloned_value))
            next_key += 1

    if not any_tagged:
        return

    nums_array = ArrayObject()
    for key, value in merged_nums:
        nums_array.append(NumberObject(key))
        nums_array.append(value)
    parent_tree = DictionaryObject({NameObject("/Nums"): nums_array})
    parent_tree_ref = writer._add_object(parent_tree)

    struct_tree_root = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/StructTreeRoot"),
            NameObject("/K"): merged_k,
            NameObject("/ParentTree"): parent_tree_ref,
            NameObject("/ParentTreeNextKey"): NumberObject(next_key),
        }
    )
    struct_tree_root_ref = writer._add_object(struct_tree_root)

    # Top-level structure elements' own `/P` (parent) link currently still
    # points at whichever source's OLD `/StructTreeRoot` they were cloned
    # from (a stray, now-orphaned clone of it, harmless but wrong) - only
    # the TOP-level entries need correcting to point at the new MERGED root
    # instead. Nested `/P` links between structure elements further down
    # need no fixing - they were cloned consistently as part of the same
    # subgraph and already point at each other's correctly cloned copies.
    for child_ref in merged_k:
        child = child_ref.get_object()
        if isinstance(child, DictionaryObject):
            child[NameObject("/P")] = struct_tree_root_ref

    writer._root_object[NameObject("/StructTreeRoot")] = struct_tree_root_ref
    if "/MarkInfo" not in writer._root_object:
        writer._root_object[NameObject("/MarkInfo")] = writer._add_object(
            DictionaryObject({NameObject("/Marked"): BooleanObject(True)})
        )


def build_folder_export(entries: list[FolderExportEntry]) -> bytes:
    """Pass B - see module docstring. Each entry's `export_pdf` is expected
    to already be the output of `build_document_export` (Pass A already
    applied). Document order in `entries` is the caller's responsibility
    (document-service orders by `kennzeichen`, not title).

    Post-Roadmap Phase 42 Session 4: unlike Pass A's single-source
    `clone_from`, this pass genuinely merges every tagged source's structure
    tree into one coherent, correctly renumbered `/StructTreeRoot` - see
    `_merge_struct_trees` for the mechanism. `PdfReader` instances (not raw
    `BytesIO`) are constructed and kept here (rather than letting
    `writer.append()` build and discard its own internally) specifically so
    `_merge_struct_trees` can read each source's OWN structure tree/pages
    afterward from the SAME reader instance `.append()` used - required for
    pypdf's clone-cache-based `/Pg` remapping trick to apply."""
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
    struct_sources: list[tuple[PdfReader, list[PdfObject]]] = []

    toc_reader = PdfReader(BytesIO(toc_pdf))
    writer.append(toc_reader)
    struct_sources.append((toc_reader, list(writer.pages[: len(toc_reader.pages)])))

    for entry in entries:
        start_index = len(writer.pages)
        entry_reader = PdfReader(BytesIO(entry.export_pdf))
        writer.append(entry_reader)
        writer.add_outline_item(entry.title, start_index)
        struct_sources.append(
            (entry_reader, list(writer.pages[start_index : start_index + len(entry_reader.pages)]))
        )

    _merge_struct_trees(writer, struct_sources)

    buffer = BytesIO()
    writer.write(buffer)
    combined = buffer.getvalue()
    return _stamp_footer(combined, label_for_page=lambda i, n: f"Page {i + 1}/{n} (overall)", y=28)
