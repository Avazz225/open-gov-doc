# audit-service

Immutable, hash-chained event log (concept 3.4/5.3). Consumes
all configured event bus subjects (default `["registry.>", "document.>", "permission.>", "virus_scan.>"]`,
see `Settings.subjects`) and appends each event to a
tamper-evident hash chain.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/events?limit=100` | Recorded events, chronological |
| `GET` | `/events/verify` | Verifies the entire chain for tampering |
| `GET` | `/healthz` | Own health check |

## How it works

- Each entry: `hash = sha256(prev_hash + canonical_JSON(fields))`. The first entry chains against `GENESIS_HASH` (64 zeros).
- **Idempotent** by `event_id`: JetStream may deliver duplicates under at-least-once delivery — already known `event_id`s are skipped instead of being chained again.
- **No `deliver_new`** on subscribe: the durable consumer `audit-service` catches up seamlessly after a restart instead of missing events (unlike short-lived test subscriptions, see `dms-eventbus-client`).
- Consumer without its own stream (`ensure_stream=False`, see [ADR 0001](../../docs/adr/0001-eventbus-consumer-without-stream-ownership.md)) — only knows the producers' subject convention, not their stream names.

## Registry registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` (heartbeat, deregister on shutdown) - opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, see `docs/services/gateway-service.md` for the consumer (API gateway, dynamic routing).

## Running locally

```bash
cd infra && docker compose up -d postgres nats audit-service
curl localhost:8002/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats && cd ..
uv run pytest services/audit-service/tests
```
