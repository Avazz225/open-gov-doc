# 0139 — XJustiz import: `uebermittlungSchriftgutobjekte`, the mirror of the XDOMEA import

**Status:** accepted (P34-S1, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 34 Session 1 (XDOMEA/XJustiz completion), affects `archival-service`

## Decision

`archival-service` gains `POST /xjustiz/import`, the import-direction mirror of the existing
`POST /xjustiz/export/documents/{id}`/`POST /xjustiz/export/cases/{id}` (ADR 0129) — structurally the same
shape as the already-existing `POST /xdomea/import` (ADR 0128): accepts a `nachricht.gds.
uebermittlungSchriftgutobjekte.0005005` ZIP package (`xjustiz_nachricht.xml` + `dokumente/<paketname>`),
creates the referenced `document-service` Document(s) in a caller-given `folder_id`, and — if the package
contains an `akte` — either attaches them to an EXISTING `case-service` Case (`case_id`) or starts a
brand-new one via a caller-supplied `process_definition_id` (named after the Akte's `anzeigename`), the
exact two target options ADR 0128 established for XDOMEA, reused unchanged.

New `xjustiz.parse_uebermittlung_schriftgutobjekte()` (the mirror of `xdomea.parse_abgabe_message()`) and
`general_import.import_uebermittlung_schriftgutobjekte_package()` (the mirror of
`import_abgabe_package()`) implement the parsing/orchestration; three of `general_import.py`'s four
existing exceptions (`CaseTargetConflictError`, `CaseTargetRequiredError`, `InvalidPackageError`) are
shared verbatim between both import paths (already format-agnostic by name), with a new
`ProcessDefinitionWithoutAkteError` added alongside the existing `ProcessDefinitionWithoutVorgangError` for
the one XDOMEA-specific exception name.

## Rationale

- **A structurally faithful mirror, not a reinterpretation**: every design choice `parse_abgabe_message`/
  `import_abgabe_package` already made was re-applied identically where the two formats are structurally
  analogous — same lenient "find `dokument`/`Dokument` elements ANYWHERE under the top-level container"
  scope (covering both a standalone document and one nested inside a case/Vorgang), same "read the FIRST
  case-like element only" scope, same flattening of multiple top-level containers into one document list
  rather than rejecting the package, same two-step validate-then-parse call order, same four target-option
  validation rules (conflict/required/without-container checks) in the same order.
- **`ParsedXJustizDokument` carries only `dateiname`, unlike XDOMEA's `ParsedAbgabeDocument`'s three
  fields**: verified directly against `_build_dokument` (the export-side builder) — it writes only
  `xjustiz.fachspezifischeDaten/datei/dateiname`, no counterpart to XDOMEA's `DateinameOriginal`/
  `SonstigerName` (the latter a non-standard, project-specific stash for content type XDOMEA's export
  itself only introduced as a workaround). There is genuinely nothing else in this project's own XJustiz
  messages to parse back — the package filename doubles as both the ZIP lookup key and the imported
  document's title, an honest reflection of what's actually encoded, not a simplification of something
  richer that was available.
- **`content_type` is not derived at all for the imported document (passed as `None`)**: confirmed via
  `DocumentClient.create_document` that the multipart upload's content-type is only a client-side hint —
  `document-service`'s own magic-byte sniffing (`content_type_sniffer.py`) determines the real,
  authoritative type from the actual bytes on creation regardless of what's sent. Guessing a value from the
  file extension here would be redundant work with no functional effect, unlike XDOMEA's import (which
  does have a real `SonstigerName` field to pass through, for whatever historical reason it was added).
- **`akte_id` (the Akte's own `identifikation/id`) is kept purely for provenance, mirroring
  `vorgang_xdomea_uuid`'s exact role — deliberately NOT `aktenzeichen.freitext`** (which the export side
  stashes the original `case-service` Case ID into): `aktenzeichen.freitext` is XJustiz's own real,
  semantically-loaded business-data field (a court file reference number in genuine inter-agency use), not
  a structural identifier meant for round-tripping — treating it as such would misuse the field for any
  THIRD-PARTY package this module might one day need to read (an explicit, deliberately bounded
  non-goal for now, same "not a general-purpose reader" scope both parsers already state).
- **Shared exceptions where already format-agnostic, one new XJustiz-specific one where not**: `Invalid
  PackageError`/`CaseTargetConflictError`/`CaseTargetRequiredError` never mentioned "XDOMEA"/"Vorgang" in
  their class names to begin with, so reusing them for the XJustiz path needed no rename and no risk of a
  misleading name at a call site. `ProcessDefinitionWithoutVorgangError` does name the XDOMEA-specific
  concept in its own identifier - renaming an already-shipped, tested exception purely for symmetry would
  be a larger, purely cosmetic diff for no functional gain, so a new, separately-named
  `ProcessDefinitionWithoutAkteError` was added instead, both still mapped to `422` by the same generic
  `except (...)` tuple pattern in `main.py`.
- **`422`, not `409`, for a package referencing a content file missing from its own ZIP** — verified
  directly against `import_abgabe_package`'s own precedent (the identical XDOMEA case already maps to
  `422` via `InvalidPackageError`, not `409`) before writing the endpoint, rather than trusting the
  Phase 32+ plan's own paraphrase ("same 409 handling for data-integrity errors"), which turned out to
  describe a DIFFERENT case entirely: `general_export.py`'s `ReferencedDocumentMissingError` (`409`) is an
  EXPORT-side data-drift scenario (a case's own document reference pointing at content that has since been
  deleted from document-service) — a materially different situation from an IMPORT-side package that is
  simply malformed/incomplete as uploaded, which both this session and P31-S13b's own precedent correctly
  treat as a `422` caller/input problem, not a `409` server-side data-integrity conflict.

