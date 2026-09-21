# 0191 — federation-hub-service: `supported_process_types` enforcement + `handover` row cleanup

**Status:** accepted
**Context:** P63-S2 (Phase 63, second session of the seventh gap-analysis round). Found by the round's
`docs/services/*.md` Open Points sweep: `federation-hub-service`'s `Installation.supported_process_types`/
`.supported_document_types` were stored at registration but never checked — `POST /handovers` of an
undeclared type succeeded at the hub and only failed downstream once the target installation itself
rejected it. Separately, no cleanup mechanism existed for the `handover` metadata table at all — unbounded
growth over an installation's lifetime, the exact same shape `registry-service` already fixed for its own
instance table in Phase 58 Session 2.

## Decision

**(a) `POST /handovers` now validates `payload.process_type` against `to_installation.supported_process_
types`**, rejecting a mismatch with `422`. An EMPTY list (`Installation`'s own default, and still every
real installation's actual state today — nothing in this codebase populates this field yet) is treated as
"no restriction declared", not "accepts nothing" — otherwise this check would reject every handover that
exists in the real system today.

**`supported_document_types` is deliberately left unenforced.** `HandoverCreate` has no document-type
field at all, and `encrypted_payload` is end-to-end encrypted (ADR 0028's own "the hub never sees content"
design) — there is nothing for the hub to compare a declared document-type list against without breaking
that design. This is a real architectural constraint discovered during implementation, not an oversight:
the plan's own original finding grouped both fields together as "not enforced", but only one of them is
actually enforceable at this layer.

**(b) New `_handover_cleanup_poll_loop`** (interval `handover_cleanup_poll_interval_seconds`, default 1h)
deletes `handover` rows in a TERMINAL status (`completed`, `delivery_failed`, `result_delivery_failed`)
older than `handover_cleanup_after_seconds` (default 7 days) — same naming/default-value convention as
`registry-service`'s equivalent. A plain bulk `DELETE` (`repository.purge_stale_handovers`), not a
per-row deletion with an event publish like `registry-service`'s own `deregister`-reuse approach — there
is no existing per-handover lifecycle event for this to preserve by going row by row.

## Rationale

- **Why empty-list-means-unrestricted instead of empty-list-means-nothing-allowed**: the alternative
  reading is technically "more secure" in the abstract, but would make this fix a functional regression
  for every real handover in this project's own dev/test stack and any deployment that hasn't explicitly
  opted into declaring supported types — the field exists as an opt-in allowlist mechanism, not a
  mandatory declaration, per its own schema default (`[]`).
- **Why `supported_document_types` isn't also validated in this session**: explained under "Decision"
  above. Attempting to enforce it would require either decrypting the payload at the hub (a genuine,
  much larger architectural reversal of ADR 0028) or adding a new plaintext document-type field to
  `HandoverCreate` that the sender declares honestly (which the hub could then check, but which nothing
  stops a sender from lying about — a materially weaker guarantee than `process_type`'s current, real
  enforcement). Neither is a session-sized fix; left as a known, documented limitation.
- **Why a bulk `DELETE` instead of reusing a single-row deletion function with an event publish** (unlike
  `registry-service`'s own cleanup, which deliberately reuses `deregister` specifically to keep that
  service's existing deregistration event flowing): there is no equivalent existing per-handover deletion
  event anywhere in this codebase to preserve — inventing one that nothing would consume would be
  premature complexity for a purely internal bookkeeping cleanup.
- **Why only terminal statuses are eligible for cleanup, regardless of age**: a handover still `pending`/
  `delivered`/`pending_retry`/`result_pending_retry` should never remain in that state indefinitely in
  practice (the retry-then-give-up mechanism, ADR 0081, always eventually reaches a terminal status), but
  if one somehow does, the cleanup must not silently delete evidence of a genuinely stuck row — an
  unconditional age-only cutoff (like `registry-service`'s reachability-based one) doesn't have an
  equivalent "this row is definitely done" signal the way a terminal status does, so this session's design
  uses the stronger, more conservative check available to it.
- **Why 7 days / 1 hour, matching `registry-service`'s own values exactly**: no reason for this table to
  need a materially different retention window or poll cadence; reusing the same defaults avoids inventing
  a second, unexplained pair of numbers for the same underlying concern (bounding unbounded metadata-row
  growth).

## Consequences

- `POST /handovers` now `422`s for a `process_type` not in a target installation's non-empty declared
  list.
- New `Settings.handover_cleanup_after_seconds`/`handover_cleanup_poll_interval_seconds`; new
  `repository.purge_stale_handovers`; new `_handover_cleanup_poll_loop`, lifespan-managed identically to
  the existing `_handover_retry_poll_loop`.
- New tests: `test_create_handover_rejects_undeclared_process_type`, `test_create_handover_allows_
  declared_process_type`, `test_create_handover_allows_any_process_type_when_none_declared` (API level);
  `test_purge_stale_handovers_removes_old_terminal_rows`, `test_purge_stale_handovers_leaves_recent_
  terminal_rows_alone`, `test_purge_stale_handovers_never_removes_a_non_terminal_row_regardless_of_age`
  (repository level, matching `registry-service`'s own direct-repository-call testing convention for its
  analogous cleanup function rather than waiting on the real poll loop). `federation-hub-service` 83/83
  (+6). Rebuilt/redeployed, live-verified against the real running stack (a real signed handover with an
  undeclared `process_type` confirmed `422` with a clear message naming the declared list; a real signed
  handover with the declared type confirmed `201`/`pending_retry`, delivery itself failing only because
  the test target's `callback_base_url` doesn't resolve in this environment — expected, unrelated to this
  fix). `docs/services/federation-hub-service.md` updated (Open Points bullet closed, new "Periodic
  cleanup of old `handover` rows" paragraph, API table row, test count).
