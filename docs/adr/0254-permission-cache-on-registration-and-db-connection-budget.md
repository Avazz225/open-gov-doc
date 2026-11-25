# 0254 — Permission cache survives resource registration; a database connection budget

**Status:** accepted
**Context:** P89-S4 (user request 2026-11-21, after a load test).

The same 20-minute k6 profile as the August baseline (ramp to 200 VUs, hold, ramp down) failed badly. In
the hold phase:

| Metric | Aug 15 | Nov 21, before |
|---|---|---|
| p50, all actions | 8.2 ms | 136.9 ms |
| Error rate | 0 % | 18.8 % |
| Goodput (2xx and < 1 s) | 98.4 % | 80.2 % |

The sensors added in Phase 88 named the cause.

**The permission cache:**
- `POST /resources` and the `*.resource.created` event cleared the whole cache. Every uploaded document
  registers its own node, once synchronously and once via the event, since P39-S4 (ADR 0154, late
  September, after the August run).
- That made 23,464 invalidations in 21 minutes, a 14 % hit rate, and 91,424 recomputes at about 6.7 ms
  each: more than one core. permission-service, a single process, saturated, and every
  listing/upload/download waits on `/check`.

**The connections:**
- Slow checks held connections longer. document-, folder- and permission-service each reached
  SQLAlchemy's default 5 + 10.
- The other roughly 28 services each kept up to 5 idle pooled connections: 84 of Postgres' default 100
  were in use at rest.
- From about 140 VUs on, Postgres refused connections (`too many clients`), and the 500s cascaded
  through folder- and document-service.

Ruled out by measurement: the Phase 88 sensors (switching them off globally changed nothing), the
gateway, and machine load.

## Decision 1 — Registering a node does not invalidate; unregistered resources are not cached

A new leaf node has no assignments of its own. It changes no existing effective permission. Its own
effective permissions are computed on first use from its ancestors. The invalidation on registration
protected exactly one thing: a deny cached for the resource before its node existed.

**Option (a), targeted:** invalidate only that resource's rows on registration.
- A recompute racing the registration could still write the deny back after the delete.
- Closing that race needs the global generation bump (ADR 0239), which is the cost being removed.
- And `resource_id` isn't indexed in the cache table, so the delete would be a scan per upload.

**Option (b), chosen:** don't cache a deny for an unregistered resource.
- `_collect_effective_roles` now reports whether the resource itself is registered, and
  `get_effective_permissions` returns an uncached answer when it isn't.
- **No row for a node can exist before the node does,** so registration has nothing to invalidate.
- **The race is closed:** a racing recompute either saw no node (and caches nothing) or saw it (and
  caches the right answer).
- **The cost is small:** a check before registration misses every time, and registration is
  synchronous for documents and cases, so that window is rare.
- **Upgrade:** rows cached under the old behaviour could include such a deny. One invalidation at
  startup drops them; repeating it per replica is harmless.

**Every other `invalidate_cache` call site was reviewed and stays global:** role updates, group delete,
membership add/remove, assignment create/delete, `inherit` changes, node move and delete. These are real
permission or structure changes and rare. Subtree-scoped invalidation stays the deferred optimization it
was (ADR 0239).

## Decision 2 — A connection budget, configurable per service

A SQLAlchemy pool never closes idle connections, so a process can always hold `pool_size + max_overflow`.
About 30 database-backed services at 15 each could need 450 connections against Postgres' 100.

**The options:**
- **(a) Only raise `max_connections`:** this treats the symptom. Every Postgres connection is a backend
  process with its own memory.
- **(b) PgBouncer (transaction pooling) in front of Postgres:** the standard answer at scale.
  - It is a new component in compose and Helm.
  - asyncpg's prepared statements need `statement_cache_size=0` or PgBouncer's prepared-statement
    support.
  - Advisory locks in the alert evaluator (ADR 0245) and session-level settings would need review.
  - It is weighed here and not built: it becomes worthwhile when the number of replicas, not the number
    of services, drives the count.
