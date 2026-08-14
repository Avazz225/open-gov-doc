# 0081 — federation-hub-service: handover delivery retry/backoff, `pending_retry`

**Status:** accepted (Session 5 of 7, see Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 20 Session 5, affects `federation-hub-service`

## Decision

`POST /handovers` previously delivered the end-to-end encrypted payload fully **synchronously within the
request** to `to_installation_id`'s callback URL — a single failure (network error, non-2xx response)
immediately set `status="delivery_failed"` (terminal), with no retry whatsoever. This session carries
the pattern known from `notification-service` (ADR 0079) and `ocr-service`/`rendering-service`
(ADR 0080) over to the `Handover` initial delivery — **only** for `POST /handovers`, NOT for the
separate, symmetric `POST /handovers/{id}/result` return path (out of scope for this session).

1. **New fields** `attempts: int` (default 0) and `next_retry_at: datetime | None` on `Handover`.
   New intermediate status **`pending_retry`** (status flow now: `"pending"` → `"delivered"` |
   `"pending_retry"` → ... → `"delivery_failed"` → `"completed"` | `"result_delivery_failed"`) —
   deliberately a new name for the intermediate status, but `"delivery_failed"` is retained as the name
   of the **terminal** (exhausted) state, instead of introducing a new `..._permanent` name as in
   ADR 0078–0080 (this deviation was explicitly specified by the plan).
2. **Retry-aware delivery bookkeeping**: `repository.mark_handover_delivered` gets a new required
   parameter `max_attempts` — on success, unchanged `"delivered"`; on failure below `max_attempts`
   (default 5, `settings.max_handover_delivery_attempts`), `status="pending_retry"` with `next_retry_at`
   set via `compute_backoff_seconds`; only upon exhaustion `status="delivery_failed"`. New
   `repository.list_due_for_retry`/`repository.reset_for_retry` (identical pattern to ADR 0079/0080).
3. **New, standalone retry poll loop** (`_handover_retry_poll_loop`, interval 60s like
   `notification-service`) — the first delivery attempt stays synchronous in `POST /handovers`, only the
   RETRY runs asynchronously.
4. **New endpoint** `POST /handovers/{id}/retry` — `409` unless `delivery_failed`, otherwise
   `repository.reset_for_retry` followed by an immediate synchronous retry attempt (same rationale as
   ADR 0079/0080: a single HTTP delivery step, not a multi-phase process). Deliberately **without** an
   RBAC gate — `federation-hub-service`, like `notification-service` (before ADR 0079), has no
   `permission-service` integration; adding one would be scope creep beyond a pure resilience session.
5. **The architectural core conflict of this session — "payload never persisted" vs. "payload needed for
   later retry"**: `Handover` deliberately **never** stores the end-to-end encrypted payload itself
   (7.4, ADR 0028 "self-loopback" — the hub only logs mediation **metadata**). A retry poll tick,
   however, needs the payload to redeliver it. Solution: a purely **volatile in-process memory cache**
   `app.state.pending_handover_payloads: dict[str, dict]` (keyed by `handover_id`), populated only as
   long as a handover is actually `pending_retry`, cleared on success or exhaustion. **No new field on
   `Handover` itself** — the architectural principle "no payload is ever persisted" remains fully
   intact, not silently circumvented.

## Rationale

- **Why the notification-service pattern (ADR 0079) instead of the archival-service pattern (ADR 0078)**:
  `POST /handovers` processes synchronously within the HTTP request, no multi-phase state machine — a
  backoff wait directly in the request would block the caller, exactly the same consideration as with
  `notification-service`/`ocr-service`/`rendering-service`.
- **Why an in-memory cache instead of a new persisted payload field**: the alternative (storing the
  payload in the DB after all, only for the duration of the retry window) would undermine the
  privacy/audit principle explicitly justified in ADR 0028 — exactly the record the hub, per the
  concept, must NEVER see would then reside (even if temporarily) in its database. A loss on restart is
  the honest, documented consequence of this principle, not an implementation oversight.
- **Why the manual retry response returns `409` instead of a silent no-op when the cache entry is
  missing**: the caller (a hub operator or an admin UI) must learn that an automatic retry is NOT
  possible here, so they can prompt the sending installation to initiate a new handover with a new
  `handover_id` — `repository.create_handover` creates rows without an existence check (plain
  `session.add`), so a retry with the same `handover_id` after cache loss is not an option anyway
  (`IntegrityError` on the primary key).
- **Why `"delivery_failed"` is kept as the name of the terminal state** (a deviation from ADR
  0078–0080's `..._permanent` convention): explicit plan requirement for this session — the new
  intermediate status `"pending_retry"` is the actual new vocabulary addition; `"delivery_failed"`
  already existed as a status value and is now just reached later in time (only after exhaustion
  instead of immediately).
- **Why `POST /handovers/{id}/result` (the "result" return path) is NOT part of this session**:
  structurally symmetric, but architecturally independent (opposite direction, a different installation
  calls back) — the plan explicitly scopes this session to the initial delivery; equal treatment of the
  result return path would be a sensible follow-up session, not part of this one.

## Consequences

- **Migration of already-running installations**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the
  lifespan for `attempts`/`next_retry_at` on `federation.handover`.
- **New, documented operational limitation**: a hub restart during an open retry window
  (`pending_retry`) irrecoverably loses the cached payload — both the automatic poll tick and a manual
  `POST .../retry` then mark the affected handover as `delivery_failed`, instead of waiting endlessly
  for a payload that will never arrive or responding with `409`. Documented in `models.Handover`'s
  docstring, in the lifespan comment, and in `docs/services/federation-hub-service.md` "Open Points"
  (replaces the previous, now-resolved "no retry" line with this more precise, remaining limitation).
- **Real design bug found and fixed in self-review, BEFORE live verification**: the original version
  removed the cache entry on EVERY transition to `delivery_failed` (exhaustion), not only on success —
  exactly the moment at which `POST .../retry` is first permitted (its 409 gate requires
  `status == "delivery_failed"`). In practice, the cache would almost always already have been empty
  by the time an admin was actually allowed to call the retry endpoint — the "manual restart" path
  would have almost never worked, without any test catching it (the original tests injected the cache
  entry manually instead of checking the real behavior). Fixed: the cache entry is now removed only on
  actually SUCCESSFUL delivery (uniformly in `create_handover`, `retry_handover`, and
  `_run_retry_tick`), so it survives exhaustion as well, until either a retry succeeds or the hub
  restarts. Similar finding category to the `reset_for_retry` bug in ADR 0080, but here caught during
  the author's own design review before live verification rather than after.
- **Tests**: 43 (previously 35, +8: `pending_retry` behavior on an unreachable target instead of
  immediate `delivery_failed`, exhaustion reaching `delivery_failed` WITH the payload still cached,
  regression test for the cache bug described above via the poll loop, `/retry` endpoint status gate,
  successful manual retry, `409` on a simulated lost cache entry, new `test_main.py` with four
  `_run_retry_tick` tests incl. exhaustion and cache loss). New `session_factory` fixture in
  `conftest.py` (previously missing, same gap as the three prior services in this phase).
- **Verified live against the real running stack** (image rebuild + restart of `federation-hub-service`,
  migration confirmed, **twice** due to the cache bugfix described above): `POST /handovers` against a
  deliberately unreachable `callback_base_url` returns `pending_retry` with `attempts=1`; the real,
  running `_handover_retry_poll_loop` (60s interval) autonomously picks up the due handover and
  increments `attempts` on every tick — tracked over **real ~5 minutes of wait time** (no accelerated
  setting) to actual exhaustion: `attempts` rises 1→2→3→4→5, `status` transitions exactly upon reaching
  `max_handover_delivery_attempts` from `pending_retry` to `delivery_failed`; the payload cache entry is
  demonstrably retained throughout (not the original bug); `POST .../retry` against the still
  unreachable target live-confirms the `reset_for_retry` path (`attempts` starts again at 0, lands at 1
  after the one synchronous attempt, NOT at 6 — same bug type as in ADR 0080, here correct from the
  start). The restart boundary was verified with a REAL `docker compose restart federation-hub-service`
  (not simulated clearing): two handovers still `pending_retry` at that point were confirmed by the log
  as marked `federation_handover_retry_payload_lost` and correctly landed at `delivery_failed`; a
  subsequent `POST .../retry` on one of them returned `409` with the documented message that the
  sending installation must submit a new handover.
