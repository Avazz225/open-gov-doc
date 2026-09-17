# 0158 — Multi-document PDF/UA structure-tree merge in folder export

**Status:** accepted (P42-S4, see Phase 38+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 42 Session 4 (remaining functional completion, following up on ADR 0136/P33-S2),
affects `rendering-service`

## Decision

`export_pdf.build_folder_export` now genuinely merges every constituent document's `/StructTreeRoot`
into one correctly renumbered structure tree in the combined output, instead of dropping all of them
unconditionally regardless of source count (ADR 0136's deliberate, at-the-time-correct call). Untagged
sources still contribute nothing and an all-untagged folder still produces an untagged output — nothing
is fabricated.

**Asked the user at session start** whether to (a) build a genuine partial fix limited to the
single-tagged-document case, (b) build real multi-document structure-tree merging, or (c) reaffirm the
gap as out of scope with stronger documentation — since research confirmed no pypdf/PyMuPDF/pikepdf
capability solves this, and ADR 0136 had already explicitly rejected a partial fix as worse than the
status quo. **The user chose (b)**: build the real merge.

The mechanism, implemented in a new `export_pdf._merge_struct_trees(writer, sources)`, called from
`build_folder_export` after all entries have been `.append()`-ed onto the combined `writer`:

1. For each tagged source, its top-level `/K` structure elements are cloned into `writer` via pypdf's own
   `PdfObject.clone(writer)` (the same general-purpose object-graph clone `.append()` already uses
   internally for pages). Cloning is keyed by `writer._id_translated[id(source_reader)]` — since each
   source's PAGES were already cloned into `writer` by the preceding `.append()` call, cloning anything
   *else* reachable from that same reader instance (in particular a structure element's `/Pg`
   back-reference to one of its own pages) transparently resolves to the page already appended, with
   **no manual page remapping needed**. Confirmed empirically against the actual installed pypdf version
   (6.14.2) with a full round-trip probe (build two independently tagged documents, merge, write, re-read
   from scratch, walk the resulting object graph) before writing any production code — not assumed from
   pypdf's documentation, which does not describe this clone-cache reuse explicitly.
2. What cloning does *not* handle is `/StructParents`/`/ParentTree` — these are plain integers, not
   indirect references, and they are keys into a single, per-document `/ParentTree` number tree (PDF
   spec 7.9.7/14.7.4.4). Each source numbers its own pages independently, typically starting at 0, so
   naively keeping those numbers would let a later source's key silently collide with and shadow an
   earlier source's `/ParentTree` entry once both live in the same merged document. `_merge_struct_trees`
   renumbers every tagged page's `/StructParents` with one running counter shared across all sources, and
   rebuilds `/ParentTree` from scratch as a single flat `/Nums` leaf (a number tree with no `/Kids` is
   fully spec-valid regardless of size — real producers use this shape too for moderately sized
   documents, and this project's folder exports are not expected to reach the scale where that stops
   being reasonable).
3. Top-level cloned structure elements' `/P` (parent) link is corrected to point at the new, merged
   `/StructTreeRoot` — cloning otherwise leaves it pointing at a stray, orphaned clone of the *source's
   own* old root. Nested `/P` links between structure elements further down the tree need no correction,
   since they were cloned consistently as part of the same subgraph.

## Rationale

- **No available library solves this** (confirmed via research before any implementation decision):
  `pypdf`'s `PdfWriter(clone_from=...)` only clones ONE source's entire structure tree at
  writer-construction time and cannot be invoked a second time without discarding the first;
  `.append()`/`.merge()` afterward never contribute a second source's tree (the exact limitation ADR 0136
  already documented). `PyMuPDF`/`fitz` (already a dependency, used elsewhere in this service for
  redaction) has the same category of gap in its own merge primitive (`insert_pdf()`), and `pikepdf`
  (not a dependency) would not solve it either — qpdf's own page-splicing doesn't perform structure-tree
  remapping across documents. A genuine fix therefore necessarily means direct, low-level manipulation of
  the PDF object graph — effectively implementing a missing SDK feature — not a small change.
- **The manual merge turned out to be tractable, not a from-scratch reimplementation of PDF internals**,
  because pypdf already exposes exactly the right low-level primitives (`PdfObject.clone()`, the
  `_id_translated` clone-cache, direct `DictionaryObject`/`ArrayObject` construction) and — the load-bearing
  discovery of this session — the clone-cache is reused automatically across independent `.clone()` calls
  against the same reader, which is what makes `/Pg` remapping free instead of a second manual bookkeeping
  problem. Only `/StructParents`/`/ParentTree` renumbering needed to be written by hand; everything else
  (page-content back-references, nested structure-element parent links, element identity/deduplication)
  falls out of pypdf's existing clone mechanism for free.
- **Verified end-to-end before writing any test**, via a standalone probe script building two
  independently tagged single-page PDFs (each internally numbering `/StructParents` from 0, as real
  producers typically do), merging them, writing to bytes, and re-reading from a **fresh** `PdfReader`
  with no shared in-memory object graph with the writer — confirming the merged tree is not just
  "present" but internally self-consistent: each page's renumbered `/StructParents` resolves through the
  rebuilt `/ParentTree` to the correct structure element, `/Pg` back-references point at the correct
  page (not swapped between sources), and top-level `/P` links point at the new merged root. This
  matches the project's established practice of validating a design empirically before committing to it
  in production code, especially for a subtly stateful, easy-to-get-wrong PDF-internals mechanism like
  this one.
- **Annotation-level `/StructParent` (singular) is deliberately not remapped** — a genuinely narrower
  scope cut, not an oversight found and left unfixed. This project's own rendering pipeline (LibreOffice
  conversion for office formats, OCR-derived text layers for scans, reportlab-rendered TOC/history/footer
  pages) does not produce annotation-level structure tags in practice; only page-content `/StructParents`
  (plural) occurs. Revisiting this would only become relevant if a future source of tagged form-field
  annotations enters the pipeline, which nothing currently does.
