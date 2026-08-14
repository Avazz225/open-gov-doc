# 0063 — Auth decoupling from Keycloak: local technical accounts with their own token issuer

**Status:** accepted (session 1 of 3, see phase 18 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 18 (user directive after the "Open Points" triage of 2026-08-13),
affects `auth-service`, `libs/dms-auth-client`

## Decision

Superuser break-glass (4.6) and domain admin accounts previously depended entirely on real
Keycloak user accounts — login ran through Keycloak's password grant, break-glass activation via a
Keycloak user attribute (`dms_superuser_expires_at`). If Keycloak is unreachable, this also makes the
emergency-shutdown break-glass unusable — a contradiction of the actual purpose of an
emergency mechanism. At the user's explicit request ("the superuser shouldn't live in Keycloak at
all, it should only live in the app"), these accounts will henceforth be kept exclusively in
`auth-service`'s own database, independent of Keycloak.

This first session (P18-S1) lays the infrastructure, without yet migrating `POST /login`/break-glass
(follows in P18-S2/S3):

1. **`TechnicalAccount`** (new model, `auth` schema) — `username`, `password_hash` (bcrypt, the first
   time this service hashes a password itself), `account_type` (`superuser`|`domain-admin`),
   `role_name`, `enabled`, `expires_at`. Carries the same break-glass semantics as before via the
   Keycloak attribute, now owned by the app itself.
2. **`LocalSigningKey`** (new model, singleton row, same pattern as `FederationIdentity`) — its own
   RSA-2048 key pair, generated idempotently on first access (`local_token_issuer.
   ensure_signing_key`, reuses the already existing `federation_crypto.generate_keypair()`,
   no new crypto duplication within the same service). A stable `kid` ensures that
   already-issued tokens stay valid across a restart as well.
3. **`GET /.well-known/jwks.json`** — delivers the public key in the same JWKS format as
   Keycloak's `/protocol/openid-connect/certs`, ungated (public key, no sensitive data).
4. **`local_token_issuer.mint_token()`** — issues a token with an identical claim shape to Keycloak's
   (`sub`, `preferred_username`, `realm_access.roles`, `aud`), so that downstream consumers (`GET /me`,
   `permission-service` calls) don't need to know anything about its origin.
5. **`MultiIssuerTokenValidator`** (new in `libs/dms-auth-client`) — delegates to one of several
   `TokenValidator` instances, selected via the respective token's `iss` claim (pure duck typing,
   the same `.validate(token) -> dict` interface as `TokenValidator` itself, `make_current_user_
   dependency` doesn't notice the difference). `auth-service`'s own `_validator` becomes, as of this
   session, a `MultiIssuerTokenValidator` composed of the Keycloak and local validators — fully
   additive, existing Keycloak logins/tokens remain valid unchanged.

## Rationale

- **Why a second issuer instead of a Keycloak attribute with fallback login**: the central
  requirement is that break-glass NOT depend on Keycloak's availability. A Keycloak attribute would
  itself need Keycloak access for the activation check — so it wouldn't solve the problem. A
  fully separate, locally signed token path does.
- **Why the same claim shape as Keycloak instead of a custom format**: every existing caller
  (`GET /me`, `permission-service`'s `sub`-based mapping, the gateway identity header) already reads
  `sub`/`preferred_username`/`realm_access.roles` — a divergent format would have forced changes to
  every single consumer, disproportionate for a pure issuance-path change.
  `MultiIssuerTokenValidator` makes the origin transparent to all downstream consumers.
  `libs/dms-auth-client` is so far only instantiated directly in `auth-service`/`gateway-service`
  (confirmed via grep) — all other services consume exclusively the `X-DMS-*` headers forwarded by
  the gateway, not self-validated tokens; the multi-issuer extension therefore initially affects
  only these two places (`gateway-service`'s own migration follows in P18-S2, once `/login`
  actually issues local tokens).
- **Why `bcrypt` instead of a new `passlib` foundation**: `bcrypt` was already present transitively
  in the venv (via another dependency), no new heavyweight dependency needed; `passlib`'s
  additional abstraction layer (multiple interchangeable hash schemes) has no consumer here that
  would need it — this service will henceforth only hash passwords for technical accounts, a single
  scheme suffices.
- **Why a `_LazyValidator` wrapper instead of the validator directly**: `app.state.combined_validator`
  only exists after the lifespan start (needs DB access for the persisted
  signing key), but `get_current_user` must, as before, already exist as a finished
  FastAPI dependency at module import time. The wrapper only delays the actual
  `.validate()` call until the first real request (uvicorn only accepts connections after the
  lifespan start has completed anyway) - pure duck typing, no change to `make_current_user_dependency`
  itself is needed.

## Consequences

- **Two token issuers in the system, both accepted by `TokenValidator`/`MultiIssuerTokenValidator`** —
  when debugging in the future, think of `iss` claims, no longer automatically assume Keycloak as
  the sole source.
- **Still no functional change for end users** — `POST /login` continues to issue exclusively
  Keycloak tokens, superuser/domain admins still exist only as Keycloak accounts. This session only
  delivers the verified infrastructure (model, key, minting, validation); P18-S2 actually migrates
  the superuser, P18-S3 the domain admin accounts.
- **`gateway-service`'s own `TokenValidator` is not yet switched to multi-issuer** — as long as
  no local tokens are actually issued (not until P18-S2), this is uncritical; but must be caught up
  before P18-S2's completion, otherwise a freshly logged-in superuser would fail at the gateway
  even though `auth-service` itself validates its token correctly.
- Verified live against the real running stack: `GET /.well-known/jwks.json` delivers a valid
  JWKS entry, an existing Keycloak login (`users-admin`) continues to work unchanged via `GET /me`,
  and the `kid` remains stable across a real container restart (`local-1` identical before and after
  `docker compose restart auth-service`).
