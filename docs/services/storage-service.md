# storage-service

**Responsibility:** Storage abstraction layer over interchangeable backend plugins — file content resides in one or more configured backends (redundancy, since P3-S4; any number of them, including instances of the same type, since P5b-S6), the shared DB holds only metadata (reference, checksum, size, copy status, storage device identity) (concept 3.6).

**Concept reference:** 3.6, 1a (Azure Blob backend, since Post-Roadmap Phase 24 Session 1), 5.1/5.2a (Object Lock/WORM, since P7-S1), 5.6 (archive target role, since P7-S3)
**Own Postgres schema:** `storage` (tables `object_metadata`, `object_copy`, `backend_identity`, `guard_config`)

## API

| Method | Path | Description |
|---|---|---|
| `PUT` | `/objects/{key:path}?retain_until=...` | Upload, computes SHA-256, writes to the configured targets according to the write strategy, upserts metadata — `retain_until` (optional, since P7-S1) sets `ObjectCopy.retention_until` and activates real S3 Object Lock on targets with `object_lock_mode=governance` (see below) |
| `GET` | `/objects/{key:path}` | Download - reads from the first copy with status `ok` in target priority order, automatic fallback (404 if no copy is available) |
| `DELETE` | `/objects/{key:path}?bypass_governance=false` | Delete on all targets (idempotent), then metadata + copy entries — `403` if a locked copy (`retention_until` in the future, target in governance mode) is affected without a valid bypass. Bypass requires `bypass_governance=true` **and** a role from `Settings.governance_bypass_role` in the `X-DMS-Roles` header (since P7-S1, [ADR 0030](../adr/0030-storage-object-lock-governance-mode.md)) |
| `GET` | `/object-metadata/{key:path}` | Read metadata |
| `GET` | `/objects/{key:path}/copies` | Copy status per configured target (`pending`/`ok`/`failed`/`failed_permanent`) |
| `GET` | `/object-verify/{key:path}` | Fixity check of the primary target: re-read the checksum, compare against the reference value |
| `GET` | `/object-verify/{key:path}/all` | Fixity check across **all** configured targets, updates `object_copy` |
| `GET` | `/storage/usage` | Aggregated storage consumption per backend (`{backend, object_count, total_size_bytes}[]`, `GROUP BY backend` over `object_metadata`, since P7-S2b) — only consumer so far: the storage-consumption report of `reporting-service` (see `docs/services/reporting-service.md`), a live query rather than an own read model |
| `POST` | `/replication/process-pending` | Process the retry queue - replicates pending copies, intended for periodic external invocation |
| `GET` | `/guard-config` | Current guard configuration (`allow_degraded_start`) — creates the default row on first call (P5b-S6) |
| `PUT` | `/guard-config` | Updates `allow_degraded_start` — takes effect only on the **next** start, not on the running instance |
| `GET` | `/operational-config` | Current operational parameters (`write_strategy`, `quorum_count`, `max_replication_attempts`, since **Post-Roadmap Phase 22 Session 6**, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md)) — creates the default row from the previous env-var values on first call |
| `PUT` | `/operational-config` | Updates the operational parameters — takes effect **without a restart**, `422` if `write_strategy=quorum` with the chosen `quorum_count` cannot be satisfied against the (structurally still fixed) number of targets |
| `GET` | `/guard-status` | Per configured target: last confirmed device ID, timestamp, number of not-yet-replicated copies (Admin UI status block) |
| `POST` | `/guard-status/{target_id}/reidentify` | Accepts an intended storage device swap at runtime (no restart needed), P5c-S2 |
| `PUT` | `/guard-status/{target_id}/config` | Live-edit target metadata (`object_lock_mode`, `role`, since **Post-Roadmap Phase 22 Session 7**, [ADR 0092](../adr/0092-storage-target-metadata-editable.md)) — `404` on an unknown target, `422` if the change would leave no regular target left. Takes effect without a restart |
| `PUT` | `/objects/{key:path}/archive-copy` | Writes **only** to the configured archive targets (`role="archive"`, 5.6, since P7-S3) — `503` without a configured archive target |
| `GET` | `/objects/{key:path}/archive-copy` | Reads exclusively from archive targets (retrieval, since P7-S3) — independent of the live state of the same key |
| `GET` | `/objects/{key:path}/archive-copy/verify` | Fixity check of the archive copy, filtered to archive targets (since P7-S3) |
| `DELETE` | `/objects/{key:path}/live-copies` | "Dehydrate" (5.6, since P7-S3) — removes copies only from the regular live targets, the archive copy remains untouched. Same governance-lock gate as the regular delete |
| `GET` | `/healthz` | Health check incl. active targets and write strategy |

