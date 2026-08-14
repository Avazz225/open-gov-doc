# 0068 — `GET /users/lookup`/`GET /users/directory` gated via the "everyone" group

**Status:** accepted (session 3 of 11, see phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 19 session 3, affects `auth-service`, `permission-service`

## Decision

Building on [ADR 0067](0067-everyone-gruppe-permission-service.md) (real "everyone" group in
`permission-service`), this session replaces the two hardcoded "every authenticated user
may..." bypasses in `auth-service` with a real permission check:

1. **`GET /users/lookup`/`GET /users/directory`** now call, before the actual logic,
   `_require_permission(user, "users.lookup" | "users.directory", ...)` — a new, generic
   helper (`main.py`) that checks `app.state.permission_client.has_permission(...)` and returns
   `403` on `False`. `_require_user_management` (the existing domain admin gate helper) now
   internally calls this same generic helper instead of duplicating the three lines.
2. **Nothing changes in actual behavior**: the "everyone" role seeded in P19-S2 continues to grant
   `users.lookup`/`users.directory` to every authenticated principal by default — the
   difference is purely structural: an admin can now revoke this permission via the (future,
   P22 bundle) role management, without changing code.

## A real bug found and fixed (ADR 0067's `Literal` tightening was too narrow)

ADR 0067 tightened `schemas.RoleAssignmentCreate`/`RoleAssignmentOut.principal_type` from `str` to
`Literal["user", "group"]`. The existing test run for `auth-service`/`permission-service` stayed
green — but `config-service` and `migration-service` already use a THIRD value in production,
`principal_type="service"` (technical accounts without a Keycloak account, e.g. `config-service`'s
`_CONFIG_ADMIN_PRINCIPAL_ID`), only discovered during this session's regression run (`test_realm_roles.py`'s
`authorized_principal` fixture in `auth-service` also uses `"service"` and failed with `KeyError:
'role_assignment'`, since `POST /role-assignments` newly returned `422` instead of the expected
`RoleAssignmentActionResult`). Fixed by extending it to `Literal["user", "group",
"service"]` — the original intent (real validation instead of a mere comment) is
preserved, only extended to include the already-used third value.

## A second, independent bug found and fixed (`update_role` didn't invalidate the cache)

While developing a negative test (deliberately removing a permission from the "everyone" role,
expecting `403`), it was noticed: `permission_service.repository.update_role` (`PUT /roles/{id}`) —
unlike EVERY other permission-changing operation in this module (`create_role_assignment`,
`delete_role_assignment`, `set_resource_inherit`) — did not call `invalidate_cache()`. An
already cached principal would only have lost a permission revoked via a role update after an
independent, random cache clear (e.g. a completely unrelated role assignment somewhere else in the
system). Uncritical as long as roles were edited rarely and without an acute security expectation —
with the "everyone" group (ADR 0067, this session), an admin now potentially edits this specific
role DELIBERATELY, to immediately revoke a permission from a principal. Fixed via an
`invalidate_cache()` call at the end of `update_role`, with a regression test. Confirmed live
against the real stack: `PUT /roles/{id}` (everyone without `users.lookup`) takes effect on the
IMMEDIATELY next `GET /users/lookup` call through the gateway, no restart needed.

## Rationale

- **Why a generic `_require_permission` helper instead of two further copies of
  `_require_user_management`**: three near-identical 6-line blocks would have been pure
  duplication - the generic helper takes `permission`/`message` as parameters,
  `_require_user_management` itself becomes a thin caller of it.
- **Why no negative test via a real, permanently missing permission on a newly created user was
  possible**: the "everyone" role applies to EVERY principal, even a freshly created one -
  there is no user who lacks it. The negative test therefore must temporarily manipulate the
  shared "everyone" role itself (new `everyone_role_without` fixture in `auth-service/tests/conftest.py`,
  same restoration pattern as `role_assignment_immediate`).

## Consequences

- **Tests**: `auth-service` 92 (previously 90, +2 new negative tests for `lookup`/`directory` - the two
  existing positive tests remain unchanged and green, since the "everyone" role reproduces the
  current behavior by default). `permission-service` 125 (previously 124, +1: `update_role` cache
  invalidation; the `Literal` fix itself needed no new test, only the extension of the existing
  type annotation). `config-service`/`migration-service` unchanged and green after the `Literal` fix.
- **Fully verified live against the real running stack** (after rebuilding both service images):
  `GET /users/lookup`/`GET /users/directory` through the gateway work unchanged (200) with the
  default permissions; after deliberately revoking `users.lookup` from the "everyone" role via `PUT
  /roles/{id}`, the same call immediately returns `403` (`{"detail": "Fehlende Berechtigung 'users.lookup'
  (everyone-Gruppe entzogen?)"}`), `GET /users/directory` remains unaffected (`users.directory` not
  revoked) — proving both the actual enforcement and the cache invalidation fix
  in a single call. Restored afterward.
- **An already pre-existing, independent problem in `config-service`'s test suite discovered, NOT
  fixed** (outside this session's scope): `permission.role_assignment.create` is configured as
  requiring four-eyes approval on this real installation (already documented by
  `auth-service`'s P18-S3 session), `config-service/tests/conftest.py::authorized_principal` does
  not (unlike `auth-service`'s `role_assignment_immediate`) temporarily disable this — 11 tests
  fail as a result, independently confirmed (the failure disappears entirely when the approval
  requirement is temporarily disabled, reappears after restoration). Not part of this session (a
  different service, different test infrastructure) — documented as a known, pre-existing point,
  analogous to the webdav-connector PROPFIND timeout from P18-S3.
