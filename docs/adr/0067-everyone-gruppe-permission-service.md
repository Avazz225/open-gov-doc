# 0067 — "everyone" group in permission-service

**Status:** accepted (session 2 of 11, see phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 19 session 2, affects `permission-service`

## Decision

`RoleAssignment.principal_type` previously only had the comment `# "user" | "group"` as a stated
intent — `"group"` was never evaluated by `_collect_effective_roles`, such a value was effectively
dead schema. This session turns `principal_type="group"` for exactly one reserved value
(`principal_id="everyone"`) into real functionality:

1. **`repository._collect_effective_roles`** now checks, at every resource node traversed, in
   addition to the existing `RoleAssignment.principal_id == principal_id` condition, whether a
   `(principal_type="group", principal_id="everyone")` assignment exists — via an `or_`/`and_`
   extension of the existing query, no second query. Every caller (regardless of its own
   `principal_id`) is thereby implicitly treated as a member of the "everyone" group.
2. **New `repository.ensure_everyone_role`** (bootstrap, same idiom as `ensure_domain_admin_roles`):
   idempotently creates a `Role("everyone", permissions=["users.lookup", "users.directory"])` AND the
   corresponding `RoleAssignment(principal_type="group", principal_id="everyone", resource_id=ROOT)` —
   unlike domain admin roles, "everyone" has no external account to which the assignment would
   otherwise be attributed, so the assignment itself is seeded, not just the role.
3. **`schemas.RoleAssignmentCreate`/`RoleAssignmentOut`'s `principal_type`** tightened from `str` to
   `Literal["user", "group"]` — matches the already established `Literal` style of this file
   (`BatchCheckRequest.access_type`, `RoleAssignmentActionResult.status`) and makes "group" visible
   as a real, validated value instead of a mere comment. **Subsequently corrected in P19-S3** (ADR
   0068): `"service"` is a third, already production-used `principal_type` (`config-service`,
   `migration-service`) — the original `Literal["user", "group"]` was too narrow and would have
   rejected their role assignments, see ADR 0068 "A real bug found".
4. **`main.py`'s lifespan** calls `ensure_everyone_role` directly after `ensure_domain_admin_roles`.

## Rationale

- **Why exactly these two permissions (`users.lookup`, `users.directory`) are seeded**: they
  correspond to the two endpoints currently hardcoded open in `auth-service` (`GET /users/lookup`,
  `GET /users/directory` — both deliberately WITHOUT an `admin.user_management` gate, see their
  docstrings, "every authenticated user may..."). This session does NOT yet change `auth-service`
  itself (that's P19-S3) — the role is only prepared here with the matching permission names so that
  P19-S3 can directly call `has_permission(principal_id, "users.lookup")` without having to create a
  role itself first.
- **Why the assignment is made directly against the session instead of via the (four-eyes-capable)
  `POST /role-assignments` endpoint**: `ensure_everyone_role` runs as bootstrap infrastructure in
  the lifespan, exactly like `ensure_domain_admin_roles` always has - this is not a runtime admin
  action, an approval requirement would be inappropriate here (and would never be satisfiable on a
  freshly installed instance for lack of a second admin).
- **Why `principal_id="everyone"` instead of a dedicated group concept with multiple named
  groups**: the roadmap deliberately only foresees ONE reserved group identifier ("every
  authenticated principal is implicitly a member") - a full group management system (custom
  groups, membership management) is not part of this session and is not planned for phase 19
  according to the roadmap either. `principal_type="group"` remains open as a schema field for a
  later extension, without this decision preempting it.
- **Why no change to `auth-service` in this session**: the roadmap deliberately separates "build
  the mechanism" (P19-S2) from "actually replace the existing bypass" (P19-S3) — smaller,
  independently verifiable sessions instead of one large combined change.

## Consequences

- **Real behavioral change in `permission-service` itself**: EVERY principal (even one never seen
  before, an arbitrary string) has, as of now, `users.lookup`/`users.directory` in its effective
  permissions at the root resource — confirmed live against the real stack (`GET
  /effective-permissions/<never-seen principal>/root` returns `roles: ["everyone"]`,
  `permissions: ["users.directory", "users.lookup"]`). Two existing `permission-service`-owned
  API tests (`test_full_flow_via_api`, `test_list_role_assignments_filters_by_principal_id`)
  previously checked exact equality against an empty or role-specific permission set at the
  root resource — both adjusted to include the new "everyone" baseline, not a behavioral bug.
- **No other service is affected**: `users.lookup`/`users.directory` are new permission strings not
  previously used anywhere in the project (confirmed via grep) - every existing `/check`/`/check/batch`
  call checks a DIFFERENT, specific permission and thereby remains unchanged, regardless of the
  underlying `permissions` list now containing two additional entries.
- **Verified idempotent across a real restart**: `docker compose restart
  permission-service` run twice in a row (after an image rebuild) produces no duplicate `everyone`
  roles/assignments (same `id` before/after the second restart).
- **`auth-service`'s hardcoded bypasses (`GET /users/lookup`, `GET /users/directory`) remain
  unchanged until P19-S3** — this session only delivers the foundation, no enforcement.
