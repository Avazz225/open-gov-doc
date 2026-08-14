# 0064 — Superuser migration to local tokens + gateway multi-issuer

**Status:** accepted (session 2 of 3, see phase 18 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 18 session 2, affects `auth-service`, `gateway-service`

## Decision

Building on the infrastructure laid in [ADR 0063](0063-auth-entkopplung-lokale-technische-konten-dual-issuer-jwt.md)
(local token issuer, `MultiIssuerTokenValidator`), this session actually functionally migrates the
superuser from Keycloak to `TechnicalAccount`:

1. **`superuser.py` fully converted to async/DB** — all functions (`ensure_superuser_account`,
   `activate`, `deactivate`, `get_status`, `get_principal_id`, `deactivate_if_expired`) now take a
   `session_factory` instead of `KeycloakAdmin` and operate on `TechnicalAccount` rows. The
   poll loop (`_superuser_poll_loop`), the NATS consumer (`consumer.py`) and both
   `/superuser/*` endpoints were adjusted accordingly — structurally unchanged (same
   poll/consumer idiom), only the data source changes.
2. **`bootstrap.py`'s Keycloak portion removed**: `_ensure_superuser_expires_at_attribute`
   (Keycloak profile attribute declaration) and the `superuser.ensure_superuser_account(admin)` call
   are dropped entirely — account creation now happens in `main.py`'s async lifespan, right next to
   the local signing key from ADR 0063 (both DB operations; `bootstrap.ensure_realm_and_client` is
   synchronous and remains purely Keycloak-focused for domain admins/realm/client/Kerberos).
3. **`POST /login` recognizes technical accounts**: looks up the username in `technical_account`
   before attempting a Keycloak password grant. Hit → local password check (`bcrypt`) +
   `enabled`/`expires_at` check, otherwise the existing Keycloak path unchanged.
4. **`POST /refresh` recognizes local tokens** via the `iss` claim (`local_token_issuer.is_local_token`,
   a pure peek without signature verification, only for branching) and, given a valid, still-active
   account, issues a fresh token pair instead of attempting Keycloak's refresh grant (which would fail
   for local tokens anyway, since no Keycloak session exists).
5. **`gateway-service`'s own `TokenValidator` is now a `MultiIssuerTokenValidator`** — the second
   instance points at `auth-service`'s `/.well-known/jwks.json` (new `auth_service_base_url`
   setting, direct east-west address like every other cross-service call in this project). The local
   issuer string is duplicated as a constant in `gateway_service/main.py` (no shared
   `libs/` package knows it, `gateway-service` fundamentally imports no code from `auth-service`).

## Rationale

- **Why `POST /login`/`POST /refresh` branch internally rather than a separate endpoint**: the
  existing frontends (`user-ui` etc.) only know `/login`/`/refresh` — a separate
  `/technical-login` endpoint would have required changing every calling site for no real added
  value. The branching itself is cheap (a table lookup by username before the Keycloak call).
- **Why the same generic 401 error message for a wrong password AND a disabled/expired
  account**: distinguishable messages would reveal whether an account exists at all or is just
  currently disabled — the identical principle to Keycloak's own, equally opaque error message for
  a disabled account previously.
- **Why a `jti` claim was added (found during test development)**: `mint_token()` previously built
  claims only from `sub`/`username`/`roles`/`aud`/`iat`/`exp` — with two calls for the same account
  within the same second (e.g. login immediately followed by refresh), `iat`/`exp` were identical and
  every other claim was as well, making two actually independently issued tokens byte-identical. A
  `jti` (registered JWT claim, `secrets.token_urlsafe(16)`) fixes this robustly, independent of
  time resolution.
- **Why `role_name` on `TechnicalAccount` was later made nullable**: designed in ADR
  0063 as generic for all future technical accounts, but the superuser itself needs no
  permission-service role (its special privileges run via direct name comparison at several
  points in the system). Since this project works without Alembic (only `create_all`), the
  already-created table column additionally had to be caught up manually via `ALTER TABLE ...
  DROP NOT NULL` in both the test and the dev database — a `create_all` call alone does not change
  column constraints on an already-existing table. Relevant for future model changes in this
  early development phase: column-constraint changes need a manual follow-up
  step until a real migration solution is introduced.

## Consequences

- **The bug known since P6-S6 and never fixed ("superuser account cannot log in interactively",
  missing `firstName`/`lastName`/`email` on a historically incompletely created Keycloak account)
  is gone without replacement** — there is no more Keycloak account that could be in this state.
- **Fully verified live against the real running stack** (not only unit/integration tests):
  login before activation → 401; activation; login **through the gateway** → 200 with a valid token
  pair; `GET /me` through the gateway with the local token → 200 (proves `gateway-service`'s own
  multi-issuer migration, not just `auth-service`'s); a call against `document-service` with the same
  token through the gateway was passed through (422 due to a required business field, not 401) —
  proving the identity is accepted system-wide, not just locally in `auth-service`;
  `POST /refresh` through the gateway → 200 with a fresh pair; deactivation → subsequent refresh → 401.
  A normal Keycloak login (`users-admin`) through the same gateway works unchanged.
- **`gateway-service` now needs a new environment variable** (`DMS_AUTH_SERVICE_BASE_URL`) —
  added in `infra/docker-compose.yml` (`http://auth-service:8000`); if it is missing in a future
  deployment configuration, locally issued tokens would fail at the gateway with 401 even though
  `auth-service` itself validates them correctly (not a silent failure — the missing JWKS source would
  trigger an HTTP error on JWKS fetch on the first validation attempt of a local token).
- **Domain admin accounts (`users-admin`/`config-admin`) are untouched by this session** — remain
  Keycloak accounts until P18-S3.
