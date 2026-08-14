# object-type-service

Object type definitions (attributes, required fields, naming conventions,
conditional rules) for documents and folders (Concept 2.2) + validation
endpoint ("Constraint Engine", 4.5).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/object-types` | Create |
| `GET` | `/object-types?applies_to=document\|folder` | List |
| `GET` | `/object-types/{id}` | Single definition |
| `PUT` | `/object-types/{id}` | Update |
| `DELETE` | `/object-types/{id}` | Delete |
| `POST` | `/object-types/{id}/validate` | Validate `{name, attributes}` against the definition |
| `GET` | `/healthz` | Health check |

Details/schema format: see `../../docs/services/object-type-service.md`.

## Constraint Engine as a library, not a separate service

The validation logic lives in `libs/dms-constraint-engine` (a pure, stateless
function). This service is the only one that imports it - other
services exclusively call `/object-types/{id}/validate` over HTTP.
Rationale: `../../docs/adr/0003-constraint-engine-as-library.md`.

## Registry registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` (heartbeat, deregister on shutdown) - opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, see `docs/services/gateway-service.md` for the consumer (API gateway, dynamic routing).

## Running locally

```bash
cd infra && docker compose up -d postgres object-type-service
curl localhost:8007/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres && cd ..
uv run pytest services/object-type-service/tests
```
