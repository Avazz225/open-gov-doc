# Rolling Updates (Concept 10.5)

An operational procedure, not a service — the first document of this category in
the project. Describes how a single service is updated without
interruption in this Docker Compose deployment target (P10-S0 finding: no
orchestration platform present), reusing the drain mechanism
built in P10-S2 (`registry-service`).

## Basic mechanism: parallel operation instead of in-place replacement

`scripts/rolling-update.sh <service>` executes the choreography
described in 10.5:

1. Query the existing instance ID(s) of the service type from the registry.
2. Build the new image.
3. Start a second, temporary container ("canary")
   **without a published host port** (`docker compose run -d --no-deps --name
   dms-<service>-canary`) — no conflict with the running, port-bound
   compose service. `DMS_SELF_ADDRESS` is overridden to the
   container's own DNS name, since a `docker compose
   run` container does not automatically get the shared service network alias.
4. Poll (`GET /instances/{service_type}`) until the registry reports the canary
   as a new, healthy, `"active"` instance — the health/readiness
   check from 10.5 ("goes through a health/readiness check there before
   it is even considered ready") is realized via the already
   existing heartbeat/`healthy` computation of the registry, no new protocol.
5. Set the old instance(s) to draining (`POST /instances/{id}/drain`, P10-S2)
   — stops accepting new requests, running operations continue
   untouched (`gateway-service`'s `InstanceResolver` excludes them
   from routing new requests).
6. Wait out the grace period (`--drain-grace-seconds`, default 30s).
7. Stop/remove the old, regular compose instance — `docker compose stop`
   sends `SIGTERM`; the service's own lifespan shutdown logic
   already deregisters itself via `dms-registry-client`
   (`DELETE /instances/{id}`), no explicit call needed in the script.
8. Freshly start the regular, port-published container (finally
   replacing the canary), likewise waiting for its readiness.
9. Set the canary to draining, grace period, stop/remove.

At every point in time, at least one healthy, active instance remains
reachable — no time window with no service at all.

## Limitations: honestly documented simplifications

- **Grace period instead of a generic "running operations complete"
  signal**: there is no cross-service mechanism for
  detecting when a draining instance truly has no open operations left
  (this would differ per service — an open BPMN process,
  an ongoing storage write, an open signature task, ...).
  A fixed, configurable time period is the pragmatic choice that
  Concept 10.5 itself provides for the general case.
- **Consumer services included since P95-S2a**
  ([ADR 0264](../adr/0264-event-bus-queue-groups.md)).
  - **Before:** services with their own NATS durable consumer were excluded. A second container died
    with "consumer is already bound to a subscription".
  - **Now:** durables are queue groups, so old and new container share the consumer during the switch.
  - **Verified:** `scripts/rolling-update.sh permission-service` (canary, drain, regular instance) on the
    dev stack.
  - **Helm:** the same holds for the Kubernetes `RollingUpdate` (the surge pod joins the group) and for
    `autoscaling`.
- **No actual container automation as a live service** (the P10-S1
  boundary still stands): `rolling-update.sh` is a script executed by a
  human/CI, not a permanently running
  automation component with Docker socket access.

## Rollback

Concept 10.5 requires that a rollback remain possible "as long as the
draining of the old instance is not yet fully complete". Two
cases:

- **Canary does not become healthy in time** (step 4 fails): the
  script aborts *without* draining the old instance(s) — nothing
  was switched over, rollback is trivial (there is nothing to roll back).
- **After a drain has already completed, the new version turns out to
  be faulty**: manually reactivate the old instance
  (`POST /instances/{old_id}/activate`, since P10-S3 — the reverse of
  `/drain`) and, in turn, set the faulty new instance to draining
  (`POST /instances/{new_id}/drain`) or stop it. No automatic
  error detection (would need ongoing health/error-rate monitoring —
  monitoring territory, phase 11).

**Since Phase 59 Session 4** ([ADR 0181](../adr/0181-registry-service-instance-mutation-authorization.md)):
both `/drain` and `/activate` require `Authorization: Bearer <registry_operator_key>` —
fully locked (`403`) unless the registry-service container has
`DMS_REGISTRY_OPERATOR_KEY` set. A manual `curl` for the rollback above
needs the same header, e.g.:
`curl -X POST -H "Authorization: Bearer $REGISTRY_OPERATOR_KEY" "$REGISTRY_URL/instances/{old_id}/activate"`.

## Limitations: the persistence layer as a special case (expand/contract)

This procedure works fully uninterrupted for updates
without a database schema/storage format change. For schema changes, the
**expand/contract pattern** applies (also called "parallel change"):

- **Expand**: an additive migration step adds new structures without
  removing existing ones — old and new service versions can use the
  database in parallel during this time. **This convention already exists in
  this project, only named here for the first time**: `document-service`
  since P7-S1 (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... DEFAULT ...`),
  `registry-service`'s `status` column and `plugin-orchestration-service`'s
  `placement_method` column (both P10-S2) are real examples — no
  Alembic in this project phase (see `CONTRIBUTING.md`), `create_all`
  only creates missing *tables*, but never alters existing ones, hence the
  additional idempotent `ALTER TABLE` line in the respective `main.py`.
