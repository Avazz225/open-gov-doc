# 0130 — `PUT /approval-config` self-gated; `POST /roles` gains four-eyes

**Status:** accepted (P32-S1, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 32 Session 1 (post-Phase-31 gap re-analysis — security & RBAC hardening), affects
`permission-service`, `admin-ui`, plus test infrastructure in `auth-service`, `config-service`,
`document-service`, `folder-service`, `migration-service`, `teamspace-service`, `webdav-connector`,
`workflow-service`

## Decision

`PUT /approval-config/{action_type}` is now self-gated behind `admin.user_management`, using the same
`_require_role_management` helper already gating `POST`/`PUT /roles` (ADR 0071) — this reverses
[ADR 0089](0089-approval-settings-ui-config-endpoint-stays-ungated.md)'s "not this session" deferral.
`POST /roles` additionally gains the generic four-eyes mechanism (ADR 0022) for the new
`permission.role.create` action type, wrapping its response in a `RoleActionResult`
(`status`/`role`/`approval_request_id`) — the same envelope shape `POST /role-assignments` has had since
P17-S3 — regardless of whether four-eyes is actually configured for it.

## Rationale

- **The endpoint is genuinely reachable by any authenticated end user, not just a theoretical risk**:
  confirmed directly, not assumed. `gateway-service`'s proxy route
  (`@app.api_route("/api/{service_type}/{path:path}")`) has no path-level allow-list beyond
  `public_routes`/`maintenance_mode_allowed_routes`, neither of which includes `approval-config` — any
  valid bearer token reaches it. `admin-ui`'s `ApprovalSettings.tsx` calls it exactly this way, with no
  client-side capability check (ADR 0089 deliberately chose not to fake one). An authenticated user with
  zero admin capabilities could therefore disable the four-eyes requirement for any action type
  system-wide before this session.
- **ADR 0089's blast-radius concern was real, but was estimated ("over a dozen test suites... eight
  services") rather than mapped call-site by call-site** — this session did the full mapping instead of
  re-estimating: every single PUT to `approval-config` across the repo (9 files across 8 services,
  including `permission-service`'s own suite) plus, once `POST /roles`'s response shape changed
  unconditionally, every production and test caller that read a bare `RoleOut` from it. Mapping first
  turned a "would touch a dozen files" estimate into a concrete, boundable list, most of which the
  project's own existing `ROLE_ADMIN_PRINCIPAL_ID`/`users_admin_headers`-style fixtures already covered
  (see Consequences) — the remaining true gaps were smaller and more specific than the original estimate
  suggested.
- **`admin.user_management` was already the established precedent for exactly this domain**: ADR 0071
  already reasoned that role/scope-lock management is "user/permission management"; toggling four-eyes
  for permission-changing actions (including, now, role creation itself) is squarely the same domain —
  no new capability was created.
- **Why `POST /roles` gets four-eyes now, and why `POST /role-assignments` already had it**: `POST
  /role-assignments` was retrofitted with four-eyes at P17-S3 (14.2 "permission change" pre-configuration
  for the eGov package) but `POST /roles` — arguably the more foundational of the two permission-changing
  actions — never was. Bundled into this same session on explicit user instruction, since both fixes
  touch the same code area and the same test-infrastructure blast-radius analysis applies to both.
- **Response envelope changes unconditionally, not only when four-eyes is active**: same precedent as
  `RoleAssignmentActionResult` (P17-S3) and `ScopeLockActionResult` (P6-S4) — a conditional response shape
  (bare object vs. wrapper depending on config) would make every caller branch on the *installation's*
  current configuration rather than the response's own `status` field.
- **`x_dms_principal` doubles as `initiated_by`, not a body field**: unlike `create_role_assignment`
  (`RoleAssignmentCreate.principal_id`, a person who did not necessarily initiate the request), `RoleCreate`
  has no actor field of its own, and a header is already mandatory here for the baseline capability check
  — reusing it avoids introducing a second identity source for the same request.

## Consequences

- **Automated "get-or-create role" bootstrap flows already carried the same deferral risk that
  `permission.role_assignment.create`'s existing four-eyes wiring always has** — `migration-service`'s
  `apply_role_assignment` and `teamspace-service`'s `_ensure_role` create roles as an unattended,
  system-internal step with no human waiting to approve a deferred request. This is not a new risk
  introduced here: `requires_approval` defaults to `false` per action type until an admin explicitly
  opts in (same as every other four-eyes-eligible action), and any installation that already enabled
  four-eyes for `permission.role_assignment.create` already accepted this exact trade-off for automated
  role-assignment flows. Extending it to role *creation* is consistent, not novel.
- **`teamspace-service` needed a new bootstrap** (`_ensure_bootstrap_permissions`, mirroring
  `config-service`'s/`migration-service`'s own): its `PermissionServiceClient` previously sent no
  `X-DMS-Principal` at all ("deliberately ungated... no technical account needed", a comment this session
  proved wrong) and would otherwise have started failing `POST /roles` with `401` at every fresh
  teamspace creation.
- **`config-service`'s and `migration-service`'s own service principals already had
  `admin.user_management`** from their existing bootstraps (`_REQUIRED_ROLE_NAMES` already included
  `"domain-admin-users"` in both, for unrelated prior reasons) — both were safe for the RBAC gate without
  any change; only their response-shape unwrapping needed a fix (`migration-service`'s `dms_client.py`)
  or turned out to need no fix at all (`config-service`'s `apply_roles` never reads `create_role`'s return
  value).
- **`auth-service`'s bootstrap fixture is a genuine chicken-and-egg case**: its
  `_bootstrap_domain_admin_role_assignments` runs before any `TechnicalAccount` exists — it IS the
  bootstrap. Resolved with a dedicated, test-only principal granted via the still-ungated `POST
  /role-assignments` (the same ADR 0023 carve-out that lets domain-admin accounts bootstrap themselves
  in production), not by reusing a real domain-admin account that doesn't exist yet at that point.
- **A pre-existing, unrelated latent bug found and fixed while here**: `webdav-connector`'s
  `_grant_document_write` test helper called `POST /roles` with no `X-DMS-Principal` at all — already
  broken against ADR 0071's gate (predates this session), just never exercised in a way that surfaced it.
  Fixed alongside since gating `PUT /approval-config` required touching this exact function anyway.
- **`admin-ui`'s `createRole` return type changes** from `Role` to a new `RoleActionResult`
  (`status`/`role`/`approval_request_id`); `UserManagement.tsx` gained a `rolePending` state and hint,
  mirroring the existing `assignmentPending` pattern for role assignments.
- **Tests**: `permission-service` gains gating tests for `PUT /approval-config` (401/403) and a
  `permission.role.create` four-eyes defer/consume pair (API + consumer); `admin-ui` gains two role
  four-eyes tests (created/pending-approval) mirroring the existing role-assignment pair; every affected
  service's test suite updated to pass `X-DMS-Principal` on `PUT /approval-config` calls and to unwrap
  `["role"]` from `POST /roles` responses.
- **`docs/services/permission-service.md` "Open Points"** loses the item this ADR closes; a new item
  notes `PUT /roles/{id}` (role *update*) still has no four-eyes wiring, deliberately out of this
  session's scope (not requested, and update — unlike creation/assignment — has no direct precedent to
  mirror).
