# dms-registry-client

Self-registration of a service with the registry (Concept 3.2a) — registers
itself on startup, keeps itself marked `healthy` via a periodic heartbeat, and
deregisters cleanly on shutdown. The basis for the API gateway's
registry-based routing (3.5, since P4-S1).

## Usage

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

`maybe_start_registration` returns `None` if `registry_service_base_url`
or `self_address` are not set (default in `BaseServiceSettings`) —
discovery is thus opt-in, not a hard dependency for the service itself.

## Behavior on errors

If the registry is unreachable (on start, heartbeat, or deregistration),
a warning is logged, but no exception is raised — a service must not
fail because of an unreachable registry, as that would contradict the
"plug in additionally" principle (the registry is itself a service that can
be temporarily down without the rest of the system coming to a halt).

## Tests

Against a real running `registry-service` instance (no mock):

```bash
cd infra && docker compose up -d postgres nats registry-service && cd ..
uv run pytest libs/dms-registry-client/tests
```
