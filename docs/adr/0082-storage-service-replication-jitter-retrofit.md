# 0082 — storage-service: full-jitter backoff for the replication retry queue

**Status:** accepted (Session 6 of 7, see Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 20 Session 6, affects `storage-service`

## Decision

`storage-service` already had the full base pattern that the other four sessions of this phase
(ADR 0078–0081) had to newly introduce: `ObjectCopy.attempts` + `max_replication_attempts` (default 5)
+ terminal status `failed_permanent` (see [ADR 0004](0004-storage-redundancy-scope.md)). Only a single
element was missing — full-jitter backoff between attempts. `POST /replication/process-pending`
previously immediately reprocessed every `status IN ("pending", "failed")` row on EVERY call, with no
wait time at all. This session retrofits `libs/dms-retry`'s `compute_backoff_seconds` (same formula as
the four other resilience spots in this phase).

1. **New field** `next_retry_at: datetime | None` on `ObjectCopy` (no new terminal status needed —
   `failed_permanent` already exists).
2. **`repository.list_pending_copies`** now additionally filters on
   `next_retry_at IS NULL OR next_retry_at <= now()` — `NULL` (new row, never failed yet) is always
   immediately due.
3. **`repository.record_copy`** gets a new `next_retry_at` parameter which — unlike `retention_until`'s
   "only apply on an explicit value" pattern — is UNCONDITIONALLY set (like `last_error`, default
   `None`): a successful or fresh write attempt needs no remaining backoff time, and any previously set
   value must disappear.
4. **`replication.process_pending`** computes, on a `"failed"` result (whether due to a missing source
   copy or a backend write error), a full-jitter-backed `next_retry_at` via a new
   `_next_retry_at(attempts)` helper function; on `"failed_permanent"` explicitly `None` (no further
   automatic attempt).
5. **No CronJob in this session** — the plan explicitly defers the actual periodic execution
   (`/replication/process-pending`, `/object-verify/{key}/all`) to **P26-S4** (Helm chart CronJob
   template), since Phase 26 does not yet exist. [ADR 0004](0004-storage-redundancy-scope.md)'s
   decision "explicit endpoint instead of in-process background task" (testability/restart semantics)
   remains unchanged — this session only changes WHEN a row is picked up again within a run, not WHO
   triggers the run.

## Rationale

- **Why no new poll loop like ADR 0079–0081**: `storage-service` deliberately has NO in-process
  background task (see ADR 0004) — the retry queue is exclusively processed by external calls to
  `POST /replication/process-pending`. Jitter only changes which rows such a call actually picks up
  (due vs. still waiting), not the calling architecture itself.
- **Why `next_retry_at` is set unconditionally instead of conditionally** (a deviation from the
  `retention_until` pattern in the same function): `retention_until` is a value set once, rarely
  changed, that MUST survive intermediate calls (fixity checks, error cases). `next_retry_at`, by
  contrast, describes the immediately upcoming next attempt of this ONE operation — it must be
  freshly and correctly determined on every call, never a stale value that a later call could
  accidentally leave in place (a successful write attempt leaving behind an old backoff value would be
  its own small bug).
- **Why `verify_all_copies` (fixity check) does NOT also get backoff**: a row marked `"failed"` by a
  fixity check (checksum mismatch) is a different failure case than a technical replication failure —
  it had already replicated successfully once and gets overwritten/retried anyway by a subsequent
  `process_pending` run; backoff for this is not part of the gap addressed in this session (originally
  named in `libs/dms-retry`'s own docstring) and would be scope creep beyond a pure jitter retrofit.

## Consequences

- **Migration of already-running installations**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS
  next_retry_at` in the lifespan on `storage.object_copy`.
- **Existing test needed adjustment**: `test_process_pending_marks_permanently_failed_after_max_attempts`
  previously called `process_pending` twice in a tight loop and expected the second call to
  immediately pick up the same row again — with real jitter (even though `attempt=0`'s backoff is only
  `uniform(0, 1)` seconds), that is no longer deterministically guaranteed. The test now explicitly
  resets `next_retry_at` into the past between the two calls (same pattern as the tick tests of the
  four other services in this phase).
- **New tests**: 113 (previously 109, +4) — `next_retry_at` is set after a failure and prevents an
  immediate re-pickup; after artificially advancing the timestamp, the row is picked up again;
  `list_pending_copies` filters out a not-yet-due row while letting a due one (different key) through
  unchanged.
- **`POST /replication/process-pending`/`GET /object-verify/{key}/all` remain without any scheduler
  until P26-S4** — the same gap already documented in `docs/services/storage-service.md` "Open Points",
  now refined to "jitter already in place, only the external triggering is still missing".
- **Verified live against the real running stack** (image rebuild + restart, migration confirmed): a
  real object was uploaded, a second `object_copy` row was created via a direct SQL insert with a
  `backend_id` not configured in the container (produces a real `KeyError` on the backend dict access
  in `process_pending`, no mocking) — the first `POST /replication/process-pending` call returns
  `attempts=1` and a `next_retry_at` set in the near future; an IMMEDIATELY following second call
  returns `processed=0` (the row is not yet due); after manually resetting `next_retry_at` into the
  past, a third call picks up the row again (`attempts=2`) — confirming the full jitter cycle 1:1
  against the real container.
