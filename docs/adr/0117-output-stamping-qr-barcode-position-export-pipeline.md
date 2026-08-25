# 0117 — Output stamping: QR/barcode, configurable position, wired into the export pipeline

**Status:** accepted (P31-S6, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 6 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #8 "Output stamping"),
affects `rendering-service`, `document-service`

## Decision

`rendering-service`'s existing `watermark.py` (a fixed diagonal, semi-transparent, text-only overlay,
exposed as the on-demand `POST /render/watermark`) is generalized in place: `add_text_watermark` becomes
`add_stamp(data, *, stamp_type, value, position)`, where `stamp_type` is `"text"` (default, reproduces the
original diagonal stamp exactly when `position` is left at its own default `"diagonal-center"`), `"qr"`, or
`"barcode"` (Code128), and `position` is either `"diagonal-center"` (text only) or one of the four page
corners (`"top-left"`/`"top-right"`/`"bottom-left"`/`"bottom-right"`, all three stamp types). New
dependencies: `qrcode` (QR generation) and `python-barcode` (Code128), both rendered to a PNG via Pillow
(already a dependency) and embedded with reportlab's `drawImage`, then merged via the same
`pypdf`-`merge_page()` idiom the original watermark already used.

`document-service`'s `ExportConfig` (the Phase 28 PDF-export feature's installation-wide settings row)
gains four new fields — `stamp_enabled` (default `false`), `stamp_type`, `stamp_value_template` (a
`str.format()` template resolved against the exported document, `{document_id}`/`{kennzeichen}`), and
`stamp_position` — making stamping an **optional automatic step** of both `POST /documents/{id}/export`
and the combined folder export, applied per document (not once on the combined folder PDF) by having
`_build_document_export_pdf` call the new `RenderingClient.stamp()` (a thin proxy to
`POST /render/watermark`) on its own already-composed Pass-A output, right after `export_document()`
and before returning.

## Rationale

- **Generalize the existing primitive rather than building a parallel one**: `watermark.py` already solved
  "burn a reportlab overlay into every page of a PDF via `pypdf`" — the only two things genuinely missing
  per the gap analysis were image-based stamp content (QR/barcode) and a non-diagonal position. Both are
  additive to the existing function's shape (an overlay canvas + a merge step), so extending
  `add_text_watermark` in place was the smaller, more honest change than introducing e.g. a separate
  `stamp_pdf.py` module that would duplicate the overlay/merge logic `export_pdf.py`'s own footer stamping
  (`_stamp_footer`) already demonstrates is a reusable pattern in this service.
- **`POST /render/watermark`'s request shape changed (`text` → `value`, plus new `stamp_type`/`position`
  fields) without a backward-compatibility shim**: confirmed via a full-codebase search that this endpoint
  has exactly one caller anywhere in the system — its own test suite. No frontend, no other service, and
  (before this session) no orchestrated caller in `document-service` ever used it. A clean rename is honest
  about the widened scope (`text` was never an accurate parameter name for a QR/barcode value); a
  compatibility wrapper would only protect a caller that doesn't exist.
- **`diagonal-center` stays text-only, not silently reinterpreted for QR/barcode**: rotating a QR code or
  barcode 45° would make it unscannable — the whole point of a machine-readable stamp. Rather than
  silently falling back to a corner position when a caller leaves `position` at its default while asking
  for `stamp_type="qr"`/`"barcode"`, the combination is rejected with `422` at both the rendering-service
  endpoint (immediate on-demand feedback) and `document-service`'s `PUT /export-config` (immediate
  config-write feedback, instead of a deferred failure only discovered the next time someone exports).