## Backend Plugin Interface (3.6)

`StorageBackend` (ABC): `write(key, data, *, lock_until=None)`, `read`, `delete(key, *, bypass_governance=False)`, `exists`, `checksum` — `lock_until`/`bypass_governance` since P7-S1 for Object Lock/WORM (see below), accepted but ignored by `LocalFilesystemBackend` (a documented limitation, no real WORM on local storage). Three implementations:

- **`LocalFilesystemBackend`** — covers both "local filesystem" and **NFS**: in Kubernetes, the configured path is the mount point of a PVC; the backend only sees a folder, regardless of whether NFS or block storage is behind it. Writes atomically (temporary file + `os.replace`) instead of using platform-specific `fcntl` locking — its semantics are inconsistent across NFS implementations, whereas an atomic rename-after-write works reliably on NFSv4+ and prevents partial-write corruption from concurrent writers to the same key. Concurrent *editing* of a document is the job of document-service locking (4.2), not this layer.
- **`S3Backend`** — `aioboto3`, default is MinIO, works identically against any S3-compatible provider.
- **`AzureBlobBackend`** (Post-Roadmap Phase 24 Session 1, concept 1a) — `azure-storage-blob` (`azure.storage.blob.aio`), connection-string auth (no `azure-identity`/AAD — deliberately not the added complexity for this dev-focused project), works identically against real Azure Blob Storage and the local Azurite emulator (default for tests/dev, analogous to MinIO for `S3Backend`). `lock_until`/`bypass_governance` are a **documented no-op** here (see "Object Lock/WORM" below) — no real Azure Immutable Blob Storage.

All three are independently tested: `LocalFilesystemBackend` against a real filesystem (`tmp_path`), `S3Backend` against real MinIO, `AzureBlobBackend` against real Azurite (none mocked).

## Data Model

- `object_metadata`: `object_key` (PK), `backend` (target `id` of the primary target at the time of creation/last overwrite — since P5b-S6 a target `id`, no longer a backend *type*, see below), `checksum_sha256`, `size_bytes`, `content_type`, `created_at`, `updated_at`.
- `object_copy`: `object_key` + `backend_id` (composite PK, FK to `object_metadata`), `status` (`pending`/`ok`/`failed`/`failed_permanent`), `checksum_sha256`, `attempts`, `last_error`, `next_retry_at` (nullable, since **Post-Roadmap Phase 20 Session 6**, [ADR 0082](../adr/0082-storage-service-replication-jitter-retrofit.md) — full-jitter backoff, see below), `retention_until` (date, nullable, since P7-S1 — see "Object Lock/WORM" below), `created_at`, `updated_at` — one row per configured target and object.
- `backend_identity` (new, P5b-S6): `target_id` (PK), `device_id`, `verified_at` — last confirmed device ID per configured target, stored independently of the target itself (see "Storage Device Swap Guard" below).
- `target_override` (Post-Roadmap Phase 22 Session 7, [ADR 0092](../adr/0092-storage-target-metadata-editable.md)): `target_id` (PK), `object_lock_mode`, `role`, `updated_at` — sparse (only targets with an actually set override have a row), see "Target Metadata" below.
- `guard_config` (new, P5b-S6): a single row with fixed `id=1`, `allow_degraded_start`, `updated_at` — same pattern as `ocr_config` (ocr-service, [ADR 0016](../adr/0016-ocr-configurability-compose-profile-and-live-settings.md)).

