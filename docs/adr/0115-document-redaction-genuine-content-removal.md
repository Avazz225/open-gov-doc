# 0115 — Document redaction: genuine content removal, not a visual overlay

**Status:** accepted (P31-S4, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 4 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md)), affects `rendering-service`,
`document-service`, `apps/user-ui`

## Decision

A user selects rectangular regions on a PDF's pages; `rendering-service`'s new `POST /render/redact`
burns them into the PDF using PyMuPDF's redaction annotations (`page.add_redact_annot()` +
`page.apply_redactions()`) — which **genuinely remove** the covered text/graphics from the content
stream, not merely draw over them. `document-service`'s new `POST /documents/{id}/redact` orchestrates
the whole workflow: downloads the original's current-version bytes, calls rendering-service, and creates
the result as a **new, independent document** via the existing document-creation pipeline
(`_prepare_document_fields`/`_persist_new_document`), linked back to the original via the P6-S3
provenance fields (`derived_from_document_id`/`derived_from_version_number`) plus a new
`Document.derivation_type = "redaction"` discriminator.

## Rationale

- **Genuine removal, not overlay — chosen specifically because of the plan's other requirement**
  ("exclude the redacted copy's full-text index entry from exposing the removed content"). `rendering-
  service` already had a PDF-mutation precedent (`watermark.py`, `pypdf`'s `page.merge_page()`), but that
  technique only overlays a semi-transparent stamp on top of unchanged underlying content — the covered
  text remains fully extractable by any PDF text reader, OCR, or search index. PyMuPDF's redaction API
  actually deletes the covered content from the page. Because of this, the redacted copy's later
  `ocr-service` pass (native-text-layer extraction or Tesseract, whichever applies) structurally cannot
  find the removed text — search-service's indexing pipeline (`pipeline.reindex_document`, preferring OCR
  full text, falling back to a rendering-service `substitute_text` rendition) therefore excludes the
  redacted content **automatically, with zero changes to search-service**. Building a separate
  "exclude this text from the index" mechanism would have been strictly worse: more code, and a second
  place where the exclusion could silently drift out of sync with what the PDF actually contains.
- **New `pymupdf` dependency for `rendering-service`** — not a new supply-chain risk: `ocr-service` has
  depended on it since early in the project (`pymupdf>=1.24`, used there for page rasterization and
  native-text-layer extraction), this session adds the identical version constraint to
  `rendering-service`. `pypdf`/`reportlab` (already used by `watermark.py`) were not viable alternatives
  for this session's need — neither exposes true redaction/content-stream removal.
- **Reuses the normal document-creation pipeline for the redacted copy**, rather than a bespoke creation
  path: the copy gets its own freshly-assigned `Kennzeichen` (if the object type has a generator), its
  own retention/classification seeding, and — critically — is picked up by the exact same downstream
  OCR → rendering → search-indexing event pipeline as any other document. No special-casing was needed
  anywhere in that pipeline for "this document is a redaction" — **but this only works because the
  published event's `event_type` is left at the default `"document.created"`, not a custom
  `"document.redacted"`**: rendering-service's consumer dispatches on an exact string match against
  `"document.created"`/`"document.version.created"` only (`consumer.py`'s `make_handler`, everything else
  hits a silent `else: return`). A first implementation used a custom event type here and was caught live,
  before shipping, by search-service simply never indexing the redacted copy at all — not even for its
  still-present, non-redacted text. The redaction-specific context (`derivation_type`, `source_document_id`,
  region count) instead travels in the event *payload*, which `audit-service` records regardless of the
  exact `event_type` via its existing `document.>` wildcard subscription.
- **No virus re-scan on the redacted copy** — the bytes are server-derived from an already-scanned
  source (the original document, which passed virus scanning when it was itself uploaded), not newly
  submitted by an external client. Same reasoning and precedent as `POST /documents/
  from-quarantine-release` (P15-S2, ADR 0052), which likewise skips a redundant scan of already-vetted
  bytes.
