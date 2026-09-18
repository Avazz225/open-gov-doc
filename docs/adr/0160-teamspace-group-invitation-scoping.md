# 0160 — Teamspace group invitation: scoping (is the old blocker actually gone?)

**Status:** accepted
**Context:** P43-S2 (Post-Roadmap Phase 43) — a **scoping-only** session, explicitly no implementation
commitment (see `IMPLEMENTATION_PLAN.md` "Phase 43"). Re-examines ADR 0043's own "Group invitation
remains completely unbuilt" consequence, prompted by the plan's own observation that its stated
blocker ("waiting on AD/Keycloak group integration") looks stale now that ADR 0093/P24-S2 built
AD-group→role mapping.

## Decision

**The blocker is only partially stale — not fully resolved, as the plan's framing assumed.**
ADR 0093 (P24-S2) and its extension ADR 0153 (P39-S3, composite AND-rules, default role, four-eyes)
built a real, working AD-group→**role** resolution: a Keycloak `groups` JWT claim is read at `GET /me`
and mapped to `permission-service` roles, live, every call. But the specific piece a teamspace group
invitation actually needs — **"who is currently a member of AD group X," as a queryable list** — was
never built by that work and still does not exist anywhere in this project today. `GET /me`'s own
response body deliberately never returns the raw `groups` claim, only the roles it resolves to
(`services/auth-service/src/auth_service/main.py:629-657`), and no other endpoint calls Keycloak's
group-membership admin API at all (confirmed by inspecting every `KeycloakAdmin` call site in
`auth-service` — none touches a group). ADR 0093 solved a *different* problem (bulk role-granting via
group membership) that happens to sound adjacent to this one but doesn't provide the missing piece.

**Recommendation for a future build session** (not committed here):

1. **A new, narrow `auth-service` endpoint** exposing a group's current member list — e.g.
   `GET /groups/{name}/members` returning `[{id, username}, ...]`, the same minimal shape as the
   existing `GET /users/lookup` precedent (ADR 0043's own bullet on that endpoint). Implementation is a
   thin wrapper around `python-keycloak`'s already-vendored `KeycloakAdmin.get_group_by_path()`/
   `get_group_members()` — a real library capability, currently completely unused anywhere in this
   codebase, not a new external dependency. The gating question (who may query "who is in group X",
   given this is a genuinely wider existence-oracle than `/users/lookup`'s single-name lookup) is a real
   open decision for that session, not resolved here.
2. **A new `teamspace-service` mapping table**, e.g. `teamspace_ad_group(teamspace_id, ad_group_name,
   invited_by, invited_at)` — records "this teamspace is bound to this AD group," analogous in spirit to
   `teamspace_member` but for the group binding itself, not a snapshot of its members.
3. **Live resolution, not a one-time snapshot copy, for who currently has access via that binding** —
   this is the one genuine architectural fork this scoping surfaces, and the recommendation is explicit:
   resolve the bound AD group's current members from Keycloak on an ongoing basis (a periodic poll loop,
   this project's own established idiom for "something needs to stay in sync without a push mechanism,"
   e.g. `_task_claim_expiry_poll_loop`) and reconcile `permission-service` role assignments accordingly
   (grant to newly-added members, revoke from removed ones) — NOT a one-time loop over the existing
   per-user `POST /teamspaces/{id}/members` invite path at bind time. A snapshot copy would immediately
   go stale: someone added to the AD group later stays invisible to the teamspace, someone removed from
   AD keeps their teamspace access (and its live `permission-service` role assignment) until manually
   cleaned up — a real, security-relevant staleness gap, not merely a UX inconvenience.
4. **UI**: an "invite by AD group" affordance in `user-ui`'s `TeamspacesPane.tsx`, calling the new
   group-lookup endpoint to preview membership before binding, analogous to the existing invite-by-
   username flow it already has.

## Rationale

- **Why "partially stale," not "fully resolved" or "still fully blocked"**: both extremes would be
  wrong. Treating the blocker as fully gone (the plan's own initial framing) would have led a build
  session to discover mid-implementation that no group-membership-listing capability exists — exactly
  the kind of surprise this project's research-before-planning discipline exists to avoid (the same
  discipline that, e.g., corrected the P42-S1 XDOMEA plan's premise and the P42-S3 Kennzeichen-display
  plan's premise before implementation in this same phase). Treating it as still fully blocked would
  ignore that ADR 0093/0153 genuinely removed the ORIGINAL, broader "no AD/Keycloak group integration at
  all" obstacle ADR 0043 cited — Keycloak groups are now a first-class, actively-used concept in this
  project, just not yet exposed as a raw member list.
- **Why live resolution over a snapshot copy, specifically**: ADR 0093 itself already made and justified
  this exact choice for role mapping — "Keycloak/AD remains the sole source of truth," explicitly
  rejecting a `permission-service`-side `Group`/`GroupMembership` mirror for the same staleness reason
  (ADR 0093, lines ~50-56). Recommending a snapshot copy for teamspace group invitation would silently
  contradict that already-settled project-wide precedent for no good reason — the live/poll approach is
  the only one consistent with it.
- **Why a poll loop rather than resolving lazily on every teamspace access**: lazy per-request resolution
  would mean every teamspace-gated action (not just membership listing) makes an extra live Keycloak
  call, and — more importantly — the actual ENFORCEMENT mechanism today is a `permission-service` role
  assignment (`teamspace-member`, checked by `search-service` and, since ADR 0149, by direct
  `folder-service`/`document-service` access too), not a live check inside `teamspace-service` itself.
  Those assignments must actually EXIST as rows for enforcement elsewhere to work at all — they cannot be
  computed purely on-the-fly at the point of use by a different service. A reconciling poll loop is
  therefore not just a style preference but the only shape that can keep those assignment rows correct
  over time without a push mechanism (Keycloak has none for group-membership changes in this project).
- **Why the gating question for the new group-members endpoint is deliberately left open**: `GET
  /users/lookup` already accepted a real, documented tradeoff (an existence oracle for individual
  usernames, ADR 0043 "Consequences") on the grounds of "everyone should be able to invite." A group's
  full member list is a strictly wider disclosure than a single name's existence, and this project has no
  established precedent yet for how broadly that should be exposed — a genuine design fork for the build
  session, not something a scoping pass should pre-decide.

## Consequences

- **`docs/adr/0043-...md`'s "Group invitation remains completely unbuilt" consequence is corrected, not
  reversed**: it remains true that group invitation is unbuilt, but the REASON is now precisely scoped
  (a specific missing group-member-listing capability, not "no AD/Keycloak integration exists at all,"
  which is no longer accurate since ADR 0093/0153).
- **A future build session inherits a concretely bounded starting point**: one new narrow endpoint (with
  its own gating decision left open), one new mapping table, and an explicit recommendation for the
  sync mechanism (poll-loop reconciliation, not a snapshot) — avoiding the "premature to design before
  the foundation exists" trap this project has repeatedly named and avoided in its other scoping-only
  sessions (ADR 0147 for the XDOMEA handoff, since built in ADR 0159).
- **No code diff in this session** — `auth-service`, `teamspace-service`, `user-ui` are all unchanged.
  `PROGRESS.md` marks this session explicitly as scoping-only, no feature, per Phase 43's own Definition
  of Done.
- **This project's "Keycloak/AD is sole source of truth, never mirrored" principle (ADR 0093) is
  reaffirmed and extended** to a second feature area (teamspace group binding) before that feature is
  ever built, rather than being silently violated by a first implementation attempt reaching for the
  simpler-looking snapshot-copy shortcut.
