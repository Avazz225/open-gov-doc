# 0207 — Structured e-invoice (ZUGFeRD/Factur-X) support: scoping

**Status:** accepted
**Context:** P72-S2 (Phase 72, a **scoping-only** session, explicitly no implementation commitment — see
`IMPLEMENTATION_PLAN.md` "Phase 72"). The plan's own framing named "structured e-invoice format support"
generically (XRechnung/ZUGFeRD/Factur-X style formats) as an intake/OCR-pipeline plugin that recognizes
and extracts the structured payload, auto-populating the invoice object type's attributes through the
existing constraint engine instead of relying on OCR text extraction. This session verifies the real
premises first (does any of this exist already, what does the current pipeline actually support) before
recommending a bounded shape for a future build session.

## What already exists (verified against the real code, not assumed)

- **No existing "structured extraction → attributes" pathway of any kind.** `document-service`'s
  `POST /documents` takes `attributes` as a plain multipart form field the caller supplies — manual UI
  entry or a scripted client, always. The constraint engine (`libs/dms-constraint-engine`) is a pure
  validation function against an already-populated `attributes` dict; it has no knowledge of documents,
  OCR, or file content at all. Nothing anywhere in this project today derives attribute values from a
  document's own content server-side.
- **No existing "Invoice" object type** — "Rechnung" appears only as an illustrative example in
  `Konzept.md` and as test fixtures in `object-type-service`'s own test suite, never as seeded/real
  system data.
- **`ocr-service`'s engine-selection plugin point exists but solves a different problem.**
  `TextLayerExtractor` (native-text-layer vs. Tesseract) is genuinely pluggable, but every engine's job
  is producing an `OcrResult` (full text/words) — none of them write object-type attributes, and nothing
  in the pipeline sniffs for an embedded structured payload before choosing a text-extraction engine.
- **The reusable pattern already exists, just in a different service**: `archival-service`'s
  `xdomea.py` already does exactly the "bundle a real external XSD, validate untrusted XML offline (a
  custom `_LocalSchemaResolver` prevents network-based XXE), parse into typed data" shape this session
  needs — for a different standard (XDOMEA), but the identical approach.
- **No PDF-embedded-file extraction exists anywhere.** PyMuPDF (`fitz`) is already an `ocr-service`
  dependency and used throughout its engines, but nothing calls its `embfile_*()` API (or `pypdf`'s
  `.attachments`) — this would be new code, though the library is already present in the monorepo.

## Decision

**Recommend scoping the future build to ZUGFeRD/Factur-X specifically (the hybrid PDF+embedded-CII-XML
format), not bare XRechnung/UBL files.** A standalone XML invoice with no PDF has no visual
representation anywhere in this project's pipeline (PDF preview, OCR, rendering — all assume a
viewable document) — accepting bare XML would mean solving "render an invoice XML as a human-readable
document" as a prerequisite, a materially larger, separate problem. ZUGFeRD/Factur-X's hybrid shape
(a normal, viewable PDF that also happens to carry the machine-readable payload as an embedded file)
fits this project's existing document model with zero new assumptions — the PDF still goes through the
normal OCR/preview/rendering pipeline exactly as today; only the ADDITIONAL attribute-population step is
new.

Concrete recommendations for the future build session:

