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
- **Consumer services excluded**: services with their own
  exclusive NATS durable consumer (the same list as
  `scripts/run-tests.sh`'s `CONSUMER_SERVICES`) cannot be updated with this
  script — a second, simultaneously running container would fail to subscribe
  with "consumer is already bound to a
  subscription". A true parallel operation for
  consumer services would need NATS queue groups instead of exclusive
  durable names (an architecture change to the event bus client, not part
  of this session).
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
  instances have been updated and the old ones are fully drained**. So far
  no table in this project has needed a contract step
  (every additive column is still in use to date) — should a column
  genuinely become superfluous in the future, removing it belongs as its own,
  named step in the respective session, not in the same step
  as the expansion.
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
