# 0263 — Permission cache: invalidation per principal, siblings computed together; pool sizing

**Status:** accepted
**Context:** P95-S1d. P95-S1 measured the permission cache (`docs/operations/performance-ranking.md`). A
folder with 1,000 children, listed by 5 fresh principals:

| Case | median |
|---|---|
| first listing (cold) | 3,744 ms |
| second listing (warm) | 62 ms |
| the same principals after one role assignment to someone else | 3,506 ms |

Two causes:
- **Every permission change cleared everything.** That is the generation bump of ADR 0239, kept coarse
  on purpose there and in ADR 0254. A single role assignment, for anyone and anywhere, made every user's
  cache cold.
- **A batch computed its misses one by one.** `/check/batch` (ADR 0259) read the hits in one query, but
  computed each miss with its own ancestor walk. That is a few queries per tree level, plus one per role.
  1,000 siblings walked the same parent chain 1,000 times.

There was also the database pool. py-spy put 3–4 % of permission-service's CPU into HMAC, which is SCRAM
authentication of fresh connections. The pool sensors showed up to 14 connections in use at once
(sampled at scrape, so real peaks are higher). That is over the base pool of 10 (ADR 0254: 10 + 20).
SQLAlchemy closes an overflow connection as soon as it goes back to a full pool, so every burst reopens
connections. (The P95-S1 report wrongly said "2 + 3"; that is the default for the other services.)

## Decision 1 — A generation per principal, next to the global one

- **New table and column:** a table `permission.principal_cache_generation` (principal id → counter,
  missing = 0) and a column `principal_generation` on every cache row.
- **Lookups:** read both generations first, in one round trip. A row counts as a hit only while **both**
  still match.
- **Writes:** only move a row forward, comparing (global, principal) as a pair.
- **The race argument of ADR 0239 holds per dimension.** A recompute that read a principal's old
  generation writes a row that never counts as a hit once the change has bumped it. The existing race
  tests run on user assignments and so now exercise exactly this path; a new test checks a revoke racing
  a recompute while the global generation stays put.

**Which change invalidates whom:**

| Change | Invalidates |
|---|---|
| Role assignment (create/delete) to a user or service | that principal |
| Role assignment to a group | the group's members (and the group id itself, since lookups match by id) |
| Role assignment to "everyone" | everything (global) |
| Member added to or removed from a group | that member |
| Group deleted | its members |
| Role created, changed or deleted | everything (anyone can hold the role) |
| Node moved or deleted, `inherit` changed | everything |

**Groups need one lock.** Without it, a member added while an assignment to the group is uncommitted
could miss both invalidations:
- the assignment change bumps only the members it saw;
- the membership change committed earlier, and a recompute in between cached the group's old rights
  under the new member's current generation.

So every change to a group's members or to its assignments first locks the group row (`FOR UPDATE`). The
two serialize, and whichever commits second invalidates the member afterwards. A test holds an uncommitted
group assignment and shows that adding a member waits (lock timeout).

**Not subtree-scoped.** Structural changes stay global. Scoping them would need a generation per
resource and a walk over it on every lookup, and moves, deletions and `inherit` changes are rare.
Registering new nodes doesn't invalidate at all (ADR 0254).

## Decision 2 — A batch computes its misses together

`_collect_effective_roles_many` follows the same rules as the single walk: own assignments, then upward
while `inherit`, with the principal's groups and "everyone". The work is shared:
- It goes level by level: the targets, then the parents of those that inherit, and so on, with one query
  for nodes and one for matching assignments per level.
- Each node's chain of roles is computed once (memoised), so siblings share their parent's chain.
- All roles come in one query, and all rows are written in one upsert (chunked at 1,000 rows), under the
  generations read at the start of the batch.
- Unregistered resources still aren't cached (ADR 0254).

**Measured:**
- A cold batch of 300 children under a three-level chain takes at most 10 SQL statements (test).
- A test compares the batch with the single computation for every resource of a tree with `inherit=False`
  in the chain, a group, "everyone" and an own assignment on one child.

## Decision 3 — The hot services keep more connections open

Same totals as ADR 0254, a different split:
- permission-service: 20 + 10 (before 10 + 20);
- document- and folder-service: 12 + 8 (before 8 + 12).

The budget formula (pool size + overflow) is unchanged. At rest the compose stack holds up to 18 more
idle connections, against `max_connections=300`. Overflow is still there for bursts beyond that.

## Results (dev stack, after the change)

| Case | before (P95-S1) | after |
|---|---|---|
| cold listing, 1,000 children | 3,744 ms | 192 ms |
| warm listing | 62 ms | 47 ms |
| after a role assignment to someone else | 3,506 ms (cold again) | 49 ms (stays warm) |

**Connections:** 25 concurrent `/check` calls for 20 s opened about 15–26 new Postgres sessions before
and about 5 after, net of the idle baseline (about 5 per 20 s). The latency of that test didn't change
(p50 about 155–165 ms): at that concurrency permission-service is bound by its own CPU, not by the
handshakes. P95-S1e measures the full profiles.

## Consequences

- **Sensor:** `permission.cache.invalidations` has a `scope` label (`global`, `principal`). The principal
  count is the number of principals invalidated.
- **Storage:** one more `BIGINT` per cache row, and one counter row per principal ever invalidated. That
  table grows with principals, not with requests.
- **Locks:** group changes take a row lock. Concurrent edits of the same group serialize, which is
  harmless at admin rates.
- **Upgrade:**
  - The column is added with the existing ad-hoc pattern.
  - Existing rows get `principal_generation = 0`, which matches a principal without a counter row, so
    they stay valid.
  - The startup invalidation (ADR 0254) still clears everything once per start.

## Addendum (P95-S1e): workflow-service and case-service pools

The second measurement showed two more pools at their limit under the extended scenario:
- **workflow-service:** at 2 + 3 for the whole run. 75 % of its request time was pool checkout (the
  SQLAlchemy instrumentation's `connect` span measures every checkout, not only new connections).
- **case-service:** at its 5 at peaks.

They now get 8 + 4 and 4 + 4 (compose and Helm). The Helm budget at full autoscaling goes from about 370
to about 400, within `postgresql.maxConnections: 500`.

**Not yet measured in isolation:** the run after the change started on the first run's leftovers and
isn't comparable. P95-S1f measures it.
