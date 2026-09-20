# 0174 — `favorite-service`'s `user_id` ownership check against `X-DMS-Username`, not `X-DMS-Principal`

**Status:** accepted
**Context:** P54-S3 (Phase 54, "Critical Authorization Bugs" — third and last session of the fifth
gap-analysis round). Found by this round's live-code security sweep: `favorite-service` never read any
`X-DMS-*` identity header anywhere (confirmed via grep — zero hits). `create_favorite`/`list_favorites`/
`delete_favorite` take `user_id` as a fully client-supplied body/query field, used directly for every DB
filter, with no check that it matches the caller's own identity — any authenticated user could
view/add/delete any OTHER user's favorites by passing a different `user_id`.

The plan for this session (written before the fix was attempted) assumed this would be a small,
mechanical fix requiring no design decision — check `user_id` against `X-DMS-Principal`, matching
`document-service`'s own `scope="personal"` precedent. Attempting exactly that revealed a real,
previously-unconsidered nuance that would have broken production if shipped as planned, hence this ADR.

## Decision

**Check `user_id` against the gateway-verified `X-DMS-Username` header (Keycloak `preferred_username`),
not `X-DMS-Principal` (Keycloak `sub`, a UUID).**

## Rationale

- **Why not `X-DMS-Principal`, the more obvious choice**: `apps/user-ui`'s only real caller of this
  service's write/read paths, `CasesPane.tsx`, already sends `user_id: useAuth().user.username` — the
  Keycloak `preferred_username`, never `sub`. This is not a new choice made in this session; it's the
  identifier scheme this service's stored `Favorite.user_id` rows have used in practice since the feature
  shipped. Checking `user_id != x_dms_principal` would have compared a username string against a UUID on
  every real, already-existing favorites call — rejecting all of them with a `403`, a genuine production
  regression this session would have introduced while trying to fix a security gap.
- **Why `X-DMS-Username` is an equally valid, equally unspoofable check**: read directly in
  `gateway-service`'s `proxy()` handler (`services/gateway-service/src/gateway_service/main.py`) — both
  `X-DMS-Principal` and `X-DMS-Username` are set from the same verified JWT `claims`, via the same
  unconditional `headers.update(identity_headers)` that runs for every non-`public_routes` request,
  always overwriting whatever a raw client sent. There is no meaningful trust difference between the two
  headers; the only real question is which identifier scheme matches this service's own existing data.
- **Why not migrate `Favorite.user_id` to `sub` instead of accepting `username`**: that would require a
  data migration for every existing favorite row and a corresponding `apps/user-ui` change, a
  meaningfully larger change than this session's actual scope (closing an authorization gap, not
  redesigning the identifier scheme) — and `username` is not itself a security weakness here, since the
  gateway verifies it with the same rigor as `sub`.

## Consequences

- `create_favorite`/`list_favorites`/`delete_favorite` all gained a `x_dms_username: str = Header(default="")`
  parameter and a shared `_require_own_user_id(user_id, x_dms_username)` check: `401` with no header,
  `403` if `user_id` doesn't match, otherwise proceeds unchanged.
- No `apps/user-ui` change needed — `CasesPane.tsx` already sends exactly the value this check now
  requires, confirmed by reading its source before finalizing the fix rather than assuming.
- New regression tests updated the shared test header helper to `X-DMS-Username` and added four new
  tests proving cross-user create/list/delete are rejected and a missing header is rejected, while
  confirming the rejected delete attempt genuinely left the target favorite unchanged (not just a
  correct status code).
- Live-verified directly against the real running container: an attacker's own valid `X-DMS-Username`
  targeting another user's `user_id` returns `403` for all three operations; the exact header/value shape
  `apps/user-ui` actually sends in production returns the expected success response, confirming this fix
  does not regress the real feature.
