# archival-service

**Responsibility:** Records disposal & long-term archiving of documents and cases (5.6). **Documents**: after the active phase expires (object type deadline or manual trigger), transfers documents mandatorily as PDF/A (fallback: plain PDF) to a separate archive target, removes the live storage copy after a transition period ("dehydration"), and provides an audited, role-gated retrieval process. **Cases** (since P7-S3b): for closed cases, generates a real, schema-validated XDOMEA 4.0.0 records-disposal message + packages the referenced document contents into a transfer package. In both cases, coordinates only the transfer mechanics — `document-service`/`case-service` remain the sole authority for the respective lifecycle fields.

**Concept Reference:** 5.6
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

- **document-service** (`DocumentClient`): `GET .../due-for-archival`, `GET .../{id}`, `GET .../{id}/versions/{n}`, `GET .../{id}/has-active-hold`, `PUT .../{id}/archived`, `.../dehydrated`, `.../rehydrated`. Since P7-S3b, additionally `GET .../{id}/versions/{n}/content` (`download_version_content`) — returns the actual file content of a version, for XDOMEA packaging of closed cases.
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

7 files, all sourced from the official KoSIT infrastructure (no GPL third-party mirror, see `xdomea_schema/README.md` for exact source URLs): `xdomea-Baukasten.xsd`, `xdomea-Datentypen.xsd`, `xdomea-Nachrichten-AussonderungDurchfuehren.xsd`, `xdomea-Typen-AussonderungDurchfuehren.xsd`, `xoev-code.xsd`, `xoev-basisnachricht-unqualified-g2g_1.1.xsd`, `din-norm-91379-datatypes.xsd` — exactly the dependency chain of the `Aussonderung.Aussonderung.0503` message, not the full XDOMEA schema scope. Automatically built in as package data by `hatchling` (verified: `uv build --wheel` includes all 7 `.xsd` files in the wheel).

### API

| Method | Path | Description |
|---|---|---|
| `GET` | `/case-archival-transfers?status=...` | All case transfers, optionally filtered — since **P19-S7** gated by `archival.read` |
| `GET` | `/case-archival-transfers/{id}` | Single transfer — `404` for an unknown `id`; since **P19-S7** gated by `archival.read` |
| `POST` | `/case-archival-transfers/{id}/retry` | Case counterpart to `/archival-transfers/{id}/retry` (since **P20-S2**, [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md)) |
| `GET` | `/case-archival-transfers/{id}/package` | Downloads the (optionally decrypted) ZIP package directly — `403` without `archive_retrieval_role`, `404`/`409` analogous to document retrieval. **No** writing back to a live target (unlike documents): a case has no own live storage space, only a plain download; since **P19-S7** additionally gated by `archival.read` |

### Data Model

`case_archival_transfer`: `id` (UUID PK), `case_id`, `status`, `encrypted` (boolean), `storage_object_key` (nullable until `packaged`), `checksum_sha256` (nullable until `packaged`), `error_message` (nullable), `attempts` (integer, default 0, since P20-S2), `next_retry_at` (nullable, since P20-S2), `locked_at`/`packaged_at`/`verified_at`/`released_at` (each nullable), `created_at`/`updated_at`.

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

