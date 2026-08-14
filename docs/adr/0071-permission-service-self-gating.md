# 0071 — permission-service self-gating (roles/scope locks)

**Status:** accepted (session 6 of 11, see phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 19 session 6, affects `permission-service`, `config-service`,
`migration-service`

## Decision

`permission-service` previously checked no permission at all on two groups of its own
security-relevant endpoints:

1. **`POST /roles` and `PUT /roles/{id}`** — any arbitrary, unauthenticated caller could
   previously create new roles with arbitrary permissions or rename/reconfigure existing ones.
2. **`POST /scope-locks` and `DELETE /scope-locks/{id}`** — already four-eyes-capable (P6-S4), but
   without an RBAC pre-check: anyone could trigger/release a scope lock,
   regardless of the four-eyes configuration.

Both now get a real `admin.user_management` check:

- **`_require_role_management(session, x_dms_principal)`** (new helper in `main.py`) — `401` without
  the `X-DMS-Principal` header, otherwise `repository.require_capability(session, x_dms_principal,
  "admin.user_management")`, `403` on denial. Used by `create_role`/`update_role`.
- **Scope locks use `payload.locked_by`/`payload.released_by`** instead of a header — both fields
  already existed as the actor source for the four-eyes `initiated_by` logic, a second,
  inconsistent identity source in the same endpoint would have been unnecessary. The
  capability check runs **before** the existing `get_approval_config` branch, otherwise an
  unauthorized caller could trigger a `pending_approval` request without holding the
  base capability at all.
- **`GET /roles` remains deliberately ungated** — plain readability is assumed by `dms-permission-client`'s
  `get_role_id` and various other services (role get-or-create by name).
- **`POST /role-assignments` remains deliberately ungated** — see "Rationale" below (ADR 0023).

**Three real consumers had to be updated**, since they already held the necessary capability at
their own bootstrap, but (unlike their sibling clients) previously did not send an
`X-DMS-Principal` header:

- `config-service`'s `PermissionServiceClient` (`clients.py`) — header `config-service` added. No
  bootstrap addition needed: `_REQUIRED_ROLE_NAMES` already included `"domain-admin-users"` since P17-S1.
- `migration-service`'s `LocalDmsClient` (`dms_client.py`) — header `migration-service` added (affects
  both scope-lock methods AND `apply_role_assignment`'s role get-or-create).
- `migration-service`'s own bootstrap (`main.py::_ensure_config_admin_permission`) — previously only
  `"domain-admin-config"` (`admin.object_config`), extended with `"domain-admin-users"`
  (`admin.user_management`).

## Rationale

- **Why `admin.user_management` instead of a new capability**: `config-service` already holds it
  (no new grant needed), and semantically role/scope-lock management is "user/permission
  management" — the same choice as `auth-service`'s `_require_service_user_management` for
  realm roles.
- **Why `POST /role-assignments` is NOT gated**: ADR 0023's chicken-and-egg case explicitly
  concerns this endpoint — `auth-service`'s bootstrap creates the very first role assignment for
  `users-admin` via it, which does not yet hold any permission itself (phase 18's technical
  accounts solve this problem for login/break-glass, not for this one bootstrap step). Research
  before this session confirmed: role *creation* itself (`POST /roles`) does not depend on an
  HTTP bootstrap path for ANY service — every service creates its startup roles directly against
  `repository`, never via its own HTTP API. The chicken-and-egg problem thus does not exist for
  `/roles`, so this session can safely gate it without touching ADR 0023's exception.
- **Why scope locks use a body field instead of a header**: `ScopeLockCreate.locked_by`/
  `ScopeLockRelease.released_by` are already the established actor source in these two
  endpoints (four-eyes `initiated_by`) — an additional, independent `X-DMS-Principal`
  header would have created two competing identity sources in the same request.
- **Known, accepted consequence**: `permission-service` (unlike, e.g., `document-service`) has
  **no generic superuser bypass** for `require_capability` — only `POST
  /maintenance-mode/lift` has a hardcoded superuser special-case check. A break-glass superuser
  without an explicit `admin.user_management` assignment could therefore, after this session, no
  longer create roles or set scope locks. That is a larger, architectural change outside this
  session's scope — documented, not fixed.

## Consequences

- **Tests**: `permission-service` 128 (previously ~122, threading the new `role_management_headers`
  fixture through `test_api.py` plus two new negative tests, `_grant_permission_via_api` helper
  extended with a `headers` parameter; additionally two tests in `test_scope_lock_events.py` caught
  up with a local `_grant_scope_lock_permission` helper function). `config-service` 48, `migration-
  service` 8 — both unchanged and green after the header fix. `ruff check`/`ruff format --check` clean for
  all three services.
- **Fully verified live against the real running stack** (after image rebuild of all three
  services + restart): `POST /roles` without a header → `401`; with the wrong principal → `403`; with
  `X-DMS-Principal: config-service` → `200`. `POST /scope-locks` with an empty `locked_by` → `403`.
  `migration-service`'s own scope-lock acquisition/release cycle (`locked_by="migration-service"`
  plus the `X-DMS-Principal` header from the client constructor) → succeeded. Both services'
  bootstrap logs show no `*_role_missing` warning, both hold `domain-admin-users` per `GET
  /role-assignments?principal_id=...` after the restart.
- **A data cleanup finding during live verification**: `permission.role_assignment.create` was
  still set to `requires_approval=true` in the running development database from an earlier,
  manual live verification — broke `config-service`'s own `authorized_principal` test fixture
  (which expects an *immediate* role assignment). Unrelated to this session, but blocked its
  test run against the real stack — reset to `false`.
- **No general superuser bypass for `require_capability`** (see "Rationale") — remains open,
  noted in `docs/services/permission-service.md` "Open Points".