- **Contract**: a second, *downstream* migration step removes
  the no-longer-needed old structures, but **only after all
  instances have been updated and the old ones are fully drained**.
  ~~So far no table in this project has needed a contract step~~ — **out of
  date (found in P94-S4, [ADR 0260](../adr/0260-database-schema-migrations-scoping.md))**:
  seven contract steps exist (three `DROP COLUMN`, three `DROP CONSTRAINT`,
  one `DROP INDEX`, e.g. `workflow.federation_identity.api_key`). They ran in
  the same release that stopped using the structure, on the new version's
  first start - harmless only because the structures were already unused in
  the release before. **Rule until ADR 0260's runner exists:** a drop,
  `NOT NULL` or type change goes into a *later* release than the one that
  stopped using it, named in that session and here; new columns stay
  nullable or get a constant default; backfills are guarded (run only when
  the column was just added).
- **Measured lock cost (P94-S4, ADR 0260):** PostgreSQL takes `ACCESS
  EXCLUSIVE` before checking `IF NOT EXISTS`, so every start's no-op
  `ADD COLUMN IF NOT EXISTS` waits behind any open transaction on that
  table, and every request after it queues behind the waiting ALTER (3 s
  wait and a 4 s `SELECT` in the test). Avoid starting new instances while
  long transactions (exports, reports) run; ADR 0260 proposes a journal so
  applied steps aren't re-issued at all.
- The same principle applies analogously to storage format changes (3.6) —
  not yet encountered in practice.
- No claim is made that *every* structural change is
  possible without interruption — for a fundamental restructuring,
  a short, planned maintenance window (analogous to the maintenance mode during
  restore, 10.4) may be unavoidable in individual cases.

## API compatibility during a rollout

Since old and new service versions communicate with
other, not-yet-updated services simultaneously for the duration of the drain, every
service version within a rollout must remain backward-/forward-compatible with
the other version (no breaking API changes within a
single rollout operation) — the same basic discipline already
described for version compatibility between federated installations (7.4),
here applied at the service level within a single
installation.

## Database connection budget (P89-S4, [ADR 0254](../adr/0254-permission-cache-on-registration-and-db-connection-budget.md))

Every service process keeps a connection pool that never shrinks by itself. Each process can therefore
hold `DMS_DB_POOL_SIZE + DMS_DB_MAX_OVERFLOW` connections:
- **Default:** 2 + 3.
- **permission-service:** 20 + 10.
- **document-service and folder-service:** 12 + 8.
- **storage-service:** 4 + 6.
- **workflow-service:** 8 + 4, since P95-S1e.
- **case-service:** 4 + 4, since P95-S1e.

**Since P95-S1d** ([ADR 0263](../adr/0263-scoped-permission-cache-and-pool-sizing.md)), the hot services keep
more connections open (`pool_size`) and need less overflow. The totals and the budget are unchanged.

**Why:** SQLAlchemy closes an overflow connection as soon as it is returned to a full pool, so every
burst above the base size reopens connections, each with a SCRAM handshake (3–4 % of permission-service's
CPU in P95-S1).

**The cost:** at rest, up to 18 more idle connections in compose.

**The rule:** Postgres' `max_connections` must cover the sum over all services of maximum replicas
(`maxReplicas` with autoscaling) × pool budget, plus Keycloak's own pool and a reserve for admin
sessions.

**Current values:**

| Environment | Services at full scale | Plus | `max_connections` |
|---|---|---|---|
| docker-compose (one replica each) | about 200 | Keycloak and admin sessions | 300 |
| Helm, chart's own Postgres (`postgresql.maxConnections`) | about 400 at full autoscaling | Keycloak, whose pool can reach 100 | 500 |

**When you change replicas:**
- Raising `replicas`/`maxReplicas` or a pool size raises the requirement.
- Recompute and raise `postgresql.maxConnections`, or the setting of an external Postgres, with it.
- If the budget is exceeded, Postgres refuses new connections (`too many clients`) under load. A full
  pool only makes a request wait up to `DMS_DB_POOL_TIMEOUT_SECONDS`.