## Consequences

- **`XJustizImportResultOut` is a new, separate response schema from `XdomeaImportResultOut`** (same
  `case_id`/`case_created`/`document_ids` fields, `akte_anzeigename` instead of `vorgang_betreff`) — keeps
  XJustiz's own terminology (Akte, not Vorgang) honest in the API surface rather than overloading a
  field name that means something format-specific.
- **No frontend changes** — this session is API-only, per the plan's own scoping (a frontend entry point
  for XJustiz is P34-S2's separate concern).
- **Tests**: `archival-service` 133 (previously 118, +15): `test_xjustiz.py` gained 4 pure-function
  round-trip tests (case export→import, empty-case export→import, standalone-document export→import, a
  malformed-package `ParseError` case) mirroring `test_xdomea.py`'s existing four exactly; `test_api.py`
  gained 10 endpoint tests (401/403/422×4/200×3 — standalone document, attach to existing case, create new
  case) mirroring the ten existing `/xdomea/import` tests one-for-one, using the same `AsyncMock`-based
  `document_client`/`case_client` fixture pattern (real `permission-service` calls, mocked sibling
  services, matching this test file's own established convention).
- **Live-verified end-to-end against the real, rebuilt running stack**: a real document was uploaded,
  exported via `POST /xjustiz/export/documents/{id}`, and imported back via the new `POST /xjustiz/import`
  as a standalone document — content byte-identical, confirming the full real round trip works, not just
  the mocked unit tests. A real case round trip (export → import with `process_definition_id`) also
  confirmed the case name/`akte_anzeigename` correctly round-trips and a genuine new case is created via a
  real `process_definition_id` — the case's own document reference was excluded from that particular
  export because it had no `snapshot_version_number` yet (an OPEN case's document references aren't
  snapshotted until closure, a pre-existing, unrelated filter already established by
  `build_case_export_package_xjustiz`, not something this session touches or needed to work around; the
  document-inclusion code path itself is already covered by the mocked `test_api.py` case-import tests,
  which don't depend on that real-service-side gating).