- **`Document.derivation_type`, not a new generic typed-link table** — the P6-S3 provenance fields
  (`derived_from_document_id`/`derived_from_version_number`) already exist and, until this session, were
  entirely write-only (round-tripped in `DocumentOut`, never read or queried by anything, per this
  session's own research). They lacked only a discriminator for *why* a document was derived — `office-
  addin`'s unrelated "new from template" flow sets the same two opaque fields for a completely different
  purpose. A single additive, nullable `derivation_type` column (currently only ever `"redaction"`)
  extends the existing mechanism exactly as the plan calls for ("extends the existing but never-wired
  provenance fields"), instead of introducing a new generic relationship-type system this codebase has no
  other precedent for (the closest analog, case-service's `CaseDocumentReference`, is itself a
  single-purpose join table with an implicit relationship, not a generic typed-link mechanism).
- **`GET /documents/{id}/derived`** is the first genuine reader of `derived_from_document_id` anywhere in
  the codebase — "wiring" the field, as the plan asks, means more than round-tripping it in API
  responses. Deliberately not redaction-specific (no `derivation_type` filter) so any future derivation
  reason benefits from the same "what was derived from me" lookup.
- **Classification inheritance**: the redacted copy's `classification_level` is seeded from the
  **original's current level** (not the object type's static default, which `_prepare_document_fields`
  would otherwise compute) — redaction must never silently declassify a document. Since classification
  can only ever be raised, never lowered (ADR 0114), the original's current level is always at least as
  high as what the object type would seed for a brand-new document of the same type, so this is a safe,
  simple override.
- **`document.read` gate on the original**, mirroring the PDF export feature's endpoint (P28, ADR 0107) —
  both endpoints read the document's actual stored content. Creating the redacted copy itself is
  deliberately left as ungated as `POST /documents` already is — a pre-existing, documented gap in
  document-service's core CRUD authorization that this session does not newly introduce or attempt to
  close (out of scope here; a document-service-wide authorization pass is a separate, larger concern).
- **Frontend: page images via new proxy endpoints in `document-service`** (`GET .../redaction-preview/
  page-count`, `GET .../redaction-preview/page-image`), never a direct browser→rendering-service or
  browser→storage-service call — consistent with this project's architecture, where the browser only
  ever talks to services through the gateway and document-service already owns all storage-key access on
  a document's behalf. Any PDF page can be rasterized this way via PyMuPDF's `get_pixmap()`, regardless
  of whether it has a native text layer or is a scan — unlike `ocr-service`'s page images, which exist
  only for the Tesseract-processed subset (a real gap this session's page-image endpoint does not share).
- **Frontend UX scoped as a bounded MVP**: one page at a time (Previous/Next navigation) with click-drag
  rectangle drawing over the rasterized page image — the same percentage-of-image positioning technique
  already established for the OCR word overlay (`PreviewPane.tsx`), just with new mouse-driven drawing
  interaction instead of read-only display. A full thumbnail-grid, multi-page-at-once editor was
  considered and deliberately not built — unbounded complexity/performance cost for large PDFs with no
  concrete requirement calling for it in this session.

## Consequences

- A redacted copy is a completely ordinary, independent document from every other service's point of
  view — it can itself later be redacted again, retitled, moved, classified, deleted, etc., with no
  special-casing required anywhere.
- `DocumentVersion.classification_level`'s per-version snapshot semantics (ADR 0114) apply to the
  redacted copy's own version history exactly as to any other document — a later raise on the redacted
  copy does not retroactively change what its first version shows.
- The redaction-preview proxy endpoints re-download the source PDF from storage-service on every single
  page-count/page-image call (no caching across calls) — accepted as a deliberate simplicity/inefficiency
  trade-off for what is not a hot-path feature; a future session could add short-lived caching if a real
  installation finds this too slow for large PDFs.
- No "un-redact"/declassify-adjacent mechanism exists — once burned in, the removed content is genuinely
  gone from the redacted copy (by design); the *original* document remains completely unchanged and
  retrievable, so nothing is destructively lost at the system level.
- **Incidental fix, unrelated to redaction itself, found via this session's live browser verification**:
  `permission-service`'s `get_effective_permissions` cache-population step was not concurrency-safe
  (`session.merge()` performs a non-atomic existence-check-then-write) — the redaction-preview UI is simply
  the first real caller anywhere in the app to fire two back-to-back `document.read` checks on the same
  resource fast enough to expose the resulting `IntegrityError` on a cold cache as a real `500` in the
  browser. Fixed with the same `INSERT ... ON CONFLICT DO NOTHING` pattern already established for
  object-type-service's counter rows (P5e-S1); a regression test was added. Documented here rather than in
  a new ADR since it is a bugfix to existing, already-decided behavior, not a new design decision.
