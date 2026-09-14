# 0136 — PDF/UA tag preservation on export: the real root cause was upstream of the export merge

**Status:** accepted (P33-S2, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 33 Session 2 (accessibility completion, following up on ADR 0119/P31-S8), affects
`rendering-service`

## Decision

An already-tagged PDF source now keeps its `/StructTreeRoot` through `document-service`'s single-document
export (`POST /documents/{id}/export`). Two independent fixes, both using `PdfWriter(clone_from=<reader>)`
instead of an empty `PdfWriter()` fed page-by-page or via `.append()`:

1. **`PdfArchiveRenderer._tag_pdf`** (`renderers/pdf_archive.py`) — the actual root cause, found live during
   this session's own end-to-end verification (see "Rationale"). Every already-PDF export first passes
   through this method (`main.py`'s `render_export_document` calls it via the shared renderer dispatch,
   `content_type == "application/pdf"`), which previously rebuilt the file via `PdfWriter()` +
   `writer.add_page(page)` per page — dropping any existing struct tree unconditionally, before
   `export_pdf.build_document_export` ever saw the data.
2. **`export_pdf.build_document_export`** (`export_pdf.py`) — the export-history merge itself. Was
   `PdfWriter().append(section)` for each of `[document_pdf, history_pdf]` (order depending on
   `history_position`); now `PdfWriter(clone_from=<document reader>)` first, with `history_pdf` merged in
   afterward via `.merge(0, ...)` (prepend) or `.append(...)` (append) depending on `history_position`.

`build_folder_export` (the multi-document combined export) is **unchanged and still drops tags** — a
deliberate, documented scope boundary, not an oversight (see "Rationale").

## Rationale

- **The plan's own framing ("the export pipeline never produces a tagged PDF regardless of input... pypdf's
  writer has no structure-tree-copying capability at all", ADR 0119) turned out to be a correct symptom
  description but an imprecise root-cause claim** — confirmed empirically (this session's own probing,
  pypdf 6.14.2): `PdfWriter(clone_from=reader)` DOES preserve an existing `/StructTreeRoot` faithfully;
  only `PdfWriter().append(...)`/`.add_page(...)` (rebuilding from an empty writer) drops it. Fixing only
  `build_document_export` (the change made first, following the plan's literal wording about the "export
  pipeline's merge/stamp pass") produced an export that still tested `is_tagged_pdf() == False` end-to-end
  against the real, rebuilt stack — surfacing that an EARLIER, unrelated-looking step
  (`PdfArchiveRenderer._tag_pdf`, part of the "convert to PDF" dispatch, not the merge/stamp pass at all)
  was the actual point of loss. Live, end-to-end verification (not just the pure-function unit tests) is
  what caught this — the unit-level fix looked complete and passed its own tests in isolation.
- **`_tag_pdf`'s name is a false cognate with PDF/UA "tagging"**: it has always meant "mark this PDF as an
  archival copy" (`/Producer`/`/Title` metadata, for the records-disposal substitute-representation feature,
  3.7/5.6) — an unrelated, much older concept that happens to share the English word "tag". This
  naming collision likely contributed to the original ADR 0119 research not identifying it as the actual
  culprit.
- **Both fixes use the identical `clone_from`-first technique, discovered once and applied twice**: adding
  further pages to an already-`clone_from`-populated writer via `.merge()`/`.append()` does not clear its
  existing struct tree (confirmed empirically) — the mechanism generalizes cleanly to both call sites
  without new code, just a different point of construction.
- **`build_folder_export` deliberately stays unfixed**: it combines a TOC page plus N independent documents'
  already-exported PDFs into one writer via `.append()` for every entry after the first — even with the
  `clone_from`-first technique, only ONE source's struct tree could ever survive that way (whichever becomes
  the initial `clone_from`), and preserving more than one document's tags in a single merged PDF would need
  actual structure-tree merging across independent sources (remapping `/StructParents`/marked-content
  references per page under one coherent parent tree) — a capability pypdf does not provide. A partial fix
  where document #1 keeps its tags and documents #2..N silently don't would be a worse, more misleading
  outcome than the current, consistent "folder export never preserves tags" — matching this project's
  established preference for honestly documented limits over partial, confusing completeness. Locked in
  with a dedicated regression test (`test_build_folder_export_still_drops_struct_tree_known_limitation`)
  so a future refactor doesn't silently produce that half-fixed, worse state.
- **No frontend change needed**: `user-ui`'s existing accessibility warning (`PreviewPane.tsx`, shown when
  `!accessibilityCheck.is_tagged`) already only fires for an UNtagged source ("your source has no structure,
  so the export won't either") — its polarity was never about "we'll strip your existing tags," so it
  remains accurate and unaffected by this fix; a tagged source's export no longer loses its tags, and the
  warning correctly stays silent for it, exactly as before.
- **`PdfArchiveRenderer` is also used by the separate records-disposal archival-copy feature (5.6, not the
  export flow)** — the same fix benefits that path too, as a natural consequence of fixing the shared
  method, not a deliberately expanded scope.

## Consequences

- **Single-document export now genuinely preserves an already-tagged source's accessibility structure** —
  the real gap ADR 0119's warning could only flag, not fix, is closed for the common case (exporting one
  document).
- **Folder export (`build_folder_export`) still drops tags for every constituent document, regardless of
  whether any of them were individually tagged** — an honestly documented, unchanged limitation; no UI
  warning exists for the folder-export flow either way (out of scope, matches ADR 0119's own boundary).
- **No formal PDF/UA conformance validation was added** — `is_tagged_pdf`'s `/StructTreeRoot`-presence check
  remains the same documented approximation as before (no veraPDF integration, same limitation
  `docs/services/rendering-service.md` "Open Points" already names for PDF/A).
- **Tests**: `rendering-service` +5 (98 total, up from 93): `test_export_pdf.py` gained 3
  (`build_document_export` preserves tags for both `history_position` values, leaves an untagged source
  untagged) and 1 folder-export regression-lock test; `test_renderers.py` gained 1
  (`PdfArchiveRenderer` preserves a tagged source's struct tree). Live-verified end-to-end against the real,
  rebuilt running stack: a manually tagged PDF was uploaded, exported, and confirmed to retain
  `/StructTreeRoot` in the downloaded result — the fix was iterated live (found the first fix alone was
  insufficient via this exact live check, before the second, real root cause was found and fixed) rather
  than declared complete on unit tests alone. An untagged PDF's export was also re-verified to behave
  identically to before (no regression).
