# 0065 — Domain admin migration to local technical accounts

**Status:** accepted (session 3 of 3, see phase 18 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 18 session 3, affects `auth-service`

## Decision

Building on [ADR 0063](0063-auth-entkopplung-lokale-technische-konten-dual-issuer-jwt.md) (local
token issuer) and [ADR 0064](0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)
(superuser migration), this session migrates the two remaining technical accounts
(`users-admin`/"user management", `config-admin`/"workflow configuration") from Keycloak users to
`TechnicalAccount` rows:

1. **`bootstrap.py`'s `_ensure_domain_admin_accounts(admin: KeycloakAdmin)` removed** - account
   creation now happens in `main.py`'s async lifespan, right next to the superuser (ADR 0064), via a
   new `domain_admins.py` module (`ensure_domain_admin_account`, `get_technical_account_id`) -
   structurally almost identical to `superuser.py`, with two differences: `enabled=True` immediately
   (no break-glass) and multiple accounts distinguished by `role_name` rather than a singleton.
2. **`DOMAIN_ADMIN_ACCOUNTS`** in `bootstrap.py` simplified from `list[tuple[username, role_name,
   last_name]]` to `list[tuple[username, role_name]]` - `last_name` was purely a Keycloak profile
   requirement.
3. **The existing role-assignment loop in `main.py`'s lifespan remains structurally unchanged** (`for
   username, role_name in DOMAIN_ADMIN_ACCOUNTS: ... ensure_role_assignment(...)`), only the source of
   `principal_id` changes from `admin_users.list_users(keycloak_admin)` to
   `domain_admins.get_technical_account_id(session_factory, username)`.
4. **`POST /login`/`POST /refresh` needed no change** - both have already branched generically since
   ADR 0064 based on a `technical_account` lookup by username or the `iss` claim, respectively,
   regardless of whether the found account is a superuser or a domain admin.

## Rationale

- **Why no separate "is this a break-glass account" distinction was needed in the login path**: the
  branching logic from ADR 0064 only knows "technical account yes/no", not the `account_type` - the
  enabled/expires check is trivially satisfied for domain admins (always `enabled=True`,
  `expires_at=None`), no special case needed in the code.
- **Why `notification-service`/`signature-service` (authenticate themselves as `users-admin` via the
  generic `POST /login`) remain unchanged**: checked before implementation (`grep` across all
  services for `"users-admin"`/`"config-admin"`) - every caller uses exclusively the generic
  `/login` endpoint with username/password, never a Keycloak-specific API. The branching in
  `/login` is fully transparent to them.
- **Why no new test case for "Keycloak unreachable" was needed (unlike what the
  original phase 18 planning hinted at)**: this proof was already delivered in ADR 0064 for the
  superuser (identical mechanism, identical `MultiIssuerTokenValidator`) - a second, structurally
  identical session verification for domain admins would have provided no additional
  insight.

## A real test infrastructure problem found and fixed along the way

The existing `_clean_tables` fixture (`tests/conftest.py`) previously cleared `auth.technical_account`
before EVERY single test. For the superuser (ADR 0064) this was uncritical, since `role_name=None` -
no role assignment against `permission-service` needed. For domain admins this is a real problem: the
real test installation has `permission.role_assignment.create` configured as requiring four-eyes
approval (an already-applied eGov configuration package) -
`PermissionServiceClient.ensure_role_assignment` deduplicates via `(principal_id, role_id, resource_id)`.
Since `principal_id` got a new auto-increment ID on EVERY test run (because the row was freshly created
each time), an already-approved assignment could NEVER be found again - every test run got stuck on a
new, unanswered approval request. This problem did not occur with the old Keycloak account, because its
UUID remained stable across the entire test session (never deleted by `_clean_tables`).

Fixed via two changes in `tests/conftest.py`:

- `_clean_tables` now only deletes `technical_account` rows with `account_type != 'domain-admin'`
  (`DELETE` instead of `TRUNCATE`, since `TRUNCATE` doesn't support `WHERE`) - domain admin rows
  remain stable across the whole session, everything else (currently only the superuser) continues
  to be reset per test.
- New session-wide, `autouse` fixture `_bootstrap_domain_admin_role_assignments`: disables
  `permission.role_assignment.create`'s approval requirement ONCE for a throwaway `TestClient(app)`
  start (identical pattern to the already existing `role_assignment_immediate` fixture, just
  session- instead of test-scoped), exactly what a real reviewer UI approval would do once - after
  that, the installation setting remains unchanged.

An initial attempt to solve the problem via fixture parameter ordering (`def
domain_admin_auth_headers(role_assignment_immediate, client)`) failed: pytest guarantees NO
execution order for two mutually independent fixtures based on declaration order - what matters is
the order in which the calling TEST FUNCTION requests its fixtures (many tests request `client` before
`domain_admin_auth_headers`, by which point the app had long since started before the approval
requirement was disabled). The session-wide fixture sidesteps this ordering problem entirely, since it
is guaranteed to run before any function-scoped fixture.

## Consequences

- **Fully verified live against the real running stack**: after rebuilding the `auth-service` image
  (a plain container restart via `scripts/run-tests.sh` uses the old, unchanged image, a
  `--build` was needed for live verification), the `iss` claim of both freshly issued
  tokens (`users-admin`, `config-admin`) showed `dms-auth-service-local` instead of the Keycloak
  realm URL. Since `permission.role_assignment.create` also requires four-eyes approval on the real
  dev installation, the very first role assignment after the migration was left, as expected,
  in "pending" state (same behavior as in the test environment, bypassed there by the new
  fixture). Manually approved via `POST /approval-requests/{id}/approve` (exactly the step
  the reviewer UI would automate in practice) and `auth-service` restarted: `GET /users` with a
  `users-admin` token → 200, `GET /me` with a `config-admin` token → 200 with
  `realm_roles: ["domain-admin-config"]`, each via the gateway (`/api/auth-service/...`).
- **Old Keycloak accounts for `users-admin`/`config-admin` remain as orphaned entries** - `POST
  /login` finds the `TechnicalAccount` first and never reaches the Keycloak fallback again. No
  automated cleanup in this session (no data-loss risk, since the accounts simply go unused) -
  manual cleanup via the Keycloak admin console possible but not required.
- **Phase 18 (auth decoupling from Keycloak) is thereby complete** - both superuser AND domain
  admins now live fully independent of Keycloak's availability, in `auth-service`'s own
  database. The chicken-and-egg problem from ADR 0023 (permission-service self-gating needs a
  technical account that doesn't depend on Keycloak) is thereby solved for phase 19 (P19-S6).
