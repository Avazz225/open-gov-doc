# 0241 — Service instances: replacement, scaling and expected replicas

**Status:** accepted
**Context:** P87-S1 (Phase 87).

The user named the rules (2026-11-20):
- A server that is replaced by another, for example by a restart, should be forgotten once the new one
  is registered. The same goes for scaling events.
- A service that disappears and never comes back keeps its old entry.
- An expected number of replicas per type alarms when there are too few, for example one where two or
  three are expected, and when there are too many.

## Today

`registry-service` keeps one `service_instance` row per process. `dms-registry-client` mints the
`instance_id` per process lifetime (`<service_type>-<8 hex>`), so every restart registers a new row.
- **Clean shutdown** deregisters the row (`DELETE /instances/{id}`).
- **Killed processes** leave their row behind. That covers a container that is killed, an OOM, a crashed
  pod, and a `uv run` that doesn't forward SIGTERM.
- **Unreachable** means the last heartbeat is older than `heartbeat_timeout_seconds` (15 s). It is
  decided on read and only affects routing.
- **Cleanup** is a loop that deletes every row unreachable for 7 days, whatever its type
  (`unreachable_cleanup_after_seconds`, polled hourly).

The result is the "Leichen" the user saw in the registry overview: a week of dead rows after every
restart of the dev stack. It is also the opposite of the second rule, because a type that vanished for
good loses its rows after a week, too.

fleet-management doesn't register anything itself. An operator adds each `ManagedInstallation` with
its own key and removes it again, and the status is fetched live. Its debris comes from e2e runs that
didn't clean up.

## Decision 1 — One rule per type: keep only as many dead entries as are missing

The plan listed two rules, "one new instance supersedes one unreachable old one, oldest first" and
"on scale-down, surplus unreachable entries go". They collapse into one rule, evaluated per
`service_type`:

> Let **R** be the reachable, `active` instances of a type, and **min** its expected minimum (Decision 3;
> no expectation counts as min = 1). If R ≥ 1, the type keeps at most `max(0, min − R)` unreachable
> entries: the most recently seen ones, which are the likeliest to be the ones still missing. Every
> other unreachable entry is **forgotten**. If R = 0, nothing is forgotten.

How the user's cases come out:

| Case | Before | After the rule |
|---|---|---|
| Restart (1 → new 1) | 1 dead, 1 live | dead one forgotten |
| Rolling restart of 3 | 3 dead, 3 live | all 3 forgotten |
| Scale down 3 → 1 (no expectation, or min 1) | 2 dead, 1 live | both forgotten |
| One of 3 crashed, not replaced (min 3) | 1 dead, 2 live | kept, and the type is under-replicated (Decision 4) |
| Scale up 1 → 3 | — | nothing to do |
| Type vanished (R = 0) | n dead | all kept, flagged "vanished" |

- **Grace period.** An entry is only forgotten once it has been unreachable for longer than
  `forget_after_seconds` (default 60 s). A paused process (GC, a slow node) that resumes heartbeating in
  that window keeps its row.
- **Late heartbeat after forgetting.** A heartbeat that arrives after the entry was forgotten gets the
  404 that `dms-registry-client` already answers by re-registering. Forgetting can never strand a live
  instance.
- **`draining` instances** count neither as R nor as dead. They are leaving on purpose, and
  `scripts/rolling-update.sh` deregisters them when the drain completes.
- **When it runs:** in the existing poll loop, now every 30 s instead of hourly. Running it on
  `register` as well would only shorten the window, and the loop also has to cover scale-downs where
  nothing registers.
- **Effect on existing debris:** on upgrade, the first tick forgets the existing pile for every type
  that has a live instance. Nothing needs migrating.

## Decision 2 — The 7-day blanket cleanup goes; vanished types stay until an admin forgets them

A backstop that deletes vanished types after 7 days contradicts the second rule. Decision 1 already
covers everything that has a successor. So:
- `unreachable_cleanup_after_seconds` and its deletion loop are removed.
- A vanished type keeps its entries indefinitely and is flagged `vanished` in the overview.
- **Admins forget entries by hand:** `POST /instances/{id}/forget`, gated by `admin.monitoring`
  (`domain-admin-monitoring`), is for a service that was decommissioned for good. Today
  `DELETE /instances/{id}` is self-deregistration and stays gated by the instance's own principal.
- **Every forget publishes `registry.instance.forgotten`.** Its payload carries `service_type` and a
  `reason`: `replaced` (Decision 1) or `admin`. The actor is `system:registry` or the admin's principal.
  audit-service already consumes `registry.>`, so the audit trail says why an entry went, which the plan
  asked for. The old cleanup published `registry.instance.deregistered`, the same event as a clean
  shutdown, which hid the difference.

## Decision 3 — Expected replicas: a range per type, from Helm by default, overridable by admins

