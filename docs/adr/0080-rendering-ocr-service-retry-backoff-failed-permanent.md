# 0080 — rendering-service & ocr-service: retry/backoff, `failed_permanent`

**Status:** accepted (Session 4 of 7, see Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 20 Session 4, affects `rendering-service` and `ocr-service`

## Decision

`Rendition`/`OcrResult` previously had **no retry mechanism**: a technical failure (renderer plugin
error, OCR engine error) immediately set `status="failed"` (terminal). Both services are structurally
closer to `notification-service` (ADR 0079) than to `archival-service` (ADR 0078): processing happens
synchronously inline in the NATS handler (`document.created`/`document.version.created`), no
multi-phase process. This session carries the ADR-0079 pattern over to both services, with a
service-specific adaptation for `rendering-service`.

1. **New fields** `attempts: int` (default 0) and `next_retry_at: datetime | None` on both models.
2. **Retry-aware failure recording**: `ocr-service`'s new `repository.record_failure` and
   `rendering-service`'s new `repository.record_failure` (counterpart to `notification-service`'s
   `attempt_delivery`) — below `max_ocr_attempts`/`max_rendering_attempts` (default 5 each), `status`
   stays `"failed"` (retry-eligible) with `next_retry_at` set via `compute_backoff_seconds`; only upon
   exhaustion does `status` transition to `failed_permanent`. `ocr-service`'s `"skipped"` status
   (word-count ceiling/content-type allowlist, a deliberate non-error decision) is unaffected by this
   — only the `"failed"` path (`UnreadableDocumentError`, engine exception) is retry-eligible.
3. **New, standalone retry poll loops** (`_ocr_retry_poll_loop`/`_rendition_retry_poll_loop`, interval
   60s each like `notification-service`) — the first attempt stays synchronous in the NATS handler,
   only the RETRY runs asynchronously.
4. **New endpoints** `POST /ocr-results/{id}/retry` and `POST /renditions/{id}/retry` — `409` unless
   `failed_permanent`, otherwise `repository.reset_for_retry` (resets `attempts=0`/
   `error_message=None`/`next_retry_at=None`) followed by an immediate synchronous retry attempt (same
   rationale as ADR 0079: a single processing step, not a multi-phase process, an admin expects an
   immediate result). **The `reset_for_retry` step is mandatory** — see the bug found during live
   verification under "Consequences".
5. **`rendering-service` peculiarity — retry per renderer, not per version**: unlike `OcrResult` (exactly
   one authoritative row per version), `Rendition` has **multiple rows per version** (one for each
   matching rule from `select_renderers()`). A retry must therefore NOT re-run the entire
   `process_version` rule cascade (which would unnecessarily regenerate already-successful renditions)
   — new `renderers.get_renderer_by_type(rendition_type)` looks up exactly the one affected renderer,
   and a new `pipeline.retry_rendition()` function re-runs ONLY that one. `ocr-service` does not have
   this problem: its retry simply calls `process_version` again, since there is only ever one result
   per version anyway.

## Rationale

- **Why the notification-service pattern (ADR 0079) instead of the archival-service pattern (ADR 0078)**:
  both new services process synchronously inline in a NATS handler, not as a multi-phase state
  machine — a backoff wait directly in the handler would block the consumer, exactly the same
  consideration as with `notification-service`.
- **Why `rendering-service`'s retry targets ONLY the failed renderer**: the natural multi-row nature per
  version (a renderer failure deliberately does not block the remaining rules by existing design, see
  `process_version`'s docstring) applies symmetrically to the retry as well — a full re-run of
  `process_version` would not only be wasteful (unnecessarily regenerating already-successful
  renditions), but could also cause inconsistent side effects if the `RENDERERS` rule set has changed
  in the meantime.
- **Why `ocr-service`'s `"skipped"` status is NOT retry-eligible**: a skipped processing (word-count
  ceiling, content-type allowlist) is a deliberate, audit-trail-visible administrative decision, not a
  technical error — it should not be automatically "fixed" just because time passes (the reason for the
  skip does not change with elapsed time, only with a deliberate configuration change, which would
  reassess the next regular processing attempt anyway).
