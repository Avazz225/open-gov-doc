# 0098 — Gateway instance selection is workload-aware, but deliberately per-replica only (not shared via Redis)

**Status:** accepted
**Context:** Concept 3.5, Session P25-S4 (`gateway-service`)

## Decision

Since P25-S4, `InstanceResolver.pick()` selects, among several healthy candidates of a
`service_type`, the instance with the fewest currently open requests, instead of purely at
random as before (ADR 0005, `random.choice`). The open-request counter (`dict[str, int]`, keyed
by instance address) lives **exclusively in the process memory of the respective
`InstanceResolver` instance** — with multiple horizontally scaled gateway replicas behind a load
balancer, each replica keeps its own, independent counter. There is deliberately **no**
cluster-wide shared counter (e.g. via Redis), even though exactly this pattern was introduced
just one session earlier (P25-S3, ADR 0097) for the rate limiter.

Reserve/release happens via an async context manager (`resolver.reserved_instance(instances)`)
that calls `pick()`, increments the counter before the upstream call, and releases it again in a
`finally` — even in the event of an `httpx.HTTPError` during the upstream call itself. Tie-break
when multiple instances share the same minimum: random among the minimum candidates (prevents
the same first instance in the list from always being preferred at rest, when all counters are
at 0).

## Rationale

The rate limiter (ADR 0097) had to be shared cluster-wide out of necessity: a purely local
counter would have been a **bypassable security guarantee** — a client could have effectively
multiplied the limit by distributing its requests across multiple gateway replicas. For instance
selection this bypass incentive is completely absent: a "too evenly distributed" load-balancing
counter gives a client no advantage it could specifically exploit. Instance selection is a pure
performance/fairness heuristic, not an access control mechanism.

A cluster-wide shared counter via Redis would have been technically possible (analogous to ADR
0097, e.g. `INCR`/`DECR` per instance address), but would have a real cost: **two additional
Redis round trips per proxied request** (one before, one after the actual upstream call, in
addition to the already-existing rate-limit round trip from P25-S3) — on the hottest path of the
entire system (practically every request goes through `proxy()`). This cost is out of proportion
to the benefit: even a purely per-replica-local view already approximates "prefer a
lightly-loaded instance" well enough, since each replica sees only a slice of overall traffic
anyway, and with multiple replicas behind a load balancer that slice is already reasonably evenly
distributed on its own. In the worst case, the lack of cluster-wide visibility leads to a
somewhat suboptimal, but never security-relevant, incorrect distribution.

## Consequences

- With multiple gateway replicas, load distribution is locally optimal per replica but only
  approximate globally — a single instance choice that looks "unlucky" from the outside across
  multiple replicas is possible but inconsequential (no security problem, only a slightly
  suboptimal distribution).
- No new infrastructure need (no additional Redis access) — unlike P25-S3, this session adds no
  new dependency or latency to the proxied request path.
- A real switch to a cluster-wide view remains possible later (the same Redis service as for the
  rate limiter would already be available), but is not part of this decision and is currently not
  deemed necessary.
- Still not latency-aware (only counts open requests, no actual response-time measurement) — see
  "Open Points" in `docs/services/gateway-service.md`.
