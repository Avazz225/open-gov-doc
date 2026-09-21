# 0196 — document-service: force-unlock failure event, checkin_version lock precheck before scan/upload

**Status:** accepted
**Context:** P66-S3 (Phase 66, "Security/Correctness Quick Wins" — third and last session of the eighth
gap-analysis round's plan). Two bundled `document-service` correctness fixes.

## Decision

**(a) `consumer.py` now publishes `document.force_unlock.failed` when a queued, approved force-unlock
execution fails.** The plan's own framing bundled two claims about force-unlock's four-eyes gate:
"defaults to `False` when unconfigured" and "no feedback channel when a queued execution fails." Before
implementing anything, both were checked against the actual code and ADR 0022 (the four-eyes mechanism's
own ADR):

- **The default-`False`-when-unconfigured claim is NOT a gap** — it is the established, ADR
  0022-documented, project-wide convention ("configurable per action type, not globally enforced")
  applied uniformly to every four-eyes-gated action type in this codebase except one
  (`auth.superuser.activate`, break-glass, intentionally pre-seeded `requires_approval=True` at startup —
  itself explicitly contrasted against force-unlock's convention in permission-service's own startup
  code). Changing force-unlock's default would mean inventing a second, new, deliberate exception to
  ADR 0022's stated philosophy — a real authorization-model decision this session's own Definition of
  Done (in `IMPLEMENTATION_PLAN.md`) explicitly said not to expect. **No code change made for this half**;
  `docs/services/document-service.md` corrected to state this is confirmed-intentional, not an open gap.
- **The missing feedback channel IS a real, fixable gap** — ADR 0022's own Consequences section already
  named it explicitly: "`permission-service` does not learn whether the action actually executed... a
  failed force-unlock is only logged locally." `consumer.py`'s failure branch (previously
  `logger.warning(...)` and `return`, nothing else) now additionally publishes `document.force_unlock.failed`
  (`approval_request_id`, `released_by`, `reason`), reusing the `actor="system:<service>"` convention
  already established elsewhere (`notification-service`'s `publish_notification_result`,
  `ocr-service`'s `_persist_failure`). This makes the failure visible system-wide — every event is
  captured by `audit-service`'s `document.>` subscription regardless of whether anything else consumes it.

**`permission-service` does not yet consume the new event to update `ApprovalRequest.status`.** ADR
0022's Consequences section already named the larger gap this would close ("no `"executed"` state") as
known future work, not attempted in the session that introduced the mechanism. Building that full
state-machine extension (a new `ApprovalRequest` status, a new consumer in permission-service) is a
separate, larger change than "publish an event on failure" — left as accepted, documented residual, not
attempted here to keep this session's scope matched to what the plan actually asked for.

**(b) `checkin_version` now checks the lock-conflict precondition before the virus scan and the storage
upload, not only afterward.** Confirmed against the actual code: `POST /documents/{id}/versions`
previously ran the virus scan (a real HTTP round-trip to virus-scan-service) and the full storage-service
upload unconditionally, before `repository.checkin_version` ever checked whether the document was locked
by someone else — a request that was always going to `409` still paid for both. Neither check depends on
file content. New `repository.check_lock_for_checkin()` — the same lock-conflict logic extracted from
`checkin_version`, called first — rejects with `409` immediately after the existing RBAC/maintenance/
license checks, before `file.read()`. `checkin_version` itself still re-checks the lock internally,
unchanged — cheap, and genuine defense-in-depth against the lock being acquired between the precheck and
the actual write within the same request.

## Rationale

- **Why verify the "default False" premise instead of just implementing what the plan described**: this
  project's established discipline (used throughout the Phase 65+ round) is to verify every claimed gap
  against the actual current code/ADRs before acting on it — several earlier sessions in this same round
  found claimed gaps that were already closed or, as here, already correct by design. Implementing a
  "fix" for something that turns out to be the deliberate, documented convention would have introduced a
  real inconsistency (one action type behaving differently from all the others with no new ADR to justify
  why), not resolved one.
- **Why publish the event unconditionally rather than also building the `ApprovalRequest` status
  extension**: the plan's own Definition of Done for this session says no new ADR is expected — the
  status-extension half is a genuine new architectural decision (new enum value, new consumer,
  cross-service state synchronization semantics) that deserves its own scoped session if pursued, not a
  same-session addition bundled under "no new ADR expected."
- **Why the lock precheck is a separate function rather than reordering inside `checkin_version` itself**:
  `checkin_version` needs the already-fetched `document`/`lock` rows for its OWN logic further down
  (conflict-copy filename, version numbering) — duplicating the lookup via a small, separately-callable
  function is simpler than threading pre-fetched rows through `checkin_version`'s existing signature, and
  keeps the defense-in-depth re-check inside `checkin_version` trivial (unchanged).

## Consequences

- `services/document-service/src/document_service/consumer.py`: force-unlock's `NotFoundError` branch now
  publishes `document.force_unlock.failed` in addition to the existing log line.
- `services/document-service/src/document_service/repository.py`: new `check_lock_for_checkin()`.
- `services/document-service/src/document_service/main.py`: `checkin_version` calls
  `check_lock_for_checkin` before `file.read()`/the virus scan/the storage upload.
- `docs/services/document-service.md`: three spots updated — the general virus-scan-ordering description,
  the force-unlock Open Points bullet (default-False clarified as intentional, feedback-channel bullet
  partially struck), and a new row in the published-events table for `document.force_unlock.failed`.
- Tests: `document-service` 414/414 (was 413) — new `test_checkin_lock_conflict_returns_409_without_scanning_or_uploading`
  (monkeypatches `virus_scan_client.scan` to a spy, asserts it is never called when the request 409s on a
  lock conflict); `test_consumer.py`'s
  `test_approved_force_unlock_for_already_unlocked_document_is_logged_not_raised` renamed to
  `..._publishes_failure_event` and its assertion changed from `published == []` to asserting the new
  event.
- Rebuilt/redeployed. **Live-verified against the real running stack**: a document locked by `alice`,
  check-in attempted by `bob` — `409` in ~11ms (fast enough to confirm the scan/upload were skipped
  entirely, versus a real virus-scan-service/storage-service round trip). The force-unlock failure-event
  path itself was not separately live-verified end-to-end through the full
  approval-request-plus-NATS-plus-audit-service chain — already covered by the passing, in-process
  `test_consumer.py` regression test (real `Event`/`to_bytes()` round trip, real DB), and scripting a full
  live approval flow just for this was judged not worth the setup cost for a session already covered by an
  automated test exercising the exact same code path, the same tradeoff ADR 0189 made for its own
  impractical-to-script live case.