- **(c) A budget, chosen:**
  - `dms-db-base.build_engine` sizes the pool from `DMS_DB_POOL_SIZE`/`DMS_DB_MAX_OVERFLOW`/
    `DMS_DB_POOL_TIMEOUT_SECONDS`.
  - Small defaults: 2 + 3, timeout 30 s.
  - The services on every request path get more: permission 10 + 20, document and folder 8 + 12,
    storage 4 + 6.
  - `max_connections` is set explicitly: 300 in compose, and `postgresql.maxConnections: 500` in Helm.
  - **The formula** (documented in `docs/operations/rolling-updates.md`): the sum over services of
    maximum replicas × budget, plus Keycloak's pool, plus a reserve. Helm comes to about 370 for the
    services at full autoscaling.
  - **Effect:** a full pool now makes a request wait instead of Postgres refusing it.

## Results

Re-run of the identical profile after both changes (`run-20261121T183750Z`), hold phase at 200 VUs:

| Metric | Aug 15 | Nov 21, before | Nov 21, after |
|---|---|---|---|
| p50, all actions | 8.2 ms | 136.9 ms | 23.5 ms |
| p95 | 89.7 ms | 725.6 ms | 187.0 ms |
| p99 | 2,012 ms | 1,017 ms | 447 ms |
| Error rate | 0 % | 18.8 % | 0 % (1 of 25,693 listings) |
| Goodput | 98.4 % | 80.2 % | 99.97 % |
| Throughput | 97.3 req/s | 90.6 req/s | 97.9 req/s |

- **Cache:** no invalidations (23,464 before); hit rate 75 % (14 % before). The misses are mostly each
  new document's first check.
- **permission-service CPU:** about 0.5 cores on average (about 0.93 before).
- **Connections:** 43 in use at rest (84 before); at most 17 checked out at once across all services.
- **Tail:** p99 and goodput are better than in August.
- **The one error:** a gateway `502` after 5 ms with no matching entry in folder-service's log. The likely
  explanation is a keep-alive connection the upstream had already closed. It is one in about 100,000
  requests and wasn't pursued further.

## What remains: about 2× per-request latency at low load

Even at a handful of VUs the August run was faster:

| Action | Aug 15 | Nov 21, after |
|---|---|---|
| `list_folder` | 5 ms | 11 ms |
| `upload` | 25 ms | 42 ms |
| `download` | 10 ms | 17 ms |

That is not the cache: it no longer invalidates and hits mostly.

**Candidates:** the work added per request since August, for example:
- per-document resource nodes, so downloads check the document node and not only the folder;
- acting identity (ADR 0240);
- HTTP sensors;
- more checks per request.

Telling them apart needs a per-hop breakdown. That is what distributed tracing (P94-S1) provides, so the
question is recorded there instead of being guessed at here.

**Answered in P94-S1** ([ADR 0250](0250-distributed-tracing.md), "Measured"): new synchronous hops, not a
slower database. Listing and download each gained one permission check since August (+4.7 / +5.2 ms), and
the upload gained four calls (folder lookup with its own check, write check, resource-node registration,
virus-scan's check). Every hop costs 2.5–4 ms of the service's own request handling even for a cache hit.

## Consequences

- permission-service: the startup invalidation; `create_resource` and the `*.resource.created` handler
  no longer invalidate; tests for both rules and the integration path.
- `dms-db-base`: `pool_settings()` plus pure tests.
- compose: pool env for the four hot services and `max_connections=300`.
- Helm: pool env for the same services and `postgresql.maxConnections` (rendered as a Postgres
  argument).
- Every service picks up the new pool defaults on its next build. The phase-close regression runs again
  because a shared library changed.

**Note (P95-S2b, [ADR 0265](0265-permission-hot-path-and-replicas.md)):** the startup invalidation of
Decision 1 is removed. Every installation has started since P89, and with replicas each new instance
would have made the whole cache cold.