- **The merged `/ParentTree` is a flat `/Nums` leaf, never `/Kids` subtrees** — a deliberate simplification
  matching the spec's own allowance (a number tree may always be a single flat leaf regardless of size);
  reading existing trees (`_read_number_tree`) still handles the `/Kids` form for robustness against
  real-world tagged PDFs from other producers that might already use it, even though this module never
  writes that form itself.
- **No new dependency added** — the fix uses only `pypdf`, already present, at a lower API layer
  (`pypdf.generic`) than the module previously used, not a new library.

## Consequences

- **`build_folder_export` now genuinely preserves PDF/UA structure across multiple independent tagged
  source documents** — the gap ADR 0136 explicitly, deliberately left open (and locked in with its own
  regression test) is closed for real, not partially/misleadingly.
- **The single-tagged-document case through `build_folder_export` is also now fixed as a natural
  consequence** — previously dropped unconditionally regardless of source count (even one tagged document
  alone lost its tags going through Pass B), now correctly preserved; this was not a separate design
  decision, just the N=1 case of the same general mechanism.
- **A mixed tagged/untagged folder preserves exactly the tagged documents' tags and fabricates nothing
  for the untagged ones** — a real, content-driven partial result, not the "misleading partial fix" ADR
  0136 was concerned about (which was about ARBITRARILY keeping only the first of several equally-tagged
  sources due to a code limitation, not about correctly reflecting which sources actually have content to
  preserve).
- **Still no formal PDF/UA conformance validation** — `is_tagged_pdf`'s `/StructTreeRoot`-presence check
  remains the same documented approximation as before (no veraPDF integration, same limitation
  `docs/services/rendering-service.md` "Open Points" already names for PDF/A). This session verifies
  structural correctness of the merge itself (via direct object-graph walks in tests, the same resolution
  path a real assistive-technology consumer would take), not full ISO 14289 conformance of the output.
- **Annotation-level `/StructParent` remains unremapped** — documented, narrow scope cut (see
  "Rationale"), not a silently incomplete fix.
- **Tests**: `rendering-service` net +3 (101 total, up from 98): `test_export_pdf.py` gained a real,
  navigable structure-tree fixture (`_add_real_struct_tree`, replacing the old bare-presence-only marker
  for these specific tests) and a resolution helper (`_resolve_struct_tag_for_page`), plus 4 new tests
  (single tagged document preserved, two tagged documents merged without `/ParentTree` collision,
  all-untagged folder still fabricates nothing, mixed tagged/untagged folder preserves exactly the tagged
  one) replacing the 1 old regression-lock test whose premise ("folder export always drops tags") no
  longer holds.
