# folder-service

Folder hierarchy (concept 2.1): create, rename, move, delete
(only when empty). Publishes structural events, through which the Permission Service
keeps its permission inheritance in sync.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/folders` | Create (`name`, `parent_id` default `"root"`, `created_by`, optional `object_type_id`/`attributes`) |
| `GET` | `/folders/{id}` | Metadata |
| `GET` | `/folders/{id}/children` | Direct subfolders |
| `PATCH` | `/folders/{id}` | Rename/move/change attributes |
| `DELETE` | `/folders/{id}` | Delete (409 if not empty) |
| `GET` | `/healthz` | Health check |

Details/events: see `../../docs/services/folder-service.md`.

## Structural contract with the Permission Service

This service implements exactly the contract that `permission-service` has
provisionally expected since P2-S2 (`folder.resource.created/.moved/.deleted`) -
no adjustment was needed. Verified live end-to-end in P3-S3: a folder created
via this API appears immediately in the Permission Service's
`resource_node` tree.

## Object-type validation

If a folder carries an `object_type_id`, this service validates the
attributes against the Object-Type Service before creation (`POST
/object-types/{id}/validate`) - without `object_type_id` the check is skipped.

## Registry registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` (heartbeat, deregister on shutdown) - opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, see `docs/services/gateway-service.md` for the consumer (API gateway, dynamic routing).

## Running locally

```bash
cd infra && docker compose up -d postgres nats object-type-service folder-service
curl localhost:8008/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats object-type-service && cd ..
uv run pytest services/folder-service/tests
```

`test_object_type_validation.py` requires a running Object-Type Service.
