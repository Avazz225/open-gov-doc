# document-service

Documents as the core entity (concept 2.1): CRUD, durable versioning (2.1a),
editing lock including force-unlock and conflict copy (4.2). Never holds
file content itself — every access goes through the Storage Service's HTTP API (3.6).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/documents` | Create (multipart: `file`, `title`, `created_by`, ...) |
| `GET` | `/documents/{id}` | Metadata |
| `DELETE` | `/documents/{id}?deleted_by=...` | Soft delete |
| `GET` | `/documents/{id}/content` | Content of the current version |
| `GET` | `/documents/{id}/versions` | All versions (including conflict copies) |
| `POST` | `/documents/{id}/versions` | Check-in (multipart: `file`, `expected_base_version_number`, `created_by`) |
| `GET`/`POST`/`DELETE` | `/documents/{id}/lock` | Read/set/regularly release lock |
| `POST` | `/documents/{id}/lock/force-release` | Administrative force-unlock |
| `GET`/`PUT` | `/upload-config` | Read/change format whitelist (since P5d-S1) |
| `GET` | `/healthz` | Health check |

Details/schema/events: see `../../docs/services/document-service.md`.

## Content-type detection & format whitelist (since P5d-S1)

The stored `content_type` is determined from the actual file bytes via
`python-magic`/`libmagic`, not taken from the header sent by the
client. An admin-editable whitelist (`GET`/`PUT
/upload-config`, empty = no restriction) rejects unlisted formats
with `400` before virus scanning/storage are performed.

## Conflict protection instead of a "supervised" lock

The concept describes force-unlock via a third lock state
("released, but supervised"). This service deliberately forgoes this and
instead achieves the same data protection through an always-active, optimistic
version check at check-in: every upload states which version it
is based on (`expected_base_version_number`); if that differs from the
current main version at that time, a standalone conflict copy is created instead
of a silent overwrite. Rationale: see
`../../docs/adr/0002-document-locking-optimistic-conflict-detection.md`.

## Storage: content-addressed object keys

Objects are stored under `documents/{document_id}/{sha256}` in the
Storage Service — this avoids the ordering dependency "key needs
version number, version number needs completed DB write" and
automatically deduplicates identical content.

## Folder and object-type integration (since P3-S3)

- `folder_id` (optional): checked against the Folder Service on creation
  (`GET /folders/{id}`) - unknown folder ID → 400.
- `object_type_id` + `attributes` (optional, `attributes` as a JSON string in the
  multipart field): checked against `POST /object-types/{id}/validate` of
  the Object-Type Service - invalid attributes → 400 with error list.
- Both checks are skipped entirely if the respective field is not set
  (no enforced folder/object type).

## Registry registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` (heartbeat, deregister on shutdown) - opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, see `docs/services/gateway-service.md` for the consumer (API gateway, dynamic routing).

## Running locally

```bash
cd infra && docker compose up -d postgres nats minio storage-service object-type-service folder-service document-service
curl localhost:8006/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats minio storage-service object-type-service folder-service && cd ..
uv run pytest services/document-service/tests
```

All tests run against real infrastructure (Postgres, NATS, Storage/Folder/
Object-Type Service via HTTP) — no mocks.