## Target Set: Any Number of Backend Instances (3.6, since P5b-S6)

`Settings.targets` is a real list of `BackendTargetConfig` entries (`id`, `type: "local"|"s3"|"azure"`, plus type-specific credentials) instead of the previous fixed two-slot structure (`backend`/`secondary_backend`, [ADR 0004](../adr/0004-storage-redundancy-scope.md)) — **`id`, not `type`, is the unique key**, which allows any number of instances of the same type within the same target set (e.g. two independent S3 providers in addition to a local/NFS target). Configured as a JSON list in `DMS_TARGETS` (pydantic-settings natively decodes complex field types from a single environment variable, see [ADR 0017](../adr/0017-storage-device-identity-guard.md)):

```
DMS_TARGETS='[{"id":"local","type":"local","base_path":"/data/storage"},
  {"id":"s3-eu","type":"s3","endpoint_url":"...","access_key":"...","secret_key":"...","bucket":"...","region":"..."},
  {"id":"azure-eu","type":"azure","connection_string":"...","container":"..."}]'
```

**`type="azure"`** (Post-Roadmap Phase 24 Session 1) requires `connection_string` (a full Azure Storage connection string, connection-string auth instead of `azure-identity`/AAD) and `container` (the Azure counterpart to `bucket`, its own field rather than reusing `bucket`). The default for tests/dev is [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) (`infra/docker-compose.yml`, service `azurite`, blob port 10000) — works identically against real Azure Blob Storage using the same, publicly documented Azurite dev connection string (fixed account `devstoreaccount1` + a fixed, published dev account key, not a real secret). The `azurite` container runs with the `--skipApiVersionCheck` flag, since the pinned Azurite image does not necessarily know every `x-ms-version` sent by the current `azure-storage-blob` SDK and would otherwise reject with `400 InvalidHeaderValue`.

The primary target is always the first entry (determines write synchronicity under `primary_async` as well as read priority). `replication.py` itself was, per ADR 0004, already fully generic with respect to arbitrary target `id` strings — the previous two-slot restriction lived exclusively in `Settings`/`backends/__init__.py`, and it has been removed there in this session.

**Rebalancing when a target is newly added** (since **P5c-S2**): a change to the target set requires a restart anyway (`Settings` is only read at startup) — that very restart, via the storage-device-swap-guard's first-start bootstrap (see below), automatically also triggers rebalancing: `repository.seed_pending_copies_for_new_target` creates a `pending` row for every already-existing object that does not yet have a copy on the new target, which the same retry queue (`POST /replication/process-pending`) then catches up on. No separate trigger mechanism, no Admin UI action needed.

## Operational Parameters (Post-Roadmap Phase 22 Session 6, [ADR 0091](../adr/0091-connector-operational-config-live-editable.md))

`OperationalConfig` (DB singleton, `id=1`, same get-or-create pattern as `GuardConfig`) makes
`write_strategy`/`quorum_count`/`max_replication_attempts` live-editable via `GET`/`PUT /operational-config`
— read freshly from the DB on every affected request (no `app.state` cache), so it takes effect without
a restart. Deliberately **not** part of this session: the target set itself (`Settings.targets`,
incl. `access_key`/`secret_key`) and `object_lock_mode`/`role` per target remain env-var-only —
credentials would have required a new encryption/masking infrastructure, `object_lock_mode`/
`role` are WORM/records-disposal relevant (5.1/5.2a/5.6), and an accidental live change would have
compliance-relevant consequences. `PUT /operational-config` repeats the same quorum
feasibility check as startup (`_validate_settings`) against the (structurally still fixed)
number of targets, `422` on infeasibility. Admin UI: `/storage-operational-config/`.

## Redundancy & Fixity (Concept 3.6, since P3-S4)

