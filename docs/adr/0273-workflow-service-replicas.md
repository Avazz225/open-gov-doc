# 0273 — workflow-service with several replicas

**Status:** accepted (P101-S1)
**Context:** ADR 0020 documented the gap. With more than one replica, every replica polls the same
instances, so a due boundary timer fires and is escalated once per replica. workflow-service therefore
ran with one replica, the last stateful single point in the request path. This session closes the
gap and checks the rest of the service for the same kind of problem.

## Decision 1 — One replica per tick, by a transaction-scoped advisory lock

Each poll tick takes `pg_try_advisory_xact_lock(4, <tick>)` in the transaction that does the tick's
work. This is the pattern of monitoring-service's alert tick (ADR 0245), here in the two-integer form
next to the existing namespaces 1–3 (definition and DMN locks, ADR 0096).
- **The holder** does the tick and commits; the lock ends with its transaction.
- **The others** get `false` and skip that tick.
- **No leader election, no lease:** a replica that stops, or dies mid-tick, releases the lock with its
  connection. The next tick of any replica takes over, so at most one poll interval is lost.
- **Advisory locks are per database,** so test runs against `dms_test` don't block the stack.

Ticks under the lock:
- **SLA timers** (`run_sla_tick`, key 1). Optimistic locking (ADR 0185) alone wasn't enough. Both
  replicas let the engine fire the timer, with its side effects (a connector call, for example), and
  only then did the second commit fail. That failure also rolled back its whole tick, so the replicas
  kept discarding each other's work.
- **Claim abandonment** (`run_claim_expiry_tick`, key 2). There was no protection at all: both
  replicas read a due claim and notified its holder twice.

## Decision 2 — Startup work one replica at a time

Replicas that start together wait for each other (`pg_advisory_xact_lock(4, 3)`) on:
- **Seeding the „Umlauf" template:** both saw no template and created versions 1 and 2. The second
  replica now waits and finds it.
- **Refreshing the task responsibilities:** both updated the same rows in no defined order.

The ready-task index backfill already shares its batches through `FOR UPDATE SKIP LOCKED` (P95-S1b) and
stays as it is.

## Decision 3 — Caches that a write elsewhere can make stale

The business-calendar cache (P14-S5) was refreshed only in the replica that wrote the calendar. Another
replica then computed `business_days(…)` deadlines with the old holidays, in the SLA tick and when a
timer is reached on the request path. Every replica now reloads it at the start of each SLA tick,
whether or not it gets the lock (one small query every 30 s). After a calendar change, other replicas
are behind by at most one poll interval.

The other in-process state is unaffected:
- the licence status is cached per process with a TTL;
- the sensor configuration is polled by each replica;
- the approval consumer is a shared durable, so each event reaches one replica.

## Decision 4 — Two replicas by default in Helm

- `replicas: 2` with a PDB (`minAvailable: 1`); autoscaling (if switched on) runs from 2 to 3.
- The connection budget already counts maxReplicas 3 for this service (ADR 0254/0263).
- The registry's replica expectation follows from the values (`min = max = 2`), so "replicas missing"
  alarms if one is gone.
- Compose stays at one replica (development).

## Consequences

- **Exactly once per tick, not per timer across a crash:** a replica that dies after firing a timer but
  before committing leaves the instance unchanged. The next tick fires it again, which is the same
  "at least once" ADR 0020 already had for one replica.
- **Live, with two replicas** (an override with a second container, 5-s tick):
  - a 25-s timer escalated once, from replica 1; replica 2's counter stayed at 0;
  - with replica 1 stopped right after an instance started, replica 2 escalated that instance's timer
    once and on time;
  - the template exists once.
- **Tests:** 5 in `test_replicas.py`:
  - the lock;
  - a due timer and an abandoned claim with two replicas, the claim with a forced interleaving;
  - the template seeding;
  - the calendar refresh.

  With the lock switched off, all five fail.
- **Not covered here:** other services' loops (e.g. mail-connector's mailbox poll, whose duplicates the
  database's unique key catches) keep their own single-replica notes.
