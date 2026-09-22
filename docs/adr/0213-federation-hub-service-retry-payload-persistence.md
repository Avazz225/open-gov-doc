# 0213 — federation-hub-service: persist handover retry payloads instead of process memory

**Status:** accepted
**Context:** P73-S3 (Phase 73, ninth gap-analysis round). Since ADR 0081/Phase 40 Session 3, an encrypted
handover payload that still needs retrying (forward leg: `app.state.pending_handover_payloads`; return
leg: `app.state.pending_handover_result_payloads`) lives only in two in-process dicts, keyed by
`handover_id`. This is a real, if narrow, data-loss risk: a hub restart during an open retry window
silently loses the ability to automatically redeliver — `_run_retry_tick`/`_run_result_retry_tick` find
the `Handover` row via `list_due_for_retry` but `cached is None`, log
`federation_handover_retry_payload_lost`, and give up (`status="delivery_failed"`), even though the
sending installation did everything right. `models.Handover`'s own docstring already documents this as a
known, deliberate limitation, not a silent gap.

## Decision

**New table `federation.handover_retry_payload`**, composite PK `(handover_id, leg)`:
- `handover_id VARCHAR(36)` — FK to `handover.id`, **`ON DELETE CASCADE`** (so
  `repository.purge_stale_handovers`'s existing plain bulk `DELETE` on terminal `Handover` rows continues
  to need zero Python-side change — cascade handles orphaned payload rows automatically, including the
  `delivery_failed`/`result_delivery_failed` case where a payload deliberately stays cached past
  exhaustion for a manual `POST .../retry`).
- `leg VARCHAR(16)` — `"forward"` or `"result"`, distinguishing the two independent retry legs
  (`create_handover`'s delivery to `to_installation_id` vs. `submit_handover_result`'s delivery back to
  `from_installation_id`) on the same `handover_id`.
- `payload JSON` — the same small dict (`delivery_body`/`result_body`) currently held in the in-process
  cache, stored as-is (same `JSON` column type `models.py` already uses for
  `supported_process_types`/`supported_document_types`).
- `created_at DATETIME(timezone=True)`.

**Every read/write/pop against the two dicts becomes a `repository` call against this table instead**:
`create_handover`/`submit_handover_result` (write on failed first attempt), `_retry_forward_delivery`/
`_retry_result_delivery` (read for a manual `POST .../retry`, delete on success), `_run_retry_tick`/
`_run_result_retry_tick` (read for the poll loop, delete on success). `app.state.pending_handover_payloads`/
`..._result_payloads` and the dict parameters threaded through `_handover_retry_poll_loop`/
`_run_retry_tick`/`_run_result_retry_tick`/`build_samplers` are removed outright — the `session`/
`session_factory` already available at every one of those call sites is now sufficient on its own, no
dict needs threading through in parallel.

**Sensors (`metrics.build_samplers`, Phase 40 Session 4) switch from `len(dict)` to a `SELECT COUNT(*) ...
WHERE leg = 'forward'|'result'` query**, via a new `repository.count_pending_payloads(session, leg=...)`.
This isn't just a mechanical swap: that session's own docstring explicitly declined to ALSO expose the
DB's `pending_retry`/`result_pending_retry` row counts as a second pair of sensors specifically *because*
they "can diverge from the cache size after a restart" — once the cache itself moves into the DB, that
divergence risk disappears entirely (there is only one source of truth left), so the sensor becomes more
accurate as a side effect of this fix, not a separate decision.

## Rationale

- **Does not weaken ADR 0028's end-to-end encryption model.** The hub never decrypts `encrypted_payload`/
  `encrypted_result` — persisting the retry cache stores the exact same opaque ciphertext bytes the hub
  already held in process memory, just on disk instead of in RAM. Durability and confidentiality are
  independent properties here: this change improves the former without touching the latter. `models.Handover`
  itself still gets no payload column, preserving that table's own "metadata only" design intent
  unchanged — the new table is a distinct, purpose-scoped retry cache, not a reversal of ADR 0028's
  "Handover records only metadata" decision for the `Handover` table itself.
- **Why a separate table, not a payload column on `Handover`**: `Handover` is a permanent audit record
  (never deleted except by the terminal-status cleanup poll); the retry payload is transient by
  definition — it exists only between a failed attempt and the next successful one, then is deleted. Two
  different lifecycles deserve two different tables, and keeping `Handover` payload-free preserves the
  "no field for the payload" invariant `models.Handover`'s own docstring states, rather than quietly
  contradicting it a few lines below.
- **Why `ON DELETE CASCADE` rather than a Python-side delete-before-delete**: `purge_stale_handovers` is a
  single bulk `DELETE` with no ORM object loaded per row (`session.execute(delete(Handover).where(...))`)
  — there is no Python-side `Handover` instance to cascade from manually. A DB-level `ON DELETE CASCADE`
  is the only way to keep that function's own "plain bulk delete, no per-row event" design (its own
  docstring's explicit reasoning) while still not leaking orphaned payload rows.
- **Bonus, not the primary goal**: after this change, an automatic restart-time recovery becomes possible
  for the first time — the poll loop's `cached is None` branch (today's `federation_handover_retry_payload_lost`
  give-up path) becomes effectively dead code for the "hub was merely restarted" case, since the payload
  survives a restart in the DB. It remains reachable only for a genuinely corrupted/missing row (should not
  happen in practice) — kept as a defensive fallback, not removed, same "must not silently delete evidence
  of it if it does" caution `purge_stale_handovers`'s own docstring already applies elsewhere in this file.
- **Consistent with every other retry/backoff mechanism in this project**: `archival-service`,
  `storage-service`, `rendering-service`, and `notification-service` (ADR 0079) all persist their own retry
  state durably — `federation-hub-service` was the one deliberately-documented exception, and the plan's
  own framing ("closes a real, if narrow, data-loss risk") treats that exception as worth closing now that
  it's this round's turn, not as a permanent design choice.

## Consequences

- `services/federation-hub-service/src/federation_hub_service/models.py`: new `HandoverRetryPayload` model.
- `services/federation-hub-service/src/federation_hub_service/repository.py`: new
  `save_pending_payload(session, handover_id, *, leg, payload)`,
  `get_pending_payload(session, handover_id, *, leg) -> dict | None`,
  `delete_pending_payload(session, handover_id, *, leg)`,
  `count_pending_payloads(session, *, leg) -> int`.
- `services/federation-hub-service/src/federation_hub_service/main.py`: `create_handover`,
  `submit_handover_result`, `_retry_forward_delivery`, `_retry_result_delivery`, `_run_retry_tick`,
  `_run_result_retry_tick`, `_handover_retry_poll_loop`, and `lifespan` all updated to go through the new
  repository functions instead of `app.state.pending_handover_payloads`/`..._result_payloads`, which are
  removed.
- `services/federation-hub-service/src/federation_hub_service/metrics.py`: `build_samplers` takes a
  `session_factory` instead of the two dicts; both gauge functions become real (cheap) DB count queries.
- `docs/services/federation-hub-service.md`: "Open Points" bullet about restart data loss during an open
  retry window struck/closed; sensor description note about cache/DB divergence corrected (no longer
  applicable — see Decision above).
- New tests proving payload survival: a handover's failed-delivery payload row exists after
  `create_handover`/`submit_handover_result`; a fresh `session_factory` (simulating "hub restarted, no
  more in-process state") can still retry it successfully via both `POST .../retry` and a poll tick;
  `purge_stale_handovers` on a terminal handover with a still-cached payload leaves no orphaned
  `handover_retry_payload` row (cascade proof).
