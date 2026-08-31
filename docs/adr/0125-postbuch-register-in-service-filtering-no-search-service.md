# 0125 — Postbuch register: in-service filtering, not a search-service integration

**Status:** accepted (P31-S12c, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 12c (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #6), affects `mail-connector`,
`user-ui`; third and last of the P31-S12a → 12b → 12c split ([ADR 0123](0123-multi-inbox-model-env-var-config-no-department-rbac-yet.md))

## Decision

P31-S12a built the multi-inbox config/plumbing, P31-S12b added the actual routing action plus the
per-message `MailRoutingLogEntry` hop log ([ADR 0124](0124-mail-routing-hop-log-orthogonal-to-matching.md)).
This session adds the standalone, searchable cross-message register the concept calls the "Postbuch" — a
new `GET /routing-log` endpoint in `mail-connector` that joins `MailRoutingLogEntry` with `InboundMessage`
across every message, filterable by `mailbox_id` (either side of the hop), `routed_by`, a `routed_at`
date range (`since`/`until`), and a subject substring (`q`). `user-ui`'s `PoststellePane` gained a third
tab, "Postbuch", rendering the filtered register — gated on `mailboxes.length > 1`, same as P31-S12b's
mailbox filter/routing action.

## Rationale

- **In-service `ilike` filtering, not a `search-service` integration**: `search-service` indexes
  `document-service`/`case-service` content, not raw inbound mail — wiring `mail-connector` into it would
  be a much larger, unrelated integration effort (new indexing pipeline, new resource type) for a register
  whose entire underlying dataset is one small, already-owned table. A case-insensitive `ilike` substring
  match against `InboundMessage.subject` is the same class of mechanism SQL already offers, no new search
  infrastructure needed — proportionate to the actual size of the problem.
- **Filter shape follows `audit-service`'s `GET /events`**, the closest existing precedent for a filtered,
  capped register view in this codebase: simple query parameters (`mailbox_id`/`routed_by`/`since`/`until`/
  `q`/`limit`), no cursor-based pagination, newest-first ordering, a plain list response.
- **`mailbox_id` matches a hop on EITHER side** (`from_mailbox_id` or `to_mailbox_id`), not just the
  destination — a department wants to see both what left and what arrived at its own mailbox, not only one
  direction of its own traffic.
- **The response embeds message context (`message_subject`/`message_from_address`/
  `message_current_mailbox_id`/`message_status`) directly on each row**, rather than returning bare
  `MailRoutingLogEntry` rows that would require a second `GET /inbound/{id}` round trip per result to be
  useful — a register the user can't tell "what" from is not actually searchable in practice.
- **`user-ui`'s search box uses an explicit submit (`<form onSubmit>`), not live-as-you-type**: same pattern
  as `AussonderungPane`'s `query`/`runSearch` — a `reload(postbuchQueryOverride?)` override parameter is
  passed only at submit time, deliberately excluded from `reload`'s own `useCallback` dependency array, so
  typing doesn't fire a request per keystroke. The mailbox filter `<select>`, in contrast, reloads
  immediately on change (same as P31-S12b's inbox mailbox filter) — a discrete dropdown choice, not
  free text, so immediate reload is the right UX there.
- **Tab gated on `mailboxes.length > 1`**, same reasoning as P31-S12b's mailbox filter/routing action: with
  a single configured mailbox, routing — and thus a routing register — can never have happened; showing an
  always-empty tab would be a UI element that can never do anything useful.

## Consequences

- **No pagination beyond a `limit` cap** (default 200) — acceptable for a register still measured in
  hundreds, not millions, of hops per installation; a true cursor-based/offset pagination scheme is a
  later addition if volume ever warrants it, not built preemptively here.
- **Still no per-mailbox/department RBAC** — the register, like every other `/inbound`-area endpoint, is
  visible to any `poststelle_role` holder regardless of mailbox. The same, already-documented gap from
  ADR 0123/0124.
- **P31-S12 (all three parts) is now complete**: multi-inbox config (12a), routing action + per-message log
  (12b), and this session's standalone searchable register (12c) together deliver gap #6 from the eGov
  feature gap analysis in full.
