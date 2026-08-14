# registry-service

Service Discovery (Concept 3.2a): registration, heartbeat, and the active
routing table per service type. License brokering (3.2b) only follows with
the License Service (Phase 9) and is deliberately not included here yet.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/instances` | Register (upsert by `instance_id`) |
| `POST` | `/instances/{instance_id}/heartbeat` | Send heartbeat |
| `DELETE` | `/instances/{instance_id}` | Deregister |
| `GET` | `/instances/{service_type}` | Active routing table for a service type |
| `GET` | `/instances` | All instances incl. `healthy` flag (debug/admin) |
| `GET` | `/healthz` | Own health check |

**Failure detection without a background job**: an instance is considered
failed if its last heartbeat is older than `heartbeat_timeout_seconds`
(default 15s). This is computed on read, not via a mutating sweep process -
avoids race conditions and is simpler to test, with an identical result
("failed instances do not appear in the active routing table").

## Events (Concept 3.4)

Published after successful commit (`dms-eventbus-client`, stream `registry`):

- `registry.instance.registered` — `subject` = `instance_id`, `payload` = `{service_type, version}`
- `registry.instance.deregistered` — `subject` = `instance_id`, `payload` = `{service_type}`

No event per heartbeat (too high-frequency, not an audit-relevant operation).

## Running Locally

```bash
cd infra && docker compose up -d postgres nats registry-service
curl localhost:8001/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats && cd ..
uv run pytest services/registry-service/tests
```
