# config-service

Configuration import/export (concept 7.3, P12-S3): the complete system configuration
(object types including form layouts, workflows, role templates, four-eyes configuration,
sensor configuration) can be exported as a single JSON document and re-imported into another (or the
same, e.g. staging→production) system. Since **P14-S1**, also a delta/
comparison function between two exports (7.5, configurable ignore regex for differing
naming conventions). Pure orchestrator without its own Postgres schema — details: see
[`docs/services/config-service.md`](../../docs/services/config-service.md).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/config/export` | Export configuration document, optionally `?categories=roles&categories=...` |
| `POST` | `/config/compare` | Delta/comparison function between two exports (7.5, P14-S1) — ungated, read-only |
| `POST` | `/config/import` | Import configuration document (upsert per category) — gated behind `admin.object_config`, requires `X-DMS-Principal` header |
| `GET` | `/healthz` | Health check |

## Running locally

```bash
cd infra && docker compose up -d postgres object-type-service workflow-service permission-service monitoring-service registry-service config-service
curl localhost:8029/healthz
```

## Tests

Runs against the real, running container, like `webdav-connector`/`migration-service` (no
in-process `TestClient`, no mocking of neighboring services):

```bash
cd infra && docker compose up -d postgres object-type-service workflow-service permission-service monitoring-service registry-service config-service
cd ..
uv run pytest services/config-service/tests
```