- **Why NO RBAC introduction for the new retry endpoints**: both services have already had
  `ocr.write`/`rendering.write` gates since P19-S8 ([ADR 0073](0073-ocr-rendering-virus-scan-rbac.md))
  — the new endpoints use the already-existing `_require_ocr_permission`/`_require_rendering_permission`
  with `access_type="write"`, no new infrastructure needed (unlike `notification-service`, which had no
  RBAC integration at all).
- **Why `ocr-service`'s retry endpoint/poll tick uses a fresh session instead of the request session**:
  `process_version`/`retry_rendition` commit via their own, separate `session_factory()` calls — a
  repeated `get_*` on the original endpoint session would, via its identity map, return the instance
  loaded BEFORE processing (now stale) instead of the freshly committed data (SQLAlchemy's
  `Session.get()` first checks the identity cache, no re-query for an already-loaded instance).

## Consequences

- **Real bug found and fixed during live verification (both services)**: the original version of both
  retry endpoints called `process_version`/`retry_rendition` directly, WITHOUT first resetting
  `attempts`. Since `record_failure` continues counting the `attempts` value of the row that ALREADY
  EXISTS (not starting fresh at 0), a manual retry attempt that fails again immediately landed back at
  `failed_permanent` (e.g. at `max_attempts=5`: 5 → 6 ≥ 5) — a result that had once become
  `failed_permanent` could NEVER have come out of this state again, no matter how many times "retry"
  was clicked. The unit tests had not covered this, because the chosen, race-free test method (a
  permanently missing document, `DocumentNotFoundError`) never actually reached the `record_failure`
  path. Only live verification with a real, actually reprocessed document uncovered it. Fixed by a new
  `repository.reset_for_retry(session, result)` function (resets `attempts=0`/`error_message=None`/
  `next_retry_at=None`, deliberately leaves `status` untouched), called immediately before the
  reprocessing attempt in both endpoints — plus one new regression test each at both the repository
  and API level in both services.
- **Migration of already-running installations**: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in the
  respective lifespan for both new columns on `ocr.ocr_result`/`rendering.rendition`.
- **Tests**: `ocr-service` 51 (previously ~40, +11: backoff behavior, `list_due_for_retry` filtering,
  `process_version`'s `failed_permanent` path, `reset_for_retry` regression test, new `/retry`
  endpoint, new `test_main.py` for `_run_retry_tick`). `rendering-service` 44 (previously ~33, +11: same
  test pattern, plus `get_renderer_by_type`/`retry_rendition` coverage via the pipeline/API tests).
- **Deliberate test fixture caution maintained**: all new API/poll-tick tests starting `TestClient(app)`
  (thereby activating the real NATS consumer) deliberately do NOT use a real document upload for retry
  verification, but instead a permanently missing `document_id` (using the already-existing
  `DocumentNotFoundError` abort path) — a real upload would trigger a real `document.created` event,
  independently processed by the consumer started in the same test function, competing with the direct
  test call for the `attempts` bookkeeping (actually observed as a non-deterministic test failure
  during development of this session, see `ocr-service`'s `test_api.py`/`test_main.py` comments).
- **New `session_factory` fixture in both `conftest.py`** (previously missing for both services, same
  gap as with `notification-service` before P20-S3).
- **Verified live against the real running stack** (image rebuild + restart of both services,
  migration confirmed, **twice** due to the bugfix described above): a real, intentionally corrupt PDF
  document uploaded via `document-service` — the regular synchronous first attempt correctly fails
  (`status="failed"`, `attempts=1`, `next_retry_at` set); after manually setting it to
  `failed_permanent` (60s poll interval too long for a swift live check), `POST .../retry` in BOTH
  services confirms the broken behavior before the fix (`attempts` continues counting from the
  exhausted number, stays `failed_permanent`) and the correct behavior after the fix (`attempts=1`,
  `status="failed"`, retry-eligible again); `404` for unknown resources confirmed in both services.
