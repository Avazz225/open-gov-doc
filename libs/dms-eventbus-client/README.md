# dms-eventbus-client

Publish/Consume-Interface für den Event-Bus (Konzept 3.4) — Services kennen nur
`EventBusClient`, nicht das konkrete Backend.

- `EventBusClient` — abstraktes Interface (`connect`, `publish`, `subscribe`, `close`).
- `NatsEventBusClient` — Werkseinstellungs-Implementierung über NATS JetStream (`nats-py`).
- `Event` — gemeinsame Ereignis-Hülle (Konzept 3.4/5.3): `event_id`, `event_type`, `occurred_at`, `service_name`, `subject`, `payload`, `actor` (seit P7-S2 — handelnde Person, `None` bei Alt-Events/wo keine Aktions-Identität existiert), `on_behalf_of` (seit P14-S11, 4.4a — vertretene Person bei einer Aktion "im Auftrag von", siehe `docs/services/audit-service.md`). `to_bytes()`/`from_bytes()` für den Transport.

Eine Kafka-Implementierung (für große Einzelinstallationen, siehe 3.4) kann später als
weitere Klasse hinter demselben Interface ergänzt werden, ohne Aufrufer anzufassen.

## Producer vs. Konsument (siehe ADR 0001)

- **Producer** (z. B. Registry Service) geben `stream=` an (Default `ensure_stream=True`) - `connect()` legt den Stream an, falls er fehlt.
- **Reine Konsumenten** (z. B. Audit Service, der Ereignisse mehrerer fremder Services liest) verwenden `NatsEventBusClient(url, ensure_stream=False)` ohne `stream`-Namen - JetStream löst den passenden Stream beim `subscribe()` automatisch über das Subject auf, kein Stream-Ownership nötig.

## Nutzung

```python
from dms_eventbus_client import Event, NatsEventBusClient

bus = NatsEventBusClient(settings.nats_url, stream="registry")
await bus.connect()
event = Event(
    event_type="registry.instance.registered", service_name="registry-service", payload={...}
)
await bus.publish("registry.instance.registered", event.to_bytes())

# Reiner Konsument, z. B. Audit Service:
consumer = NatsEventBusClient(settings.nats_url, ensure_stream=False)
await consumer.connect()
await consumer.subscribe("registry.>", handler, durable="audit-service")
```

## Sauberes Beenden (`close()`, seit P15-S1)

`close()` ruft intern `drain()` statt `nc.close()` auf — Letzteres trennt die Verbindung sofort, ohne aktive Subscriptions serverseitig sauber abzumelden. Bei einem durable JetStream-Consumer (`subscribe(..., durable=...)`) blieb die Bindung dadurch kurzzeitig bestehen, selbst nachdem der Prozess bereits beendet war (z. B. per `docker compose stop`) — ein sofortiger Neustart mit demselben `durable`-Namen (z. B. der nächste Testlauf desselben Service, siehe `scripts/run-tests.sh`s `CONSUMER_SERVICES`) konnte dann intermittierend mit `nats: JetStream.Error consumer is already bound to a subscription` fehlschlagen (live bei P15-S1 in `scripts/run-tests.sh --build` beobachtet und reproduziert). `drain()` meldet jede Subscription explizit ab, bevor die Verbindung geschlossen wird, und behebt das Rennen an der Wurzel statt es nur unwahrscheinlicher zu machen — mit eingebautem Timeout (nats-py-Default 30s), kein Risiko eines hängenden Shutdowns.

## Tests

Integrationstest gegen echtes NATS JetStream (nutzt `infra/docker-compose.yml`):

```bash
cd infra && docker compose up -d nats && cd ..
uv run pytest libs/dms-eventbus-client/tests
```
