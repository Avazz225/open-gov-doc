# 0062 — SSO/automatic login: OIDC redirect flow with optional Kerberos/SPNEGO as an extension

**Status:** accepted
**Context:** Ad-hoc post-roadmap feature (user request after completion of the 107-session roadmap), affects
`auth-service`, `gateway-service`, `user-ui`

## Decision

Optional, installation-wide togglable (`GET/PUT /sso-config`, same singleton-row pattern as
`ShareLinkConfig`, gated on `admin.user_management`). If SSO is active, `login/page.tsx` redirects
the browser BEFORE showing the password form to Keycloak's own login page (`GET
/oidc/authorize`). If the machine holds a valid Kerberos ticket AND Kerberos is configured, Keycloak's
SPNEGO mechanism logs in automatically without a form ever being shown; otherwise (the normal case
in this sandbox) Keycloak itself shows its hosted form - no breakage, pure fallback. The return leg
(`POST /oidc/callback`) exchanges the code server-side for tokens in the already existing
`TokenResponse` format, so nothing changes in the frontend's session handling.

Three building blocks:

1. **`_ensure_client_updated` (bootstrap.py, runs on EVERY startup, not just initial setup)** - fixes
   a gap already documented in the code itself: `admin.create_client(..., skip_exists=True)`
   never updates an already-existing client. Enables `standardFlowEnabled` and registers the
   redirect URIs (`{origin}/login/callback/` per allowed origin,
   `sso_redirect_uri_allowed_origins`). Runs INDEPENDENTLY of Kerberos - this is the part that
   makes the redirect-to-Keycloak's-own-form fallback possible in the first place.
2. **`_ensure_kerberos` (bootstrap.py, conditional)** - only if `kerberos_enabled` AND all three
   Kerberos settings (`kerberos_realm`/`kerberos_server_principal`/`kerberos_keytab_path`) are set.
   Duplicates Keycloak's built-in `browser` flow (which already ships a by-default-disabled
   `auth-spnego` execution) into `dms-browser-kerberos`, sets its `requirement` to
   `ALTERNATIVE`, points the realm's `browserFlow` at it, and creates a Kerberos user federation
   component (`config` values as single-element lists - a quirk of Keycloak's component API).
3. **`GET /oidc/authorize` / `POST /oidc/callback` / `GET+PUT /sso-config` / `POST /logout`
   (auth-service, public except for `PUT /sso-config`)** - `redirect_uri` is checked against a fixed
   origin allow-list (open-redirect protection, same principle as gateway-service's
   `cors_allowed_origins`). `POST /logout` is a completely new endpoint - previously there was NO
   server-side logout mechanism at all, "log out" only cleared local tokens.

## Rationale

- **Why the scope is "implement fully, with a known test gap" rather than just an OIDC redirect
  without Kerberos**: agreed with the user (recommendation accepted) - Kerberos/SPNEGO is the actual
  "automatically logged in with local user" mechanism from the request; a plain OIDC redirect alone
  would not fulfil this core requirement. Since this sandbox has no real domain controller/KDC,
  actual automatic login via a real ticket cannot be proven here - the existing password form
  remains fully in place as a fallback, no user is locked out by the configuration.
- **Why the code exchange runs server-side in `auth-service`, no PKCE**: `dms-api` is a
  confidential client (holds `client_secret`, `directAccessGrantsEnabled` has been in place since
  the existing ROPC login) - the exchange can and should therefore happen server-side; PKCE is not
  necessary for confidential clients with server-side exchange. `state` alone suffices as
  CSRF/replay protection (held in `sessionStorage`, checked against the value returned by
  Keycloak).
- **Why `POST /oidc/callback` checks the maintenance-mode lock ONLY AFTER the code exchange** (unlike
  `/login`, which checks BEFORE): the username is not known before the exchange (it's only embedded
  in the `code`, not in the request body) - this was identified as a real, otherwise existing gap
  (SSO would otherwise completely bypass the emergency-shutdown lock) and fixed: after the exchange,
  the access token is decoded via `_validator.validate()` and `preferred_username` is checked against
  `superuser.SUPERUSER_USERNAME`; if maintenance mode is active and the user is not the superuser, the
  freshly issued tokens are discarded instead of returned.
- **Why `POST /logout` was newly introduced**: without a real server-side session end, Keycloak's
  own SSO session would remain after a local "log out" - a SPNEGO-capable browser (or one with a
  still-valid Keycloak session cookie) would log in automatically again on the next visit. Calls
  Keycloak's `.../protocol/openid-connect/logout` with the refresh token (no `id_token_hint` needed,
  since `TokenResponse` carries no ID token, no schema change needed).
- **Why `user-ui` only for now**: the remaining five apps do share the same `auth-context.tsx`
  structure, copied, but without a shared package - extending this is mechanical with the same
  pattern but deliberately not part of this ad-hoc session (no explicit user request for the other
  apps).

## Consequences

- **New `keycloak_public_base_url` setting (`DMS_KEYCLOAK_PUBLIC_BASE_URL`)** - a bug identified during implementation: `auth-service` talks to Keycloak within the compose stack internally via `http://keycloak:8080` (`DMS_KEYCLOAK_BASE_URL`); this hostname is not resolvable, however, for the BROWSER that needs to navigate to `GET /oidc/authorize`'s `authorization_url`. The "Open Point" already documented in `docs/services/auth-service.md` before this feature ("issuer hostname consistency ... once a browser-based redirect flow is added") thereby became real - fixed via a separate public base URL used only by `_authorization_endpoint` (`http://localhost:8080` in the compose stack); token/logout endpoints remain on the internal URL.
- **`auth-service` gets its second real Postgres schema object** (`SsoConfig`, after
  `FederationIdentity` since P15-S4) - same `create_all` bootstrapping, no Alembic in this project.
- **`gateway-service`'s `public_routes`/`maintenance_mode_allowed_routes`** extended with
  `auth-service:oidc/authorize`/`auth-service:oidc/callback` (exact string match, no
  wildcard, existing pattern) - both endpoints run ahead of any token check, analogous to `/login`.
- **Not verifiable live in this sandbox**: actually logging in automatically via a real
  Kerberos ticket (no domain controller/KDC present) - a documented limitation agreed with the
  user. **Fully verifiable**: the bootstrap configuration against the real running Keycloak,
  the complete redirect+callback flow through Keycloak's own hosted form (the fallback path
  most installations will go through first), clean fallback for missing/incomplete
  Kerberos configuration, and a real server-side session end via `/logout` - all part of the new
  `test_bootstrap.py`/`test_sso_flow.py` tests.
- **Point left open before the `_ensure_kerberos` implementation, still to be confirmed**: that
  Keycloak's built-in `browser` flow actually already ships the `auth-spnego` execution
  (disabled), as assumed here based on established Keycloak domain knowledge - to be verified once
  against the real running Keycloak once the stack is reachable again (see
  `test_kerberos_enabled_creates_flow_execution_and_component`, which checks exactly that).
