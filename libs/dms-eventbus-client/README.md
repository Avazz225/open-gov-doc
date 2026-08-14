# dms-eventbus-client

Publish/consume interface for the event bus (Concept 3.4) — services know only
`EventBusClient`, not the concrete backend.

- `EventBusClient` — abstract interface (`connect`, `publish`, `subscribe`, `close`).
- `NatsEventBusClient` — default implementation over NATS JetStream (`nats-py`).
- `Event` — shared event envelope (Concept 3.4/5.3): `event_id`, `event_type`, `occurred_at`, `service_name`, `subject`, `payload`, `actor` (since P7-S2 — the acting person, `None` for legacy events/where no action identity exists), `on_behalf_of` (since P14-S11, 4.4a — the represented person for an action performed "on behalf of", see `docs/services/audit-service.md`). `to_bytes()`/`from_bytes()` for transport.

A Kafka implementation (for large single installations, see 3.4) can be added
later as an additional class behind the same interface, without touching callers.

## Producer vs. consumer (see ADR 0001)

- **Producers** (e.g. Registry Service) provide `stream=` (default `ensure_stream=True`) - `connect()` creates the stream if it is missing.
- **Pure consumers** (e.g. Audit Service, which reads events from multiple foreign services) use `NatsEventBusClient(url, ensure_stream=False)` without a `stream` name - JetStream automatically resolves the matching stream on `subscribe()` via the subject, no stream ownership needed.

## Usage

```python
from dms_eventbus_client import Event, NatsEventBusClient

bus = NatsEventBusClient(settings.nats_url, stream="registry")
await bus.connect()
event = Event(
    event_type="registry.instance.registered", service_name="registry-service", payload={...}
)
await bus.publish("registry.instance.registered", event.to_bytes())

# Pure consumer, e.g. Audit Service:
consumer = NatsEventBusClient(settings.nats_url, ensure_stream=False)
await consumer.connect()
await consumer.subscribe("registry.>", handler, durable="audit-service")
```

## Clean shutdown (`close()`, since P15-S1)

`close()` internally calls `drain()` instead of `nc.close()` — the latter disconnects immediately, without cleanly unregistering active subscriptions server-side. With a durable JetStream consumer (`subscribe(..., durable=...)`), the binding therefore remained briefly in place even after the process had already terminated (e.g. via `docker compose stop`) — an immediate restart with the same `durable` name (e.g. the next test run of the same service, see `scripts/run-tests.sh`'s `CONSUMER_SERVICES`) could then intermittently fail with `nats: JetStream.Error consumer is already bound to a subscription` (observed and reproduced live during P15-S1 in `scripts/run-tests.sh --build`). `drain()` explicitly unregisters each subscription before closing the connection, fixing the race at its root rather than merely making it less likely — with a built-in timeout (nats-py default 30s), no risk of a hanging shutdown.

## Tests

Integration test against real NATS JetStream (uses `infra/docker-compose.yml`):

```bash
cd infra && docker compose up -d nats && cd ..
uv run pytest libs/dms-eventbus-client/tests
```
