# 0079 — notification-service: retry/backoff, `failed_permanent`, asynchronous retry poll loop

**Status:** accepted (Session 3 of 7, see Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 20 Session 3, affects `notification-service`

## Decision

`Notification` previously had **no retry mechanism whatsoever**: a failed `email`/`webhook` delivery
attempt immediately set `status="failed"` (terminal) — already documented as an open point in
`docs/services/notification-service.md`. This session closes the gap using the backoff building block
introduced in P20-S1 (`libs/dms-retry`), adapted to this service's peculiarity: **delivery happens
synchronously inline in the NATS handler or in `POST /notifications`** (no multi-phase process like
`archival-service`, ADR 0078) — a retry attempt must not block this path.

1. **New fields** `attempts: int` (default 0) and `next_retry_at: datetime | None` on `Notification`.
2. **`attempt_delivery`** (formerly `create_and_send`'s inline try/except, now its own, reusable
   function): on success `status="sent"`; on a `DeliveryError` below `Settings.max_notification_attempts`
   (default 5), `status` stays `"failed"` (retry-eligible) with `next_retry_at` set via
   `compute_backoff_seconds`. Only upon exhaustion does `status` transition to the new terminal status
   `failed_permanent`. `in_app` has no real delivery step and is therefore never retry-eligible —
   always immediately `"sent"`.
3. **New, standalone `_notification_retry_poll_loop`** (main.py, interval
   `notification_retry_poll_interval_seconds`, default 60s — considerably shorter than, e.g.,
   `archival_poll_interval_seconds`, since an email/webhook delivery typically makes sense to retry
   within seconds to minutes, not hours): picks up due `"failed"` notifications via `list_due_for_retry`,
   calls `attempt_delivery` again, and publishes the result (`notification.sent`/`.failed`) — only the
   FIRST attempt stays synchronous in the handler, only the RETRY runs asynchronously. The actual tick
   logic is factored into `_run_retry_tick` (independently testable, same pattern as
   `archival-service`'s `run_active_transfers_tick`).
4. **New endpoint** `POST /notifications/{id}/retry`: `409` if `status != "failed_permanent"`, otherwise
   `repository.retry_now` — resets `attempts=0`/`error=None` and **immediately** makes a new synchronous
   delivery attempt (see rationale below).
5. **`NotificationOut`** extended with `attempts`/`next_retry_at`, `status` literal extended with
   `"failed_permanent"`.

## Rationale

- **Why a new, standalone poll loop instead of a retry-aware `create_and_send` that waits/retries
  itself**: `create_and_send` runs synchronously in the NATS consumer handler or in the
  `POST /notifications` request-response cycle — a backoff wait there would either block the NATS
  consumer (delaying EVERY subsequent message on the same durable consumer) or hold the HTTP request
  open unreasonably long. A separate, asynchronous poll loop (exactly as intended by the roadmap plan:
  "a new, dedicated retry poll loop is added ... instead of blocking the NATS handler itself")
  fully decouples the retry from the still-fast first synchronous attempt.
- **Why `attempt_delivery` as its own public function instead of staying inline in `create_and_send`**:
  it is now called from three places (first attempt, poll-loop retry, manual retry) — the same
  consideration as `archival-service`'s `mark_failed`.
- **Why `status` stays `"failed"` on a retry-eligible failure instead of a new intermediate value**:
  unlike `archival-service` (multiple phases: `pending`/`locked`/`copied`/...), there is only a single
  delivery step here — `"failed"` already correctly describes "this one step is currently not
  succeeding"; whether it is retry-eligible or not is expressed via `attempts`/`next_retry_at`, not
  via another status category. Existing tests/consumers checking `status == "failed"` after a SINGLE
  failure therefore remain valid unchanged (no breaking change to the status vocabulary for the
  already-known case).
- **Why the manual retry endpoint delivers IMMEDIATELY and synchronously instead of just resetting to
  `pending` and waiting for the next poll tick** (a deliberate difference from `archival-service`'s
  `reset_for_retry`): a notification is a single, lightweight delivery step (one SMTP/HTTP request),
  not a multi-phase process running several seconds/minutes — an admin clicking "retry" expects an
  immediate result in the response, not a wait for the next poll tick (up to 60s default interval).
- **Why NO RBAC gate on the new retry endpoint**: `notification-service` currently has no
  `permission-service` integration whatsoever (no `dms-permission-client`, no RBAC check on any
  existing endpoint other than the recipient existence check) — a complete RBAC introduction just for
  this one endpoint would be scope expansion well beyond "add retry/backoff" and was not part of this
  roadmap session (Phase 19 was explicitly the RBAC phase). Remains deliberately ungated like every
  other existing `GET` endpoint of this service.
- **Why `in_app` is never retry-eligible**: there is no real delivery step that could fail (pure DB
  persistence) — `attempt_delivery`'s `try` block only covers `email`/`webhook` with a `DeliveryError`
  path, `in_app` falls through to `status="sent"`, exactly as before this session.

## Consequences

- **Migration of already-running installations**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the
  lifespan (same ad-hoc migration pattern as `archival-service`, `document-service`) for both new
  columns.
- **Tests**: `notification-service` 40 (previously 30, +10: repository level — backoff behavior
  below/at exhaustion of `max_notification_attempts`, `list_due_for_retry` filtering by status AND
  backoff window, `retry_now`; API level — new `/retry` endpoint incl. `404`/`409`/successful restart;
  new `test_main.py` — `_run_retry_tick` picks up a due notification, skips one not yet due).
- **New `session_factory` fixture in `conftest.py`** (previously missing, unlike `archival-service`) —
  needed for the new poll-tick tests.
- No new event (`notification.sent`/`.failed` remains sufficient — a retry that ultimately succeeds
  publishes `notification.sent` like a first attempt; a `failed_permanent` transition still publishes
  `notification.failed`, no new, third event variant needed).
- **Verified live against the real running stack** (image rebuild + restart, migration confirmed): a
  webhook to an unreachable URL reaches `failed_permanent` after `max_notification_attempts` attempts;
  `POST /notifications/{id}/retry` returns `409` for a still retry-eligible notification and delivers
  immediately again for a `failed_permanent` notification (correctly stays `failed_permanent` with a
  still-unreachable target); the poll loop picks up an artificially due notification.