- **Write strategies**: `quorum` (synchronous, success only once `quorum_count` targets have confirmed; on failure to reach that, already-successful partial copies are rolled back best-effort) or `primary_async` (the default for general operation: only the primary target is synchronous, further targets stay `pending` and are caught up via `POST /replication/process-pending` — a retry queue with `max_replication_attempts`, after which `failed_permanent` + an error log serves as an alerting substitute). **Since Post-Roadmap Phase 22 Session 6** ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)): `write_strategy`/`quorum_count`/`max_replication_attempts` are live-editable via `GET`/`PUT /operational-config` (`Settings.write_strategy` etc. now only supply the seed value of the first row) — see "Operational Parameters" below. **Since Post-Roadmap Phase 20 Session 6** ([ADR 0082](../adr/0082-storage-service-replication-jitter-retrofit.md)): a failure additionally sets a `next_retry_at` via full-jitter backoff (`libs/dms-retry`, the same formula as the four other resilience sessions of this phase) — `list_pending_copies` only picks up a `failed` row again once this wait time has elapsed, instead of unconditionally retrying it on every `process-pending` call.
- **Read fallback**: `GET /objects/{key}` reads from the first copy with status `ok` in target priority order (primary target first).
- **Fixity check per copy**: `GET /object-verify/{key}/all` re-reads the checksum from every backend and compares it against the reference value stored in `object_metadata` — detects bit rot/tampering independently per target, updates `object_copy.status`.
- The orchestrating logic (`replication.py`) is backend-agnostic (works with `dict[str, StorageBackend]` + a `list[str]` target priority) and testable independently of the FastAPI app singleton configuration.

## Storage Device Swap Guard (3.6, since P5b-S6, [ADR 0017](../adr/0017-storage-device-identity-guard.md))

Protects against an accidentally swapped/reset storage device that would otherwise silently be accepted as an "empty but valid" target:

- Each target gets a generated device ID on first use, stored as a marker object under the reserved key `__dms_storage_identity__` — via the existing `StorageBackend.write`/`read` interface, no new backend method. The reserved key deliberately contains no slash and therefore does not collide with real, always-segmented object keys (`type/id/...`).
- The **reference value** is additionally stored (not only in the backend itself) in `backend_identity` — on an actual fallback to an empty/wrong medium, the marker file in the backend would currently be missing, so a pure self-comparison by the backend would be ineffective.
- **Default: refuse to start** (`RuntimeError` before `yield`, the same fail-fast pattern as the existing `_validate_settings` check), as soon as a target does not match its known reference value or is unreachable. A target newly added to the target set (no known reference value present) is instead automatically "stamped" — no failure on first start.
- **Admin override** (`GuardConfig.allow_degraded_start`, `GET`/`PUT /guard-config`) allows a degraded start **provided that at least one target is demonstrably unchanged** — deliberately a proactively set standing policy rather than an emergency switch flipped at the moment of refusal (the service that would need to grant the approval would, after all, not be running at that moment; Postgres itself is independent of a broken storage backend, see ADR 0017).
- In the degraded case, all `object_copy` rows of the affected targets are automatically reset to `pending` (`repository.reset_copies_for_backend`) — the already-existing retry queue (`POST /replication/process-pending`) catches them up, no new background task (ADR 0004 continues to apply unchanged).
- `GET /guard-status` shows, per target, the last confirmed device ID/timestamp as well as the number of still-open copies — a target with `pending_copies > 0` is still in recovery.
- **Correction mechanism for intended storage device swaps** (since **P5c-S2**): `POST /guard-status/{target_id}/reidentify` adopts an already-present marker file of the new device, or stamps a new one (like the first-start bootstrap), updates `backend_identity`, and resets all previous copies of the target to `pending` via `reset_copies_for_backend` — functionally the same recovery as the automatic degraded start, but explicitly triggered by the admin and **without a restart** (Admin UI: "Accept storage device swap" button per row in `/storage-guard/`). Replaces the previously required direct correction in the `backend_identity` table.

## Target Metadata Live-Editable (Post-Roadmap Phase 22 Session 7, [ADR 0092](../adr/0092-storage-target-metadata-editable.md))

