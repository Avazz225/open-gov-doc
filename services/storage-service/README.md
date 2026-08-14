# storage-service

Storage abstraction layer over interchangeable backend plugins (Concept 3.6).
File contents never live in the relational shared DB — only reference,
checksum, and size.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `PUT` | `/objects/{key}` | Upload (body = raw content, `Content-Type` header optional) |
| `GET` | `/objects/{key}` | Download (read fallback across configured targets) |
| `DELETE` | `/objects/{key}` | Delete (all targets + metadata) |
| `GET` | `/object-metadata/{key}` | Metadata (checksum, size, backend, timestamps) |
| `GET` | `/objects/{key}/copies` | Copy status per target |
| `GET` | `/object-verify/{key}` | Fixity check of the primary target |
| `GET` | `/object-verify/{key}/all` | Fixity check across all targets |
| `POST` | `/replication/process-pending` | Process the retry queue for pending copies |
| `GET` | `/guard-config` | Read guard configuration (`allow_degraded_start`) |
| `PUT` | `/guard-config` | Change guard configuration (takes effect only on next startup) |
| `GET` | `/guard-status` | Device ID/status per target (admin UI status block) |
| `POST` | `/guard-status/{target_id}/reidentify` | Accept an intentional storage medium change without a restart |
| `GET` | `/healthz` | Health check, shows active targets + write strategy |

`{key}` allows slashes (`docs/2026/vertrag.pdf`).

## Backend Plugins (3.6)

Two implementations of the `StorageBackend` interface (write, read,
delete, existence check, checksum):

- **`local`** — local filesystem under the `base_path` configured per target.
  **Also covers the NFS case**: in Kubernetes this path is the mount point
  of a PVC — whether NFS or block storage underlies it is invisible to the
  code, both behave as a normal folder. A separate NFS backend is therefore
  not needed. Writes atomically (temp file + `os.replace`) instead of using
  platform-specific file locking, whose semantics are inconsistent across
  NFS implementations.
- **`s3`** — S3-compatible (`aioboto3`), MinIO by default for local development,
  works identically against AWS S3/Ceph RGW.

## Target Set (since P5b-S6)

`DMS_TARGETS` is a JSON list of `{id, type, ...type-specific fields}` -
any number of entries, including multiple of the same `type` (e.g. two S3
providers), since `id` and not `type` is the unique key. Replaces the earlier
fixed `DMS_BACKEND`/`DMS_SECONDARY_BACKEND` structure, see
`../../docs/adr/0004-storage-redundancy-scope.md` and
`../../docs/adr/0017-storage-device-identity-guard.md`.

```bash
DMS_TARGETS='[{"id":"local","type":"local","base_path":"/tmp/dms-storage-dev"}]'
```

- `DMS_WRITE_STRATEGY=quorum|primary_async` (default `primary_async`) + for
  `quorum` also `DMS_QUORUM_COUNT` (must be ≤ number of configured targets).
- With `primary_async`, every copy except the primary target's initially
  stays `pending` and is only caught up via `POST /replication/process-pending`
  (retry queue, no in-process background task).

## Storage Medium Change Guard (since P5b-S6)

Every target gets a generated device ID (marker object under the reserved
key `__dms_storage_identity__`), compared against the reference value stored
in the shared DB (`backend_identity`) on every startup. Default: startup
refusal on mismatch/unreachability. Admin override
`PUT /guard-config {"allow_degraded_start": true}` allows a degraded start,
provided at least one target is verifiably unchanged - afterwards automatic
flagging for catch-up replication (`POST /replication/process-pending`).
Details see `../../docs/adr/0017-storage-device-identity-guard.md`.

**Rebalancing + correction mechanism (since P5c-S2)**: a target newly added
to the target set automatically gets `pending` copies for all already
existing objects during initial-startup bootstrap (no separate trigger
needed - a target set change requires a restart anyway). An intentional
storage medium change can be accepted at runtime via `POST
/guard-status/{target_id}/reidentify` (adopts an existing marker file of the
new device or stamps a new one, resets existing copies to `pending`) -
replaces the previously required direct correction in `backend_identity`.

## Registry Registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` (heartbeat, deregister on shutdown) - opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, see `docs/services/gateway-service.md` for the consumer (API Gateway, dynamic routing).

## Running Locally

```bash
# One local target (default, no redundancy)
cd infra && docker compose up -d postgres minio storage-service
curl localhost:8005/healthz

# With redundancy (quorum across local+s3)
STORAGE_SERVICE_TARGETS='[{"id":"local","type":"local","base_path":"/data/storage"},
  {"id":"s3-minio","type":"s3","endpoint_url":"http://minio:9000","access_key":"dms_minio",
   "secret_key":"dms_minio_dev_only","bucket":"dms-storage","region":"us-east-1"}]' \
  STORAGE_SERVICE_WRITE_STRATEGY=quorum STORAGE_SERVICE_QUORUM_COUNT=2 \
  docker compose up -d --force-recreate storage-service
```

## Tests

```bash
cd infra && docker compose up -d postgres minio && cd ..
uv run pytest services/storage-service/tests
```

`test_local_backend.py`/`test_backend_factory.py` run without infrastructure
(use `tmp_path`). `test_s3_backend.py` needs real MinIO.
`test_api.py`/`test_repository.py`/`test_identity_guard.py` need Postgres
(**86 tests** in total).