- **Validate once, at the write boundary — not on every render**: `add_stamp()` itself stays a lenient,
  pure rendering function with no input validation, matching the precedent `export_pdf.py`'s
  `build_document_export` already set for `history_position` (silently treats anything not `"before"` as
  `"after"` rather than raising) — the calling layer is responsible for ensuring only valid combinations
  ever reach it. `stamp_value_template`'s placeholder validity is checked the same way: `PUT /export-config`
  dry-runs `.format(document_id="x", kennzeichen="y")` against the submitted template and rejects an
  unknown placeholder with `422` immediately, mirroring how `object-type-service`'s `kennzeichen_format` is
  validated once at configuration time, not re-validated on every use.
- **Per-document stamping, not once on the combined folder export**: a folder export merges many
  documents' own already-stamped (Pass A) PDFs into one file (`export_pdf.build_folder_export`, Pass B).
  Stamping is applied inside `_build_document_export_pdf` — the same shared helper already used by both
  the single-document export endpoint and the folder-export job's per-document loop — so every page of a
  combined folder export still carries the identity of *its own* source document, not just a single stamp
  identifying the folder as a whole. This is the entire point of the gap analysis's "paper-trail
  reconciliation" framing: a stray printed page found later must be traceable back to which document it
  came from, which only works if the stamp is per-document.
- **Config-only, deliberately no per-call override** (unlike `history_position`, which already supports a
  `?history_position=` query override): stamping is an installation-wide compliance/reconciliation policy,
  not a per-export stylistic choice — adding an override matrix here would be speculative scope beyond
  what gap #8 actually asks for ("configurable... stamped onto documents at export... for paper-trail
  reconciliation", not "chosen per export"). Can be added later if a real need for a per-call override
  surfaces.
- **`FolderExportJob` freezes the resolved stamp config at job-creation time**, not re-read from
  `ExportConfig` live at tick-processing time — the exact same reasoning already applied to
  `history_position` on the same model: a job can sit `pending` for a while, and shouldn't silently pick up
  a stamping-policy change made after the export was actually requested.

## Consequences

- A fresh installation's export behavior is completely unchanged (`stamp_enabled` defaults to `false`) —
  this session adds no new required step to any existing flow.
- **Deliberately scoped to the export pipeline only**, per the plan's own wording ("wire it as an optional
  automatic step into the Phase 28 export pipeline"). The gap analysis also mentions "print, e-mail
  dispatch, or handoff to an external system" as future stamping trigger points — none of those have a
  unified pipeline to hook into yet in this codebase (print is a browser-native action with no server-side
  step at all; e-mail dispatch and any Fachverfahren-style handoff don't exist as features yet). Extending
  automatic stamping to those remains a follow-up once (if) those pipelines themselves are built.
- **`POST /render/watermark` remains a genuinely on-demand, unpersisted endpoint** in its own right,
  reachable independently of the export pipeline — the new `stamp_type`/`position` parameters are just as
  usable for a manual one-off stamp as the original text watermark always was; nothing in this session
  removes that use case, `document-service`'s export integration is simply its first orchestrated caller.
- `qrcode`/`python-barcode` are new supply-chain dependencies of `rendering-service` — both are widely used,
  actively maintained pure-Python (plus already-present Pillow) libraries with no native/system dependency
  beyond what Pillow itself already requires, consistent with this project's existing preference for
  dependency-light PDF tooling (`pypdf`/`reportlab`/`pymupdf`).
- **No new frontend surface for `ExportConfig`, in either direction**: `GET`/`PUT /export-config` had zero
  frontend callers anywhere in the project even before this session (Phase 28 shipped it as a raw,
  admin-API-only setting, `history_position` included) — `user-ui`'s `exportDocument()`/`exportFolder()`
  only ever call the export *action* endpoints, never the config ones. This session's four new fields are
  added to the same already-config-only surface, not a regression relative to what existed; building a
  first admin-facing settings page for `ExportConfig` (stamping or otherwise) is a reasonable, separate
  future cut, consistent with several other config-only toggles already left this way elsewhere in the
  project (see e.g. `docs/services/document-service.md` "Open Points" on four-eyes action-type toggles).