`PUT /guard-status/{target_id}/config` makes `object_lock_mode`/`role` per already-configured target
live-editable — deliberately ONLY these two metadata fields, NOT the target set itself (credentials/
`id`/`type`/`base_path` remain env-var-only, the same rationale as for `OperationalConfig`, ADR 0091:
new targets need real infrastructure, not a pure configuration value). `404` on an unknown
`target_id`. `422` if the change would leave NO regular (non-archived) target remaining — without
this check, a `role="archive"` override on the last regular target could crash every subsequent upload
with an `IndexError` (`upload_object` uses `app.state.targets[0]` as the
primary target).

`_compute_target_state()` (`main.py`) merges `Settings.targets` with all `target_override` rows
(sparse, only overridden targets have a row) into an effective target list — called at
startup AND on every `PUT`, with the result immediately written back into `app.state.target_configs`/`.targets`/
`.archive_targets`/`.lock_target_ids`. Unlike `OperationalConfig` (P22-S6, read freshly from the DB on
every affected request), it is deliberately NOT re-read from the DB on every individual
read access here — `object_lock_mode`/`role` are needed in too many places in the code
(upload/archive routing, retention guard, lock-status displays), and a `PUT`-time refresh
of `app.state` achieves the same live-reload result with a much smaller diff. **Known limitation**:
with multiple horizontally scaled replicas, a replica without its own `PUT`/restart does not see the
change — uncritical for this project's current single-replica reality. Admin UI: `/storage-guard/`
(two new checkbox columns replacing the previous purely read-only "Object Lock" column).

## Archive Target Role (5.6, since P7-S3)

Records disposal/long-term archiving (see `docs/services/archival-service.md`) needs its own, possibly cheaper/differently redundant storage target separate from the live targets — instead of a separate storage system, `BackendTargetConfig` gets a new optional field `role: "archive" | null` (default `null` = existing behavior, normal replication target):

```
DMS_TARGETS='[{"id":"local","type":"local","base_path":"/data/storage"},
  {"id":"archive","type":"local","base_path":"/data/archive","role":"archive"}]'
```

- **`resolve_targets()`** (regular upload replication) **filters archive targets out** — they are not part of the normal write/read path (`PUT`/`GET /objects/{key}`). **`resolve_archive_targets()`** instead returns exactly the targets with `role="archive"`.
- **New, dedicated endpoints** (see above) instead of a special-case branch in the existing `/objects/{key}` routes: `PUT`/`GET .../archive-copy` write/read exclusively via `app.state.archive_targets`, `.../archive-copy/verify` reuses the same fixity logic as `GET /object-verify/{key}/all`, filtered to archive targets.
- **`replication.write_to_targets()`/`delete_from_targets()`** (new, `replication.py`) instead of the existing `write_with_redundancy()`/`delete_from_all()`: archive write operations are deliberately synchronous individual operations without a primary/secondary distinction or write-strategy/quorum semantics (not part of the upload hot path). `delete_from_targets()` removes only the `object_copy` rows of the specified (live) targets in a targeted way — unlike `delete_from_all()`, which would remove **all** copy rows of a key via `repository.delete_copies_for_key` and would thereby have accidentally also deleted the archive-copy tracking row during dehydration.
- **Route-ordering trap** (actually encountered while building these endpoints, see below): Starlette matches path routes in registration order, and `{key:path}` is a greedy converter — `PUT /objects/{key:path}` (generic upload) must be registered **after** `PUT /objects/{key:path}/archive-copy`, otherwise the generic route captures every call, including `.../archive-copy`, as part of the key. All more specific suffix routes (`.../copies`, `.../archive-copy`, `.../archive-copy/verify`, `.../live-copies`) are therefore placed in the source code before the generic `PUT`/`GET`/`DELETE /objects/{key:path}` routes.
- **No Admin UI editor for the target role** — like the rest of the target set (see above), this is deployment configuration (`DMS_TARGETS`), not an Admin UI form.

## Object Lock/WORM (5.1/5.2a, since P7-S1, [ADR 0030](../adr/0030-storage-object-lock-governance-mode.md))

