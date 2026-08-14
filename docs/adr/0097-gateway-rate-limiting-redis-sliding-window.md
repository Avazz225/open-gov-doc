# 0097 — Gateway: Rate Limiting switched to Redis (sorted-set sliding window)

**Status:** accepted
**Context:** Concept 3.5, Session P25-S3 (post-roadmap feature, revisit of ADR 0005)

## Decision

1. `gateway-service`'s `RateLimiter` no longer stores its sliding-window counter per client key
   in an in-process `dict[str, deque[float]]` (ADR 0005), but in **Redis** — the
   first occurrence of Redis in this entire project. New `redis` service in
   `infra/docker-compose.yml`, new `redis_url` setting (default points to this service),
   new `redis` package dependency (`redis.asyncio`, the official async Redis client — the
   former separate `aioredis` library has been merged into it since redis-py 4.2).
2. The sliding-window semantics remain exactly the same (`max_requests`/`window_seconds`
   unchanged), implemented via a sorted set per client key (`ZADD`/`ZREMRANGEBYSCORE`/
   `ZCARD` in a MULTI/EXEC transaction) instead of the simpler fixed-window variant
   (`INCR`+`EXPIRE`).
3. `RateLimiter.allow()` is now `async` (Redis access is inherently asynchronous) — the only
   caller (`gateway_service.main.proxy()`) calls it with `await` accordingly.

## Rationale

- **Redis instead of staying in-process**: ADR 0005 deliberately documented this limitation from
  the start and explicitly foreshadowed a later migration ("switch to an external store (Redis)
  later if needed, without changing the `RateLimiter` interface itself") — exactly this case now
  arises: a horizontally scaled gateway deployment (multiple replicas behind a load balancer)
  would have kept its own, independent limit per replica with the old in-process `dict`. A client
  could have effectively multiplied the configured limit by distributing its requests across
  multiple replicas — for a login protection mechanism (rate limiting explicitly also applies to
  the public `auth-service:login` route, see `docs/services/gateway-service.md`) this is a real
  brute-force weakness, not merely a capacity concern.
- **Sorted-set sliding window instead of fixed window (`INCR`+`EXPIRE`)**: a fixed window is
  simpler (a single counter + a TTL instead of a sorted set), but has a known weakness at the
  window boundary — the end of window N and the start of window N+1 coincide in time for a
  client, which can briefly let through up to `2 × max_requests`. For login protection this is a
  real, not merely theoretical, weakness. The sorted-set approach reproduces the original
  `deque` semantics (each individual hit has its own timestamp, old hits drop out exactly after
  `window_seconds`) almost 1:1 — the price is a sorted-set member per request instead of a single
  atomic counter, negligible at the window sizes typical here (default 600 requests/60s per
  client) compared to the cleaner guarantee. A server-side Lua script (for a single atomic
  check-and-add operation without the subsequent `ZREM` fallback on overflow) would have been an
  alternative, but introduces another moving part (script deployment/versioning) for the same
  effect — not justified at the moderate load requirements here.
- **`redis.asyncio` instead of the separate `aioredis` package**: `aioredis` has been officially
  merged into `redis-py` since redis-py 4.2 (`from redis import asyncio as redis`) — no reason to
  introduce a now-archived separate dependency.
- **No Redis persistence volume** (`--save ""`, no AOF, see `infra/docker-compose.yml`): the
  rate-limit data is deliberately purely transient (TTL per client key) — a restart of the
  `redis` container itself harmlessly resets the limit (briefly more generous, no security
  problem), there is no functional reason to pay for disk I/O or a volume for this, unlike e.g.
  NATS JetStream or Postgres in this stack.

## Consequences

- New infrastructure/deployment dependency: a real installation must now run a `redis` service
  that did not exist before — for the bundled dev/test stack this is already covered by the new
  `infra/docker-compose.yml` service; for a production deployment (e.g. Kubernetes) an operator
  must plan for this additional building block.
- `gateway-service`'s only remaining role as a "stateless" service (see
  `docs/services/gateway-service.md`, "Own Postgres schema: none") still holds with regard to
  Postgres — the rate-limit data now lives in a shared but deliberately non-durable store, no new
  Postgres schema needed.
- Tests now run against a real, running `redis` container (no mock, same test strategy as
  against Postgres/NATS/MinIO everywhere else in the project) — `scripts/run-tests.sh` needs no
  adjustment for this, since `redis` is already brought up like any other Compose service before
  the test run, and `gateway-service` itself is not on the `CONSUMER_SERVICES` list (no own NATS
  consumer, no container stop/start special case needed).
- Every proxied request now incurs several additional Redis round trips (a MULTI/EXEC
  transaction, plus an additional `ZREM` in the rejection case) instead of a pure in-memory
  access — at the load requirements typical here (dev/learning project, not high-load
  production) not noticeable, but a deliberately accepted overhead that a real high-load
  installation should keep an eye on.
- Instance selection (`InstanceResolver.pick()`, random load balancing) remains unchanged
  in-process-only behavior (see P25-S4, worked on in parallel) — this session changes only the
  rate limiting, no other parts of `main.py`.
