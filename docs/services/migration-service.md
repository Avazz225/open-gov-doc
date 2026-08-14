# migration-service

**Responsibility:** Migration/Transfer Service (Concept 7.2, P12-S2): lock → copy →
verify → release in the target system → deletion in the source system after a transition
period, between two directly paired installations of this software (no hub, see ADR 0034 —
unlike 7.4/Federation Hub, 7.2 describes a one-time, standalone transfer, not an ongoing
federated operation). Runs **itself as an auditable, resumable workflow** via
`workflow-service` (7.2 literally: "not as a special case alongside it") — every instance of
this service can be both the source and the target of a transfer.

**Concept reference:** 7.2
**Own Postgres schema:** `migration` (tables `paired_installation`, `transfer`,
`inbound_transfer`)
**ADR:** [0034 — Direct installation pair + generic connector service tasks](../adr/0034-migration-service-direct-pairing-and-generic-connector-service-tasks.md)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/paired-installations` | Pair a target/source installation — leave `api_key` empty to generate a new one (returned once), or enter the key already issued by the counterpart |
| `GET`/`DELETE` | `/paired-installations[/{id}]` | List (never with `api_key`) / remove |
| `POST` | `/transfers` | Start a transfer — four-eyes-capable (4.3, `action_type=migration.transfer.start`), `404` for an unknown target, `dry_run`/`retention_days` optional |
| `GET` | `/transfers[/{id}]` | Status/list, optionally filtered by `status` |
| `POST` | `/transfers/{id}/steps/{lock\|copy\|verify\|release\|delete-source\|dry-run-check}` | Internal — target of the `connector_call` service tasks in `resources/*.bpmn`, not intended for external callers |
| `POST` | `/inbound/transfers[/...]` | Target side — called by a paired source, `Authorization: Bearer <api_key>` |
| `GET` | `/healthz` | Health check (ungated) |

## Flow (source role)

1. **`POST /transfers`** checks `ApprovalClient.requires_approval("migration.transfer.start")` (4.3,
   P6-S4 pattern) — with four-eyes active, only an `ApprovalRequest` is created, and the actual
   start happens only once `consumer.py` consumes `permission.approval.approved`. Otherwise
   `_start_transfer()` creates the `transfer` row, **commits it immediately** (not only at the end
   of the request — the first `connector_call` step fires synchronously back onto this very
   service, and a new transaction would not yet see the row) and starts a real BPMN instance in
   `workflow-service` (`migration_transfer.bpmn` or `migration_dry_run.bpmn`).
2. Each step of the BPMN definition is a `bpmn:serviceTask` with `camunda:properties`
   `taskType=connector_call`, `serviceUrl=.../transfers/{transfer_id}/steps/<phase>` —
   `workflow-service` calls these synchronously and merges the JSON response into the process
   data (see `docs/services/workflow-service.md` "Connector service tasks").
3. **`lock`**: `POST permission-service /scope-locks` on `source_folder_id` (4.7, `blocks_read
   =false` — 7.2 only requires "no write access during the migration," reading remains
   permitted).
4. **`copy`**: `POST .../inbound/transfers` on the target side (creates the target folder,
   registers `inbound_transfer`), then a recursive tree traversal (`DmsTreeClient.list_children`)
   — for each folder, its role assignments are transferred first (`permission-service`'s
   `role-assignments`, by role name instead of local `role_id`, get-or-create on the target
   side), then documents (`.../inbound/transfers/{id}/documents`, checksums are collected for
   the verify step), then subfolders (`.../inbound/transfers/{id}/folders`), recursively.
5. **`verify`**: `POST .../inbound/transfers/{id}/verify` with the collected target document IDs
   — the target side reports back its own, freshly computed checksums, compared against the
   values noted during copying.
6. **`release`**: `POST .../inbound/transfers/{id}/release` + releasing the scope lock.
7. **Deletion period**: a `bpmn:intermediateCatchEvent` timer waits `retention_days` days
   (process variable `retention_duration`, e.g. `"P30D"`) — driven by `workflow-service`'s
   already existing SLA poll loop (P6-S2), no own scheduler infrastructure needed.
8. **`delete-source`**: `POST folder-service /folders/{source_folder_id}/trash` — cascades
   automatically over the entire subtree (folders + documents, P7-S1b).

## Dry run (7.2)

Its own, shorter BPMN definition (`migration_dry_run.bpmn`, a single `connector_call` step
`dry-run-check`) instead of a gateway in the main process — none of the other four phases
(lock/copy/verify/delete) is executed. Currently only checks reachability and existence of the
target folder (`GET /folders/{id}` on the target side) — **deliberate limitation**: no full
object-type/constraint compatibility analysis, as 7.2 cites as an example ("matching object types
present?").

## Direct installation pair instead of a hub (ADR 0034)

`paired_installation` stores the API key in **plain text** — unlike `federation-hub-
service`'s `Installation` (hash only, ADR 0028), since this installation must both actively
present the key as a source and verify it as a target (`hmac.compare_digest`, constant time).
`POST /paired-installations` generates a new key if `api_key` is missing (returned once,
as with `federation-hub-service`), or adopts a key already issued by the counterpart
unchanged — the admin manually enters the same key on both sides.

## `asyncio.to_thread()` for all DMS/peer calls (ADR 0034)

`LocalDmsClient`/`PeerClient` are deliberately synchronous (`httpx.Client`, like
`dms-connector-sdk` itself, see its README). Every call runs via `asyncio.to_thread()` instead
of directly in the `async def` endpoints — a synchronous HTTP call directly there would block
the entire event loop thread. In the self-loopback case (see below) this leads to a **real
deadlock**: the blocking call waits for a response from exactly the thread it is itself
blocking — this actually occurred (`httpx.ReadTimeout`) and was fixed via `asyncio.to_thread()`.

## permission-service gating (since Post-Roadmap Phase 19 Session 6, ADR 0071)

`permission-service`'s `POST`/`PUT /roles` and `POST`/`DELETE /scope-locks` have since
required `admin.user_management`. `LocalDmsClient` (`dms_client.py`) now sends an
`X-DMS-Principal: migration-service` header for this (previously none) — affects
`acquire_scope_lock`/`release_scope_lock` AND `apply_role_assignment`'s get-or-create of the
target role. `main.py`'s `_ensure_config_admin_permission()` has since additionally
bootstrapped `domain-admin-users` (`admin.user_management`) alongside the previous
`domain-admin-config` role (`admin.object_config`) — `_REQUIRED_ROLE_NAMES =
("domain-admin-config", "domain-admin-users")`.

## Deliberate limitations

- **Self-loopback instead of a real two-installation test**: setting up a second independent
  stack is not practical in the sandbox — the same convention already established for
  `federation-hub-service` (P6-S9).
- **No historical timestamps for migrated versions** — `document-service`'s check-in sets
  `created_at`/`created_by` server-side; migrated versions carry the migration timestamp.
- **`principal_id` remains opaque** for copied permissions — no identity reconciliation between
  the user populations of two installations, works correctly with a shared user base.
- **Only the current document version is migrated**, not the full version history.
- **Migrated folders always land at the root** of the target installation — no selection of
  a different target location in this session.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DMS_DOCUMENT_SERVICE_BASE_URL` | `http://localhost:8006` | Local document-service |
| `DMS_FOLDER_SERVICE_BASE_URL` | `http://localhost:8008` | Local folder-service |
| `DMS_PERMISSION_SERVICE_BASE_URL` | `http://localhost:8004` | Local permission-service (locks, roles) |
| `DMS_WORKFLOW_SERVICE_BASE_URL` | `http://localhost:8014` | workflow-service (BPMN orchestration) |
| `DMS_DEFAULT_RETENTION_DAYS` | `30` | Default transition period, if `POST /transfers` does not specify one explicitly |
| `MIGRATION_SERVICE_PORT` | `8028` | Host port in the dev compose stack |

## Licensing

Concept 9.1 literally names "Migration Service" as an example of a separately licensable
component — `registry-service.licensable_components["migration-service"] = "demo"`, same
`LicenseStatusClient` pattern as `workflow-service`/`webdav-connector`.

## Tests

Runs, like `webdav-connector`, against the real, running container (no in-process `TestClient`
— the self-loopback smoke test needs a server reachable from outside via a real network socket,
see ADR 0034/"Deliberate limitations"). `test_full_transfer_lifecycle_self_loopback`
covers the complete flow including deletion after a `retention_days=0` period expires.
