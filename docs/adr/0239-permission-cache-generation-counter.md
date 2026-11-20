# 0239 — Permission cache: a generation counter closes the stale-row race

**Status:** accepted
**Context:** P84-S1. P83-S8 found a race in permission-service's materialized effective-permission
cache (Konzept 4.1), and the P84+ planning round confirmed it in code:
- `invalidate_cache` is a plain `DELETE FROM effective_permission_cache`.
- `get_effective_permissions` computes on a miss and then runs `INSERT … ON CONFLICT DO NOTHING`.

Under Postgres's default READ COMMITTED, two interleavings leave a stale row behind:
1. A recompute reads the assignments, a permission change commits (including the DELETE), and then the
   recompute inserts its pre-change result. Nothing conflicts, because the DELETE already ran.
2. A recompute inserts but hasn't committed. The change's DELETE cannot see that row, and the recompute
   commits after it.

In both cases the stale row answers every later lookup until some unrelated change clears the cache.
That can delay a grant, and it can also **keep a revoked permission alive**, so this is a security
defect, not only a UX glitch. The e2e suites of migration-console and process-designer had run on one
worker since P83-S8 to work around it.

## Decision

A single-row cache generation (`permission.permission_cache_generation`) and a `generation` column on
every cache row:
- `invalidate_cache` bumps the generation by an upsert, in the same transaction as the permission change,
  and then still deletes the rows to free space.
- `get_effective_permissions` reads the current generation **before** anything else. A cached row only
  counts as a hit if its generation equals the current one.
- The write is an upsert that only moves a row **forward** in generation (`ON CONFLICT … DO UPDATE …
  WHERE row.generation < excluded.generation`). It returns what this request computed instead of
  re-reading, because a re-read could return another request's row.

**Why this closes the race:**
- A recompute that started before the change carries the old generation. Whatever it writes is ignored
  afterwards, in both interleavings.
- A recompute that read the generation before the change but the assignments after it writes a correct
  result under the old generation. That costs at most one extra recompute and is never wrong.
- Readers take no locks; concurrent writers serialize on the one counter row.

**Migration:** the column is added with the existing ad-hoc pattern (`ALTER TABLE … ADD COLUMN IF NOT
EXISTS generation BIGINT NOT NULL DEFAULT 0` at startup, and the same in the test `conftest.py`). The
counter row is created by the first invalidation.

## Rejected alternatives

- **Advisory locks** (shared for a recompute, exclusive for an invalidation). The plan named this first.
  It is wrong here because permission-service's own write endpoints check the caller's capability
  through `get_effective_permissions` and then invalidate, in the same transaction (for example
  `_require_role_management` followed by role create/update). After any invalidation that check is a
  miss and would take the shared lock, and then the write wants the exclusive one. Two concurrent admin
  writes would deadlock, and Postgres would abort one of them.
- **Dropping the materialized write on read.** This gives up the cache that concept 4.1 asks for.
- **A TTL.** It only shortens the window; it does not close it.

## Consequences

- One extra single-row PK read per cache lookup.
- The coarse "clear everything" granularity is unchanged. Subtree-scoped invalidation stays the deferred
  optimization it was, and the generation counter doesn't block it.
- The two e2e suites run with parallel workers again. Their role grants moved from per-test fixtures to
  a once-per-run `globalSetup`/`globalTeardown`: grants are idempotent, so parallel per-test grants
  shared one assignment, and the first worker's teardown revoked it under the others. That was a second,
  test-only bug that `workers: 1` had also been hiding.
- Tests force both interleavings deterministically (a recompute paused after role collection while a
  grant or revoke commits, and an uncommitted insert overtaken by a commit). A counter-check with the
  generation comparison disabled makes exactly those three tests fail.