Two-tier protection against premature deletion during an active retention period:

- **Application-layer guard** (`retention_guard.py`, same pattern as the storage device swap guard): `find_locked_targets` checks before every deletion whether an `ObjectCopy.retention_until` lies in the future AND the affected target has `object_lock_mode="governance"` set (`BackendTargetConfig.object_lock_mode`, only `"governance"` is a valid value — compliance mode would technically make the forced-deletion exception required by 5.2a impossible). Blocked deletions return `403`, unless `?bypass_governance=true` **and** a role from `Settings.governance_bypass_role` (default `dms-admin`) are present in the `X-DMS-Roles` header (`has_governance_bypass_role`) — exactly the same header-role pattern as `document-service`'s `kennzeichen_admin_role`.
- **Real S3 Object Lock as additional hardening**: for `type="s3"` targets with `object_lock_mode` set, `write()` sets `ObjectLockMode="GOVERNANCE"`/`ObjectLockRetainUntilDate` when `lock_until` is passed; `delete()` uses `BypassGovernanceRetention=True` on an authorized bypass. **Critical implementation point**: on a versioned bucket (Object Lock requires versioning), a `delete_object()` without an explicit `VersionId` only removes a delete marker, and the locked version continues to exist for real — `delete()` therefore, when `object_lock_enabled`, first reads the current `VersionId` via `head_object` and passes it explicitly to `delete_object`.
- **No automatic intervention on an existing bucket**: `ObjectLockEnabledForBucket=True` is only set on the `create_bucket` branch of `ensure_bucket()` — S3 Object Lock cannot be retroactively enabled on an already-existing bucket. For a bucket that has long been in production use, the `head_bucket` success branch remains an unchanged no-op.
- **`local` backend type**: no real Object Lock equivalent, only the application-layer check applies — an honestly documented limitation, see ADR 0030.
- **`azure` backend type** (Post-Roadmap Phase 24 Session 1): likewise no real Object Lock equivalent — `AzureBlobBackend.write()`/`delete()` accept `lock_until`/`bypass_governance` but ignore them (documented no-op, the same stance as the `local` backend). Azure Blob Storage technically has an equivalent (Immutable Blob Storage/time-based retention, versioning-/policy-based), which was **deliberately not implemented** here: Azurite — this project's reference test environment — does not yet support immutability policies, and a "technical WORM" untested against Azurite would be a pretended rather than a real protection. Anyone needing real tamper protection must continue to use a `type="s3"` target with `object_lock_mode=governance` (real S3 Object Lock, see above); the application-layer guard (`retention_guard.py`) protects an Azure target the same as any other, independently of this.

## Events

None — Storage Service continues to neither publish nor consume events.

## Self-Registration (Concept 3.2a, since P4-S1)

Registers itself with the registry on startup (`libs/dms-registry-client`: register, periodic heartbeat, deregister on shutdown) - the basis for the API Gateway's routing (`docs/services/gateway-service.md`). Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; without both values, the service runs unchanged without discovery. Self-registration happens **after** the storage device swap guard — a refused start therefore does not register the service at all (no "healthy=false" registry entry, simply no entry).

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Tests

