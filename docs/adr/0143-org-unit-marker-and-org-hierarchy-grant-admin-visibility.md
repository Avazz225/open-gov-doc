# 0143 — `Group.is_org_unit` marker and admin visibility for org-hierarchy grants

**Status:** accepted (P35-S1, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 35 Session 1 (org-hierarchy & workflow polish, first session), affects `permission-service`/`admin-ui`

## Decision

`Group` gains a real `is_org_unit: bool` column (default `false`), settable via a new `PATCH /groups/{id}`
endpoint (the first update endpoint a `Group` has ever had). `create_org_hierarchy_grant`'s
`grant_kind="org_unit"` branch now resolves the deputy set from only the principal's `is_org_unit=True`
group membership(s) — previously EVERY group the principal belonged to, unioned, an explicitly
acknowledged pragmatic stand-in from ADR 0120/0121. Separately, every `Delegation` row created by
`create_org_hierarchy_grant` is now stamped with `grant_kind` (`"supervisor"`/`"supervisor_chain"`/
`"org_unit"`, `null` for a self-service delegation) — closing the other gap ADR 0121 named ("indistinguishable
from a self-service delegation except by inspecting `scope_process_definition_ids`' shape"). `admin-ui`'s
`UserManagement` (Groups section) and `DelegationsAdmin` pages surface both: a checkbox/toggle for
`is_org_unit`, and an "Herkunft" (origin) column + filter for `grant_kind`.

## Rationale

- **Closes two gaps both prior ADRs named in advance, by their exact field names** — ADR 0120's own
  Consequences predicted "a principal belonging to several unrelated ad-hoc groups has no way, from this
  data alone, to say which one (if any) is their 'org unit'" and named `is_org_unit` as the missing marker;
  ADR 0121's own Consequences named the admin-visibility gap. This session resolves both exactly as
  scoped, not by inventing new requirements.
- **A real behavior change for `grant_kind="org_unit"`, not purely additive** — before this session, EVERY
  group a principal belonged to counted toward the deputy set; after, only `is_org_unit=True` groups do,
  and a principal in no flagged group now yields an empty deputy set instead of granting through whichever
  unrelated groups happened to exist. This is the entire point of adding a real flag rather than a
  cosmetic label: the old behavior was silently over-broad (e.g. a "circulation folder buddies" group with
  no organizational meaning would previously have counted as someone's "org unit"). Existing installations
  see no behavior change until an admin deliberately flags a group — the column defaults `false`, no
  existing group is auto-promoted.
- **`grant_kind` on `Delegation`, not a separate table** — the existing `Delegation` row already carries
  everything else about an org-hierarchy grant (delegator, deputy, scope, validity window); adding one
  nullable string column is the minimal change that makes the row self-describing, avoiding a parallel
  "org hierarchy grant" table that would just duplicate `Delegation`'s own shape (the same reasoning
  `create_org_hierarchy_grant` itself already used to justify reusing `create_delegation` rather than a
  new creation path).
- **`PATCH /groups/{id}` accepts only `is_org_unit`, not `name`/`description`** — the plan's own ask is
  specifically an editable flag; `name` is unique-constrained and `description` has never needed editing
  in this project's Group UI, so widening the endpoint's scope beyond what's actually needed was avoided.
- **The admin-ui origin filter is client-side, not a new backend query parameter** — `DelegationsAdmin`
  already loads every delegation installation-wide unfiltered (a genuinely small admin dataset, same
  reasoning as the existing unfiltered `GET /delegations` call); adding server-side filtering would be
  extra plumbing for a page that already has all the data in hand.
- **Naming**: `is_org_unit` (with the `is_` prefix) deliberately keeps the plan's own literal name, even
  though the codebase's existing boolean columns on sibling models favor prefix-less names (`inherit`,
  `active`, `blocks_read`, `requires_approval`) — both ADR 0120's and ADR 0121's own text, and
  `docs/services/permission-service.md`'s "Open Points", already refer to the missing marker by this exact
  name, so keeping it avoids a needless rename mid-thread.

## Consequences

- **Tests**: `permission-service` 164 tests (up from 158, +6) — a new test proving membership in an
  UNFLAGGED group yields zero deputies (the actual behavior-change regression guard), `PATCH /groups/{id}`
  toggles the flag (plus auth/403/404 cases), and a self-service delegation's `grant_kind` is `null`; the
  existing `org_unit` grant test was additionally rewritten to flag its group (no longer the default
  everyone-counts behavior). `admin-ui` 232 tests (up from 228) — 2 new `user-management.test.tsx`
  cases (creating a flagged group, toggling an existing group's flag) and 2 new `delegations-admin.test.tsx`
  cases (the origin label for both a self-service and an org-hierarchy-derived row, the filter checkbox).
- **A CSS gap found and fixed while live-verifying**: a `.hint` placed directly inside a `.form-grid`
  (explaining the new checkbox) became its own narrow, misplaced grid cell under the existing
  `grid-template-columns: repeat(auto-fit, minmax(160px, 1fr))` layout — fixed with a new
  `.form-grid > .hint { grid-column: 1 / -1; }` rule, the same full-width technique
  `.deletion-reason-catalog` already established. Caught only because this session's Definition of Done
  requires an actual browser screenshot, not just passing component tests (jsdom has no real layout
  engine, so the misplacement was invisible to Vitest).
- **Live-verified in a real browser** against the real, rebuilt running stack: flagged a real group as an
  org unit via the UI, confirmed the "Ja"/"Nein" badge-button toggle round-trips through `PATCH
  /groups/{id}` and back through a reload, screenshotted the fixed full-width hint layout. Separately
  seeded a REAL org-hierarchy grant (a real `SupervisorAssignment` + a real `POST /org-hierarchy-grants`
  call against the running `permission-service`) and confirmed `DelegationsAdmin`'s new "Herkunft" column
  correctly showed "Org-Hierarchie: Vorgesetzte/r" for it alongside "Selbstverwaltet" for existing
  self-service rows, and that the filter checkbox hid the self-service rows while keeping the
  org-hierarchy one visible — screenshotted. Test artifacts cleaned up afterward (the seeded
  `SupervisorAssignment` deleted; the seeded `Delegation` left to expire on its own 4-hour window rather
  than force a `dms-admin`-role account into this session purely for cleanup).
- **`is_org_unit` is not retrofitted onto any existing group** — every current group defaults to `false`;
  an admin must deliberately opt each org unit in. This is a conscious choice (see Rationale), not an
  oversight — no migration/backfill logic was written.
- **Still deferred**: automatic AD-group-to-org-unit inference, and any UI for bulk-flagging several
  groups at once, remain out of scope — matches this project's existing "no automatic AD sync" deferral
  (`docs/services/permission-service.md` "Open Points").