1. **New pipeline step inside `ocr-service`, running BEFORE engine selection, not as a `TextLayerExtractor`
   implementation.** A new module (e.g. `einvoice.py`) checks every incoming PDF for an embedded XML
   file via PyMuPDF's embedded-file API before `select_engine()` runs. If found and it validates against
   the bundled CII XSD (see #2), extract the structured fields and continue; the existing OCR engine
   selection then proceeds completely unchanged afterward — a ZUGFeRD invoice still gets its normal OCR
   text layer for full-text search, this step only ADDS attribute population, it never replaces OCR.
2. **Schema validation via the exact `archival-service.xdomea` pattern**: bundle the real UN/CEFACT CII
   XSD (ZUGFeRD/Factur-X's payload format) under a new `services/ocr-service/src/ocr_service/
   einvoice_schema/` directory, load via `etree.XMLSchema` with the same offline `_LocalSchemaResolver`
   idiom `xdomea.py` already uses (untrusted, externally-supplied XML must never be validated with
   network-based schema resolution enabled). A future build session should fetch the real schema
   directly from its authoritative source before writing any mapping code — the same discipline this
   session's own sibling session (P71-S4) used for the XDOMEA `Dateiformat` codelist, fetched live from
   KoSIT's xrepository rather than reconstructed from memory. Do not hand-write or approximate the XSD.
3. **Attribute mapping via well-known, optional attribute names — no new config table.** The plugin
   writes to a small, fixed set of well-known attribute names (e.g. `invoice_number`, `invoice_date`,
   `total_amount`, `seller_name`, `buyer_name`) IF the resolved object type's schema happens to define
   them, silently skipping any that don't exist. This mirrors the already-established "well-known,
   optional additive field" pattern this project already uses elsewhere (e.g. `required_signature_level`
   on an object type) rather than inventing a new per-installation field-mapping configuration surface
   for a first vertical slice. An admin who wants ZUGFeRD auto-population simply names their invoice
   object type's attributes to match; this needs no admin-UI changes.
4. **Population happens via the existing `PATCH /documents/{id}` metadata-update endpoint** (in place
   since P4-S4), called from `ocr-service` back to `document-service` after successful extraction — no
   new document-service endpoint, no change to the upload path itself, consistent with this project's
   "enrichment happens asynchronously after upload" pattern already used by OCR/virus-scan.
5. **A validation failure or missing schema match is silent, not an error** — an ordinary PDF (no
   embedded file, or an embedded file that isn't valid CII) simply skips this step entirely and proceeds
   through the normal OCR pipeline unchanged. This is additive-only; it must never be able to make an
   otherwise-successful upload fail.

## Rationale

- **Why ZUGFeRD/Factur-X and not bare XRechnung/UBL**: the load-bearing finding of this scoping session.
  This project's entire document model assumes every document has a viewable rendering — a bare XML
  invoice breaks that assumption and would need a genuinely separate "render structured data as a
  document" feature to even be usable, not a small addition to the intake pipeline. Narrowing to the
  hybrid format is what keeps this a bounded, additive plugin instead of a second document-type concept.
- **Why a new pre-engine-selection step, not a `TextLayerExtractor` implementation**: the existing
  plugin interface's whole contract is "produce OCR text", and attribute population is a materially
  different output. Reusing that interface for a fundamentally different job would strain its contract
  rather than extend it cleanly — a separate, purpose-built step composes with it instead.
- **Why reuse `xdomea.py`'s schema-validation approach specifically**: it is the only place in this
  codebase that already solves "safely validate real-world, externally-authored XML against a real
  government/industry standard's XSD" — re-deriving that offline-resolver safety property from scratch
  would be redundant and risk missing the XXE-hardening this project already learned to apply (ADR 0184).
- **Why well-known attribute names over a new mapping config**: this project consistently prefers a
  small, well-known additive field for a first vertical slice over a new configuration surface when no
  operator need for per-installation customization has been identified yet (the same judgment already
  applied to `required_signature_level`, `reference_target`, and several other single-purpose additive
  fields throughout this codebase) — a future session can always add a real mapping UI later if an
  installation actually needs field names that don't match the well-known set.

## Consequences

- **A future build session inherits a fully bounded starting point**: format scoped to ZUGFeRD/Factur-X
  only (not bare XRechnung), the schema-validation approach decided (reuse `xdomea.py`'s pattern), the
  pipeline insertion point decided (a new pre-engine-selection step in `ocr-service`, not a
  `TextLayerExtractor`), the attribute-population mechanism decided (well-known optional attribute names
  via the existing `PATCH /documents/{id}`), and the one real premise this session corrected (no existing
  extraction-to-attributes pathway exists anywhere, contrary to what the plan's phrasing could be read to
  imply) named explicitly.
- **No code diff in this session** — `ocr-service`/`object-type-service`/`document-service` are all
  unchanged. `PROGRESS.md` marks this session explicitly as scoping-only, no feature.
- **Bare XRechnung/UBL-only invoice ingestion remains out of scope**, not because it's undesirable, but
  because it needs an independent "render structured-only data as a document" capability this project
  does not have — a separate future scoping question if a real operator need for pure-XML intake (no PDF
  at all) ever surfaces.
- **The recommended plugin itself remains scoped, not scheduled** — no session number assigned, awaiting
  a future phase if an installation's real invoice intake volume justifies it.