- `uv run pytest services/storage-service/tests` (**134 tests since Post-Roadmap Phase 24 Session 1**
  (Azure Blob backend, concept 1a) — +12 over the previous 122: `test_azure_backend.py` (10 tests, against
  real Azurite, no mocking, the same pattern as `test_s3_backend.py`) covers the write/read roundtrip,
  checksum, `exists`, delete (incl. idempotent on an already-missing key), `ObjectNotFoundError` on a
  missing key, as well as the documented no-op of `lock_until`/`bypass_governance` (deletion is
  NOT blocked, unlike `S3Backend` with `object_lock_enabled`); two new
  `test_backend_factory.py` tests (`BackendTargetConfig` requires `connection_string`/`container` for
  `type=azure`, `build_backend()` returns `AzureBlobBackend`). Azurite runs as a new
  `infra/docker-compose.yml` service (`azurite`, blob port 10000, `--skipApiVersionCheck` against
  SDK/emulator version drift), `TEST_AZURE_CONNECTION_STRING` is env-overridable like
  `TEST_S3_ENDPOINT_URL` etc., defaulting to the publicly documented, fixed Azurite dev
  connection string. Before that, 122 tests since Post-Roadmap Phase 22 Session 7
  ([ADR 0092](../adr/0092-storage-target-metadata-editable.md)), previously 117, +5: `PUT .../config` on an
  unknown target → `404`, on the only configured regular test target with `role=archive` → `422`
  ("no regular target left"), an end-to-end test uploads an object with `retain_until`,
  then activates `object_lock_mode=governance` live and confirms an immediately blocked deletion without
  a restart between upload and lock activation, plus two repository unit tests for
  `upsert_target_override`/`list_target_overrides`. `tests/conftest.py`'s teardown list was extended with
  `operational_config`/`target_override` (previously missing there, an actual finding of this session); before that 117
  tests, 4 new since **Post-Roadmap Phase 22 Session 6**
  ([ADR 0091](../adr/0091-connector-operational-config-live-editable.md)), previously 113, +4:
  `GET /operational-config` returns the env-var defaults before the first `PUT`, `PUT` updates and
  persists, `PUT` with an unsatisfiable `quorum_count` returns `422`, a real upload directly after a
  `PUT` to `strategy=quorum` proves the live reload without a restart — the three mutating tests use
  a new `operational_config_client` fixture that restores the env-var defaults after each test,
  since this service, unlike `permission-service`/`signature-service`, has no
  table-truncate fixture between tests), before that 113 tests, 4 new since **Post-Roadmap Phase 20 Session 6** ([ADR 0082](../adr/0082-storage-service-replication-jitter-retrofit.md)): a failure sets `next_retry_at` and prevents immediate re-pickup, after fast-forwarding the timestamp the row is picked up again, `list_pending_copies` filters out a not-yet-due row, the existing exhaustion test was adapted to the new backoff behavior): backend plugins, factory functions, replication, storage device swap guard, Object Lock/WORM unchanged. New since P7-S1: `retention_guard` unit tests (blocked/not blocked/bypass with/without role), `S3Backend` Object Lock tests against real MinIO (incl. the delete-marker-vs-real-version-deletion case, see above), `LocalFilesystemBackend` silently ignores the new parameters, API tests for `403` without a bypass/`200` with a valid bypass. New since P7-S3: `resolve_targets()` excludes `role="archive"`/`resolve_archive_targets()` finds them (`test_backend_factory.py`), `write_to_targets()`/`delete_from_targets()` against real `LocalFilesystemBackend` instances (`test_replication.py`), an API roundtrip uploading/verifying/downloading an archive copy as well as dehydrating-leaves-archive-copy-untouched via a new `archive_client` fixture (mutates `app.state.backends`/`app.state.archive_targets` after startup, the same pattern as `governance_client`).
- **Live verification without mocking**: a real storage device swap was simulated against the running container (identity file manually altered, restart forced) — start refusal, admin override + degraded start, automatic re-replication scheduling, and `POST /replication/process-pending` all behaved exactly as intended; see `PROGRESS.md` for the sequence. **Additionally verified since P5c-S2**: a second target added at runtime automatically received `pending` copies for a previously uploaded object on restart (rebalancing), and `POST /guard-status/{target_id}/reidentify` corrected a simulated storage device swap without a restart. **Additionally verified since P7-S1**: a purely test-only second target (a fresh MinIO bucket, `object_lock_mode=governance`) rejected a deletion without a bypass with `403` and actually deleted (not just via a delete marker) with a valid bypass — the real, production-used bucket remained untouched. **Additionally verified since Post-Roadmap Phase 24 Session 1**: a purely test-only `type=azure` target (a fresh Azurite container, briefly configured as the primary target) went through a real upload/download/fixity-check/deletion via the regular `PUT`/`GET`/`DELETE /objects/{key}` API (`backend: "azure-test"` in the metadata response, `GET .../copies` showed `status: "ok"` for the Azure target) — the test container and all `object_copy` rows created in the process were then fully cleaned up, and the default target set was restored unchanged.

