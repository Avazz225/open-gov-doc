# migration-service

Migration/transfer service (Concept 7.2, P12-S2): lock → copy → verify →
release in the target system → deletion in the source system after a
transition period, between two directly paired installations of this
software (no hub, see ADR in `docs/adr/`). Itself runs as an auditable,
resumable workflow via `workflow-service` — details in
[`docs/services/migration-service.md`](../../docs/services/migration-service.md).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/paired-installations` | Pair a target/source installation (API key returned once) |
| `GET`/`DELETE` | `/paired-installations[/{id}]` | List/remove |
| `POST` | `/transfers` | Start a transfer (supports four-eyes approval, 4.3) |
| `GET` | `/transfers[/{id}]` | Status/list |
| `POST` | `/transfers/{id}/steps/*` | Internal — target of the `connector_call` service tasks in `resources/*.bpmn` |
| `POST` | `/inbound/transfers/*` | Target side — invoked by a paired source, `Authorization: Bearer <api_key>` |
| `GET` | `/healthz` | Health check |

## Running locally

```bash
cd infra && docker compose up -d postgres nats document-service folder-service permission-service workflow-service registry-service migration-service
curl localhost:8028/healthz
```

## Tests

Runs like `webdav-connector` against the real, running container (self-loopback
smoke test instead of a second real installation, see `docs/services/migration-service.md`):

```bash
cd infra && docker compose up -d postgres nats document-service folder-service permission-service workflow-service registry-service migration-service
cd ..
uv run pytest services/migration-service/tests
```
