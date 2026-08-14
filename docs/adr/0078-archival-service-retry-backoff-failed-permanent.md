# 0078 — archival-service: retry/backoff, `failed_permanent`, manual restart

**Status:** accepted (Session 2 of 7, see Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 20 Session 2, affects `archival-service`

## Decision

`ArchivalTransfer`/`CaseArchivalTransfer` previously had **no retry mechanism whatsoever**: a technical
failure in any phase immediately set `status="failed"` (terminal, outside the `_ACTIVE_STATUSES` set)
— the transfer permanently disappeared from the active set, without any automatic retry and without a
manual control to restart it (already documented as an open point in
`docs/services/archival-service.md`). This session closes the gap using the backoff building block
introduced in P20-S1 (`libs/dms-retry`, [ADR 0077](0077-dms-retry-backoff-jitter-lib.md)):

1. **New fields** `attempts: int` (default 0) and `next_retry_at: datetime | None` on both models.
2. **`mark_failed` now behaves retry-aware**: a failure below `Settings.max_archival_attempts` (default
   5, same numeric value as `storage-service`'s `max_replication_attempts`) no longer immediately
   leaves the current phase — `status` stays, e.g., `locked`/`copied`, only `attempts`/`error_message`/
   `next_retry_at` (via `compute_backoff_seconds`) change. Only after exhausting
   `max_archival_attempts` does `status` transition to the new, real terminal status
   `failed_permanent`.
3. **`list_active_transfers`/`list_active_case_transfers`** additionally filter on
   `next_retry_at IS NULL OR next_retry_at <= now()` — a transfer with an open backoff window is
   skipped until it elapses, rather than failing immediately again on every poll tick.
4. **New endpoints** `POST /archival-transfers/{id}/retry` and
   `POST /case-archival-transfers/{id}/retry` (both `archival.write`-gated, same RBAC pattern since
   P19-S7): `409` if the transfer is not `failed_permanent`, otherwise `reset_for_retry` — sets
   `status="pending"`, `attempts=0`, `next_retry_at=null`, `error_message=null`.
5. **New columns on `ArchivalTransferOut`/`CaseArchivalTransferOut`** (`attempts`, `next_retry_at`) —
   groundwork for the admin UI visibility work in P20-S7.

## Rationale

- **Why `status` does NOT transition to a separate intermediate `failed` value on a retry-eligible
  failure**: the previous `failed` semantics were purely terminal (no return to the active set was
  intended). A failure that still has attempts remaining is conceptually not a new state, but the same
  intermediate step with an additional error record — leaving `status` in its current phase also means
  `list_active_transfers`'s already existing `_ACTIVE_STATUSES` filter works unchanged, no new status
  category needs to be added there.
- **Why manual restart goes to `pending` instead of reconstructing the interrupted phase**: on the
  transition to `failed_permanent`, the last reached intermediate phase is not separately retained
  (only one `status` field per row). Restarting at `pending` is safe and simple: every phase
  (`_advance_locked`/`_advance_copied`/`_advance_verified`) fetches its inputs fresh anyway (re-query
  rendition, re-trigger verification) — a full re-run is idempotent, whereas a precise resumption at the
  interrupted point would have required an additional data field needed only for this special case.
- **Why `max_archival_attempts` as its own setting instead of a literal value**: same pattern as
  `storage-service.max_replication_attempts` — an installation operator with particularly unreliable
  archive/rendering backends can raise the tolerance without changing code.
- **Why a single `mark_failed`/`reset_for_retry` function pair for BOTH transfer types**
  (`ArchivalTransfer` AND `CaseArchivalTransfer`) instead of two copies: the existing code already used
  the same `mark_failed` function for both models (pure attribute access, no `isinstance` branch, works
  thanks to identical field names on both) — this session preserves this duck-typing pattern rather
  than breaking it apart.
- **Why no new `failed` status category is documented anymore as actively reachable in
  documentation/API**: `failed` as its own, immediately terminal intermediate value is retired without
  replacement in favor of the more differentiated model (phase preserved + `attempts`/`next_retry_at`,
  or `failed_permanent` after exhaustion) — a pure behavior improvement, no data loss (`"failed"` rows
  already in the DB before this session remain readable unchanged, `list_transfers(status="failed")`
  continues to work as a pure filter, only the pipeline no longer produces this value going forward).

## Consequences

- **Migration of already-running installations**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the
  lifespan (same ad-hoc migration pattern as `document-service`'s `dehydrated_at`, P19-S11) for both new
  columns on both tables — `create_all` only creates new tables but does not alter existing ones.
- **Tests**: `archival-service` 71 (previously 56, +15: repository level — backoff behavior below/at
  exhaustion of `max_attempts`, `next_retry_at` filtering in `list_active_transfers`,
  `reset_for_retry`; pipeline level — transfer stays active on failure below `max_attempts`, reaches
  `failed_permanent` at `max_attempts=1`, same pattern for `case_pipeline`; API level — both new
  `retry` endpoints incl. `404`/`409`/`403` paths).
- **Verified live** (image rebuild + restart, migration confirmed via
  `\d archival.archival_transfer`): `404` for an unknown transfer, `failed_permanent → retry → pending`
  round trip for BOTH transfer types. Since the default poll interval (one hour) makes triggering a
  natural due-date in a live session impractical, the test rows were set directly in the DB to
  `failed_permanent` rather than letting a real verification fail live — the actual backoff/exhaustion
  logic is already extensively tested at the pipeline level against fake clients (see above).
- **`docs/services/archival-service.md`**: state machine tables (both), API table, "Open Points"
  (retry gap marked resolved) updated.
- **Not yet part of this session**: a visible admin UI surface for `failed_permanent` transfers with a
  "retry" button (P20-S7) — the backend foundation (new fields, new endpoints) is now in place.
- **Pre-existing, independent test race discovered during live verification, NOT fixed**:
  `TestClient(app)`'s lifespan starts the poll task BEFORE the `client` fixture body can replace
  `app.state.document_client` with an `AsyncMock()` — if the very first tick hits a still-real
  `DocumentClient` instance (default base URL `http://localhost:8006`), it can, in this sandbox (a
  real running document-service on the same host port), discover an ACTUAL document due for disposal
  and create a transfer in `dms_test`. Became visible because this session was the first to create
  real, immediately-due test documents on the live stack via `POST /documents/{id}/archive-request`
  (for the retry endpoint live verification) — after cleaning them up (`PUT .../archived`), the suite
  ran cleanly again. Out of session scope, pre-existing and independent of the code changes made here
  (also reproducible on the state before this session).
