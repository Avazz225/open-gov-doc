# 0120 — Org-hierarchy foundation: supervisor DAG, groups reused as org units

**Status:** accepted (P31-S9, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 9 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gaps #7/#10), affects
`permission-service`, `admin-ui`; prerequisite for P31-S10 (dynamic org-hierarchy-based access grants) and
P31-S11 (supervisor/team task oversight view)

## Decision

`auth-service`/`permission-service` previously modeled only flat group membership (`Group`/
`GroupMembership`, Post-Roadmap Phase 22 Session 2) and the "everyone" implicit group (Phase 19 Session 2)
— no concept anywhere in the system captured "who supervises whom". A research pass across `auth-service`,
`permission-service`, `teamspace-service`, and the existing self-service delegation mechanism (ADR 0048)
confirmed this is genuinely new ground, not a rename of something that already existed.

Per the plan's own explicit flag ("validate the data model against real org-chart shapes — single vs.
multiple supervisors — before committing"), this decision was put to the user directly rather than assumed.
**Two shape decisions were made, both by explicit user choice**:

1. **A principal may have more than one direct supervisor** (dotted-line/matrix reporting) — the model is a
   DAG, not a tree. `SupervisorAssignment` (`permission-service`) is a simple edge table (`principal_id`,
   `supervisor_principal_id`), not a single nullable `supervisor_id` column on some principal-scoped row.
2. **Org units reuse the existing `Group`/`GroupMembership` concept** rather than introducing a second,
   competing grouping mechanism. This session makes no code change for that half — it is a forward-looking
   scope decision that shapes what P31-S10 will build on, recorded here so the reasoning isn't lost between
   sessions.

New in `permission-service`: `SupervisorAssignment` model, `POST`/`GET`/`DELETE /supervisor-assignments`
(direct edges, optionally filtered by either endpoint of the relationship), `GET /supervisor-chain/{id}`
(the full transitive union of supervisors, `repository.get_supervisor_chain`, a breadth-first walk over the
DAG). A minimal admin-ui touchpoint (`UserManagement.tsx`, `/users/`, new "Organisations-Hierarchie"
section) provides create/list/delete for assignments plus a chain-lookup tool — the same page groups are
managed on, following that precedent exactly.

## Rationale

- **`permission-service`, not `auth-service`, owns this table**: `auth-service` has no local user table at
  all for regular Keycloak-backed accounts (only `TechnicalAccount`, Phase 18, which has no hierarchy
  fields and isn't the right home for org data about arbitrary principals). `permission-service` already
  owns `RoleAssignment`/`Delegation`/`Group` and the only existing runtime principal-resolution machinery
  (`_collect_effective_roles`) — P31-S10's grant logic will need to extend exactly that machinery, so
  building the supervisor concept anywhere else would mean a needless cross-service round trip for every
  resolution.
- **A DAG over a tree, because the user chose it, and the plan's own wording ("single vs. multiple
  supervisors") anticipated exactly this fork**: a single-supervisor tree is simpler (chain = one linear
  parent-pointer walk, matching `ResourceNode`'s existing single-parent hierarchy) but doesn't match real
  matrix/dotted-line reporting. The user chose the more expressive shape knowingly, accepting the added
  complexity (cycle prevention, BFS over a set-valued frontier instead of a linear walk) as the price for
  matching real org charts rather than a simplification of convenience.
- **Cycle prevention at write time, not just read time**: `create_supervisor_assignment` rejects
  self-supervision (`422`) and any edge that would close a loop (`409`) — checked by computing the
  candidate supervisor's *existing* chain and testing membership, not by attempting the insert and
  catching a downstream failure. A cycle would make `get_supervisor_chain`'s BFS either loop forever
  (without its own defensive visited-set) or, worse, silently return a principal as its own supervisor to
  P31-S10's grant logic — corrupting the very thing this session exists to make trustworthy. The BFS
  keeps the visited-set guard anyway, defensively, since write-time prevention and read-time robustness are
  cheap to have both.
- **`get_supervisor_chain` returns a union of every upward path, not a single line**: with multiple direct
  supervisors possible, "the supervisor chain" (the plan's own phrase, used again verbatim in P31-S10's
  description) is necessarily a set, not a sequence — two supervisors whose own chains reconverge (a
  diamond shape) contribute their shared ancestor only once. This directly determines how P31-S10 must
  interpret "grant the full supervisor chain access": as a set of grantees, not an ordered escalation path.
- **Same self-gating as `POST`/`DELETE /groups`**: `admin.user_management` via the existing
  `_require_role_management` — org-structure data is administered the same way as groups and roles, not
  self-service like `POST /delegations` (which a person grants about their own tasks). `GET` endpoints
  (listing, chain lookup) stay ungated, matching `GET /groups`/`GET /groups/{id}/members`/
  `GET /role-assignments` — the data reveals org shape, nothing more sensitive than group membership
  already exposes.
- **No transitive "who reports to me, all the way down" resolution built in this session**: P31-S11 only
  needs *direct* reports (`GET /supervisor-assignments?supervisor_principal_id=`) per the plan's own
  wording ("every open workflow task across their direct reports"). A downward transitive walk would be
  the same BFS shape reversed, genuinely cheap to add later if a future session needs it — not built now
  since nothing calls for it yet.
- **No admin-ui page for org-unit management**: since org units are the existing `Group`/`GroupMembership`
  concept (decision 2 above), the existing Group Management section already covers it — a second UI for the
  "same" data would be confusing, not additive.

## Consequences

- **Groups gain no `is_org_unit` marker or similar flag in this session** — a principal belonging to
  several unrelated ad-hoc groups has no way, from this data alone, to say which one (if any) is their
  "org unit" for P31-S10's "grant the assignee's/creator's org unit access" resolution. This is a real,
  deliberately deferred design question for P31-S10, not silently solved here by fiat.
- **The DAG shape means P31-S10 must decide how to treat multiple direct supervisors for its "grant the
  assignee's supervisor access" option** — grant to all direct supervisors, or pick one? This session
  makes the chain-union behavior for the *chain* option unambiguous but does not resolve the analogous
  question for the *direct supervisor* option; left for P31-S10's own design pass.
- **No cross-service validation that `principal_id`/`supervisor_principal_id` are real, currently-existing
  accounts** — same, consistent gap as `Group.principal_id`/`RoleAssignment.principal_id` throughout this
  service; a typo'd principal ID is silently accepted and simply never matches anything at resolution time.
- **No bulk-import path** (e.g. from an existing HR/AD org-chart export) — every edge is created one at a
  time via the API/admin-ui, consistent with this project's established minimal-MVP precedent for
  admin-managed relationship data (see the hand-folder reference modal, ADR 0118, "no document picker exists
  anywhere in this codebase yet").
