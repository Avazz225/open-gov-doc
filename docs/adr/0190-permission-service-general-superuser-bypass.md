# 0190 — permission-service: general superuser bypass for `require_capability`

**Status:** accepted
**Context:** P63-S1 (Phase 63, first session of the seventh gap-analysis round). Found by the round's
`docs/services/*.md` Open Points sweep: `permission-service`'s `require_capability` (the direct,
non-four-eyes baseline capability check used by role/role-assignment management, scope-lock
create/release, emergency-shutdown trigger, and the admin branches of delegation listing/revocation) had
no superuser bypass anywhere — only `POST /maintenance-mode/lift` special-cased an activated break-glass
superuser, ad hoc, for itself alone. An activated superuser (4.6) could therefore not actually manage
roles, scope locks, or delegations unless they ALSO held an explicit `admin.user_management` role
assignment — defeating the point of break-glass as "emergency access without needing prior
provisioning."

## Decision

New `main._is_active_superuser(principal_id)` helper — the same shape as every other service's own
`_is_active_superuser` (`workflow-service`/`query-service`/`plugin-orchestration-service`/etc.),
resolving `AuthServiceClient.get_active_superuser()` and comparing the returned superuser's ID against
the SPECIFIC `principal_id` being checked (not just "is some superuser active anywhere"). `lift_
maintenance_mode`'s own pre-existing inline version of this exact check is refactored to reuse the new
shared helper instead of duplicating it.

`repository.require_capability` gains a new `is_superuser: bool = False` keyword parameter, short-
circuiting to return immediately when `True`. The caller (`main.py`) is responsible for resolving
`is_superuser` via the new helper before calling — the repository module stays free of any HTTP-client
knowledge, the same DB-only separation every other function in that module already keeps. All six
direct (non-four-eyes) call sites in `main.py` now compute and pass `is_superuser`: `_require_role_
management` (used by all `POST`/`PUT /roles` endpoints), `create_scope_lock`, `release_scope_lock`,
`trigger_maintenance_mode`, the admin branch of `list_delegations`, and the admin-override branch of
`revoke_delegation`.

**Deliberately NOT extended to `_require_permission_if_configured`** (the four-eyes-initiation-
eligibility check used by `create_approval_request`): this project's own established convention (`query-
service`'s critical-action four-eyes, concept 6.1 item 4) is that an activated superuser does not bypass
four-eyes protections, only the ordinary permission model outside of them. Bypassing who may INITIATE a
four-eyes-protected action would let a superuser start an approval flow they'd otherwise need a real
role for, undermining that invariant.

## Rationale

- **Why a parameter on `require_capability` instead of a bypass inside each of the six call sites
  independently**: a single, centrally-documented short-circuit is less likely to be silently missed the
  next time a seventh direct call site is added, and keeps the "superuser acts without restriction"
  policy visible in one place rather than repeated six times.
- **Why the repository function takes a pre-resolved `bool` instead of calling out to auth-service
  itself**: `repository.py` is DB-only throughout this service (confirmed: no HTTP client import
  anywhere in the module) — an HTTP round trip inside a function whose name promises a pure permission
  check would be a surprising, easy-to-miss side effect, and would also make every existing unit test
  against `repository.require_capability` implicitly network-dependent.
- **Why the actor-matching check (not just "is any superuser active")**: mirrors `lift_maintenance_
  mode`'s own pre-existing design exactly — a caller claiming to act as principal X must actually BE the
  currently active superuser (whose ID `get_active_superuser()` returns), not merely benefit from some
  OTHER superuser being active elsewhere. Prevents a non-superuser caller from claiming the bypass just
  because break-glass happens to be active for someone else at the same time.
- **Why `trigger_maintenance_mode`'s `system.not_shutdown.trigger` check gets the same bypass**: no
  ADR/concept text carves this one out as a four-eyes-protected critical action the way concept 6.1 item
  4 does for query-service manipulation — the general "superuser acts without restriction outside
  concept 6.1's own explicit exception" policy applies uniformly here too.
- **Why NOT extending the bypass to four-eyes initiation eligibility**: explained under "Decision" above
  — this is the one place in this service where the general policy has an already-established, real
  exception elsewhere in the codebase, and this session deliberately doesn't create a second, competing
  interpretation of it.

## Consequences

- An activated break-glass superuser can now manage roles/role-assignments, create/release scope locks,
  trigger emergency shutdown, and list/revoke any delegation, without needing any additional, separately
  provisioned `admin.user_management`/`system.not_shutdown.trigger` role assignment — matching the
  concept's own "emergency access without prior provisioning" intent for 4.6.
- New tests: `test_create_role_bypassed_by_active_superuser_without_explicit_role`, `test_create_role_
  not_bypassed_by_a_different_principal_than_the_active_superuser`, `test_create_scope_lock_bypassed_by_
  active_superuser_without_explicit_role` — all boundary-patch `app.state.auth_client.get_active_
  superuser` for the duration of one test rather than performing a full real Keycloak superuser
  activation flow (orthogonal to what these tests verify — `require_capability`'s own bypass logic, not
  auth-service's activation mechanism), the same "patch the client, not the mechanism it wraps"
  convention already used elsewhere in this project. `permission-service` 185/185 (+3).
- **Investigated and dropped the plan's originally-bundled auth-service half of this session** (elevated
  audit priority for actions during an active superuser session; a rolling-inactivity timeout to
  complement the existing absolute-expiry-only timer): both turned out to be already-named, deliberately-
  scoped limitations in ADR 0023's own "Consequences" section ("No 'real' rolling inactivity
  deactivation"; "No increased audit priority for individual actions during activation"), each with real
  justification already recorded there (rolling inactivity would need new instrumentation in every
  service/gateway; the audit hash-chain model has no priority concept at all). Not a newly-discovered
  gap — re-confirmed as correctly deferred, not built here.
- Rebuilt/redeployed. Live-verified: this fix's own automated regression tests exercise the real code
  path end to end (boundary-patched auth_client, real `permission-service` HTTP round trip via
  `TestClient`, real database state change on success) — no separate manual curl verification needed
  beyond what those tests already prove, since a full live Keycloak superuser activation is outside this
  session's scope (see "Consequences" above on why the tests patch the client instead).
