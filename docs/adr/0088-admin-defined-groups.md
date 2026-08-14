# 0088 — permission-service: admin-creatable groups with real membership

**Status:** accepted (Post-roadmap Phase 22 Session 2)
**Context:** Post-roadmap Phase 22 Session 2, affects `permission-service`, `admin-ui`

## Decision

`RoleAssignment.principal_type="group"` had, since Phase 19 Session 2 ([ADR 0067](0067-everyone-gruppe-permission-service.md)),
been effective for exactly one reserved value (`principal_id="everyone"`, every authenticated principal
implicitly a member, no dedicated data row) — any other `"group"` value remained pure schema decoration,
never evaluated. This session adds real, admin-creatable groups with explicit membership:

1. **Two new tables**: `group` (`id` UUID string, `name` unique, `description`, `created_at`) and
   `group_membership` (`id`, `group_id` FK, `principal_id`, unique on `(group_id, principal_id)`).
2. **`_collect_effective_roles` extended**: before walking the resource ancestor chain, it collects once
   all `group_id`s the requested `principal_id` belongs to via `group_membership`, and at every node,
   in addition to `principal_id == principal_id` and the "everyone" condition, treats any assignment with
   `principal_type="group", principal_id IN (member groups)` as matching.
3. **New endpoints**: `POST`/`GET`/`DELETE /groups`, `GET`/`POST /groups/{id}/members`,
   `DELETE /groups/{id}/members/{principal_id}` — `POST`/`DELETE` gated via the same
   `_require_role_management` (`admin.user_management`) as `POST`/`PUT /roles` ([ADR 0071](0071-permission-service-self-gating.md)),
   `GET` endpoints deliberately remain ungated (same rationale as `GET /roles`).
4. **`apps/admin-ui`**: new "Groups" section in `UserManagement.tsx` (`/users/`) — create, delete,
   expandable member list per group (add via free-text `principal_id` entry, remove per row).

## Rationale

- **Why a dedicated table instead of extending the `"everyone"` pattern**: "everyone" deliberately has NO
  dedicated membership row (every principal is implicitly considered a member) — that pattern cannot be
  transferred to "a bounded, admin-defined subset of principals" without maintaining a real membership
  list. `group`/`group_membership` are therefore complementary to, not a replacement for,
  `EVERYONE_PRINCIPAL_ID`.
- **Why `_group_ids_for_principal` is resolved BEFORE the loop over the ancestor chain, not freshly per
  node**: a principal's membership is independent of the resource currently being queried — resolving it
  once per `_collect_effective_roles` call avoids N identical database queries for a deep resource
  hierarchy without changing the semantics.
- **Why deleting a group performs no reference check against existing `RoleAssignment` rows**:
  `role_assignment.principal_id` is a free string, not an FK to `group.id` (`principal_type` can equally
  be `"user"`/`"service"`) — a reference check would pretend an FK relationship that doesn't exist. An
  orphaned assignment simply matches no principal after deletion (empty member list), identical behavior
  to a group that was never assigned a member. Consistent with `Role`, which likewise has no delete
  endpoint/reference check.
- **Why adding a member is idempotent instead of reporting a conflict**: matches this service's otherwise
  deliberately low-friction style (cf. `ensure_everyone_role`, which likewise checks-before-creating
  instead of relying on a DB unique-constraint exception) — an admin who accidentally clicks "add" twice
  should not see an error.
- **Why the same capability (`admin.user_management`) instead of a new, dedicated groups capability**:
  groups are another building block of rights management, not a standalone domain — same rationale as
  ADR 0071 for `POST`/`PUT /roles`. A finer split (e.g. "may create groups but not roles") is not part of
  this session, could be added later if needed.
- **Why no automatic AD group synchronization**: that's a separate, larger feature ("AD group → internal
  role mapping rule engine", planned as a standalone **Phase 24 Session 2**) — this session delivers only
  the admin-manual foundation (real groups with membership) on which a future automatic synchronization
  could build, without itself synchronizing anything.

## Consequences

- **Migration**: none (two brand-new tables, `Base.metadata.create_all` creates them automatically — no
  `ALTER TABLE` needed since no existing table is modified).
- **Test infrastructure bug found and fixed**: `tests/conftest.py`'s `_clean_tables` fixture lists the
  tables to truncate in a fixed `TRUNCATE` statement instead of deriving them from `Base.metadata` — the
  two new tables were initially missing there. A first test run happened to pass (no name collision
  within that run), a second, independent run failed with `UniqueViolationError` on `group.name`, since
  groups from the first run had survived in the test DB. Fixed by adding `permission.group_membership`/
  `permission.group` to the `TRUNCATE` list, after which two consecutive runs were confirmed green.
- **Cache invalidation**: every membership/deletion change clears the entire `effective_permission_cache`
  (same coarse-grained strategy as every other permission-changing operation in this service, see
  README/docstring on `EffectivePermissionCache`) — a removed member loses permissions held via the group
  immediately, confirmed live (see below).
- **Tests**: `permission-service` 137 (previously 128, +9: create/list/delete groups including
  authentication/authorization checks, add/idempotent-add/remove member including `404` cases, and the
  core test `test_group_membership_grants_role_to_every_member` — a single role assignment to a group
  with two members grants the permission to both, a non-member remains unaffected, removing a member
  revokes the permission immediately). `admin-ui` 179 (previously 175, +4).
- **Verified live against the actual running stack** (image rebuild + restart of
  `permission-service`/`admin-ui`): a group was created live, two principals added as members, a new role
  assigned ONCE to the group (not to each principal individually) — `GET /check` confirmed the permission
  for BOTH members and its absence for a non-member; removing a member revoked the permission immediately
  (cache invalidation confirmed), the remaining member kept it; group deletion confirmed via
  `GET /groups`. No interactive browser test of the new admin UI section (no browser/Playwright available
  in this development environment, project-wide established practice) — instead covered via Vitest
  component tests plus the backend API verification through the exact same gateway calls.
- Docs: new [ADR 0088](0088-admin-defined-groups.md), `docs/services/permission-service.md`
  (API table, data model, new section "Admin-Creatable Groups", "Open Points" partially marked resolved),
  `docs/services/admin-ui.md` (page table, new section "Group Management", backend integration table,
  tests section) added.
