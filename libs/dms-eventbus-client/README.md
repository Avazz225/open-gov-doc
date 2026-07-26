# dms-eventbus-client

Publish/Consume-Interface für den Event-Bus (Konzept 3.4) — Services kennen nur
`EventBusClient`, nicht das konkrete Backend.

- `EventBusClient` — abstraktes Interface (`connect`, `publish`, `subscribe`, `close`).
- `NatsEventBusClient` — Werkseinstellungs-Implementierung über NATS JetStream (`nats-py`). Legt den Stream beim Connect an, falls er noch nicht existiert.

Eine Kafka-Implementierung (für große Einzelinstallationen, siehe 3.4) kann später als
weitere Klasse hinter demselben Interface ergänzt werden, ohne Aufrufer anzufassen.

## Nutzung

```python
from dms_eventbus_client import NatsEventBusClient

bus = NatsEventBusClient(settings.nats_url, stream="document-service")
await bus.connect()
await bus.publish("document-service.created", payload)
await bus.subscribe("document-service.created", handler, durable="search-indexer")
```

## Tests

Integrationstest gegen echtes NATS JetStream (nutzt `infra/docker-compose.yml`):

```bash
cd infra && docker compose up -d nats && cd ..
uv run pytest libs/dms-eventbus-client/tests
```
