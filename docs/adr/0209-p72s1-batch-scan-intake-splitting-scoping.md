# 0209 — Batch-scan intake document-boundary splitting: scoping

**Status:** accepted
**Context:** P72-S1 (Phase 72, a **scoping-only** session, explicitly no implementation commitment — see
`IMPLEMENTATION_PLAN.md` "Phase 72"). The plan's own framing: automatic document-boundary detection
(barcode/separator-sheet/blank-page) for a bulk mail-room scan, so the Poststelle role doesn't have to
scan one physical document at a time — named as possibly slotting in "as a new pipeline step between
virus-scan and OCR, or as an OCR-service plugin." This session verifies the real premises first.

## What already exists (verified against the real code, not assumed)

- **Document creation is strictly one-file-per-call today**: `POST /documents` takes a single
  `UploadFile`, not a list — there is no multi-file/batch upload endpoint anywhere in `document-service`.
  The only things called "batch" in this codebase are an unrelated read-side metadata lookup
  (`/documents/versions/current/batch`) and `ocr-service`'s own NATS consumer concurrency limit — neither
  is multi-document intake.
- **The pipeline is upload → virus-scan → store/create → (async, event-driven) OCR.** OCR is not inline
  with upload — `ocr-service` consumes `document.created`/`document.version.created` events after the
  document already exists as exactly one row. This matters directly for where splitting can live: by the
  time OCR's consumer sees the event, `document-service` has already committed to "this upload is one
  document" — a stage that runs only after that point cannot retroactively decide the upload was
  actually N documents without creating new ones and reconciling the original, it cannot simply
  intercept "the" document before creation the way a virus scan can.
- **The Poststelle role (`mail-connector`) has zero concept of this today.** It gates e-mail-based
  intake only, and its own attachment handling is already one-attachment-to-one-document — there is no
  existing multi-page splitting logic anywhere in this service to extend, despite the plan's phrasing
  associating this feature with that role.
- **No pipeline-stage plugin mechanism exists in `ocr-service` beyond the OCR-engine interface itself**
  (`TextLayerExtractor`, selected by `select_engine()`) — a new splitting stage would be new pipeline
  code, not a drop-in plugin of an existing extension point. `plugin-orchestration-service` is unrelated
  (it answers "where should a plugin instance run", not "what pipeline stages exist").
- **No barcode-reading, blank-page-detection, or separator-sheet code exists anywhere.** The one hit for
  "barcode" in the codebase is `rendering-service`'s `python-barcode` dependency — a barcode *generator*
  for output stamping (ADR 0117), unrelated and not reusable for *reading* barcodes back. `Pillow` is
  already an `ocr-service` dependency (usable for blank-page pixel-density heuristics with zero new
  dependencies); no barcode-*reading* library (`pyzbar`, `zxing`) or `opencv` exists anywhere in the
  monorepo today.

## Decision

**Recommend narrowing the first vertical slice to blank-page-as-separator detection only, deferring
barcode-based separator sheets.** Blank-page detection needs zero new dependencies (a pixel-density
threshold over pages already rendered via Pillow, which `ocr-service` already has for its own engines);
barcode-based separator sheets need a genuinely new system dependency (`pyzbar` + the system `zbar`
library) AND a real operational change (someone in the mail room must print and insert physical barcode
sheets) — a materially larger first step than this scoping session's own "reference implementation"
level of ambition warrants, matching this project's repeated preference for the smaller, dependency-free
slice when no concrete operator need for the larger one has been identified yet.

Concrete recommendations for the future build session:

1. **New pipeline stage inside `ocr-service`, gated by an explicit opt-in flag on upload — not an
   automatic, silent intercept of every document.** A new query parameter/form field on `POST /documents`
   (e.g. `batch_scan=true`), defaulting off, flows through to the `document.created` event payload so
   `ocr-service`'s consumer can recognize it. This is the safest possible scoping: an ordinary single
   multi-page contract or report must never be silently split apart because it happens to contain a
   mostly-white page; only an upload the Poststelle workflow explicitly marks as a batch scan is eligible.
