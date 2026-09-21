# 0179 — storage-service: gate the object-CRUD data plane behind a trusted-caller set

**Status:** accepted
**Context:** P59-S2 (Phase 59, "Critical Authorization Bugs" — second session of the sixth gap-analysis
round's live-code security sweep). `storage-service`'s entire object-CRUD data plane —
`PUT`/`GET`/`DELETE /objects/{key:path}`, `GET /object-metadata/{key:path}`,
`GET /objects/{key:path}/copies`, `GET /object-verify/{key:path}`(`/all`), and the three
`/objects/{key:path}/archive-copy*` endpoints — had **no permission check of any kind**. Only the
admin/config endpoints (`PUT /operational-config`, `PUT /guard-config`,
`POST /guard-status/{id}/reidentify`, `PUT /guard-status/{id}/config`) were retrofitted with
`_require_storage_permission` (`admin.storage`) at Post-Roadmap Phase 38 Session 3 — the object CRUD
surface, this service's actual reason to exist, was left out entirely. Since `storage-service`
self-registers with `registry-service` and is reachable through the gateway like any other service, any
authenticated user of any role could `GET`/`PUT`/`DELETE /objects/{any_key}` directly, completely
bypassing `document-service`'s per-document ACL and the WORM/retention guard's intent for
non-governance-locked targets (deletion needed no permission check to even attempt). Storage keys are
structured/predictable (e.g. `documents/{id}/{uuid}`, `archive/{document_id}/{transfer_id}.pdf`), making
enumeration practical, not just theoretical.

## Decision

Gate all eleven object-CRUD endpoints behind a new dependency, `_require_storage_caller`, which checks
`X-DMS-Principal` against a fixed set of six known trusted internal callers —
`document-service`, `archival-service`, `rendering-service`, `ocr-service`, `virus-scan-service`,
`mail-connector` — `403` for anything else, including a missing/empty header. Each of the six services'
own `StorageClient` now sends a fixed identity header (`X-DMS-Principal: <literal-service-name>`) on
every request, applied once at `httpx.AsyncClient` construction time so every call automatically carries
it, the same "fixed system-identity header" convention this project already uses for its single-caller
precedents (`migration-service`'s `_require_workflow_service_caller`, `teamspace-service`'s
`_require_auth_service_caller`) — extended here to accept a set instead of exactly one literal string,
since the object data plane genuinely has several legitimate machine callers.

**Not `admin.storage`.** The already-existing capability that gates the config endpoints was deliberately
NOT reused for the data plane, even though it would have been the smallest possible code change. Traced
every real caller first: `apps/admin-ui` (the only frontend caller of this service at all) exclusively
calls the config/guard endpoints — never the object endpoints — confirmed via grep. The object endpoints'
only legitimate callers are the six backend services above, each already having checked the real end
user's own permission (e.g. `document.read`/`.write`) before ever reaching `storage-service`. Reusing
`admin.storage` would have meant granting six ordinary backend services a human-administrator capability
(`domain-admin-storage`) just to do their normal job — wrong semantic (a machine-to-machine data-plane
call is not "administering storage"), and it would have coupled the two concerns (who may reconfigure
targets/WORM vs. who may read/write object bytes on behalf of an already-authorized end user) that this
project otherwise takes care to keep separate (see e.g. ADR 0178's own reasoning for not conflating
`notification.write`/`admin.notification_config`/the new `admin.notification_read`).

## Rationale

- **Why a set of literal strings, not a new permission-service capability**: a capability grant would
  still require SOMEONE to hold it — and the only "someone" here is six machine callers, not a human role.
  A fixed-identity check is simpler, requires no new `permission-service` role/seed data, and is
  unspoofable by a real end user for the same reason the single-caller precedents already established:
  the gateway's `proxy()` handler always overwrites any client-supplied `X-DMS-Principal` with the
  verified JWT claims, so a real user's principal is their own Keycloak `sub`, never one of these six
  literal strings.
- **Why `GET /storage/usage` and the two `/process-pending` maintenance endpoints were NOT gated in this
  session**: out of scope for this specific finding — the security sweep that found this bug named exactly
  the eleven object-CRUD endpoints above; `/storage/usage` returns aggregate usage statistics per backend
  (no object content, no keys), a materially different and lower-risk disclosure already called by
  `reporting-service`/`license-service` with no identity header at all today. Left as-is, not itemized as
  a new finding here (not previously flagged by this round's research either).
- **Why the test suite's default principal changed from a synthetic `"storage-service-tests"` string to
  the literal `"document-service"`**: `storage-service`'s own test file has roughly 60 call sites
  exercising the object-CRUD endpoints against only ~15 exercising the admin/config ones — changing the
  shared default `client` fixture's principal to a real trusted-caller identity (still separately granted
  `admin.storage` for the config tests, same as before) satisfies both gates at once without touching
  every individual object-CRUD test call site.

## Consequences

- New endpoints/behavior: all eleven object-CRUD endpoints now `403` for any caller not in
  `_TRUSTED_STORAGE_CALLERS`, including a real end user's own verified identity.
- Six client files gained a fixed default header: `document_service.storage_client.StorageClient`,
  `virus_scan_service.storage_client.StorageClient`, `rendering_service.storage_client.StorageClient`,
  `ocr_service.storage_client.StorageClient`, `mail_connector.storage_client.StorageClient`,
  `archival_service.clients.StorageClient`.
- New tests: `storage-service` +6 (`test_upload_object_without_trusted_caller_is_403`,
  `.._without_principal_header_is_403`, `test_download_object_without_trusted_caller_is_403`,
  `test_delete_object_without_trusted_caller_is_403`,
  `test_get_object_metadata_without_trusted_caller_is_403`, and a positive-path regression proving each
  of the six documented trusted callers independently passes the gate), 162/162 total. The six calling
  services' own test suites needed no new tests (their existing suites already exercise real
  upload/download round trips against a real `storage-service` instance, which now implicitly proves the
  new header is sent correctly — any missing header would have surfaced as a widespread `403` failure
  across each service's own suite, and none did).
- Live-verified against the real running stack: direct `curl` against `storage-service` confirmed `403`
  with no header and with a random authenticated identity, `201`/`200` with the literal
  `document-service` identity; a real end-to-end document upload + content download through
  `document-service`'s own public API (not a synthetic identity header) succeeded, proving the client-side
  header wiring works through a real caller, not just a direct test.
