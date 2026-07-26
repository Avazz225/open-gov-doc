# dms-registry-client

Selbst-Registrierung eines Service bei der Registry (Konzept 3.2a) — meldet
sich beim Start an, hält sich per periodischem Heartbeat als `healthy` und
meldet sich beim Shutdown sauber ab. Grundlage für das registry-basierte
Routing des API-Gateways (3.5, seit P4-S1).

## Verwendung

```python
from dms_registry_client import maybe_start_registration

registration = await maybe_start_registration(
    registry_service_base_url=settings.registry_service_base_url,
    self_address=settings.self_address,
    service_type=settings.service_name,
    version="0.1.0",
)
...
if registration:
    await registration.stop()
```

`maybe_start_registration` gibt `None` zurück, wenn `registry_service_base_url`
oder `self_address` nicht gesetzt sind (Default in `BaseServiceSettings`) —
Discovery ist damit ein Opt-in, kein Hard-Dependency für den Service selbst.

## Verhalten bei Fehlern

Ist die Registry nicht erreichbar (Start, Heartbeat oder Deregistrierung),
wird eine Warnung geloggt, aber keine Exception geworfen — ein Service darf
nicht an einer nicht erreichbaren Registry scheitern, das würde dem
"Dazustellen"-Prinzip widersprechen (die Registry ist selbst ein Service, der
zeitweise nicht laufen kann, ohne dass der Rest des Systems stillsteht).

## Tests

Gegen eine echte laufende `registry-service`-Instanz (kein Mock):

```bash
cd infra && docker compose up -d postgres nats registry-service && cd ..
uv run pytest libs/dms-registry-client/tests
```
