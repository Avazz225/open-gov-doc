# 0069 — Reverse identity resolution (UUID → username)

**Status:** accepted (session 4 of 11, see phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 19 session 4, affects `auth-service`, `apps/user-ui`

## Decision

New endpoint `GET /users/{user_id}` in `auth-service` — the counterpart to the existing `GET
/users/lookup?username=` (name → UUID, since P14-S6). `principal_id` fields are the Keycloak
`sub` UUID everywhere in the system (delegations, teamspace member lists, `X-DMS-Principal`) — no
user knows them by heart, frontends previously displayed them raw, for lack of reverse resolution.

1. **`admin_users.find_user_by_id(admin, user_id)`** (new) — mirrors `find_user_by_username`, uses
   `KeycloakAdmin.get_user(user_id)` (single fetch, not `get_users(query=...)`), catches
   `KeycloakGetError` with `response_code == 404` and returns `None`. Same minimal
   `{id, username}` response shape as the forward resolution.
2. **`GET /users/{user_id}`** (new, `main.py`) — same gate as `GET /users/lookup`
   (`_require_permission(user, "users.lookup", ...)`, the "everyone" group from ADR 0067/0068): same
   trust level, only the search direction is reversed, no new permission word needed.
   **Registration order matters**: must come after all the static `/users/...` paths
   (`/users/lookup`, `/users/directory`, `/users/count`), since FastAPI/Starlette matches routes in
   registration order — an earlier registered `/users/{user_id}` would otherwise have shadowed them.
   Placed directly next to `DELETE /users/{user_id}` (symmetric, both address a single
   account by ID).
3. **`apps/user-ui/src/lib/api.ts`**: new `lookupUserById(token, userId)` function, same
   conventions as `lookupUserByUsername` (same `UserLookup` interface, `request()` helper).
4. **New hook `apps/user-ui/src/lib/usePrincipalNames.ts`**: resolves a list of raw `principal_id`s
   into usernames, with a simple in-memory cache across the hook's lifetime (no repeated
   request for already-resolved IDs) and a fallback to the raw UUID on failure (e.g.
   `users.lookup` revoked, or an account deleted in the meantime) — never blocks the
   display.
5. **`DelegationsPane.tsx`/`TeamspacesPane.tsx`** use the new hook to show `deputy_principal_id`/
   `delegator_principal_id`/`member.principal_id` as a resolved name instead of a raw UUID.

## Rationale

- **Why no new permission, but reuse of `users.lookup`**: reverse resolution is
  conceptually the same operation as forward resolution (a known identifier is translated
  into a public username) - a second, separate permission would have offered no
  additional security value, only more administrative overhead for admins.
- **Why a dedicated hook instead of inline logic in both components**: `DelegationsPane` and
  `TeamspacesPane` need exactly the same behavior (list of raw IDs → resolved names, cache,
  failure fallback) - a third copy of the same ~20 lines would be pure duplication.
- **Why fallback to the raw UUID instead of a loading indicator/error text**: displaying a
  delegation/membership must not be blocked by a single
  name resolution failing (network error, revoked permission, deleted account) - the raw UUID
  is, in the worst case, exactly as informative as the previous status quo, never worse.
- **Why no batch endpoint** (`POST /users/resolve` or similar) **instead of N individual calls**: the
  affected lists (own delegations, teamspace members) are small in practice (typically
  single digits) - a batch endpoint would be overengineering for the current scope, and can be
  added later if needed, without changing the hook itself (a pure implementation-detail swap).

## Consequences

- **Tests**: `auth-service` 96 (previously 92, +4: positive/negative path for `GET /users/{id}`, same
  structure as the `GET /users/lookup` tests). `apps/user-ui` 169 Vitest tests green (+2 new, one
  resolution test each in `delegations-pane.test.tsx`/`teamspaces-pane.test.tsx`) - existing tests
  remain valid unchanged, since `lookupUserById` fails by default in them (not mocked) and the hook
  falls back to the raw ID in that case, exactly the previously expected behavior. `tsc --noEmit`,
  `eslint .`, `next build` clean.
- **A bug found and immediately fixed during live verification** (not a code bug, a forgotten
  deployment step): the first live check against the gateway returned `405 Method Not
  Allowed` for `GET /users/{id}` - `auth-service`'s Docker image had not yet been rebuilt (a plain
  restart doesn't pick up code changes, a repeat of the lesson from P18-S3/P19-S3). After
  `docker compose build auth-service`, the call worked as expected.
- **Fully verified live against the real running stack** (after image rebuild): `GET
  /users/lookup` returns `users-admin`'s own ID, `GET /users/{id}` resolves that same ID back to
  `{"id": ..., "username": "users-admin"}`, an unknown UUID returns `404`. All three
  pre-existing static `/users/...` routes (`count`, `lookup`, `directory`) work
  unchanged - no shadowing by the new `/users/{user_id}` route confirmed.
- **No browser verification of this session possible**: this sandbox environment has no
  browser automation tool available (no Playwright/Chromium) - frontend correctness relies on
  `tsc`/`eslint`/`vitest`/`next build` (all green) plus the backend contract's live
  verification described above, which `lookupUserById` actually calls. No actual
  display confirmed in a browser - a later session with browser access should catch up on this.
- Docs: `docs/services/user-ui.md`'s "Open Points" bullet about the raw `principal_id` display and
  `docs/services/permission-service.md`'s "I represent" bullet marked as resolved,
  `docs/services/auth-service.md`'s API table extended with `GET /users/{user_id}`.
