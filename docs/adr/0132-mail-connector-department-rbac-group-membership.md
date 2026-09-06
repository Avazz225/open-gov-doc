# 0132 — Postbuch department RBAC: enforce `owning_group_id` via group membership

**Status:** accepted (P32-S3, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 32 Session 3 (post-Phase-31 gap re-analysis), affects `mail-connector`

## Decision

`mail-connector` now enforces `MailboxConfig.owning_group_id` for `departmental` mailboxes: any
`poststelle_role` holder may still read/act on a `central` mailbox, but reading, routing (source side
only), confirming, manually assigning, or rejecting a message in a `departmental` mailbox additionally
requires real `permission-service` group membership in that mailbox's `owning_group_id`. A new
`PermissionServiceClient.is_group_member(group_id, principal_id)` fetches `GET
/groups/{group_id}/members` and filters client-side (no dedicated "is member" endpoint exists at
permission-service). `GET /mailboxes` stays deliberately unfiltered.

## Rationale

- **`owning_group_id` existed as pure metadata since P31-S12a, never enforced** — ADR 0123/0124's own
  "Consequences" named this as an open gap: any `poststelle_role` holder could read or route any
  department's mail regardless of team assignment. Confirmed via `_require_poststelle`, the only gate
  the affected endpoints had before this session.
- **A departmental mailbox's `owning_group_id` must be a real `permission-service` `Group.id`, not an
  arbitrary string** — `repository.add_group_member` 404s (`session.get(Group, group_id)`) on a group
  that was never created via `POST /groups`, which itself server-generates the `id` (a UUID,
  `GroupCreate` has no caller-settable `id` field). The pre-existing test fixture
  (`with_finanzen_mailbox`) had been using the literal string `"group-finanzen"` as if it were a group
  id — harmless while unenforced, but would have made every fixture-dependent test fail 403 the moment
  enforcement went live, since no such group ever existed. Fixed by having the fixture find-or-create a
  real group by name and grant the test principal real membership, rather than changing the enforcement
  to accommodate the placeholder string.
- **`GET /mailboxes` stays unfiltered on purpose**: this is the routing-target selector's data source —
  central intake needs to see every department's mailbox by name to route mail *to* it, and knowing a
  mailbox's name/kind exists is not itself sensitive (unlike its message content, which now is scoped).
- **Routing checks only the message's current (source) mailbox, not the target** — the existing
  "any configured mailbox may route to any other" topology (P31-S12b) is preserved; requiring target
  membership too would block central intake staff from ever routing to a department they don't belong
  to, defeating the endpoint's actual purpose.
- **An explicit `mailbox_id` filter on `GET /inbound`/`GET /routing-log` 403s outright if inaccessible**,
  while omitting it silently scopes to the caller's accessible set (central + departments they belong
  to) rather than 403ing or silently returning everything — matches this project's established
  filtered-vs-unfiltered idiom elsewhere (e.g. `document-service`'s folder-scoped listings).
  `search_routing_log`'s existing either-side hop matching means a hop touching an accessible mailbox on
  either end stays visible even to a non-member of the *other* end — deliberate, since "central" (always
  accessible) touches most hops by construction; verified with a dedicated test using two departmental
  mailboxes on both ends of a hop to isolate true exclusion from this expected either-side visibility.
- **"List members, then filter client-side" is the first group-membership-as-RBAC-gate pattern in this
  codebase** — every existing `PermissionServiceClient` elsewhere only wraps capability/permission
  checks (`/check`, `/effective-permissions`). No dedicated "is X a member of Y" endpoint exists;
  fetching the (expected-small) member list and filtering matches `document-service`'s own
  `has_permission`'s "list, then filter" idiom for its differently-shaped check, rather than adding new
  permission-service API surface for a single caller.

## Consequences

- **A departmental mailbox with an `owning_group_id` that doesn't correspond to a real
  `permission-service` group now locks everyone out except no one, silently (fail-closed)** — since
  `is_group_member` gets an empty member list for a non-existent group id (`list_group_members` has no
  FK enforcement, confirmed), not a 404. An operator misconfiguring `owning_group_id` gets a support
  ticket, not an open mailbox — the safer failure direction, but worth calling out for whoever writes
  the mailbox's `DMS_MAILBOXES` entry.
- **Every group-membership check is a live `permission-service` HTTP round trip**, same latency
  trade-off P31-S9/S10's org-hierarchy checks already accepted — no caching added, consistent with this
  service's existing low request volume (interactive mail-room use, not a hot path).
- **Live-verified against the running stack**: a real group was created and a member granted via
  `permission-service`, confirming no regression to the existing single-mailbox ("central"-only)
  production configuration, which this installation currently runs (`GET /mailboxes` still returns only
  `central`, so the departmental gate has no live department to exercise yet beyond the test suite's own
  real-`permission-service` coverage).
- **Tests**: `mail-connector` +11 (department-membership-required 403s for `GET /inbound`,
  `GET /inbound/{id}`, `POST /inbound/{id}/route` source-side, `confirm-match`, `assign`, `reject`,
  `GET /routing-log`; positive-membership success cases; unfiltered-listing exclusion; source-only
  routing to an inaccessible target succeeding). `with_finanzen_mailbox` extended to grant real
  membership instead of relying on a placeholder group id.
- **No frontend changes required**: `PoststellePane.tsx` doesn't need to know about `kind`/
  `owning_group_id` — a 403 from an inaccessible mailbox surfaces through its existing error handling,
  same as any other API error.
