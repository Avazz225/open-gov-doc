# virus-scan-service

**Responsibility:** Mandatory virus scan prior to releasing an upload (Concept 10.3), quarantine of infected files.
**Concept reference:** 10.3
**Own Postgres schema:** `virus_scan` (`scan_result`).

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/scan` | Multipart (`file`, optional `document_id`/`created_by`) → performs the scan, on a hit stores a quarantine copy in the Storage Service, persists and returns the result (`ScanResultOut`). Requires `X-DMS-Principal` + `virus_scan.write` since **Post-Roadmap Phase 38 Session 2**, see "Authorization" below. |
| `GET` | `/scans/{id}` | Single scan result — 404 for unknown `id`. Requires `X-DMS-Principal` + `virus_scan.read` since **Post-Roadmap Phase 38 Session 2**. |
| `GET` | `/scans?document_id=...` | All scans for a document (newest first). Requires `X-DMS-Principal` + `virus_scan.read` since **Post-Roadmap Phase 38 Session 2**. |
| `GET` | `/scans?status=infected` | Quarantine view (2.5, P15-S2) — requires `X-DMS-Principal` (401 without), `virus_scan.read` (since Post-Roadmap Phase 38 Session 2, see above) AND, since **Post-Roadmap Phase 19 Session 8** ([ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md)), the real permission-service permission `admin.quarantine` (403 without, role `domain-admin-virus-scan`) — replaces the previous plain `X-DMS-Roles` string-equality gate. |
| `POST` | `/scans/{id}/release` | Release after clarifying a false positive (2.5, P15-S2) — JSON body `{title, folder_id?, object_type_id?, attributes?}`, creates a real document from the quarantined bytes via `document-service`'s internal creation path (no re-scan, see ADR 0052), then deletes the quarantine copy. 401/403 as above, 404 unknown, 409 if not `status="infected"`. |
| `POST` | `/scans/{id}/purge` | Permanent deletion of a quarantine case (2.5) — removes only the quarantined bytes, the `ScanResult` row remains with `status="purged"` as evidence. 401/403/404/409 as above. |
| `GET` | `/healthz` | Health check |

`document_id` is still unknown at the initial upload (the scan runs *before* document creation, see below) — `null` is passed there; at version check-in, the `document_id` is already known.

## Synchronous gating instead of scan status (ADR 0010)

The Document Service calls `/scan` **synchronously, before** persisting an upload's content/metadata (`POST /documents`, `POST /documents/{id}/versions`) — not as an asynchronous consumer of `document.version.created`. Reason: 10.3 requires the virus scan "mandatory before release", but the existing upload path makes content immediately retrievable as soon as it is written. A purely event-driven scan would only react after release. If the scan comes back negative, the Document Service rejects the entire request with `422` — no document/version is created, nothing is stored in the Storage Service. If the Virus Scan Service is unreachable, the upload is also rejected (`503`, fail-closed). Details/rationale: [ADR 0010](../adr/0010-virus-scan-synchronous-gating.md).

## Engine plugins (3.3/3.8)

Swappable via `DMS_SCAN_ENGINE` (interface: `virus_scan_service.engines.ScanEngine`):

| Value | Engine | Note |
|---|---|---|
| `eicar` (default) | `EicarSignatureEngine` | Detects only the standardized EICAR test signature (industry standard for integration tests) — no real malware protection. |
| `clamd` | `ClamdEngine` | Talks to a separately operated `clamd` daemon via its INSTREAM protocol (`DMS_CLAMD_HOST`/`DMS_CLAMD_PORT`). Not the default in this development environment, since the initial signature database download (`freshclam`) takes minutes and requires internet access to the ClamAV mirrors. |

On a hit, the file is stored via the Storage Service under the key `quarantine/{scan_id}` (quarantine instead of deletion, for traceability/evidentiary value) — like the Document Service, the Virus Scan Service never holds file content itself.

## Integration with the backend

- **Storage Service** (3.6): `PUT /objects/quarantine/{scan_id}` on a hit; since P15-S2 also `GET`/`DELETE /objects/quarantine/{scan_id}` on release/final deletion.
- **Document Service** (since P15-S2): `POST /documents/from-quarantine-release` on a release — an internal creation path that deliberately does not trigger a re-scan (see [ADR 0052](../adr/0052-quarantaene-bereich-internal-creation-endpoint-bypasses-rescan.md)). Deliberately no `depends_on` in `docker-compose.yml` (document-service already depends on virus-scan-service in the reverse direction; a cycle would result).
- No call to other services for the scan itself — the engine runs in-process.

## Events

| Event | Payload |
|---|---|
| `virus_scan.completed` | `{document_id, filename, status: "clean"\|"infected", threat_name, created_by}` |
| `virus_scan.released` (since P15-S2) | `{document_id, filename, released_by}` |
| `virus_scan.purged` (since P15-S2) | `{filename, threat_name, purged_by}` |

Published for **every** scan, not just on a hit — the Audit Service consumes `virus_scan.>` (since this session, see `docs/services/audit-service.md`) and thereby logs, without gaps, what was scanned (5.3 explicitly requires this for OCR-like processing steps, applied here analogously). `virus_scan.released`/`.purged` automatically fall under the same already-subscribed wildcard subject — no change needed in `audit-service`.

## Self-registration (Concept 3.2a)

Registers itself with the registry via `dms-registry-client` at startup — opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Authorization (Post-Roadmap Phase 38 Session 2)

`/scan`/`GET /scans/{id}`/`GET /scans` previously had no authorization beyond plain token validity (the gateway only checked the token was valid). All three now require `X-DMS-Principal` (401 without) and, respectively, `virus_scan.write` (`/scan`) or `virus_scan.read` (the two `GET` endpoints) — 403 without. Both new capabilities were added to the "everyone" group (any authenticated principal): the only real caller of `POST /scan` is `document-service`'s `create_document`, which itself has no per-request human principal to forward (a separate, larger, deliberately out-of-scope gap — see `docs/services/document-service.md` "Open Points"), so it asserts a fixed service identity (`X-DMS-Principal: document-service`) — this only closes the actual gap (literally anyone with network access) rather than requiring a bigger unrelated fix. The pre-existing `?status=infected` quarantine gate (`admin.quarantine`, ADR 0073) is unchanged and still additionally required for that one query.

## Tests

- `uv run pytest services/virus-scan-service/tests` (**38 tests**, previously 32, +6 since **Post-Roadmap Phase 38 Session 2**: 401/403 cases for `/scan`, `GET /scans/{id}`, and `GET /scans` without `status`): engine behavior (EICAR detection incl. embedded signature, factory selection, `ClamdEngine` throws instead of falsely reporting "clean" when the daemon is unreachable), repository (CRUD, filter by `document_id`/`status`, `mark_resolved` for release/deletion), API (`/scan` clean/infected incl. quarantine key, `/scans` endpoints incl. role-gated `status=infected` view, `/scans/{id}/release`/`/purge` incl. role/404/409 cases) — runs against real Postgres/the real Storage Service AND (since P15-S2) the real Document Service, no mocks (same rationale as the other backend services).
- Document Service tests cover the integration (`test_create_document_rejects_infected_upload`, `test_checkin_rejects_infected_version_without_creating_it`) — an upload with EICAR content is rejected with `422`, no (further) version is created. Since P15-S2, additionally `test_quarantine_release_*` — the internal creation path deliberately accepts the same EICAR content (no re-scan).

## Open Points

- **`ClamdEngine` not wired up in production**: code exists and is activatable via `DMS_SCAN_ENGINE=clamd`, but no `clamd` container is part of `infra/docker-compose.yml` (rationale: see above/ADR 0010). To be added once an environment with reliable access to the ClamAV signature database is available.
- ~~**No notification of the uploader on a hit**: the Notification Service only exists from P6-S2 onward; `virus_scan.completed` is already published and can be consumed there without any change to this service.~~ — **closed in Phase 44 Session 4**: `notification-service` now consumes `virus_scan.completed` (a new subject registration + consumer branch, no change needed here — this service already published everything required). See `docs/services/notification-service.md`.
- ~~No authorization on `/scan`/`GET /scans/{id}`/`GET /scans?document_id=`~~ — **resolved in Post-Roadmap Phase 38 Session 2**, see "Authorization" above.
- **Scan latency increases upload latency** (ADR 0010) — negligible with `EicarSignatureEngine`, potentially noticeable with `clamd`/large files.
- **Release requires manual entry of `folder_id`/`object_type_id`/`attributes`** — none of these values were known at the originally failed upload. See [ADR 0052](../adr/0052-quarantaene-bereich-internal-creation-endpoint-bypasses-rescan.md) for the rationale.