2. **Splitting happens INSIDE `ocr-service`, reusing its own existing page-rendering infrastructure**
   (it already renders PDF/TIFF pages to images for Tesseract) rather than a new standalone
   microservice — the same "extend an existing service instead of spinning up a new one for a narrowly-
   scoped addition" judgment this phase's other two scoping sessions also reached (mirrors ADR 0161's "a
   fourth backend, not a new service" reasoning for `mail-connector`'s Graph backend).
3. **Detected page ranges become N new documents via the EXISTING, unchanged `POST /documents` endpoint**
   — one call per detected logical document, each carrying its own re-assembled page subset. No new
   document-service endpoint, no change to the single-document upload contract itself.
4. **The original batch-scan upload's disposition is the one genuinely open design question for the
   build session to resolve**, not settled here: whether to keep it as a raw-scan audit record, mark it
   superseded, or delete it once splitting succeeds. A future build session should FIRST check whether
   this project already has a reusable document-to-document relationship concept it can reuse for
   "these N documents were derived from this one batch scan" (there are hints of a derived-document
   concept already surfaced elsewhere in the frontend, e.g. `DerivedDocumentsPanel`, but this session did
   not verify its exact backend shape or whether it is the same kind of relationship — confirm before
   assuming it applies here) before inventing a new relationship mechanism.
5. **Barcode-based separator sheets stay explicitly deferred**, named here so a future session doesn't
   have to rediscover the dependency gap: would need `pyzbar` + system `zbar`, plus a real mail-room
   operational change (printing/inserting physical separator sheets) — worth building once an actual
   installation's blank-page heuristic proves insufficient in practice (e.g. batches containing
   intentionally blank pages as real document content), not before.

## Rationale

- **Why blank-page detection over barcode detection for the first slice**: the exact same "smaller,
  dependency-free cut when no concrete operator need for the larger one exists yet" judgment this
  project has applied repeatedly (folder-service's `folder.viewed` toggle in P71-S2, the well-known-
  attribute-names choice in this same phase's ADR 0207) — barcode detection is a real, valuable
  enhancement, but it's a second session's worth of new dependency and operational surface, not a
  reason to block the simpler capability that needs neither.
- **Why gated by an explicit flag, not automatic for all uploads**: the load-bearing safety finding of
  this scoping session. Automatic detection risks a false-positive split on an ordinary document that
  happens to contain a blank page as real content (a common occurrence — a signature page, a
  deliberately blank back side) — an explicit opt-in tied to the batch-scan workflow specifically avoids
  ever touching a document nobody asked to have split.
- **Why inside `ocr-service`, not a new service or inside `mail-connector`**: `mail-connector`'s whole
  reason for being is e-mail polling, not physical scanning — despite the plan's phrasing associating
  this with the Poststelle role, the actual workflow (a person physically scans paper at a device) has
  no natural home in an e-mail-intake service. `ocr-service` already owns "turn a stored file into
  something more structured" as its core job and already has the page-rendering machinery this needs.
- **Why splitting can't be "between virus-scan and OCR" in the most literal reading of the plan's own
  text**: `document-service` commits to exactly one document row at creation time, before any later
  pipeline stage runs — a stage genuinely positioned strictly between virus-scan and document creation
  would have to prevent that single-document creation from happening at all and take over document
  creation itself, which is a materially different (and riskier) design than a stage that runs after
  creation and produces additional documents. This session recommends the latter, correcting the
  former's implicit premise.

## Consequences

- **A future build session inherits a fully bounded starting point**: first-slice detection method
  decided (blank-page pixel-density, zero new dependencies), the opt-in gating mechanism decided (an
  explicit upload flag, not automatic), the service home decided (`ocr-service`, reusing existing
  infrastructure), and the barcode-based enhancement explicitly named as a deferred, larger second step
  rather than something to build now.
- **One genuinely open question is left for the build session, not resolved here**: the original batch
  upload's disposition/relationship to its N split children — flagged explicitly rather than guessed at,
  including a concrete pointer (check for an existing derived-document relationship concept) to check
  before inventing a new one.
- **No code diff in this session** — `ocr-service`/`mail-connector`/`document-service` are all
  unchanged. `PROGRESS.md` marks this session explicitly as scoping-only, no feature.
- **This is the first of Phase 72's three scoping sessions** — Phase 72 closes once P72-S2 (ADR 0207)
  and P72-S3 (ADR 0208) are also recorded.
