# 0001 — Event bus consumers do not own their own stream

**Status:** accepted
**Context:** Concept 3.4/5.3, Session P1-S2 (Audit Service)

## Decision

`NatsEventBusClient` (libs/dms-eventbus-client) distinguishes two roles via
the new constructor parameter `ensure_stream`:

- **Producer** (default, `ensure_stream=True`, `stream=<name>` required):
  `connect()` creates its own JetStream stream if it does not already exist.
- **Pure consumers** (`ensure_stream=False`, no `stream` name needed):
  `connect()` only connects, without declaring a stream. `subscribe()`
  still works, since JetStream resolves the matching stream server-side
  based on the subscribed subject.

## Rationale

The Audit Service (3.4/5.3) needs to consume events from an **arbitrary
number** of producer services, without knowing or owning their streams. With
the original signature from P0-S2 (`stream` was a required parameter,
`connect()` always created it), the Audit Service would have had to
instantiate a separate `NatsEventBusClient` with that producer's stream name
for every producer - unnecessary coupling to producer-internal naming that
would have had to be updated for every new service type.

## Consequences

- Consumers only know the subject convention (`<producer-stream>.>`), not the
  stream names themselves.
- A subject can only be consumed once at least one producer has already
  created the corresponding stream (the producer must have been started at
  least once before the first consumption - uncritical, since streams are
  persisted server-side and are not recreated on every producer restart).
- Existing producer code (Registry Service, future services) is unaffected
  by this change - `ensure_stream=True` remains the default.