## Open Points

- **Configuration per object type/folder instead of service-wide** — the concept allows overrides of the write strategy per object type/folder; the connection between the Object-Type/Folder Service and Storage Service needed for this is currently missing.
- **`/replication/process-pending` has been automatically run periodically since P26-S4** — `infra/k8s/dms/templates/storage-cronjob.yaml` (see [ADR 0101](../adr/0101-storage-cronjob-single-job-no-bulk-verify.md)) calls the endpoint every 15 minutes (configurable, `storageCronJob.replication.schedule`) via a k8s `CronJob` whenever operated through this Helm chart (no equivalent for `docker-compose.yml` dev operation — there the endpoint remains manual/on-demand). After a degraded start or a `reidentify` call, `pending_copies > 0` therefore resolves itself by no later than the next CronJob run, instead of "hanging" indefinitely.
- **`/object-verify/{key:path}/all` remains a purely on-demand endpoint without automatic periodic execution** — unlike the replication retry queue, this endpoint ALWAYS only verifies a single object passed via the path parameter (all *targets* of that one object, not all objects in the store); `storage-service` has no endpoint that lists object keys or supplies a batch of not-yet-verified objects (no fixity counterpart to `list_pending_copies`/`process_pending`). A P26-S4 CronJob for this was therefore deliberately NOT built (see [ADR 0101](../adr/0101-storage-cronjob-single-job-no-bulk-verify.md) for the rationale and a design proposal for a future bulk-verify endpoint analogous to the retry queue).
- **No removal of a target from the target set** — a once-configured target currently cannot be cleanly "decommissioned" (the associated `object_copy` rows would be left orphaned); only *adding* was addressed in P5c-S2. **During this session's live verification (P24-S1), this was cleaned up manually via SQL for exactly this reason** (30,410 `pending` rows seeded for all already-existing objects by the rebalancing when the test target was added) — a concrete, practically experienced instance of this already-documented gap.
- **`local` backend without real WORM** (only an application-layer guard, see ADR 0030) — anyone needing tamper-proof WORM on local storage must use an S3-compatible target with `object_lock_mode=governance`.
- **`azure` backend without real WORM** (Post-Roadmap Phase 24 Session 1, only an application-layer guard, see "Object Lock/WORM" above) — Azure Immutable Blob Storage would be technically possible but was deliberately not implemented, since Azurite (the reference test environment) does not support it; anyone needing real WORM must continue to use a `type="s3"` target with `object_lock_mode=governance`.
- **Azurite emulator version drift**: the pinned `azurite` image (`3.30.0`) does not necessarily know the `x-ms-version` sent by whichever `azure-storage-blob` SDK version is current — caught via the Azurite CLI flag `--skipApiVersionCheck` (see `infra/docker-compose.yml`); on an SDK version jump with actually incompatible (not merely unknown) request fields, this flag would no longer help and Azurite would need to be updated.
- **`replication.py`'s `process_pending` propagates `retention_until` to `record_copy`, but not `lock_until` to the backend `write()` call on caught-up replication** — relevant only once caught-up replication is regularly used for governance targets (documented in ADR 0030).
- **No automatic bucket upgrade** for buckets already in production use without Object Lock (see ADR 0030) — only newly created buckets receive `ObjectLockEnabledForBucket=True`.
- **`PUT /guard-status/{id}/config`'s live reload (Post-Roadmap Phase 22 Session 7, ADR 0092) affects only its own process instance** — with multiple horizontally scaled `storage-service` replicas, a replica without its own `PUT` call/restart does not see the change (no shared cache/pub-sub invalidation). Uncritical for the current single-replica deployment reality.
- **`PUT /guard-status/{id}/config` does NOT validate whether a `role` change makes the `quorum_count` already set via `PUT /operational-config` unsatisfiable** (ADR 0092) — only the "at least one regular target remains" check is implemented, not the finer quorum consistency check.
