# 0128 — XDOMEA import: attach to an existing case OR create a new one, `process_definition_id` required

**Status:** accepted (P31-S13b, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 13b (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #12), second of the P31-S13a → 13b → 13c
split ([ADR 0126](0126-xdomea-general-exchange-split-download-upload-not-federation-hub.md)), affects
`archival-service`

## Decision

`archival-service` gains `POST /xdomea/import` — the mirror of P31-S13a's export endpoints
([ADR 0127](0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md)). It accepts an
`Abgabe.Abgabe.0401` package (the same ZIP shape the export endpoints produce), validates it against the
real vendored schema, parses it (`xdomea.parse_abgabe_message`, new), and creates the referenced
document(s) in a caller-supplied `folder_id`. If the package contains a `Vorgang`, the caller must supply
**exactly one** of:

- `case_id` — attach the imported documents to an **existing** case (same shape as
  `mail-connector`'s own `assign_manually`), or
- `process_definition_id` — **create a brand-new case**, named after the Vorgang's `Betreff`, by starting a
  real BPMN process instance via this process definition (`case-service`'s `POST /cases` has always
  required `process_definition_id` — there is no XDOMEA-derivable value for it).

Put to the user directly via `AskUserQuestion` before implementation, since the two options carry
genuinely different scope: the recommended, narrower option was "attach to an existing case only" (matching
every other inbound-content flow in this project, which never auto-creates a case from external data); the
user chose to also support brand-new case creation, accepting the added `process_definition_id` parameter
and case-creation code path this requires.

## Rationale

- **Every other inbound-content flow in this codebase deliberately never auto-creates a case from external
  data** — `mail-connector`'s `AssignRequest.case_id` only ever attaches to an EXISTING case
  (`CaseClient.add_document_reference`), never creates one; the underlying reason is structural, not just
  convention: `case-service`'s `POST /cases` requires `process_definition_id` to start a real BPMN process
  instance, and no external XDOMEA field maps to "which local process should represent this." Attaching to
  an existing case sidesteps that gap entirely.
- **The user's chosen option (also support creating a new case) still needs that same gap closed
  explicitly**: since XDOMEA carries no process-definition information, `process_definition_id` must be a
  caller-supplied import parameter — there is no way to infer or default it. The newly-created case's
  `Betreff` comes from the imported Vorgang; its `attributes` gains a `xdomea_herkunft_uuid` field (the
  Vorgang's own `xdomeaUUID`) purely for traceability back to the source package, not enforced or read
  anywhere else.
- **`case_id` and `process_definition_id` are mutually exclusive** (`422` if both given) — attaching to an
  existing case and creating a new one from the same Vorgang are different, incompatible intents, not a
  layered fallback.
- **A package WITHOUT a Vorgang (a standalone document export) needs neither parameter** — a bare document
  import behaves exactly like `mail-connector`'s own unassigned-document creation, just `folder_id`.
  `process_definition_id` supplied without a Vorgang present is rejected (`422`) — there would be no
  Betreff to name the new case after.
- **Synchronous, mirroring P31-S13a's own export execution model**: no new DB table, no poll loop, no
  persisted job — a one-shot action, same reasoning as ADR 0127.
- **`parse_abgabe_message` is deliberately scoped to THIS module's own export shape**, not a
  general-purpose third-party XDOMEA package reader: it expects the same `dokumente/<Dateiname>` ZIP
  layout `general_export.py` produces, and looks for `Dokument` elements anywhere under a
  `Schriftgutobjekt` (covering both a `Vorgang`'s nested documents and a standalone top-level `Dokument`)
  while deliberately excluding the optional `Anschreiben` cover-letter element (a sibling of
  `Schriftgutobjekt`, not a descendant). A package with more than one top-level `Schriftgutobjekt` (this
  module's own export never produces more than one, though the schema permits it) has all of its documents
  flattened into one list rather than rejected outright — bounded leniency, not a claim of full
  cross-vendor interoperability. Reading a genuinely arbitrary third-party XDOMEA package (different
  ZIP conventions, multiple independent Vorgänge each needing their own case target, `Akte`-level nesting)
  remains explicitly out of scope.

## Consequences

- **Importing a package from a genuinely different XDOMEA-producing system may fail** if that system uses
  a different file-bundling convention than `dokumente/<Dateiname>` — the round-trip guarantee is
  explicitly "this system's own export, read back," not universal interoperability. A real limitation,
  not silently glossed over.
- **A newly-created case's `attributes.xdomea_herkunft_uuid` is informational only** — nothing else in the
  system reads or enforces it; a future session could build real provenance tooling on top of it if that
  becomes valuable.
- **No frontend entry point in this session** — same deliberate scoping as ADR 0127's document-level-only
  export UI; a case-import UI (needing a process-definition picker) is a plausible future addition, not
  built here.
