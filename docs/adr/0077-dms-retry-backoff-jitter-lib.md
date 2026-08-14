# 0077 — `libs/dms-retry`: shared backoff/jitter math

**Status:** accepted (Session 1 of 7, see Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 20 Session 1, new shared lib `libs/dms-retry`

## Decision

New, very small shared lib `libs/dms-retry` with exactly one function:
`compute_backoff_seconds(attempt, *, base=1.0, cap=300.0, rng=None) -> float` — "Full Jitter"
exponential backoff following the AWS standard formula (`random(0, min(cap, base * 2**attempt))`).

`storage-service`'s `replication.py::process_pending` already has the base pattern the user wants for
all five affected spots (storage, archival, notification, rendering/ocr, federation-hub service):
`ObjectCopy.attempts` + `max_replication_attempts` + status `"failed_permanent"` after exhaustion —
just without backoff/jitter between attempts (every `process_pending` call immediately reprocesses all
open copies again, regardless of how recently the last failure occurred). This session generalizes
only the **number formula**, not the poll-loop pattern itself.

## Rationale

- **Why a new lib instead of extending `dms-common`**: `dms-common` is explicitly described as the
  base for settings/logging/OpenTelemetry (`libs/README.md`) — a backoff formula is functionally
  independent of that and is only consumed by the five retry poll loops, not by every service. A
  dedicated, very small lib keeps the dependency explicit and optional, instead of extending
  `dms-common` (imported by EVERY service) with a function most of them never call.
- **Why "Full Jitter" instead of "Equal Jitter" or plain exponential backoff**: spreads simultaneously
  failed attempts (e.g. a backend outage that fails many `ObjectCopy` rows at once) most broadly across
  the time window — avoids a renewed thundering-herd effect at the next poll tick, without enforcing an
  additional minimum wait (as with "Equal Jitter"). Reference: AWS Architecture Blog, "Exponential
  Backoff And Jitter".
- **Why `attempt` is 0-indexed instead of 1-indexed**: fits directly onto `ObjectCopy.attempts` (starts
  at 0, before the first attempt) — the caller does not need to compute `attempts - 1`.
- **Why `rng` as an optional parameter instead of using the global `random` module directly**: allows
  deterministic unit tests (`random.Random(seed)`) without `unittest.mock.patch` on the global `random`
  module — same injection principle as `libs/dms-permission-client`'s `client` parameter for a
  prepared `httpx.AsyncClient`.
- **Why NO shared poll-loop framework** (deliberately NOT part of this session, see roadmap plan):
  the project deliberately duplicates poll loops lightweight (`_sla_poll_loop`, `_superuser_poll_loop`,
  `_archival_poll_loop` are each ~20 lines, identical try/except-continue idiom) rather than
  abstracting them — a framework abstraction for five structurally slightly different loops (some
  already synchronous inline, some already a poll loop) would be premature centralization without real
  benefit over copying a ~5-line formula.
- **No consumer yet in this session**: `compute_backoff_seconds` is only actually used starting with
  P20-S2 (archival-service) — this session only lays the shared foundation so it can be used
  identically (not slightly divergently copied) in the following four sessions.

## Consequences

- **Tests**: `libs/dms-retry` 6 new unit tests (edge cases: `attempt=0`, exponential growth before the
  cap, cap ceiling at large `attempt`, determinism with a seeded `rng`, negative `attempt` raises
  `ValueError`, default parameters produce a plausible range).
- **`uv.lock`**: `uv lock` was run, but unchanged — `dms-retry` has no dependencies yet and no
  consumer, so it only appears in the resolution graph starting with P20-S2, when `archival-service`
  declares it as a dependency.
- No Dockerfile needed to change (`COPY libs/ libs/` picks up new directories automatically, same
  pattern as `dms-permission-client`, P19-S1).
- No live verification step needed — a pure, stateless library function with no service connection.
