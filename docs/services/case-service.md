# case-service

**Responsibility:** Cases (Concept 2.3) — bundle references (not their own copies) to documents belonging to a matter. Own lifecycle via a process instance in [workflow-service](../adr/0018-spiffworkflow-lgpl-license.md) (7.1, P6-S1): while the case is open, the current version of each referenced document is resolved dynamically; upon reaching the BPMN end state, the reference structure is fixed as a **closure snapshot**. No UI. Since Post-Roadmap Phase 19 Session 5 (ADR 0070), gated by `case.read`/`case.write` RBAC (see "Open Points").

**Concept Reference:** 2.3, 5.6 (records disposal, since P7-S3b)
**Own Postgres Schema:** `case` (tables `cases`, `case_document_reference`, `case_archival_config`) — `"case"` is a reserved SQL keyword (`CASE WHEN`); SQLAlchemy quotes it automatically in generated DDL statements, but raw SQL strings (`main.py`, `tests/conftest.py`) must quote it themselves as `"case"`. The table for cases is therefore deliberately named `cases` (plural), not `case`, to avoid needing this quoting requirement twice.

## API

**RBAC since Post-Roadmap Phase 19 Session 5** ([ADR 0070](../adr/0070-case-service-rbac.md)): every endpoint below requires `X-DMS-Principal` (`401` without it) and checks `case.read`/`case.write` against `permission-service` (`403` on rejection, `resource_id="root"`) — **except** `GET /cases/due-for-archival` and `PUT /cases/{id}/archived`, both purely internal callbacks from `archival-service` with no human caller, deliberately ungated.

