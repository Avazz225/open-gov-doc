# 0217 — Teamspace AD-group invitation: build

**Status:** accepted
**Context:** P74-S3 (Phase 74, tenth gap-analysis round). ADR 0160 (P43-S2) scoped this six times over —
"the blocker is only partially stale" — and named exactly what was still missing: a queryable Keycloak
group-member list, a teamspace-to-group binding table, and a live-reconciliation mechanism (never a
one-time snapshot copy, per ADR 0093's own "Keycloak/AD is sole source of truth" principle). This
session was put to the user as an explicit, final decision — build the already-designed solution, or
decline permanently — since "defer again with the same reasoning" was no longer acceptable after six
carry-forwards. **Decision: build it**, following ADR 0160's own recommendation essentially as written.

## Decision

Three new pieces, exactly as ADR 0160 scoped them:

1. **`auth-service`: `GET /groups/{name}/members`** - thin wrapper around `python-keycloak`'s
   `get_group_by_path()`/`get_group_members()` (already-vendored, previously unused anywhere in this
   codebase, confirmed by ADR 0160's own code survey). Returns `UserLookupOut[]` (`{id, username}`,
   reused directly - same minimal shape as `GET /users/lookup`), `404` if the group doesn't exist.
   **Deliberately NOT exposed to interactive callers** - service-to-service only, gated by a new, narrow
   `service.group_lookup` capability (seeded role `service-group-lookup`), same "own capability, not
   folded into an existing one" shape as `service.user_lookup` (Phase 50 Session 2). This resolves ADR
   0160's own explicitly-left-open "design fork" (a group roster is a wider disclosure than a single
   name's existence) in favor of the narrower option: only `teamspace-service` can call it, and only
   ever already narrowed to "a manager of a specific teamspace, previewing a specific bind action" via
   that service's own `_require_manager` gate - never a general, installation-wide directory capability.
2. **`teamspace-service`: `TeamspaceAdGroupBinding` table** (`teamspace_id`, `ad_group_name`,
   `invited_by`, `invited_at`) plus a new `TeamspaceMember.source_ad_group_name` column (nullable,
   `NULL` for a manually-invited member). Four new endpoints, all manager-gated like `POST .../members`:
   `GET .../ad-group-preview` (proxies the auth-service call, for previewing membership before binding),
   `POST .../ad-group-bindings` (creates the binding + an immediate initial sync, not waiting for the
   next poll tick), `GET .../ad-group-bindings` (member-gated, listing), `DELETE .../ad-group-bindings/
   {name}` (removes the binding + immediately revokes every membership row that binding itself created).
3. **`_ad_group_reconciliation_poll_loop`** - new background poll loop (default 300s, same "poll instead
   of push" idiom as every other background job in this project, ADR 0020), iterating every binding
   across every teamspace: grants `permission-service` access for AD-group members not yet reflected as
   a `TeamspaceMember` row, revokes access for members that binding created but who are no longer in the
   group. **Never overwrites a manually-invited member's attribution** - if a person already has a
   manual `TeamspaceMember` row (from a direct `POST .../members` invite), the reconciler skips creating
   a duplicate (the existing `UniqueConstraint(teamspace_id, principal_id)` already prevents two rows for
   the same person) and never claims that row as its own, so leaving the AD group later doesn't strip
   access that was separately, deliberately granted.

`apps/user-ui/src/components/TeamspacesPane.tsx` gained a new "AD-Gruppen-Einladung" section: bound-groups
list (with an unbind button, manager-only), a group-name input + preview button showing the real current
roster, and a "Gruppe binden" button once previewed. Member rows show `(über AD-Gruppe <name>)` next to a
group-invited member's name.

## Rationale

- **Why live/poll reconciliation, never a snapshot copy**: ADR 0160's own already-settled reasoning
  (extending ADR 0093's principle to a second feature area) - a snapshot would let someone added to the
  AD group later stay invisible, or someone removed keep access until manually cleaned up. Not
  re-litigated here.
- **Why the group-lookup endpoint is service-to-service only, not exposed via an "everyone" permission
  like `GET /users/lookup`**: resolves ADR 0160's own explicitly-left-open fork. A full group roster is
  a materially wider disclosure than confirming a single, already-known username exists - routing every
  interactive call through `teamspace-service`'s own manager gate keeps the actual disclosure surface
  identical to "a manager previewing a bind they're about to make," never a general directory feature.
- **Why `source_ad_group_name` on `TeamspaceMember` rather than a separate join table**: a person can
  only ever have one membership row per teamspace (the pre-existing `UniqueConstraint`) - attribution is
  therefore a property of that one row, not a many-to-many relationship. A nullable string column is the
  smallest shape that answers "did the reconciler create this row, and if so for which binding" without
  a new table whose only cardinality would be 1:1 with `TeamspaceMember` anyway.
- **Why bind-time does an immediate sync instead of waiting for the next poll tick**: a manager binding a
  group expects to see its effect right away, not after up to 300s - the same "act now, let the poll
  loop maintain it afterward" shape `document-service`'s various on-demand-plus-poll-loop features
  already use elsewhere in this project.
- **Why unbind revokes immediately rather than just deleting the binding row**: leaving stale
  `permission-service` grants around after a binding is explicitly removed would be a real, if narrow,
  authorization drift - the same reasoning that makes the poll loop revoke departed members in the first
  place, just triggered synchronously instead of on the next tick.

## A real bug found and fixed during this session's own live verification

`repository.delete_teamspace` deletes `TeamspaceMember`/`TeamspaceAppointment`/`TeamspaceContact` rows
before deleting the `Teamspace` row itself, but was missing the equivalent cleanup for the new
`TeamspaceAdGroupBinding` table - an active binding's foreign key blocked the deletion outright
(`IntegrityError`, not silently ignored). Found live, not merely inferred: reproduced via a real browser
session that created a teamspace, bound a real Keycloak group to it, then tried to delete the teamspace
through the actual UI flow, which surfaced the real `500`. Fixed by adding the same
`TeamspaceAdGroupBinding.__table__.delete()` cleanup step the other three tables already had, and a new
regression test (`test_delete_teamspace_with_an_active_ad_group_binding_succeeds`) that reproduces the
exact scenario.

## Consequences

- **Live-verified end-to-end**, both via direct API calls against the real running stack (create a real
  Keycloak group, add a real user, preview → bind → confirm the real `permission-service` grant →
  unbind → confirm the grant is revoked and a separately, manually-invited member is untouched) and via
  a real headed-browser session driving the actual `user-ui` flow (screenshots: empty state, preview
  showing the real member, bound state showing "e2euser (über AD-Gruppe ...)" in the member list and the
  binding in the "AD-Gruppen-Einladung" section with an unbind button).
- **Operator setup step, same convention as `service-user-lookup`** (Phase 50 Session 2): the
  `service-group-lookup` role is seeded in `permission-service`'s catalog automatically, but must be
  GRANTED to the `teamspace-service` principal once by an operator (or, in this dev stack, by the test
  suite's own autouse fixture, which the live-verification session relied on directly) - not
  auto-provisioned at startup, matching every other machine-to-machine capability in this project.
- **`docs/adr/0160-...md` remains as the scoping record** (not superseded) - this ADR documents the
  actual build and the one real bug it surfaced; ADR 0160's own analysis of what was and wasn't already
  possible before this session stays accurate and useful history.
- **`docs/services/auth-service.md`, `docs/services/teamspace-service.md`, `docs/services/user-ui.md`
  updated** with the new endpoints/UI section; `IMPLEMENTATION_PLAN.md`'s Phase 74 entry for P74-S3
  struck through as done, no longer "deferred."
