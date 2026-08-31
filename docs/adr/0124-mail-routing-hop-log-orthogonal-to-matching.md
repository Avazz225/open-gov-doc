# 0124 — Routing between mailboxes: an orthogonal, append-only hop log

**Status:** accepted (P31-S12b, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 12b (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #6), affects `mail-connector`,
`user-ui`; second of the P31-S12a → 12b → 12c split ([ADR 0123](0123-multi-inbox-model-env-var-config-no-department-rbac-yet.md))

## Decision

P31-S12a built the multi-inbox config/plumbing (`Settings.mailboxes`, `InboundMessage.mailbox_id`) but no
way to move a message between mailboxes once it arrived. This session adds the actual hand-off action —
`POST /inbound/{id}/route` — plus a new `MailRoutingLogEntry` table recording every hop (the literal
beginning of the "Postbuch" the plan asks for, though the standalone searchable cross-message register is
still P31-S12c, not this session). `InboundMessageOut` gained an embedded `routing_log` (same pattern
`attachments` already uses). `user-ui`'s `PoststellePane` gained its first mailbox-aware UI: a mailbox
filter (only shown once more than one mailbox is configured), a "Weiterleiten" action per message, and the
per-message routing history.

## Rationale

- **Routing is orthogonal to matching/assignment, not a new status value**: `status`/`match_type`/
  `match_value`/`proposed_target_type`/`proposed_target_id` are left completely untouched by a route
  action — only `mailbox_id` changes, plus a permanent log row. A document/case reference candidate found
  in a message's text doesn't become invalid just because a different mailbox now administers it; treating
  routing as a genuinely separate axis avoids conflating "who currently handles this" with "what does this
  message resolve to."
- **Append-only hop log, no soft-delete**: `MailRoutingLogEntry` has no `removed_by`/`removed_at` fields
  (unlike `FolderDocumentReference`/`CaseDocumentReference`, P31-S7's own reference-join precedent) — a
  routing hop, once it happened, is permanent history with nothing to "undo," only further hops.
  `InboundMessage.mailbox_id` always reflects the current location; the log accumulates every past
  transition. This is deliberately the exact shape P31-S12c's searchable register will read from.
- **A message already `confirmed`/`rejected` cannot be routed** (`409`, same status-check pattern
  `confirm_match`/`assign_manually`/`reject_message` already use, checked inline in `main.py` rather than
  via a repository-level exception class — matching this file's existing convention, where the declared but
  unused `NotInStatusError` was never actually the mechanism in practice). Routing a terminally-resolved
  message would be meaningless — there's no longer an open item for anyone to review at the new location.
- **Target must be a currently configured mailbox, and must differ from the current one** (`422` for
  either violation) — an unconfigured target would create an unpollable dead end no mailbox actually
  services; routing to the same mailbox is a caller mistake, not a meaningful action, so it's rejected
  rather than silently treated as a no-op (unlike, say, idempotent group-membership adds elsewhere in this
  project — there's no natural "retry" scenario for routing the way there is for re-adding a membership).
- **No topology restriction on which mailbox may route to which**: any configured mailbox can hand off to
  any other, matching the "everyone with `poststelle_role` can act on everything" posture P31-S12a already
  established (per-mailbox RBAC remains deliberately deferred, see ADR 0123 "Consequences" — this session
  doesn't change that; routing is a workflow signal, not an access-control boundary, and building
  restrictive topology rules on top of an unenforced role would be a false sense of structure without a
  matching enforcement mechanism).
- **Duplicate-`source_uid`-at-target is checked BEFORE the mutation, not left to the DB constraint**:
  `route_message` pre-checks whether the target mailbox already holds a message with the same
  `source_uid` (`InboundMessage.__table_args__`'s composite `uq_inbound_message_mailbox_source`, P31-S12a)
  and raises a dedicated `DuplicateInTargetMailboxError` (translated to `409` in `main.py`) instead of
  letting a raw `IntegrityError` surface as an unhandled `500`. **Found live during this session's own
  verification**: two mailboxes independently polling the same physical mail account (a plausible real
  deployment shape, not just a test artifact - e.g. a shared departmental inbox also polled centrally
  during a migration) can each ingest their own copy of a message sharing the same backend-native UID;
  routing one copy into the other's mailbox then collides on the composite unique constraint. The
  pre-check-before-mutation shape matches this project's general preference for explicit validation over
  parsing raw DB constraint violations (same posture as the status/target-mailbox checks above).
- **`user-ui` mailbox-awareness gated on `mailboxes.length > 1`**: the filter dropdown and "Weiterleiten"
  action stay hidden entirely for a single-mailbox installation (still the default dev-stack shape) —
  showing a filter with one option, or a route action with no valid targets, would be a UI element that
  can never do anything useful. `PoststellePane` fetches `GET /mailboxes` once on mount, independent of the
  inbox/outbox tab reload cycle, and resolves `mailbox_id`s to display names client-side via that list
  (mirroring the existing raw-principal-ID display gap elsewhere in this project — here, no separate
  resolution endpoint is needed since `GET /mailboxes` already returns the full, small, credential-free
  list in one call).

## Consequences

- **No standalone cross-message "Postbuch" register view yet** — the routing history is only visible
  per-message (embedded in `GET /inbound/{id}`'s `routing_log`), not as its own searchable log across every
  message and mailbox. That remains P31-S12c, building directly on this session's `MailRoutingLogEntry`
  table (no schema change anticipated there, only new read/search endpoints and a dedicated view).
- **Per-mailbox/department RBAC is still not built** — routing changes which mailbox's filtered list a
  message appears under (a soft, UX-level separation), but any `poststelle_role` holder can still see and
  act on every mailbox regardless of routing history. The same, already-documented gap from ADR 0123.
- **No notification on routing** — a department's mail-room staff aren't proactively alerted that a new
  item was routed to them; they'd need to check `PoststellePane` (or, once it exists, P31-S12c's register)
  to notice. Not part of this session's scope; a plausible, separate future addition via the existing
  `notification-service` if department-level visibility becomes a real workflow need.