| Method | Path | Description |
|---|---|---|
| `POST` | `/cases` | Create (`name`, optional `object_type_id`/`attributes`, `process_definition_id`, `created_by`, optional `initial_data`) — validates `object_type_id` (if set) against the Object-Type Service (always as a root object, no folder parentage), then starts a process instance in workflow-service with `business_key = case_id`. `400` for an unknown `process_definition_id`. Since **P15-S3**, also automatically assigns a `vorgangsnummer` (case reference number, 2.3/2.5), see below |
| `GET` | `/cases` | List, filter by `status`/`object_type_id` |
| `GET` | `/cases/by-vorgangsnummer?value=...` | Case reference number lookup (2.5/3.3, since P15-S3) — registered before `/cases/{id}`. Returns a list (consistent with document-service's file reference number lookup), even though `vorgangsnummer` is globally unique by construction. For the new `mail-connector` |
| `GET` | `/cases/due-for-archival` | Internal call from `archival-service` (5.6, since P7-S3b) — registered before `/cases/{id}` so that `"due-for-archival"` is not interpreted as `{case_id}` |
| `GET` | `/cases/{id}` | Detail — `404` |
| `POST` | `/cases/{id}/documents` | Add document reference (`document_id`, `added_by`) — `400` if `document_id` is unknown according to the Document Service, `404` if the case is unknown, `409` if it is already closed |
| `DELETE` | `/cases/{id}/documents/{document_id}` | Soft-remove a reference (`removed_by`) — the row is retained (traceability), `404`/`409` analogous |
| `GET` | `/cases/{id}/documents` | All references (active + removed) including resolved version: open → live `current_version_number`/`deleted_at` from the Document Service, closed → fixed `snapshot_version_number`, no more Document Service calls needed |
| `POST` | `/cases/{id}/archive-request` | Manual records-disposal trigger (5.6, since P7-S3b) — `409` if the case is not yet closed |
| `GET` | `/cases/{id}/archive-status` | Read disposal status (`archive_after`/`archived_at`, since P7-S3b) |
| `PUT` | `/cases/{id}/archived` | Internal callback from `archival-service`, once the XDOMEA package is verified (since P7-S3b) — publishes `case.archived` |
| `GET`/`PUT` | `/case-archival-config` | Installation-wide disposal configuration (`default_archive_after_days_closed`, `archive_encryption_enabled`, since P7-S3b) |
| `GET`/`PUT` | `/case-number-config` | Case reference number format string (2.5, since P15-S3, default `{YYYY}-{Laufende_Nummer}`) — `400` on an unknown placeholder or missing `{Laufende_Nummer}` |
| `GET` | `/healthz` | Health check |

## Data Model

- `cases`: `id` (UUID), `name`, `object_type_id` (opaque reference, optional), `attributes` (JSON), `status` (`"open"`|`"closed"`), `process_definition_id`/`process_instance_id` (opaque references to workflow-service), `created_by`/`created_at`, `closed_at` (nullable), `archive_after`/`archived_at` (both nullable, 5.6, since P7-S3b), `vorgangsnummer` (nullable — only assigned for cases newly created from P15-S3 onward, 2.3/2.5).
- `case_document_reference`: `id`, `case_id` (FK), `document_id` (opaque reference to document-service), `added_by`/`added_at`, `removed_by`/`removed_at` (both nullable — soft deletion instead of hard delete), `snapshot_version_number` (nullable, only set after closure).
- `case_archival_config` (5.6, since P7-S3b): single row (`id=1`, same singleton pattern as document-service's `RetentionConfig`) — `default_archive_after_days_closed` (integer, nullable), `archive_encryption_enabled` (boolean), `updated_at`.
- `case_number_config`/`case_sequence` (2.5, since P15-S3): see "Case Reference Number" below.

The `business_key` of the started process instance is deliberately **identical to the case ID** (no separate field) — the sole basis on which `consumer.py` matches a later instance completion to the right case (see "Closure Snapshot" below).

## Two-Stage Reference Model (2.3)

- **While the case is open**: `GET /cases/{id}/documents` resolves `current_version_number`/`deleted_at` live for every active reference via `GET /documents/{id}` (document-service) — no own state, always the current status. A soft-deleted original remains retrievable through this endpoint (`deleted_at` set instead of `404`) — this alone already covers the concept requirement "reference remains traceable when the original is deleted," without any extra logic in this service.
- **Closure snapshot**: once the associated process instance reaches the BPMN end state (`workflow.instance.completed`, see below), the then-current version is fixed in `snapshot_version_number` for every reference still active at that point. Later changes to the referenced original document no longer affect this case afterward. References already removed (soft-deleted before closure) remain without a snapshot.
- If a referenced document is no longer reachable via document-service at closure time, the reference remains without a `snapshot_version_number` (no error, no lost reference) — the same "remains traceable" handling as for regular read access.

## Closure Snapshot: Event Instead of Polling

case-service is the **first consumer** of workflow-service's `workflow.instance.completed` (previously a pure producer, see `docs/services/workflow-service.md`). The handler (`consumer.py`) looks up the case directly via `business_key` — if it finds no matching case (instance does not originate from case-service) or it is already `closed`, the event is ignored. There is no polling endpoint in workflow-service; this event is the only available mechanism.

**Known race condition** (not fixed, see "Open Points"): if `POST /cases` starts a fully automated process without a manual task, workflow-service may already publish `workflow.instance.completed` before this service's own `Case` row is committed within the same request — the event would then find no matching case and be lost, leaving the case incorrectly `open`. For this session's live verification, a process with a manual task was deliberately used (only reaching the end state after explicit task completion, well after the `POST /cases` response), so this case does not occur.

## Object Type Integration (2.2)

Like document-service/folder-service: `object_type_id` is optional; if set, `POST /object-types/{id}/validate` is called. Unlike documents/folders, this **always assumes a root object** (`parent_object_type_id=None`, `parent_is_root=True`) — a case does not conceptually live in the folder tree (2.2a), but is a standalone object type.

## Records Disposal (5.6, since P7-S3b)

Only **closed** cases are eligible for records disposal — the actual transfer mechanics (generating the XDOMEA 4.0.0 message, packaging referenced document contents, encrypting, archiving) live entirely in `archival-service` (see `docs/services/archival-service.md` "XDOMEA Records Disposal for Cases"), this service remains the sole authority for the case lifecycle fields.

- **Triggering**: unlike documents (`ObjectType.default_archive_after_days`, per object type), there is **no per-object-type default** here — `ObjectType.applies_to` is strictly `"document"`|`"folder"`, cases use `object_type_id` only loosely for attribute validation, with no own `applies_to` category. Instead, an **installation-wide** `CaseArchivalConfig.default_archive_after_days_closed` (singleton, same pattern as `RetentionConfig`), resolved in `close_case()` at closure time (`archive_after = closed_at + default_archive_after_days_closed`) — not at creation as with documents, since records disposal is not possible before that. Additionally a manual trigger `POST /cases/{id}/archive-request`, which returns `409` if the case is not yet closed.
- **Encryption**: also installation-wide (`CaseArchivalConfig.archive_encryption_enabled`), for the same reason there is no counterpart to `ObjectType.archive_encryption_enabled`.
- **No dehydration for cases themselves** — a case has no own live content (only references); only the referenced documents go through their own, independent P7-S3 archiving/dehydration cycle.
- **`GET /cases/due-for-archival`** filters on `status="closed" AND archive_after <= now AND archived_at IS NULL` — registered before `/cases/{id}` (route ordering, see above).

## Case Reference Number (2.3/2.5, since P15-S3)

Every new case automatically gets a server-generated, **installation-wide unique** `vorgangsnummer` (case reference number) (`POST /cases` internally calls `repository.next_vorgangsnummer()` before the row is created) — basis for the new `mail-connector` (2.5/3.3), which needs to be able to automatically match incoming mail to a case based on a case reference number found in the subject/body.

- **A single installation-wide counter instead of a per-object-type counter** (unlike document-service's file reference number generator, P5e-S1) — `ObjectType.applies_to` does not recognize `"case"` as its own category (see "Object Type Integration" above); a separate, simpler generator directly in this service avoids an invasive extension of `object-type-service`. Format configurable via `GET`/`PUT /case-number-config` (default `{YYYY}-{Laufende_Nummer}`, placeholders `{YYYY}`/`{YY}`/`{Laufende_Nummer}`), atomic year counter (`case_sequence`, `SELECT ... FOR UPDATE`, identical idiom to `object_type_service.ObjectTypeSequence`).
- **Not changeable via PATCH** (unlike `Kennzeichen`, the file reference number) — a stable, purely system-assigned reference is a prerequisite for reliable matching, no use case for a subsequent admin change in this session.
- **`GET /cases/by-vorgangsnummer?value=...`** returns a list (consistent with the analogous document endpoint), even though the case reference number is globally unique by construction.
- Complete architecture rationale: [ADR 0053](../adr/0053-posteingang-postausgang-pop3-loopback-connector-and-cross-service-matching.md).

## Events

**Publishes** (stream `case`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `case.created` | `{name, created_by}` |
| `case.document.added` | `{document_id, added_by}` |
| `case.document.removed` | `{document_id, removed_by}` |
| `case.closed` | `{process_instance_id}` |
| `case.archived` | `{}` (5.6, since P7-S3b) — callback from `archival-service`, once the XDOMEA package is verified |

**Consumes:** `workflow.instance.completed` (see "Closure Snapshot" above).

**Audit integration**: since this session, the Audit Service also consumes `case.>` (same immediate-addition pattern as for every previous new producer stream).

## Self-Registration (Concept 3.2a)

Registers itself with the registry at startup (`libs/dms-registry-client`), identical pattern to every other service. Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`. The gateway needs no code change of its own — routing runs fully dynamically via `service_type="case-service"`.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Tests

`uv run pytest services/case-service/tests` (**45 tests**, of which 6 new since **P15-S3**: unique `vorgangsnummer` for each new case, `GET /cases/by-vorgangsnummer` hit/empty, `case-number-config` roundtrip including rejection of a format without `{Laufende_Nummer}`/with an unknown placeholder; previously 39 tests, of which 13 new since P7-S3b):
- `test_repository.py` — pure DB logic (creating, adding/removing references, closure snapshot including edge cases: a removed reference stays without a snapshot, a missing `document_id` in the snapshot dict stays without a snapshot) — runs against real Postgres as everywhere in the project, no HTTP calls (`repository.py` knows nothing about sibling services). Since P7-S3b additionally: `archive_after` resolution in `close_case` (with/without a configured default), `CaseArchivalConfig` CRUD, `list_due_for_archival` filter, `request_archive` including `CaseNotClosedError` for an open case, `mark_archived`.
- `test_consumer.py` — a simulated `workflow.instance.completed` event sent directly to the handler (no real NATS needed, same pattern as `notification-service/tests/test_consumer.py`), fake `DocumentClient` instead of real HTTP.
- `test_api.py` — real integration tests against locally reachable `workflow-service`/`document-service`/`object-type-service` instances (same pattern as document-service's `folder_client`/`object_type_client` tests) — each test case creates its own process definition/test document. Deliberately does **not** cover actual asynchronous event delivery (see `test_consumer.py` for consumer logic, live smoke test for end-to-end wiring). Since P7-S3b additionally: `/cases/due-for-archival`, `/case-archival-config` roundtrip, `409` for a records-disposal request on an open case, complete trigger→callback roundtrip for a case closed directly via `session`/`repository` (no full BPMN run needed to test only the records-disposal endpoints).
- **Live smoke test**: `docker compose build case-service document-service` + `up -d`, uploaded a BPMN process with a manual task, created a case (`process_instance_id` set), referenced two documents, checked in a new document version (dynamic reference confirmed: `GET .../documents` shows the new version), completed the manual task, waited briefly, confirmed closure (`status="closed"`, `snapshot_version_number` fixed, further version changes to the original no longer take effect) — test data subsequently deleted.
- Pure backend session, no browser test needed (not on the UI sessions list in `IMPLEMENTATION_PLAN.md`).

## Open Points

- ~~No role check/RBAC~~ — **fixed in Post-Roadmap Phase 19 Session 5** ([ADR 0070](../adr/0070-case-service-rbac.md)): all human-usable endpoints now check `case.read`/`case.write` against `permission-service` (`X-DMS-Principal` required). The two purely internal machine-to-machine callbacks (`GET /cases/due-for-archival`, `PUT /cases/{id}/archived`) remain deliberately ungated, see ADR 0070 "Decision".
- **No resource tree entry in permission-service** (still open, documented as a deliberate trade-off since P19-S5 — ADR 0070): unlike folder-service, case-service does not register `resource.created`/`.moved`/`.deleted` events — there is no existing precedent for non-folder objects in the permission-service tree (document-service does not register its documents individually either), and a case has no folder parent node anyway. The new RBAC check (see above) therefore uniformly uses `resource_id="root"` instead of a case-specific resource — fine-grained control per case would require this tree structure first.
- **Race condition for fully automated processes without a manual task** — see "Closure Snapshot" above. Not fixed (outside this session's core scope), cases with a process that already completes synchronously at start incorrectly remain `"open"` in this rare case.
- **Process-specific working copies (2.3)** deliberately do not live here, but as an extension of document-service (`derived_from_document_id`/`derived_from_version_number`/`originating_case_id` on `POST /documents`, since this session) — see `docs/services/document-service.md` "Working Copies". case-service itself does not create working copies.
- **No existence check for `object_type_id` outside of validation** — like document-service/folder-service, no retroactive check for subsequently changed object type constraints.
- **No cross-phase link to P15-S3 wired up yet** — the mail inbox/mail room functionality there is planned to build on this case API, but is not part of this session.
- ~~No role/permission check on the new records-disposal endpoints~~ (5.6, since P7-S3b) — **partially fixed in P19-S5** (ADR 0070): `POST /cases/{id}/archive-request` (human action) is now gated (`case.write`). `PUT /cases/{id}/archived` (internal callback from `archival-service`, no human caller) remains deliberately ungated — consistent with document-service's own, likewise ungated `PUT /documents/{id}/archived`.
- **No retry for a failed records disposal** — a `failed` `CaseArchivalTransfer` in `archival-service` remains terminal; a repeated `POST /cases/{id}/archive-request` here would fail on the active-transfer exclusion there, as long as the old row is not handled separately.
