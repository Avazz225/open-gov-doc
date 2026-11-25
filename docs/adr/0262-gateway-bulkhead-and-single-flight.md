# 0262 — Gateway: a bulkhead per upstream service, single-flight lookups

**Status:** accepted
**Context:** P95-S1a. In P95-S1's extended load test a stalled workflow-service took the whole system
down with it (`docs/operations/performance-ranking.md`):
- **One shared pool:** the gateway proxied every request through one `httpx.AsyncClient` (default pool:
  100 connections). Workflow requests held connections for up to the 30-s timeout, and every other route
  waited for a free one. A list_folder trace spent 17.6 s in the gateway and 21 ms in folder-service;
  the gateway itself used 0.22 cores.
- **Cache stampede:** the gateway's 5-s caches for registry resolution and the maintenance-mode state
  had no single-flight. On every expiry each request in flight fetched again, through the same starved
  pool, and that accounted for 48 % of the gateway's samples under that load.

## Decision

**A bulkhead per upstream service type** (`UpstreamBulkhead` in `upstream.py`):
- Each service type gets its own `httpx.AsyncClient` (its own connection pool) and a concurrency limit:
  `DMS_UPSTREAM_MAX_CONCURRENCY_DEFAULT` (64) and overrides in `DMS_UPSTREAM_MAX_CONCURRENCY` (for
  example `workflow-service=32,document-service=128`).
- A request that finds its service's share full waits at most `DMS_UPSTREAM_QUEUE_WAIT_SECONDS` (1 s).
  It then gets `503` with `Retry-After: 1` and the detail "… ist ausgelastet". Failing fast beats
  queueing: a client can retry, while a request queued for 30 s holds memory and a connection, and helps
  nobody.
- Other service types are untouched; one stalled service fills only its own share.

**Single-flight for the gateway's own lookups:**
- On expiry, registry resolution (per service type) and the maintenance-mode state are fetched once.
  Concurrent callers await that fetch (`asyncio.shield`, so a caller that gives up doesn't cancel it for
  the others).
- Both lookups use a small pool of their own (`DMS_CONTROL_POOL_MAX_CONNECTIONS`, 10, with a 5-s
  timeout), so proxied traffic can't starve them.

**Visibility:** sensors `gateway.upstream.in_use` (gauge) and `gateway.upstream.refused` (counter), both
labelled `upstream`. They aren't labelled `service`, because monitoring-service's scrape proxy sets that
label itself and would overwrite it.

## Why not …

- **…only a larger shared pool:** it postpones the starvation without removing it; a stalled service
  fills any size.
- **…per-service timeouts:** they help the stalled service's callers, not the others; the shared pool
  stays the coupling.
- **…a circuit breaker:** a natural next step (open after N timeouts, refuse for a while). The bulkhead
  already contains the damage; a breaker would only shorten the stalled service's own failures.
  Recorded as a candidate.

## Verified

- **Unit tests:** 50 concurrent resolutions or maintenance checks make one fetch; a full share refuses
  within the wait time and leaves another service type alone; each type has its own client.
- **API test:** a slow upstream with 5 concurrent requests against a share of 3 gives three `200` and two
  fast `503` with `Retry-After`, while a request to another upstream answers in under 0.5 s.
- **Live:**
  - workflow-service frozen with `docker pause`, 120 concurrent requests to it through the gateway;
  - meanwhile folder listings took a median of 21–23 ms (max 194–264 ms);
  - 64 workflow requests held its share and finished after the unpause, along with 20 that got a slot
    in time; 36 were refused after 1 s;
  - `gateway_upstream_refused_total{upstream="workflow-service"} 36` in monitoring.

## Consequences

- **The gateway can now hold up to 64 connections per service type** instead of 100 in total. The
  upstream services' own capacity, not the gateway, limits a single service.
- **Clients may see `503` under overload** where they used to wait until a `502` after 30 s. The UIs
  already treat `503` as "try again".
- **P95-S1e measures the extended scenario again** to show the effect under the real load.
