# archival-service

**Responsibility:** Records disposal & long-term archiving of documents and cases (5.6). **Documents**: after the active phase expires (object type deadline or manual trigger), transfers documents mandatorily as PDF/A (fallback: plain PDF) to a separate archive target, removes the live storage copy after a transition period ("dehydration"), and provides an audited, role-gated retrieval process. **Cases** (since P7-S3b): for closed cases, generates a real, schema-validated XDOMEA 4.0.0 records-disposal message + packages the referenced document contents into a transfer package. In both cases, coordinates only the transfer mechanics — `document-service`/`case-service` remain the sole authority for the respective lifecycle fields.

**Concept Reference:** 5.6/14.2 (general XDOMEA export/import + XJustiz export, Post-Roadmap Phase 31 Sessions 13a/13b/13c)
**Own Postgres Schema:** `archival` (tables `archival_transfer`, `case_archival_transfer`)

## Architecture Decisions

- **No new BPMN process**: `workflow-service` is a real BPMN engine (SpiffWorkflow) without a programmatic "step by step" API — a new process would require a hand-modeled diagram, which is not a sensible investment for a purely system-driven, linear flow with no human intermediate steps. Instead, a **poll loop + status field state machine**, exactly the same idiom as `document-service`'s `_retention_poll_loop`/`reporting-service`'s `_report_schedule_poll_loop`: each phase is committed individually before the next step begins, an error in one transfer does not abort processing of the remaining transfers in the same tick, resumption after a crash follows from the persisted `status`, not from engine checkpointing.
- **Universal PDF/A conversion instead of an original-format fallback**: an originally planned "PDF/A where possible, otherwise archive the original format" solution was discarded (user requirement: all common document types must be transferable, a PDF fallback is okay, a silent original-format fallback is not). The actual conversion already happens upstream in `rendering-service`'s `PdfArchiveRenderer` (LibreOffice headless + Pillow, since P7-S3 — see `docs/services/rendering-service.md`); this service only reads the finished `pdf_archive` rendition.
- **Storage via a new archive target role in `storage-service`** instead of a dedicated storage system: `BackendTargetConfig.role: "archive"` (see `docs/services/storage-service.md`) — reuses ~90% of the existing multi-backend/fixity infrastructure (ADR 0017); an archive target is simply another configured backend, just with a different role (e.g. a cheaper or differently redundant provider).
- **"Dehydration" instead of physical deletion**: the `Document` row in `document-service` is never deleted (metadata remains findable, per the concept's literal wording) — only the content on the live storage targets is removed after `Settings.dehydration_delay_days` (default 30), same principle as `TrashConfig.restore_period_days`.
- **Legal hold gates only dehydration, not archiving** — an additional, secure archive copy does no harm, whereas removing the live copy does (consistent with "legal hold overrides any due action", 5.2).
- **Encryption via a lightweight `KeyStore` plugin interface** ([ADR 0029](../adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)): only the interface (`get_key(key_id) -> bytes`) plus a trivial `EnvKeyStore` default implementation (a single key from `DMS_ARCHIVE_ENCRYPTION_KEY`, explicitly for dev/test purposes) are shipped — a real KDBX connector (`pykeepass`, GPL-3.0) is, per [ADR 0029](../adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md), deliberately a separately installable package outside the standard image. AES-256-GCM via `cryptography` (already a real dependency in `workflow-service`/`signature-service`).
- **Retrieval as its own, role-gated operation**: `POST /archival-transfers/{id}/retrieve` requires `Settings.archive_retrieval_role` (default `dms-admin`) in the `X-DMS-Roles` header (injected by the gateway) — same pattern as `storage_service.governance_bypass_role`. Writes the decrypted content back under exactly the same live storage key that `document-service` already knows (`DocumentVersionOut.storage_object_key`, publicly visible since P7-S3), so the regular download path continues to work unchanged afterward.
- **No NATS consumer/producer**: this service is purely poll-/HTTP-based (candidate discovery via `GET /documents/due-for-archival`, no event-driven triggering) — `document.archived`/`document.dehydrated`/`document.rehydrated` are published by `document-service` itself (domain-owner principle) when this service calls its internal callback endpoints, not by this service.
- **XDOMEA 4.0.0 instead of 3.0.0** (since P7-S3b, [ADR-0029 Addendum](../adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md)): the version 3.0.0 originally named in ADR 0029 was, according to the official KoSIT registry, about to expire at the time of the P7-S3b implementation and was only findable via a GPL-3.0 third-party mirror — 4.0.0 is the current standard, cleanly obtainable via the official KoSIT schema infrastructure (`schema.kdo.de`, `xoev.de`), no licensing concern.
- **Only the 0503 message ("records disposal"), not the full bilateral negotiation flow** (0501 offer directory → 0502 assessment directory → 0504–0507 confirmations): the full flow requires an actually responding second system (an archive system), which does not exist here. 0503 is the actual export/transfer message with the content data — sufficient to produce a valid records-disposal transfer handable off to an external archive.
- **General inter-agency handoff (14.2, Post-Roadmap Phase 31 Sessions 13a/13b/13c + Phase 34 Session 1, [ADR 0127](../adr/0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md)/[ADR 0128](../adr/0128-xdomea-import-existing-or-new-case-required-process-definition.md)/[ADR 0129](../adr/0129-xjustiz-uebermittlungschriftgutobjekte-general-message.md)/[ADR 0139](../adr/0139-xjustiz-import-uebermittlung-schriftgutobjekte.md)) reuses `xdomea.py`'s infrastructure (and, for XJustiz, a parallel `xjustiz.py`) but is a genuinely different message and a genuinely different execution model per format**: `Abgabe.Abgabe.0401` (not `Aussonderung.Aussonderung.0503`) or XJustiz's `nachricht.gds.uebermittlungSchriftgutobjekte.0005005` for an arbitrary document or case, built/parsed synchronously on demand (`POST /xdomea/export/documents/{id}`/`.../cases/{id}`/`.../xdomea/import`/`.../xjustiz/export/documents/{id}`/`.../xjustiz/export/cases/{id}`/`.../xjustiz/import`, same shape as document-service's own `POST /documents/{id}/export`) rather than through this service's async, multi-phase, encrypted disposal state machine — a one-shot handoff package isn't a legally significant records-disposal operation, none of that machinery's retry/verification/encryption phases apply. See "General XDOMEA Export for Inter-Agency Handoff"/"General XDOMEA Import for Inter-Agency Handoff"/"General XJustiz Export for Inter-Agency Handoff"/"General XJustiz Import for Inter-Agency Handoff" below.

## State Machine

`ArchivalTransfer.status`: `pending → locked → copied → verified → released → dehydrated` (+
`failed_permanent` with `error_message`, reachable only after `Settings.max_archival_attempts` is
exhausted, since Post-Roadmap Phase 20 Session 2, [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md)).

| Status | Meaning | Transition triggered by |
|---|---|---|
| `pending` | Transfer created, not yet processed | `discover_due_transfers` (phase 1 of each tick) — creates exactly one row for each due document not yet being processed (`GET /documents/due-for-archival`) |
| `locked` | Processing started | Symbolic marker (no distributed lock system, only one instance of this service is intended) — looks up the `pdf_archive` rendition of the current document version; as long as it is not yet `ready`, the transfer stays here (no error, next tick retries) |
| `copied` | Archive copy written | Rendition downloaded, optionally encrypted (`ObjectType.archive_encryption_enabled`), written to the archive targets via `PUT /objects/{key}/archive-copy` |
| `verified` | Fixity check passed | `GET /objects/{key}/archive-copy/verify` — all returned copies must be `ok`, otherwise the phase is retained (see below) |
| `released` | Document marked as archived | `PUT /documents/{id}/archived` (document-service publishes `document.archived`) |
| `dehydrated` | Live copy removed | Second, independent tick phase (`run_dehydration_tick`): `released_at + dehydration_delay_days <= now`, no active legal hold (`GET /documents/{id}/has-active-hold`) → `DELETE /objects/{key}/live-copies` + `PUT /documents/{id}/dehydrated` |
| `failed_permanent` | `max_archival_attempts` exhausted | A technical failure (conversion, verification, unexpected exception) increments `attempts`; below the limit, `status` stays in its current phase (only `error_message`/`next_retry_at` change, see "Retry & Backoff" below) — only on the last permitted attempt does `status` switch here |

### Retry & Backoff (Post-Roadmap Phase 20 Session 2, [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md))

A failure **below** `Settings.max_archival_attempts` (default 5) does NOT leave the current phase —
`attempts` is incremented, `error_message` is set, and `next_retry_at` is set to a point in the near
future via `compute_backoff_seconds` (`libs/dms-retry`, full-jitter exponential backoff). `list_active_transfers`
skips a transfer whose `next_retry_at` is still in the future — the next poll tick that runs AFTER this
point picks it up again and retries the same phase. Only on the `max_archival_attempts`-th unsuccessful
attempt does `status` switch to `failed_permanent` (terminal, `next_retry_at=null`).

`POST /archival-transfers/{id}/retry` (gated by `archival.write`) manually restarts a `failed_permanent`
transfer: `409` if the transfer is not `failed_permanent`, otherwise reset to
`status="pending"`, `attempts=0`, `next_retry_at=null`, `error_message=null` — the pipeline starts from
scratch (each phase fetches its inputs fresh anyway, a restart is idempotent).

`retrieve_archival_transfer` (retrieval) resets a `released`/`dehydrated` transfer back to `status="released"` with a freshly set `released_at`/`rehydrated_at` and `dehydrated_at=null` — the transition period until the next dehydration deliberately starts over, rather than being immediately due again. **Caution with `dehydration_delay_days=0`** (e.g. for test purposes): the due-date check (`released_at <= now - delay_days`) then makes *every* `released` transfer immediately due again — a retrieval is immediately re-dehydrated by the next tick, even though `released_at` was just freshly set. Verified live (see `PROGRESS.md` "P7-S3"); with a realistic period (production default 30 days), the restored content remains reachable for the full transition period as intended.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/archival-transfers?status=...` | All transfers, optionally filtered by status (Admin UI status table) — since **P19-S7** gated by `archival.read` |
| `GET` | `/archival-transfers/{id}` | Single transfer — `404` for an unknown `id`; since **P19-S7** gated by `archival.read` |
| `POST` | `/archival-transfers/{id}/retry` | Manual restart (since **P20-S2**, [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md)) — `404` for an unknown transfer, `409` if `status != "failed_permanent"`, gated by `archival.write` |
| `POST` | `/archival-transfers/{id}/retrieve` | Retrieval — `403` without `archive_retrieval_role` in the `X-DMS-Roles` header, `404` for an unknown transfer, `409` if the transfer is not `released`/`dehydrated` (no reliable archive copy yet); since **P19-S7** additionally gated by `archival.write` (RBAC runs before the role gate) |
| `GET` | `/released-items?q=` | Records-disposal access area (2.5, since **P15-S5**) — hydrated, searchable view combining `released` documents AND cases, `403` without `archive_retrieval_role`, see "Records-Disposal Access Area" below; since **P19-S7** additionally gated by `archival.read` |
| `GET` | `/healthz` | Health check |

No `POST` route to manually create a transfer — triggering runs via `document-service`'s `POST /documents/{id}/archive-request` (sets `archive_after=now`); this service's next poll tick automatically picks up due documents.

## Data Model

`archival_transfer`: `id` (UUID PK), `document_id`, `status`, `archive_format` (`"pdf_a"`, nullable until `copied`), `encrypted` (boolean), `storage_object_key` (archive key, nullable until `copied`), `checksum_sha256` (nullable until `copied`), `error_message` (nullable), `attempts` (integer, default 0, since P20-S2), `next_retry_at` (nullable, since P20-S2), `locked_at`/`copied_at`/`verified_at`/`released_at`/`dehydrated_at`/`rehydrated_at` (each nullable, set at the respective phase transition), `created_at`/`updated_at`.

## Backend Integration

- **document-service** (`DocumentClient`): `GET .../due-for-archival`, `GET .../{id}`, `GET .../{id}/versions/{n}`, `GET .../{id}/has-active-hold`, `PUT .../{id}/archived`, `.../dehydrated`, `.../rehydrated`. Since P7-S3b, additionally `GET .../{id}/versions/{n}/content` (`download_version_content`) — returns the actual file content of a version, for XDOMEA packaging of closed cases. **Since Post-Roadmap Phase 38 Session 2**: `mark_archived`/`mark_dehydrated`/`mark_rehydrated` send a fixed `X-DMS-Principal: archival-service` header — document-service has since checked `document.disposal_callback` (see `docs/services/document-service.md` "Records Disposal & Long-Term Archiving"), granted to this exact identity via a dedicated seeded role. **Since Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)): `get_document`/`create_document`/`get_version`/`download_version_content` now send the same fixed identity, since `GET /documents/{id}`/`.../content`/`.../versions/{n}` and `POST /documents` themselves started requiring a valid principal (previously none).
- **rendering-service** (`RenderingClient`): `GET /renditions?document_id=&version_number=` (filtered client-side on `rendition_type == "pdf_archive"` — no server-side filter parameter on the rendering-service side), `GET /renditions/{id}/content`. Since **Post-Roadmap Phase 19 Session 8** ([ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md)), both calls send a synthetic `X-DMS-Principal: system:archival-service` header — rendering-service has since checked `rendering.read`.
- **storage-service** (`StorageClient`): `PUT`/`GET /objects/{key}/archive-copy`, `GET .../archive-copy/verify`, `DELETE /objects/{key}/live-copies`, `PUT /objects/{key}` (live-target write during retrieval).
- **object-type-service** (`ObjectTypeClient`): `GET /object-types/{id}` — only for `archive_encryption_enabled`.
- **case-service** (`CaseClient`, since P7-S3b): `GET .../due-for-archival`, `GET .../{id}`, `GET .../{id}/documents` (document references including a fixed `snapshot_version_number`), `PUT .../{id}/archived`, `GET .../case-archival-config` (installation-wide encryption configuration). **Since P19-S5** (case-service RBAC, [ADR 0070](../adr/0070-case-service-rbac.md)), `get_case`/`list_document_references`/`get_archival_config` send a synthetic `X-DMS-Principal: system:archival-service` header (case-service has since checked `case.read`) — `list_due_for_archival`/`mark_archived` remain without the header, case-service deliberately leaves these two ungated.

## XDOMEA Records Disposal for Cases (5.6, since P7-S3b)

Second function of this service (see above) — extends the transfer infrastructure built in P7-S3 with an XDOMEA 4.0.0 records-disposal message for closed cases (`case-service`, 2.3), instead of a PDF/A copy of a single document.

### State Machine

`CaseArchivalTransfer.status`: `pending → locked → packaged → verified → released` (+
`failed_permanent`, same retry/backoff behavior as `ArchivalTransfer` above, since
Post-Roadmap Phase 20 Session 2). **No `dehydrated` status** — unlike a document, a case has no own live content that could be removed (only references to documents with their own, independent P7-S3 archiving/dehydration lifecycle). The `Case` row itself is never deleted.

| Status | Meaning | Transition triggered by |
|---|---|---|
| `pending` | Transfer created | `discover_due_case_transfers` — creates a row for each due, closed case (`GET /cases/due-for-archival`) without an already running transfer |
| `locked` | Processing started | Case + active (not soft-deleted) document references loaded |
| `packaged` | XDOMEA package written | Document content loaded per reference, `xdomea.build_aussonderung_message()` generates + `xdomea.validate_message()` checks it against the actual vendored schema, everything packed into a ZIP file (`aussonderung.xml` + `dokumente/<package-name>` per document), optionally encrypted (`CaseArchivalConfig.archive_encryption_enabled`, case-service), uploaded via `PUT /objects/{key}/archive-copy` |
| `verified` | Fixity check passed | `GET /objects/{key}/archive-copy/verify` — all copies must be `ok`, otherwise `failed` |
| `released` | Case marked as archived | `PUT /cases/{id}/archived` (case-service publishes `case.archived`) |
| `failed_permanent` | `max_archival_attempts` exhausted | XDOMEA validation error, verification not `ok`, or an unexpected exception — identical retry/backoff behavior to `ArchivalTransfer` (see "Retry & Backoff" above) |

### Package Format

A ZIP file (`zipfile`, Python standard library, no new dependency): `aussonderung.xml` at the root (the validated 0503 message) + `dokumente/{uuid}.{ext}` per referenced document version — the filename inside the package is **the exact same value** the XML references under `Format/Primaerdokument/Dateiname` (`xdomea.package_filename()`, deterministic from `document_id`/`version_number` via `uuid.uuid5`).

### XDOMEA Message Generation (`xdomea.py`)

`build_aussonderung_message(case, documents) -> bytes` builds the `Aussonderung.Aussonderung.0503` message via `lxml.etree` and maps **`Case` → `xdomea:Vorgang`** (no `Akte` wrapper — per the schema, `Schriftgutobjekt` is an `xs:choice` between `Akte`/`Vorgang`, a bare `Vorgang` at the top level is structurally valid, matching case-service's flat data model without Akte/Vorgang nesting), **`CaseDocumentReference` → `xdomea:Dokument`** for each active reference with a fixed `snapshot_version_number`.

Deliberate simplifications (documented, not hidden):
- **`Format/Name` is always code `"100"` ("other") + `SonstigerName`** = the actual content type — no complete MIME-type-to-XDOMEA-codelist mapping. Structurally permitted: `DateiformatCodeType` per the schema only enforces `code` + an optional `name` as free text plus a `listVersionID` attribute, no XSD enumeration of the actual codelist (the codelist is a separate, non-schema-enforced vocabulary reference).
- **`Format/Version` always `"unbekannt"` ("unknown")** — this service does not track a format-specific version number (e.g. "PDF 1.4") per document version.
- **`xdomeaUUID` deterministic** (`uuid.uuid5`, not `uuid4`) from `case_id`/`document_id` — reproducible on a retry of the same transfer, only the outer `nachrichtenUUID` itself is newly generated on every build (schema requirement: "a new UUID must be generated for every message").

`validate_message(xml_bytes)` validates the generated message against the actual, locally vendored schema (`xdomea_schema/`, `lxml.etree.XMLSchema`) — raises `ValidationError` on any schema violation, no silent fallback. An `lxml.etree.Resolver` resolves the external `xoev.de` imports contained in the schema (`xoev-code.xsd`, the G2G base message module, DIN 91379 data types) to the local files — **no network access at runtime or in tests**.

### Vendored Schema Files (`xdomea_schema/`)

9 files, all sourced from the official KoSIT infrastructure (no GPL third-party mirror, see `xdomea_schema/README.md` for exact source URLs): `xdomea-Baukasten.xsd`, `xdomea-Datentypen.xsd`, `xdomea-Nachrichten-AussonderungDurchfuehren.xsd`, `xdomea-Typen-AussonderungDurchfuehren.xsd`, `xdomea-Nachrichten-AbgabeDurchfuehren.xsd`, `xdomea-Typen-AbgabeDurchfuehren.xsd` (since Post-Roadmap Phase 31 Session 13a), `xoev-code.xsd`, `xoev-basisnachricht-unqualified-g2g_1.1.xsd`, `din-norm-91379-datatypes.xsd` — the dependency chain of the `Aussonderung.Aussonderung.0503` AND `Abgabe.Abgabe.0401` messages, not the full XDOMEA schema scope. Automatically built in as package data by `hatchling` (verified: `uv build --wheel` includes all 9 `.xsd` files in the wheel).

### API

| Method | Path | Description |
|---|---|---|
| `GET` | `/case-archival-transfers?status=...` | All case transfers, optionally filtered — since **P19-S7** gated by `archival.read` |
| `GET` | `/case-archival-transfers/{id}` | Single transfer — `404` for an unknown `id`; since **P19-S7** gated by `archival.read` |
| `POST` | `/case-archival-transfers/{id}/retry` | Case counterpart to `/archival-transfers/{id}/retry` (since **P20-S2**, [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md)) |
| `GET` | `/case-archival-transfers/{id}/package` | Downloads the (optionally decrypted) ZIP package directly — `403` without `archive_retrieval_role`, `404`/`409` analogous to document retrieval. **No** writing back to a live target (unlike documents): a case has no own live storage space, only a plain download; since **P19-S7** additionally gated by `archival.read` |

### Data Model

`case_archival_transfer`: `id` (UUID PK), `case_id`, `status`, `encrypted` (boolean), `storage_object_key` (nullable until `packaged`), `checksum_sha256` (nullable until `packaged`), `error_message` (nullable), `attempts` (integer, default 0, since P20-S2), `next_retry_at` (nullable, since P20-S2), `locked_at`/`packaged_at`/`verified_at`/`released_at` (each nullable), `created_at`/`updated_at`.

## General XDOMEA Export for Inter-Agency Handoff (14.2, Post-Roadmap Phase 31 Session 13a, [ADR 0127](../adr/0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md))

Unlike the disposal pipeline above (async, multi-phase, closed-cases-only, always addressed to "Archiv"),
this is a synchronous, on-demand export of an arbitrary document or case for handoff to a named external
authority — no persisted job, no poll loop, no encryption phase.

| Method | Path | Description |
|---|---|---|
| `POST` | `/xdomea/export/documents/{id}?leser_name=...` | Exports the document's CURRENT version (same scope as document-service's own `POST /documents/{id}/export`, Phase 28) as an `Abgabe.Abgabe.0401` package — `404` unknown document, `422` empty `leser_name`, gated by `archival.write` |
| `POST` | `/xdomea/export/cases/{id}?leser_name=...` | Exports every currently-active document reference of the case (case need NOT be closed, unlike disposal) as an `Abgabe.Abgabe.0401` package — `404` unknown case, `409` if one of the case's OWN document references points at a document that no longer exists (data drift, see "A live-verification-found data-integrity distinction" below), `422` empty `leser_name`, gated by `archival.write` |

Both return the ZIP directly (`Response(media_type="application/zip")`), same response shape as
`GET /case-archival-transfers/{id}/package`.

### `Abgabe.Abgabe.0401`, not `Aussonderung.Aussonderung.0503`

Confirmed via the real, official KoSIT schema that XDOMEA 4.0.0 organizes messages into distinct groups per
process — Aussonderung (disposal), **Abgabe** (handover on jurisdiction/system change — "Die Nachricht
beschreibt den vollständigen Export von Schriftgutobjekten bei Zuständigkeitswechseln zwischen Behörden
oder bei Systemwechseln", a direct match for this session's ask), Übermittlung, Geschäftsgang, etc. `xdomea.py`
gained `build_abgabe_message_for_case`/`build_abgabe_message_for_document` + `validate_abgabe_message`
(a second, separately loaded `etree.XMLSchema` against `xdomea-Nachrichten-AbgabeDurchfuehren.xsd`) — see
[ADR 0127](../adr/0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md) for the full
design reasoning.

**A real, schema-verified surprise, found only by compiling against the actual vendored schema**: the 0401
message's `Schriftgutobjekt/Vorgang` is typed the GENERIC `xdomea:VorgangType` (`xdomea-Baukasten.xsd`),
while the 0503 message's is `VorgangAussonderungType` (`xdomea-Typen-AussonderungDurchfuehren.xsd`,
disposal-specific — requires an extra `Kontextobjekt` element `VorgangType` doesn't have at all). An
initial implementation attempt assumed these were the same shared type and failed real schema validation
immediately (`lxml.etree.DocumentInvalid: Element 'Kontextobjekt': This element is not expected.`) —
`xdomea.py` therefore has two separate Vorgang-builders, `_build_vorgang_aussonderung`/
`_build_vorgang_generic`, not one shared between both messages.
`DokumentOderDokumentMitSchriftstueckType`, by contrast, genuinely IS identical across both message
families and remains shared (`_build_dokument_wrapper`).

### A live-verification-found data-integrity distinction, fixed properly

Exporting a real dev-stack case whose `CaseDocumentReference` pointed at an already-deleted document
produced a `404` mislabeled "case unknown" — misleading, since the case itself was real; the actual problem
was one of the case's OWN document references being stale. Fixed by fetching the case in `main.py` FIRST
(translating only that lookup's `404` to "case unknown") and passing the already-fetched case dict into
`general_export.build_case_export_package`, which now raises a distinct `ReferencedDocumentMissingError`
for a missing per-reference document, translated to `409` (a data-integrity condition, not a caller
mistake) — same pre-check-before-conflation principle as `mail-connector`'s `DuplicateInTargetMailboxError`
(ADR 0124).

## General XDOMEA Import for Inter-Agency Handoff (14.2, Post-Roadmap Phase 31 Session 13b, [ADR 0128](../adr/0128-xdomea-import-existing-or-new-case-required-process-definition.md))

The mirror of the export endpoints above — `POST /xdomea/import` (multipart: `file` the uploaded ZIP
package, `folder_id` required, `case_id`/`process_definition_id` both optional and mutually exclusive)
creates the document(s) a package contains, gated by `archival.write`.

- **A package with a `Vorgang` requires exactly one of `case_id` (attach to an EXISTING case) or
  `process_definition_id` (create a brand-new one)** — `422` for either violation (both given, neither
  given, or `process_definition_id` given for a package with no `Vorgang` at all). See
  [ADR 0128](../adr/0128-xdomea-import-existing-or-new-case-required-process-definition.md) for the full
  design reasoning, including why the recommended, narrower default (existing-case-only, matching every
  other inbound-content flow in this project) was NOT what got built here — the user explicitly chose the
  larger scope.
- **A brand-new case is named after the imported Vorgang's `Betreff`**, started via the caller-supplied
  `process_definition_id` (`case-service`'s own `POST /cases` requirement — there is no XDOMEA-derivable
  value for it), with `attributes.xdomea_herkunft_uuid` set to the Vorgang's own `xdomeaUUID` for
  traceability (informational only, nothing else reads it).
- **`xdomea.parse_abgabe_message`** (new) reads a validated 0401 message back — originally scoped to
  THIS module's own export shape (the same `dokumente/<Dateiname>` ZIP layout `general_export.py`
  produces), hardened in **Post-Roadmap Phase 34 Session 4** ([ADR 0142](../adr/0142-xdomea-import-third-party-package-hardening.md))
  for genuine third-party packages: a `Schriftgutobjekt/Akte`-wrapped `Vorgang` hierarchy is recognized
  (the Akte's own Betreff/UUID names the case, its nested Vorgang's documents are found), more than one
  top-level `Vorgang` has its Betreffe combined instead of silently keeping only the first, a document's
  latest `Version` (not blindly the first) is used when several exist, and a `Dokument` with no
  retrievable primary content (schema-legal, `Version` is `minOccurs="0"`) is SKIPPED — reported via a new
  `skipped_document_count` field — rather than rejecting the entire package. **Post-Roadmap Phase 42
  Session 1 closed both remaining gaps ADR 0142 flagged**: `Teilvorgang`/`Teilakte` (sharing `VorgangType`/
  `AkteType` with `Vorgang`/`Akte` but a distinct element name, recursively unbounded per the real vendored
  schema — confirmed via direct inspection, not assumed) now serve as a FALLBACK naming source, activating
  only when the primary Akte/Vorgang candidate's own Betreff is empty (a schema-legal but degenerate case)
  — the primary naming path itself never actually fails outright for a well-formed package, since a
  `Teilvorgang` can only ever occur nested inside a real `Vorgang`-tagged ancestor the existing search
  already finds. `DokumentMitSchriftstueck` (the schema's OTHER `DokumentOderDokumentMitSchriftstueck`
  choice member — a document with nested physical/paper-page `Schriftstueck` scans, each itself a full
  `DokumentType`) is now walked the same tolerant way as `Dokument`, importing real scanned content when
  present and counting a missing one in its own, separately-tracked `skipped_schriftstueck_count` field
  (a structurally different reason for having no content than a plain `Dokument` simply missing its
  `Primaerdokument`). Looks for `Dokument` elements anywhere under a
  `Schriftgutobjekt` (covers a `Vorgang`'s nested documents, an `Akte`'s nested documents/Vorgänge's
  documents, and a standalone top-level `Dokument`, excludes the optional `Anschreiben` cover letter, a
  sibling of `Schriftgutobjekt` not a descendant). Round-trip-verified: building a package with
  `build_abgabe_message_for_case`/`_for_document` and parsing it back with `parse_abgabe_message` recovers
  the original Betreff/document metadata exactly.
- **Document creation reuses `mail-connector`'s own established client pattern**: `DocumentClient.
  create_document`/`CaseClient.create_case`/`CaseClient.add_document_reference` (all new) mirror
  `mail_connector.document_client`/`case_client`'s exact HTTP call shapes — document-service's own
  `POST /documents` already runs the mandatory virus scan (10.3), no import-specific bypass needed.
- **No frontend entry point this session** — same deliberate scoping as P31-S13a's case-level export (no
  existing case-browsing UI to attach a "pick a process definition" control to), see ADR 0128
  "Consequences".
- **A live-verification-only finding: `python-multipart` had to be added to `pyproject.toml`**. The full
  pytest suite passed cleanly with the new `File`/`Form`-based endpoint BEFORE this was added — this
  service's local dev/test run shares one workspace `.venv` where `document-service`'s own declared
  `python-multipart` dependency was already present transitively. The rebuilt Docker image, whose
  dependency set is built strictly from THIS service's own `pyproject.toml`, crash-looped on startup with
  `RuntimeError: Form data requires "python-multipart" to be installed` until it was added here directly.
  A concrete demonstration of why this project's Definition of Done requires an actual Docker rebuild +
  live verification, not just a green pytest run, before a session counts as done.

## General XJustiz Export for Inter-Agency Handoff (14.2, Post-Roadmap Phase 31 Session 13c, [ADR 0129](../adr/0129-xjustiz-uebermittlungschriftgutobjekte-general-message.md))

`POST /xjustiz/export/documents/{id}?empfaenger_name=...`/`.../cases/{id}?empfaenger_name=...` — the
XJustiz counterpart to P31-S13a's XDOMEA export endpoints, same synchronous execution model, same
`archival.write` gate, same `404`/`422`/`409` (data-drift) error shapes. Builds and validates `nachricht.
gds.uebermittlungSchriftgutobjekte.0005005` ("Übermittlung Schriftgutobjekte") — the ONE XJustiz message
this session implements, per ADR 0126's "first vertical slice" scoping. No frontend entry point this
session, see [ADR 0129](../adr/0129-xjustiz-uebermittlungschriftgutobjekte-general-message.md)
"Consequences" for why. The import direction was added in **Post-Roadmap Phase 34 Session 1**, see
"General XJustiz Import for Inter-Agency Handoff" below. A `user-ui` document-export entry point was
added in **Post-Roadmap Phase 34 Session 2** ([ADR 0140](../adr/0140-xjustiz-frontend-export-entry-point.md),
see `docs/services/user-ui.md`). Case-level export (and XDOMEA/XJustiz import) got their own frontend
entry point in **Post-Roadmap Phase 34 Session 3**, via the new `user-ui` "Umlaufmappen" case-browsing
pane ([ADR 0141](../adr/0141-case-browsing-ui-user-ui-list-detail.md), see `docs/services/user-ui.md`).

- **Why this message, out of XJustiz's real 158-message-type/30-module catalog** (confirmed directly
  against the official `xjustiz.justiz.de` 3.6.2 schema, not assumed): `uebermittlungSchriftgutobjekte` is
  a general-purpose, cross-cutting document/file transmission message defined in XJustiz's own base module
  (Grundmodul), documented as usable across every communication scenario, not tied to any single judicial
  process — the direct structural counterpart to XDOMEA's `Abgabe.Abgabe.0401` (ADR 0127).
- **New `xjustiz.py`** (mirrors `xdomea.py`'s shape): `build_uebermittlung_schriftgutobjekte_for_document`/
  `_for_case` + `validate_uebermittlung_schriftgutobjekte`, against a newly vendored schema
  (`xjustiz_schema/`, 11 XJustiz-specific files downloaded directly from `xjustiz.justiz.de`, plus
  `xoev-code.xsd`/`din-norm-91379-datatypes.xsd` reused verbatim from `xdomea_schema/` — the identical
  shared XÖV-framework files both standards depend on).
- **A real, schema-verified structural surprise**: XJustiz's `Akte` (its case-like structural unit, used
  instead of an XDOMEA-style `Vorgang`) nests its documents inside `akte/xjustiz.fachspezifischeDaten/
  inhalt/dokument`, NOT as flat siblings of `akte` — an initial attempt assumed the flatter XDOMEA shape
  and was caught only by reading `Type.GDS.Akte`'s own `inhalt` sub-structure in the vendored XSD.
- **A required attribute easy to miss from a type's own `xs:sequence`**: `nachrichtenkopf`'s
  `xjustizVersion` attribute (fixed `"3.6.2"`) is an `xs:attribute` appended AFTER `Type.GDS.
  Nachrichtenkopf`'s sequence block — found live via the schema validator's own rejection, not by
  inspection.
- **Generic-fallback codes do not line up across different XJustiz codelists**: `gds.dokumentklasse`'s
  "Andere / Sonstige" is code `001` (externally-versioned Typ3, fetched live via xrepository.de's
  genericode API); `gds.aktentyp`'s "Andere / Sonstige" is code `017` (embedded Typ2 enumeration, `001`
  there means "Zivilakte") — confirmed independently for each list, never assumed from the other.
- **`xjustiz.package_filename`'s convention is the opposite order of `xdomea.package_filename`'s**:
  `{Dokumentname}_{UUID}.{ext}` (a specification RECOMMENDATION, not schema-enforced — `dateiname` is
  plain free text) vs. XDOMEA's `{UUID}{ext}` (schema-enforced via `stringDateinameType`'s own regex).
- **No frontend entry point this session** — same deliberate scoping as
  [ADR 0127](../adr/0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md)'s
  case-export-has-no-UI decision. (The import direction, deferred at the time this section was written,
  was added in Post-Roadmap Phase 34 Session 1 — see "General XJustiz Import for Inter-Agency Handoff"
  below. A `user-ui` document-export button was added in Post-Roadmap Phase 34 Session 2, see
  [ADR 0140](../adr/0140-xjustiz-frontend-export-entry-point.md) and `docs/services/user-ui.md`. Case-level
  export and both import directions got their own frontend entry point in Post-Roadmap Phase 34
  Session 3's "Umlaufmappen" pane, see [ADR 0141](../adr/0141-case-browsing-ui-user-ui-list-detail.md).)

## General XJustiz Import for Inter-Agency Handoff (14.2, Post-Roadmap Phase 34 Session 1, [ADR 0139](../adr/0139-xjustiz-import-uebermittlung-schriftgutobjekte.md))

The mirror of the export endpoints above — `POST /xjustiz/import` (multipart: `file` the uploaded ZIP
package, `folder_id` required, `case_id`/`process_definition_id` optional and mutually exclusive) —
structurally the same shape as "General XDOMEA Import" below, reusing its exact two target-option rules
(attach to an existing case via `case_id`, or start a brand-new one via `process_definition_id`, named
after the package's `akte`'s `anzeigename`). New `xjustiz.parse_uebermittlung_schriftgutobjekte()` (the
XJustiz mirror of `xdomea.parse_abgabe_message()`) and
`general_import.import_uebermittlung_schriftgutobjekte_package()` implement it.

- **`ParsedXJustizDokument` carries only the package `dateiname`** — unlike XDOMEA's `Primaerdokument`
  (which separately carries `DateinameOriginal`/a project-specific `SonstigerName` content-type stash),
  `_build_dokument` (the export side) writes only one field; there is genuinely nothing else in this
  project's own XJustiz messages to recover, so the package filename doubles as both the ZIP lookup key
  and the imported document's title. `content_type` is passed as `None` on creation — `document-service`'s
  own magic-byte sniffing determines the real content type from the actual bytes regardless of what the
  upload's multipart hint says, confirmed directly, so guessing one from the filename extension here would
  be redundant.
- **`akte_id`** (the Akte's own `identifikation/id`, an XJustiz-internal UUID) **is kept for provenance
  only, mirroring `vorgang_xdomea_uuid`'s exact role** — deliberately NOT `aktenzeichen.freitext` (which
  the export side stashes the original `case-service` Case ID into), a real, business-meaningful XJustiz
  field in genuine third-party use, not meant for structural round-tripping.
- **Three of `general_import.py`'s four exceptions are shared verbatim with the XDOMEA import path**
  (`CaseTargetConflictError`/`CaseTargetRequiredError`/`InvalidPackageError`, already format-agnostic by
  name); a new `ProcessDefinitionWithoutAkteError` was added alongside the existing XDOMEA-specific
  `ProcessDefinitionWithoutVorgangError` rather than renaming it purely for symmetry.
- **A referenced-content-file-missing-from-the-ZIP error maps to `422`, not `409`** — verified against
  `import_xdomea`'s own identical precedent before writing this endpoint (not assumed from the plan's own
  paraphrase, which described a different, EXPORT-side data-drift scenario, `409`'s actual precedent here —
  see ADR 0139 "Rationale").
- **No frontend entry point this session** — API-only; a frontend entry point (case-scoped, attaching
  always to the currently-open case) was added in Post-Roadmap Phase 34 Session 3's "Umlaufmappen" pane,
  see [ADR 0141](../adr/0141-case-browsing-ui-user-ui-list-detail.md) and `docs/services/user-ui.md`.

## Signature Long-Term Verifiability Depends on `signature-service` (3.10/5.6)

Concept 3.10 explicitly requires the PAdES-B-LTA profile for signed documents that go through records
disposal (5.6) — a signature on a disposed document must stay verifiable long after this service has
released/dehydrated it, past the point any active correction would even be possible. This service does
**not** implement or trigger any part of that itself, and has **no runtime dependency of any kind on
`signature-service`** — this cross-reference exists purely so that a reader assessing 5.6 coverage from
this service's docs alone doesn't miss it. `signature-service` produces every signature at the full
B-LTA profile unconditionally and self-manages the periodic archive-timestamp-chain extension a B-LTA
signature needs over the years, entirely on its own (its own `document_client`, no callback into this
service or any other) — see [`docs/services/signature-service.md`](signature-service.md) "PAdES-B-LTA"
and [ADR 0155](../adr/0155-internal-tsa-and-self-contained-pades-b-lta.md).

## KeyStore Plugin (5.6, [ADR 0029](../adr/0029-aussonderung-xdomea-eigenimplementierung-kdbx-plugin.md))

`keystore.KeyStore` (ABC, one method `get_key(key_id) -> bytes`) — same plugin pattern as `storage_service.backends.interface.StorageBackend`. Shipped: `EnvKeyStore`, reads exactly one key from `Settings.archive_encryption_key` (base64, 32 bytes). **No fallback to a randomly generated key** if configuration is missing — that would change on every restart and permanently render already-encrypted archive copies undecryptable; `get_key()` instead raises `KeyNotFoundError`. `crypto.py` implements AES-256-GCM (nonce prepended to the ciphertext bytes) — simpler than the RSA-hybrid, cross-installation encryption in `workflow_service.federation_crypto`, since only a single symmetric key from the `KeyStore` is needed here, no public-key cryptography between two parties.

## Records-Disposal Access Area (2.5/5.6, since P15-S5)

Documents/cases that have already undergone records disposal but are still within the transition period remain searchable/viewable via `GET /released-items?q=`, instead of being findable only indirectly via audit trail references (Concept 5.6, literally). Complete architecture rationale: [ADR 0055](../adr/0055-aussonderungs-zugriffsbereich-hydrated-read-only-view.md).

- **Purely a filtered view, no new data store** — `browse.build_released_items` combines `repository.list_transfers(status="released")`/`list_case_transfers(status="released")` with live metadata from `document_client.get_document`/`case_client.get_case` (title/`attributes["Kennzeichen"]` or `name`/`vorgangsnummer` respectively). A single no-longer-resolvable reference is skipped (logged), not the entire request aborted — same principle as `directory_federation.search_all_peers` (ADR 0054).
- **Role gate reused** — `settings.archive_retrieval_role` (default `dms-admin`), the same role that already protects `/retrieve` and `/case-archival-transfers/{id}/package`, instead of a new setting (Concept 2.5 names "a dedicated archive/registry role" for this area — congruent with the already-existing retrieval role).
- **`purge_at`** (computed from `released_at + dehydration_delay_days`) only for documents — cases have no `dehydrated` status and thus no automatic point at which they "disappear" from this area (Concept 5.6 literally, but only technically mapped for documents) — a deliberately open point, see ADR 0055.
- **Search** as server-side substring filtering against title/name + file reference number/case reference number after hydration, no dedicated search index — sufficient for the expected order of magnitude (only items within the transition-period window).
- **Frontend uses exclusively already-existing actions** — `AussonderungPane` (User UI, IconRail 🗄️, role-gated) offers "retrieval" (documents, calls `/retrieve`) and "download package" (cases, calls `/case-archival-transfers/{id}/package`) — both endpoints already existed before this session, but were only reachable via the plain admin table in the Admin UI.

## Events

None of its own — `document.archived`/`document.dehydrated`/`document.rehydrated` are published by `document-service` when this service calls its `PUT .../archived`/`.../dehydrated`/`.../rehydrated` endpoints (see `docs/services/document-service.md`). Since P7-S3b analogously: `case.archived` is published by `case-service` when this service calls its `PUT .../archived` endpoint (see `docs/services/case-service.md`).

## Self-Registration (Concept 3.2a)

Registers itself with the registry at startup (`libs/dms-registry-client`), identical pattern to every other service. Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Tests

- `uv run pytest services/archival-service/tests` (**142 tests**, of which 4 new since **Post-Roadmap
  Phase 42 Session 1**: `test_xdomea.py` — a top-level `Akte` with no Betreff of its own falls back to a
  nested `Teilvorgang`'s Betreff for case naming, that fallback does NOT override an already-usable
  primary Betreff, a `DokumentMitSchriftstueck`'s real `Schriftstueck` content is actually imported (not
  silently dropped), and a contentless `Schriftstueck` is counted in its own `skipped_schriftstueck_count`
  rather than `skipped_document_count`; one existing `test_api.py` assertion (the exact response dict for
  a standalone-document import) extended with the new field, no behavior change. Before that, 138 tests,
  of which 5 new since **Post-Roadmap Phase 34 Session 4** ([ADR 0142](../adr/0142-xdomea-import-third-party-package-hardening.md)): `test_xdomea.py` (4 new — a document with zero `Version` elements is skipped, the latest of several versions wins over the first, an Akte-wrapped Vorgang's own Betreff/UUID is read and its nested document is found, multiple top-level Vorgänge's Betreffe are combined with all their documents collected; one existing test additionally rewritten in place from asserting a raised `ParseError` to asserting the new skip-not-reject behavior, no net count change from that one), `test_api.py` (1 new — a hand-crafted, schema-valid, mixed-content package posted to `POST /xdomea/import` succeeds for the importable document and reports `skipped_document_count` for the one that couldn't be) — before that, 133 tests, of which 14 new since **Post-Roadmap Phase 34 Session 1** ([ADR 0139](../adr/0139-xjustiz-import-uebermittlung-schriftgutobjekte.md)): `test_xjustiz.py` (4 new — `parse_uebermittlung_schriftgutobjekte` round-trips both `build_uebermittlung_schriftgutobjekte_for_case`/`_for_document`'s output exactly, including the empty-case and standalone-document shapes, and raises `ParseError` on a schema-valid-but-structurally-unusable message missing `datei/dateiname`), `test_api.py` (10 new, mirroring the ten existing `/xdomea/import` tests one-for-one — auth/role gates, a non-ZIP upload rejected, `case_id`+`process_definition_id` given together rejected, an Akte package with neither given rejected, `process_definition_id` given for an Akte-less package rejected, a standalone-document import creates it in the target folder, an Akte package attaches its documents to an EXISTING case, an Akte package creates a BRAND-NEW case named after its `anzeigename` and attaches documents to it, a package referencing a ZIP entry that isn't actually present is rejected `422`) — before that, 119 tests, of which 19 new since **Post-Roadmap Phase 31 Session 13c** ([ADR 0129](../adr/0129-xjustiz-uebermittlungschriftgutobjekte-general-message.md)): `test_xjustiz.py` (11 new — both builders validated against the real, vendored XJustiz 3.6.2 schema, a standalone document has no `akte`, empty/multi-document cases, a case's `anzeigename` set correctly AND its documents confirmed nested inside `akte/.../inhalt/dokument` — not flat siblings — a regression guard for the structural mistake found live this session, the generic "Andere / Sonstige" `aktentyp` code confirmed as `017` not `001`, reproducibility across retries, structurally-invalid-XML rejection, the `{Name}_{UUID}.{ext}` filename convention confirmed as the OPPOSITE order of XDOMEA's own, filename determinism, unsafe-character sanitization), `test_api.py` (8 new — auth/role/empty-`empfaenger_name` gates, `404` for an unknown document/case, a full document export producing a real, schema-valid ZIP, a case export excluding removed references, the same `409` data-integrity check reused for XJustiz) — before that, 100 tests, of which 14 new since **Post-Roadmap Phase 31 Session 13b** ([ADR 0128](../adr/0128-xdomea-import-existing-or-new-case-required-process-definition.md)): `test_xdomea.py` (4 new — `parse_abgabe_message` round-trips both `build_abgabe_message_for_case`/`_for_document`'s output exactly, including the empty-case and standalone-document shapes, and raises `ParseError` on a schema-valid-but-structurally-unusable message missing `Primaerdokument/Dateiname`), `test_api.py` (10 new — auth/role gates, a non-ZIP upload rejected, `case_id`+`process_definition_id` given together rejected, a Vorgang package with neither given rejected, `process_definition_id` given for a Vorgang-less package rejected, a standalone-document import creates it in the target folder with no case involvement, a Vorgang package attaches its documents to an EXISTING case, a Vorgang package creates a BRAND-NEW case named after its Betreff and attaches documents to it, a package referencing a ZIP entry that isn't actually present is rejected) — before that, 86 tests, of which 15 new since **Post-Roadmap Phase 31 Session 13a** ([ADR 0127](../adr/0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md)): `test_xdomea.py` (7 new — both `build_abgabe_message_for_case`/`build_abgabe_message_for_document` validated against the real, vendored `Abgabe.Abgabe.0401` schema, empty/multi-document cases, Betreff/leser-name/document-UUID content assertions, reproducibility across retries, a standalone document has no `Vorgang` wrapper, structurally-invalid-XML rejection, a regression guard confirming the 0503 message's own `Kontextobjekt`/`RueckmeldungArchivkennung` fields are unchanged after the `_build_vorgang` refactor split), `test_api.py` (8 new — auth/role/empty-`leser_name` gates on both new endpoints, `404` for an unknown document/case, a full document export producing a real, schema-valid ZIP, a case export excluding removed references, `409` — found live during this session's own verification, not by a test first — when a case's own document reference points at a deleted document) — before that, 71 tests, of which 15 new since **Post-Roadmap Phase 20 Session 2** — retry/backoff behavior below/upon exhaustion of `max_archival_attempts`, `next_retry_at` filtering in `list_active_transfers`, `reset_for_retry`, same pattern for `case_pipeline`, both new `retry` endpoints including `404`/`409`/`403`, see [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md)). Of these, 9 new since P15-S5: `test_keystore.py`/`test_crypto.py` (roundtrip, wrong key, missing key, fresh nonce per call), `test_repository.py` (CRUD, active-transfer detection including exclusion of terminal statuses, dehydration due-date filter), `test_pipeline.py` (full phase cascade `pending → released` against fake clients, staying in `locked` while the rendition is not ready, `failed` on failed conversion/verification, encryption path, dehydration tick including legal hold blocking), `test_api.py` (endpoint wiring with mocked external clients — role gate `403`, status gate `409`, successful retrieval including live upload/`mark_rehydrated` call). Since P7-S3b additionally: **`test_xdomea.py` validates the generated message against the actual, vendored XDOMEA 4.0.0 schema** (no mock, no simplified subset — the most valuable test of this session, verifies the complete schema chain end to end without network access), `test_case_pipeline.py` (phase cascade `pending → released` including ZIP content check, exclusion of soft-deleted document references, encryption path, `failed` on verification error), `test_api.py` extension for `/case-archival-transfers`. Since **P15-S5** additionally: `test_browse.py` (hydration of document+case, skipping unresolvable references, substring filtering against title/reference number, sorting by `released_at`), `test_api.py` extension for `/released-items` (role gate, empty, exclusion of non-`released` transfers, hydrated mixed results, search filter).
- No dedicated live Docker smoke test section here — see `PROGRESS.md` "P7-S3"/"P7-S3b" for the complete end-to-end flow across multiple services (object type with `default_archive_after_days`, PDF/`.docx`/`.png` documents, dehydration, legal hold blocking, encrypted retrieval; since P7-S3b additionally a closed case with several documents, package download, independent second validation of `aussonderung.xml` outside the pytest suite). Since **Post-Roadmap Phase 42 Session 1**: a real, schema-valid (validated against the actual vendored XSD, not just parsed) hand-built package — a top-level `Akte` with no Betreff, a nested `Vorgang`→`Teilvorgang` carrying the real Betreff and a `Dokument`, plus a second `Schriftgutobjekt` with a standalone `DokumentMitSchriftstueck`/`Schriftstueck` — posted through the real running gateway to `POST /xdomea/import` with a real `process_definition_id`: a real new case was created, correctly named after the `Teilvorgang`'s Betreff (the fallback path), both documents (the `Teilvorgang`-nested one AND the `Schriftstueck` one) were actually created with their real byte content confirmed via `GET .../content` on both, and `skipped_schriftstueck_count: 0` appeared correctly in the response. Test documents cleaned up afterward. Since **Post-Roadmap Phase 34 Session 4** ([ADR 0142](../adr/0142-xdomea-import-third-party-package-hardening.md)): a genuinely hand-crafted third-party-shaped package (a `Vorgang` moved inside a real `Akte` element, plus a second, metadata-only `Dokument` with no `Version` at all) posted directly to the rebuilt container's `POST /xdomea/import` — a real new case was created via a real `process_definition_id`, correctly named after the Akte's own Betreff (not the nested Vorgang's own, different Betreff), exactly one real document was created (the metadata-only one correctly absent), and the response reported `"skipped_document_count": 1` (see `PROGRESS.md`). Since **Post-Roadmap Phase 31 Session 13a**: both new endpoints live-verified against the real, freshly rebuilt container and real document-service/case-service data — a real document export produced a genuine, valid ZIP; a real case export succeeded for a case with zero active references AND correctly returned `409` for a real dev-stack case whose document reference had gone stale (see `PROGRESS.md`). Since
**Post-Roadmap Phase 31 Session 13b**: the import endpoint live-verified end to end against the real,
freshly rebuilt container (after fixing the `python-multipart` dependency gap found by this very rebuild,
see "General XDOMEA Import" above) — a real document export round-tripped back in as a genuine new
document; a real case export attached cleanly to an existing real case; a real case export created a
BRAND-NEW case via a real process definition, confirmed in case-service with the correct name, a real
started process instance, and the `xdomea_herkunft_uuid` traceability attribute set; all four validation
error paths (`case_id`+`process_definition_id` together, neither given for a Vorgang package, a non-ZIP
upload, missing principal) confirmed live (see `PROGRESS.md`). Since **Post-Roadmap Phase 31 Session 13c**:
both new XJustiz endpoints live-verified against the real, freshly rebuilt container (healthy on the first
rebuild this time — the `python-multipart` dependency fix from Session 13b already covers this session's
own `File`/`Form` needs) and real document-service/case-service data — a real document export and a real
case export both produced genuine, schema-valid ZIPs with the correct `xjustiz_nachricht.xml` filename and
`{Name}_{UUID}.{ext}` document filenames; all error paths (`404` unknown document, `404` unknown case,
`422` empty `empfaenger_name`, `401` missing principal) confirmed live; the SAME real, data-drift case from
Session 13a's own verification (a stale document reference) correctly returned the identical `409`
diagnosis through the XJustiz endpoint too, confirming the shared `ReferencedDocumentMissingError` check
works correctly for both formats (see `PROGRESS.md`). Since **Post-Roadmap Phase 34 Session 1**: the new
import endpoint live-verified end to end against the real, freshly rebuilt container — a real document was
uploaded, exported via `POST /xjustiz/export/documents/{id}`, and imported back via `POST /xjustiz/import`
as a standalone document with byte-identical content; a real, freshly created case (with a real
`process_definition_id`) was exported and re-imported creating a genuine BRAND-NEW case via a real process
definition, confirmed in case-service with the correct name (see `PROGRESS.md`).

## Open Points

- ~~No role/permission check except for retrieval~~ — **fixed in Post-Roadmap Phase 19 Session 7** ([ADR 0072](../adr/0072-archival-reporting-rbac.md)): all eight endpoints now check `archival.read`/`archival.write` against `permission-service` (`_require_archival_permission`, `resource_id="root"`). The existing `archive_retrieval_role` gate (X-DMS-Roles) for retrieval/`/released-items`/package download remains additionally in place, unchanged.
- ~~**No retry for `failed` transfers**~~ — **fixed in Post-Roadmap Phase 20 Session 2** ([ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md)): automatic retry with full-jitter backoff up to `max_archival_attempts`, then `failed_permanent` + manual restart via `POST .../retry`. Still open: an Admin UI visibility/control for this (P20-S7).
- **Encryption with only a single, static key** (`EnvKeyStore`) — no key rotation/multi-tenant support, see "KeyStore Plugin" above.
- **Only the 0503 message, no full XDOMEA negotiation flow** (see above) — 0501/0502/0504–0507 are not implemented, since there is no responding second system.
- **`Format/Name` always code "100" instead of a complete MIME-type-to-XDOMEA-codelist mapping** (see above) — structurally schema-valid, but semantically less precise than a real format mapping (e.g. the specific PDF code instead of "other").
- ~~No searchable "records-disposal special area" (2.5)~~ — closed since **P15-S5**, see "Records-Disposal Access Area" above and [ADR 0055](../adr/0055-aussonderungs-zugriffsbereich-hydrated-read-only-view.md).
- **Cases have no automatic "disappears after transition period" mechanism** (visible since P15-S5) — `CaseArchivalTransfer` has no `dehydrated` status, so a case remains visible in the records-disposal access area indefinitely, even long after a transition period named in Concept 5.6 has elapsed. A real case-purge concept is not part of this session, see ADR 0055 "Consequences".
- **Test fixture race in `test_api.py`'s `client` fixture** (discovered during live verification of P20-S2, pre-existing, not fixed): `TestClient(app)`'s lifespan starts the poll task before the fixture body can replace `app.state.document_client` with an `AsyncMock()` — if the very first tick hits the still-real `DocumentClient` instance, and a real, currently due test document exists on the same host (e.g. from manual live verification), a real transfer can end up in `dms_test` and cause subsequent "empty" assertions to fail. Outside this session's scope, see [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md) "Consequences".
- **General XDOMEA export (Post-Roadmap Phase 31 Session 13a) had no case-level `user-ui` entry point at first** — `case-service`'s "Case" concept had essentially no dedicated browsing UI anywhere in `user-ui` at the time, so there was no natural existing screen to attach a "export this case" button to; document-level export (`PreviewPane`) was the only frontend entry point that session. **General XDOMEA import (Post-Roadmap Phase 31 Session 13b) had NO `user-ui` entry point at all at first** — same reasoning, plus a case-import UI would additionally have needed a process-definition picker that didn't exist anywhere in `user-ui` at the time (that's `process-designer`'s domain) — the import-creates-new-case alternative therefore remained API-only until **Post-Roadmap Phase 42 Session 1** added a minimal picker (a `<select>` populated from `GET /process-definitions`, not a full `process-designer`-style picker) to the case list view's new `NewCaseImportSection`, closing this gap — see `docs/services/user-ui.md`. `parse_abgabe_message` was originally scoped to this module's own export shape only; **Post-Roadmap Phase 34 Session 4** ([ADR 0142](../adr/0142-xdomea-import-third-party-package-hardening.md)) hardened it for genuine third-party packages (`Akte`-wrapped Vorgang hierarchies, multiple top-level Vorgänge, version history, documents with no retrievable content skipped rather than rejecting the whole package) — see "General XDOMEA Import for Inter-Agency Handoff" above and ADR 0142 for the still-bounded remaining scope. **General XJustiz document export got a `user-ui` entry point in Post-Roadmap Phase 34 Session 2** ([ADR 0140](../adr/0140-xjustiz-frontend-export-entry-point.md), see `docs/services/user-ui.md`) — only the one representative message type (`uebermittlungSchriftgutobjekte`) is implemented, per ADR 0126's "first vertical slice" scoping; XJustiz's other 157 message types across 29 further specialized modules (family law, criminal law, insolvency, enforcement, etc.) are entirely out of scope. `parse_uebermittlung_schriftgutobjekte` is likewise deliberately scoped to this module's own export shape, not a general-purpose third-party XJustiz reader, same reasoning as `parse_abgabe_message` above. **Post-Roadmap Phase 34 Session 3 closed the remaining case-level UI gap**: a new minimal `user-ui` case-browsing pane ("Umlaufmappen") gained all four remaining buttons — XDOMEA case export/import and XJustiz case export/import, both imports always attaching to the currently-open case (a deliberate choice for the case-DETAIL forms, not a limitation — the case-LIST view's separate `NewCaseImportSection`, added in **Post-Roadmap Phase 42 Session 1**, is where the process-definition-picker/new-case path now lives instead) — see [ADR 0141](../adr/0141-case-browsing-ui-user-ui-list-detail.md) and `docs/services/user-ui.md`. **P31-S13 (a/b/c) and all of Phase 34 (P34-S1 through P34-S4) are now fully complete** — see [ADR 0126](../adr/0126-xdomea-general-exchange-split-download-upload-not-federation-hub.md)/[ADR 0127](../adr/0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md)/[ADR 0128](../adr/0128-xdomea-import-existing-or-new-case-required-process-definition.md)/[ADR 0129](../adr/0129-xjustiz-uebermittlungschriftgutobjekte-general-message.md)/[ADR 0139](../adr/0139-xjustiz-import-uebermittlung-schriftgutobjekte.md)/[ADR 0140](../adr/0140-xjustiz-frontend-export-entry-point.md)/[ADR 0141](../adr/0141-case-browsing-ui-user-ui-list-detail.md)/[ADR 0142](../adr/0142-xdomea-import-third-party-package-hardening.md).
- ~~**Automatic DMS-to-DMS package handoff via `federation-hub-service` remains scoped but not built**~~ — **built in Post-Roadmap Phase 43 Session 1** ([ADR 0159](../adr/0159-dms-to-dms-xdomea-handoff-implementation.md), implementing the Post-Roadmap Phase 37 Session 1 scoping, [ADR 0147](../adr/0147-cross-installation-xdomea-handoff-scoping.md)): `workflow-service`'s `taskType=federated` now has a reserved `xdomea.case_handoff` process type that calls the general export/import endpoints below automatically on both sides — no changes needed HERE at all, both endpoints were reused completely unchanged (a new `archival_client.py` in `workflow-service` calls them exactly as a human/the existing UI would). See `docs/services/workflow-service.md` "DMS-to-DMS XDOMEA handoff" for the full mechanism. The manual download/upload flow remains the only way to hand a package to a genuinely foreign, non-DMS system, which ADR 0147 found is not a gap that further engineering here could close.
- **`general_export.build_case_export_package` silently excludes an OPEN case's document references, found live during Post-Roadmap Phase 43 Session 1's own verification of the session above** — the filter (`r["removed_at"] is None and r["snapshot_version_number"] is not None`, shared with the disposal pipeline's own `case_pipeline._build_package`) only ever matches a reference once `snapshot_version_number` has been fixed, which happens exclusively when a case is **closed**. ADR 0127 explicitly states "unlike disposal, the case does NOT need to be closed first," but in practice exporting a still-open case with real, active document references produces a schema-valid but Betreff-only package (zero `Dokument` children) with no error or warning — confirmed live: the identical case/export call before and after closing the case (completing its own BPMN task) went from a 1-file ZIP (`abgabe.xml` only) to a 2-file ZIP including the document, no other change. Affects BOTH `POST /xdomea/export/cases/{id}` (the pre-existing, human-driven endpoint, since Phase 31) and the new automatic handoff above equally - not something the handoff session introduced, but found by it. A future session should decide whether the export should use each reference's `current_version_number` as a fallback when `snapshot_version_number` is unset, or whether "case must be closed first" should instead become an enforced precondition (contradicting ADR 0127's stated intent) - not decided here, out of scope for a session about transport, not export semantics.