The user's own example is "2 – 3 expected", and Helm's HPA already describes each type as
`minReplicas..maxReplicas`. So the expectation is a **range** `min..max` (`max` optional, meaning "no
upper limit"), not a single number.

**Sources, highest first:**
1. **Admin override.** A new table `registry.expected_replicas` (`service_type` PK, `min_replicas`,
   `max_replicas` nullable, `updated_by`, `updated_at`).
   - Edited through `GET /replicas`, `PUT /replicas/{service_type}` and `DELETE /replicas/{service_type}`,
     which restores the default.
   - Writes are gated by `admin.monitoring`.
2. **Helm default.** The chart renders `DMS_EXPECTED_REPLICAS_DEFAULTS` (JSON) into registry-service's
   Deployment, built from every enabled service:
   - `autoscaling.enabled` gives `minReplicas..maxReplicas`;
   - otherwise `replicas..replicas`.
3. **None.** No alarms for the type. It still counts as min = 1 for Decision 1, and as `vanished` in the
   overview when R = 0.

**Consequences of the source order:**
- docker compose sets no defaults. The dev stack alarms on nothing until an admin sets an expectation,
  so stopping a service for a test run (`run-tests.sh` stops auth-service for 15 minutes) doesn't send
  mail.
- The expectation isn't part of config packages (config-service, 7.3). It describes one installation's
  topology, not portable configuration.

## Decision 4 — Alarm states, debounced, persisted, one event per transition

**States per type:**

| State | Condition |
|---|---|
| `ok` | min ≤ R ≤ max |
| `under` | 1 ≤ R < min |
| `vanished` | R = 0 |
| `over` | R > max |
| `unmonitored` | no expectation |

**Debounce.** A state change counts only after it has held for `replica_alarm_after_seconds` (default
120 s). This prevents an alarm for each restart and each rolling update, where `active` instances briefly
exceed `max` until the old ones drain.

**Persisted state.** The alarm state is stored in `registry.replica_alarm_state` (`service_type`, `state`,
`since`, `pending_state`, `pending_since`). It survives a registry restart, so the restart doesn't re-fire
open alarms. The transition is a conditional `UPDATE … WHERE state = <old>`: with several registry
replicas, only one wins and publishes.

**Events** on the `registry` stream, one per committed transition:
- `registry.replicas.under` covers `under` and `vanished`, with `reachable: 0` for the latter;
- `registry.replicas.over`;
- `registry.replicas.restored`, back to `ok`.

Each event carries `service_type`, `reachable`, `min` and `max`.

**Metrics** (sensor catalog, labelled by `service_type`):
- `registry.replicas.reachable`;
- `registry.replicas.expected_min`, `registry.replicas.expected_max` (absent when unset);
- `registry.replicas.state` (−1 under/vanished, 0 ok, 1 over).

Prometheus or Grafana can alert on these independently of notification-service.

**Notifications.**
- notification-service subscribes to the three subjects. It mails a new `operations_admin_email`
  (default `ops-admin@dms.local`, same shape as `storage_admin_email`) and writes an in-app notification
  for that recipient.
- There are three templates: under, over and restored. `restored` closes the loop, so a mailbox shows
  the alarm ended without anyone checking the UI.

## Decision 5 — fleet-management shows replica health; it doesn't apply the rules to installations

The rules are about processes that replace each other. Installations don't: an operator adds and
removes them, and one installation never succeeds another. So fleet-management doesn't prune or
supersede.
- **Replica summary per installation.** The installation status gains a summary from the
  installation's own `GET /replicas`: how many types are `under`, `vanished` and `over`. An operator sees
  "Kiel: 2 types under-replicated" without logging into Kiel's admin-ui.
- **Unreachable installations** stay listed, as they are today, until an operator removes them.
- **Test debris.** The fleet entries the user saw pile up come from e2e runs. P87-S3 makes the suites
  that create installations or instances clean up after themselves.

## Consequences

**P87-S2: registry-service.**
- Decision 1's rule replaces the cleanup loop:
  - `forget_after_seconds`;
  - the loop now runs every 30 s;
  - `registry.instance.forgotten` with a reason.
- `POST /instances/{id}/forget`.
- Expectations:
  - the `expected_replicas` table and API;
  - Helm `DMS_EXPECTED_REPLICAS_DEFAULTS`, plus `helm template` lint.
- Alarms:
  - the alarm state table with debounce and the conditional transition;
  - the three events;
  - the four labelled sensors.
- Tests: restart, rolling restart, scale up, scale down, a crashed replica under an expectation, a
  vanished type, over-provisioning, debounce, a late heartbeat after forgetting, and two concurrent
  transition attempts.

**P87-S3: admin-ui, notification-service, fleet-management, hygiene.**
- **admin-ui registry view grouped by type:**
  - expected range and its source, reachable, draining and unreachable counts;
  - state with `since`, and the expectation editor;
  - "Vergessen" on vanished entries;
  - forgotten entries in the audit view.
- **notification-service:** the subscription, `operations_admin_email` and the three templates.
- **fleet-management:** the per-installation replica summary.
- **Test hygiene:** registry, fleet and e2e suites remove what they register.

**Docs:** `docs/services/registry-service.md`, the notification and fleet docs, and the Helm values
reference.

## Implementation status

- **P87-S2 (done):** everything listed for P87-S2 above.
  - Two details beyond the list:
    - `dms-metrics-client` gauges accept `labelnames`, plus `GuardedGauge.clear()`;
    - expectation changes publish `registry.replicas.expectation_set`/`_reset` with the admin as actor.
  - **Transitions between alarm states:** a change from one alarm state to another (`under` → `vanished`,
    `over` → `under`) publishes the event of the new state. `unmonitored` → `ok` is silent.
  - **Live on the dev stack:** 287 registry rows (257 dead) shrank to 30 live ones on the first ticks
    after the upgrade, and the old registry-service instance itself went once its grace had passed.