- `uv run pytest services/archival-service/tests` (**71 tests**, of which 15 new since **Post-Roadmap Phase 20 Session 2** — retry/backoff behavior below/upon exhaustion of `max_archival_attempts`, `next_retry_at` filtering in `list_active_transfers`, `reset_for_retry`, same pattern for `case_pipeline`, both new `retry` endpoints including `404`/`409`/`403`, see [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md)). Of these, 9 new since P15-S5: `test_keystore.py`/`test_crypto.py` (roundtrip, wrong key, missing key, fresh nonce per call), `test_repository.py` (CRUD, active-transfer detection including exclusion of terminal statuses, dehydration due-date filter), `test_pipeline.py` (full phase cascade `pending → released` against fake clients, staying in `locked` while the rendition is not ready, `failed` on failed conversion/verification, encryption path, dehydration tick including legal hold blocking), `test_api.py` (endpoint wiring with mocked external clients — role gate `403`, status gate `409`, successful retrieval including live upload/`mark_rehydrated` call). Since P7-S3b additionally: **`test_xdomea.py` validates the generated message against the actual, vendored XDOMEA 4.0.0 schema** (no mock, no simplified subset — the most valuable test of this session, verifies the complete schema chain end to end without network access), `test_case_pipeline.py` (phase cascade `pending → released` including ZIP content check, exclusion of soft-deleted document references, encryption path, `failed` on verification error), `test_api.py` extension for `/case-archival-transfers`. Since **P15-S5** additionally: `test_browse.py` (hydration of document+case, skipping unresolvable references, substring filtering against title/reference number, sorting by `released_at`), `test_api.py` extension for `/released-items` (role gate, empty, exclusion of non-`released` transfers, hydrated mixed results, search filter).
- No dedicated live Docker smoke test section here — see `PROGRESS.md` "P7-S3"/"P7-S3b" for the complete end-to-end flow across multiple services (object type with `default_archive_after_days`, PDF/`.docx`/`.png` documents, dehydration, legal hold blocking, encrypted retrieval; since P7-S3b additionally a closed case with several documents, package download, independent second validation of `aussonderung.xml` outside the pytest suite).

## Open Points

- ~~No role/permission check except for retrieval~~ — **fixed in Post-Roadmap Phase 19 Session 7** ([ADR 0072](../adr/0072-archival-reporting-rbac.md)): all eight endpoints now check `archival.read`/`archival.write` against `permission-service` (`_require_archival_permission`, `resource_id="root"`). The existing `archive_retrieval_role` gate (X-DMS-Roles) for retrieval/`/released-items`/package download remains additionally in place, unchanged.
- ~~**No retry for `failed` transfers**~~ — **fixed in Post-Roadmap Phase 20 Session 2** ([ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md)): automatic retry with full-jitter backoff up to `max_archival_attempts`, then `failed_permanent` + manual restart via `POST .../retry`. Still open: an Admin UI visibility/control for this (P20-S7).
- **Encryption with only a single, static key** (`EnvKeyStore`) — no key rotation/multi-tenant support, see "KeyStore Plugin" above.
- **Only the 0503 message, no full XDOMEA negotiation flow** (see above) — 0501/0502/0504–0507 are not implemented, since there is no responding second system.
- **`Format/Name` always code "100" instead of a complete MIME-type-to-XDOMEA-codelist mapping** (see above) — structurally schema-valid, but semantically less precise than a real format mapping (e.g. the specific PDF code instead of "other").
- ~~No searchable "records-disposal special area" (2.5)~~ — closed since **P15-S5**, see "Records-Disposal Access Area" above and [ADR 0055](../adr/0055-aussonderungs-zugriffsbereich-hydrated-read-only-view.md).
- **Cases have no automatic "disappears after transition period" mechanism** (visible since P15-S5) — `CaseArchivalTransfer` has no `dehydrated` status, so a case remains visible in the records-disposal access area indefinitely, even long after a transition period named in Concept 5.6 has elapsed. A real case-purge concept is not part of this session, see ADR 0055 "Consequences".
- **Test fixture race in `test_api.py`'s `client` fixture** (discovered during live verification of P20-S2, pre-existing, not fixed): `TestClient(app)`'s lifespan starts the poll task before the fixture body can replace `app.state.document_client` with an `AsyncMock()` — if the very first tick hits the still-real `DocumentClient` instance, and a real, currently due test document exists on the same host (e.g. from manual live verification), a real transfer can end up in `dms_test` and cause subsequent "empty" assertions to fail. Outside this session's scope, see [ADR 0078](../adr/0078-archival-service-retry-backoff-failed-permanent.md) "Consequences".
