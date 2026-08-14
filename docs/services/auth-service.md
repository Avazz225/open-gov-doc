# auth-service

**Responsibility:** Thin OIDC broker in front of Keycloak — holds the client secret and admin access, callers only see login/refresh/token validation (Concept 4.4). No own IAM logic, no own user table.

**Concept Reference:** 4.4/2.5 (contacts, since P15-S4)/7.4 (federated contact search, since P15-S4)/14.1 (realm roles for configuration packages, since P17-S1)
**Own Postgres Schema:** `auth` (since P15-S4, `federation_identity` — a singleton row for the optional federated contact search; since the ad-hoc post-roadmap SSO feature additionally `sso_config`, also a singleton row; since Phase 18 additionally `local_signing_key` (singleton) and `technical_account`, see "Auth Decoupling from Keycloak" below; since **P24-S2** additionally `ad_group_role_mapping`, see "AD Group→Role Mapping" below). Until P15-S4 the service was fully stateless; Keycloak itself continues to manage its own data in its own schema `keycloak` (see `infra/postgres-init/001-schemas.sql`).

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/login` | `{username, password}` → password grant against Keycloak, returns access/refresh tokens. **Since P6-S6**: reads `X-DMS-Maintenance-Active` (injected by the gateway, 4.8) — if maintenance mode is active and `username` is not the superuser account, `503` instead of login. **Since Phase 18 Session 2/3**: recognizes technical accounts (`technical_account` table lookup) before the Keycloak path and authenticates them locally (bcrypt) — since Session 2 the superuser, since Session 3 additionally both domain admin accounts, see "Auth Decoupling from Keycloak" below |
| `POST` | `/refresh` | `{refresh_token}` → new tokens. **Since Phase 18 Session 2**: recognizes locally issued refresh tokens via the `iss` claim and issues a fresh pair without Keycloak involvement |
| `GET` | `/me` | Validate bearer token (JWKS, stateless, no round trip to Keycloak), return normalized identity. **Since P24-S2**: `realm_roles` additionally contains the roles derived from the `groups` JWT claim (AD group→role mapping, 4.4) alongside Keycloak's raw `realm_access.roles`, merged and deduplicated into the same list — see "AD Group→Role Mapping" below |
| `GET` | `/users` | List users (since P4-S3, basis for the Admin UI user management) — reads directly from Keycloak. **Gated since P6-S5**: requires the capability `admin.user_management` (domain "user/permission management", 4.6), otherwise `403` |
| `POST` | `/users` | Create user (`username`, `email`, `password`, `first_name`, `last_name`) — 409 for an already-taken username. Gated like `GET /users` |
| `DELETE` | `/users/{id}` | Delete user — 404 for an unknown `id`. Gated like `GET /users` |
| `GET` | `/users/{id}` | **Since P19-S4** (ADR 0069): reverse identity resolution, counterpart to `GET /users/lookup` — returns only `{id, username}`, `404` for an unknown `id`. Same gate as `GET /users/lookup` (`users.lookup` via the "everyone" group). Must be registered after all static `/users/...` paths (registration order, see ADR 0069) |
| `GET` | `/me/preferences` | Theme preference of the logged-in account (`{theme}`, default `"auto"`) — since P4-S6 |
| `PUT` | `/me/preferences` | Set theme preference (`{theme}` ∈ `light`/`dark`/`high-contrast`/`auto`, otherwise 422) — since P4-S6 |
| `GET` | `/superuser/status` | Break-glass status (4.6, since P6-S5): `{active, expires_at}`, since **P6-S6** additionally `principal_id` (since Phase 18 Session 2 the `TechnicalAccount.id`, previously the Keycloak `id`, for the Permission Service's not-shutdown lift check, 4.8) — 404 if the superuser account has not yet been created |
| `POST` | `/superuser/deactivate` | Early, voluntary deactivation (since P6-S5) — complements the automatic expiry enforcement via the poll loop |
| `GET` | `/users/lookup` | Exact name resolution (`?username=`) — returns only `{id, username}`, `404` for an unknown name. New in P14-S6, for `teamspace-service`'s invite-by-username (2.5): deliberately NOT gated behind `admin.user_management` like `GET /users` above — every person, not just domain admins, should be able to invite others to a team workspace. Since P19-S3 (ADR 0068) gated via the "everyone" group from permission-service (`users.lookup`, pre-seeded since P19-S2) instead of only `Depends(get_current_user)` — actual behavior is unchanged, but the permission is now admin-editable. See [ADR 0043](../adr/0043-teamspace-service-membership-and-permission-integration.md) |
| `GET` | `/users/count` | Internal call from `license-service` (9.1 "named accounts" model, since P9-S1) — ungated, since no service holds a real Keycloak bearer token for `Depends(get_current_user)` |
| `GET` | `/sessions/count` | Internal call from `license-service` (9.1 "concurrent users" model, since P9-S1) — `KeycloakAdmin.get_client_sessions_stats()`, ungated |
| `GET` | `/users/directory?q=` | Directory search (2.5/4.4, since P15-S4, Keycloak `search` parameter — prefix per field, no substring, see "Contacts" below) — no `admin.user_management` gate, but since P19-S3 (ADR 0068) checked via the "everyone" group from permission-service (`users.directory`) instead of merely requiring authentication |
| `GET` | `/users/directory/federation-status` | Whether federated contact search is enabled on this installation (`{enabled, peer_installation_count}`) — ungated, controls the visibility of the corresponding frontend section |
| `GET` | `/users/directory/federated?q=` | Federated search across all known peer installations that have opted in to contact search (2.5/7.4, since P15-S4) — `403` if not enabled on this installation |
| `POST` | `/users/directory/federated-search-inbound` | Called by a peer installation (public route, no `X-DMS-Principal`) — authenticated via `X-Installation-Signature`/`X-Installation-Id`, see "Contacts" below |
| `GET` | `/realm-roles` | **Since P17-S1** (14.1): current Keycloak realm roles, filtered to exclude Keycloak built-ins (`offline_access`, `uma_authorization`, `default-roles-*`) — ungated, returns only names (identical trust model to `permission-service`'s `GET /roles`) |
| `POST` | `/realm-roles` | **Since P17-S1**: idempotently creates the given realm roles (`{names: [...]}`, `create_realm_role(..., skip_exists=True)`) — requires an `X-DMS-Principal` header with `admin.user_management` permission (service-to-service, no Keycloak JWT endpoint), otherwise `403`. Does not assign the role to anyone, see "Realm Role Management" below |
| `GET` | `/healthz` | Own health check |
| `GET` | `/.well-known/jwks.json` | **Since Phase 18** (ADR 0063): public key for tokens of local technical accounts, same format as Keycloak's JWKS. Ungated |
| `GET` | `/oidc/authorize?redirect_uri=&state=` | **Ad-hoc post-roadmap** (SSO, see ADR 0062): checks `redirect_uri` against `sso_redirect_uri_allowed_origins` (400 otherwise, open-redirect protection), returns `{authorization_url}` — the client navigates there itself. Public (login entry point) |
| `POST` | `/oidc/callback` | `{code, redirect_uri}` → exchanges the code server-side for tokens, returns the same `TokenResponse` shape as `/login`. Checks not-shutdown ONLY AFTER the exchange (username unknown beforehand) — see ADR 0062. Public |
| `GET` | `/sso-config` | `{enabled, updated_at}` — whether SSO is active installation-wide. Ungated, `login/page.tsx` queries this before showing the form |
| `PUT` | `/sso-config` | Set `{enabled}` — gated on `admin.user_management`, same domain as user management |
| `POST` | `/logout` | `{refresh_token}` → actually ends the session on the Keycloak side (`.../protocol/openid-connect/logout`) — previously there was no server-side logout mechanism |
| `GET` | `/ad-group-mappings` | **Since P24-S2** (4.4): all configured AD group→role mappings (`{id, ad_group_name, role_name, created_at, created_by}`). Gated on `admin.user_management`, same domain as `GET /users` |
| `POST` | `/ad-group-mappings` | **Since P24-S2**: creates a new mapping (`{ad_group_name, role_name}`), `201`. Audited via `auth.ad_group_role_mapping.created`. Takes effect from the next `GET /me` resolution onward. Gated like `GET /ad-group-mappings` |
| `DELETE` | `/ad-group-mappings/{id}` | **Since P24-S2**: deletes a mapping, `404` for an unknown `id`. Audited via `auth.ad_group_role_mapping.deleted`. Gated like `GET /ad-group-mappings` |

## Realm/Client Bootstrap

On every start (`ensure_realm_and_client`, idempotent via `skip_exists=True`):
- Realm `dms`
- Confidential client `dms-api` with `directAccessGrantsEnabled=true`, `standardFlowEnabled=false` (no browser redirect flow in this session)
- Audience mapper, so that `aud` in the access token contains `dms-api` instead of just `account` (Keycloak default without a mapper)
- Declared user profile attribute `dms_theme` (since P4-S6, see below) — without this declaration, Keycloak's declarative user profile silently drops the attribute on every `update_user` call
- Realm role `dms-admin` (since **P5e-S2**, `create_realm_role(..., skip_exists=True)`) — the first role actually evaluated in the system, see `docs/services/document-service.md` "File Reference Number Generator" (privileged change of `attributes["Kennzeichen"]`)
- ~~Declared user profile attribute `dms_superuser_expires_at`~~ / ~~superuser account created here~~ — **removed since Phase 18 Session 2** ([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)): the superuser no longer lives in Keycloak, its idempotent creation now happens async in `main.py`'s lifespan (`superuser.ensure_superuser_account`, DB-based), no longer here in this synchronous, purely Keycloak-focused bootstrap step.
- ~~Technical domain admin accounts created here~~ — **removed since Phase 18 Session 3**
  ([ADR 0065](../adr/0065-domain-admin-migration-lokale-technische-konten.md)): `users-admin`/
  `config-admin` no longer live in Keycloak, their idempotent creation now happens async in
  `main.py`'s lifespan (`domain_admins.ensure_domain_admin_account`, DB-based), right next to the
  superuser, no longer here in this synchronous, purely Keycloak-focused bootstrap step.
- **Since the ad-hoc post-roadmap SSO feature**: `_ensure_client_updated` (runs on EVERY start, not just initial setup) activates `standardFlowEnabled` and registers the redirect URIs (`{origin}/login/callback/` per `sso_redirect_uri_allowed_origins`) — fixes the `skip_exists` gap named below for exactly these two fields. `_ensure_kerberos` (conditional, only if `kerberos_enabled` and all three Kerberos settings are set) additionally sets up Kerberos/SPNEGO, see "SSO/Automatic Login" below and [ADR 0062](../adr/0062-sso-automatischer-login-oidc-redirect-und-optionales-kerberos.md).
- **Since P24-S2**: `_ensure_groups_mapper` (also runs on EVERY start) adds an `oidc-group-membership-mapper` to the client (claim name `groups`, `full.path=false`) — without this mapper, Keycloak does NOT include group memberships in the access token (unlike roles via `realm_access.roles`); the AD group→role mapping (see below) would otherwise always be ineffective. See [ADR 0093](../adr/0093-ad-group-role-mapping-simple-1to1-scope-cut.md).

**Known limitation**: `skip_exists=True` continues to prevent a later change to the rest of the client configuration (e.g. a new mapper) from being applied to an already-existing client — uncritical for dev/test, but to be kept in mind for production configuration changes. Only `standardFlowEnabled`/`redirectUris` are exempt from this since the SSO feature (see above).

## Theme Preference (Concept 8, since P4-S6)

Cross-UI theming (light/dark/high-contrast/automatic, User UI and Admin UI) stores its preference on the user account across devices instead of only locally in the browser — rationale and pitfalls (declarative user profile trap) in [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). Summary: `dms_theme` is a declared Keycloak user attribute, read/written via the existing admin client (`admin_users.get_theme_preference`/`set_theme_preference`), exposed via `/me/preferences`. No new persistence component needed.

## Domain-Separated Admin Roles (4.6, since P6-S5)

Domain admin "roles" are deliberately **not Keycloak realm roles** (unlike `dms-admin`), but native `Role` rows in `permission-service` (see `docs/services/permission-service.md`) — `auth-service` only creates the associated **technical accounts** and assigns them the role via an HTTP call to `permission-service` (`permission_client.py`, `PermissionServiceClient.ensure_role_assignment`). Complete architecture rationale, see [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). Currently actually created: `users-admin` (domain "user/permission management") and `config-admin` (domain "workflow configuration", since P6-S6) — **as `TechnicalAccount` rows instead of Keycloak accounts since Phase 18 Session 3** ([ADR 0065](../adr/0065-domain-admin-migration-lokale-technische-konten.md)), see "Auth Decoupling from Keycloak" below. Role assignment continues to run best-effort at lifespan startup — if `permission-service` is not yet reachable (or the assignment on this installation requires four-eyes approval and has not yet been approved), it is skipped and retried on the next restart (no retry loop).

## Superuser Break-Glass (4.6, since P6-S5, local instead of Keycloak since Phase 18 Session 2)

A single account `superuser`, disabled by default (`enabled=False`). **Since Phase 18 Session 2**
([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)) a `TechnicalAccount`
row in the service's own `auth` schema instead of a Keycloak user account — break-glass thereby functions
independently of Keycloak's reachability, the actual purpose of an emergency mechanism. Reactivation
continues to run **exclusively** via the Permission Service's generic four-eyes mechanism
(P6-S4, ADR 0022): `POST /approval-requests` with `action_type="auth.superuser.activate"` against
`permission-service`, which pre-configures `requires_approval=True` and
`required_permission="breakglass.approve"` for this action type at its own startup (stricter than the
"any second person" rule from 4.3 — both initiator *and* approver must hold the role `breakglass-approver`).
Upon approval, `auth-service` (**the first NATS consumer of this service ever**, `consumer.py`) consumes
the published `permission.approval.approved` and activates the account: `enabled=True` +
`expires_at` column (`activated_at + superuser_activation_minutes`, default 30 min, now a real
DB column instead of a Keycloak attribute) — publishes `auth.superuser.activated` afterward.

A periodic poll loop (`_superuser_poll_loop`, `superuser_poll_interval_seconds`, default 30s — exactly the same pattern as workflow-service's SLA timer monitoring, [ADR 0020](../adr/0020-sla-timer-polling.md)) automatically deactivates expired activations and publishes `auth.superuser.deactivated` (`reason="expired"`, or `"manual"` for `POST /superuser/deactivate`). **Deliberate simplification** (see ADR 0023): a single absolute expiry timestamp instead of separate total-duration and rolling 10-minute-inactivity timers.

`POST /login` recognizes the superuser username via a `technical_account` table lookup and
authenticates locally (bcrypt password check + `enabled`/`expires_at` check), instead of attempting a
Keycloak password grant — the bug known since P6-S6 and never fixed ("superuser account cannot
log in interactively", missing required fields on a historically incompletely created Keycloak
account) has thereby disappeared without replacement — there is no more Keycloak account that could be
in that state.

## Not-Shutdown (4.8, since P6-S6)

`POST /login` reads the `X-DMS-Maintenance-Active` header injected by the gateway on every proxied request (default `"false"` if login is called directly against the service instead of via the gateway — in which case maintenance mode is effectively never in force, see `docs/services/gateway-service.md`): if it is `"true"` and the requested `username` is not `superuser.SUPERUSER_USERNAME`, the login is rejected with `503` **before** a password grant against Keycloak is even attempted — literal implementation of "new logins except for the superuser are rejected" (4.8). The superuser login itself is not automatically successful as a result — a wrong password still returns `401`, the header only decides whether an attempt is made at all. Complete architecture rationale (gateway as enforcement point, header broadcast pattern) in [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

## Auth Decoupling from Keycloak (Post-Roadmap Phase 18, see ADR 0063/0064/0065)

Superuser break-glass and domain admin accounts (`users-admin`/`config-admin`) have, since
Phase 18, functioned fully independently of Keycloak's reachability (user directive: "the superuser
should not live in Keycloak at all, the same applies to the domain admins").

- **`TechnicalAccount`** (model, `auth` schema) — storage location for superuser/domain admin accounts,
  `password_hash` via `bcrypt` (the first self-hashed password in this service). `role_name`
  nullable — `NULL` for the superuser (special privileges run via direct name comparison, not RBAC),
  set (`domain-admin-users`/`domain-admin-config`) for the two domain admin accounts
  (`domain_admins.py`, since Session 3, structurally almost identical to `superuser.py`: `enabled=True`
  immediately instead of break-glass, otherwise the same idempotent creation pattern).
- **`LocalSigningKey`** (singleton row, same pattern as `FederationIdentity`) — its own
  RSA-2048 key pair, generated idempotently on first access, stable `kid` across restarts.
- **`GET /.well-known/jwks.json`** — returns the public key in the same JWKS format as
  Keycloak's `/protocol/openid-connect/certs`, ungated.
- **`local_token_issuer.mint_token()`** — issues tokens with the identical claim shape as Keycloak
  (`sub`/`preferred_username`/`realm_access.roles`/`aud`), plus `jti` (prevents byte-identical tokens
  for two issuances for the same account within the same second, e.g. login immediately followed by
  refresh — actually encountered during test development).
- **`_validator` is a `MultiIssuerTokenValidator`** (new in `libs/dms-auth-client`, selects via the
  `iss` claim) combining the Keycloak and local validator — fully additive, existing Keycloak logins
  remain valid unchanged. A `_LazyValidator` wrapper delays access to
  `app.state.combined_validator` until the first request, since the local signing key only becomes
  available after a DB access in the lifespan.
- **`POST /login`/`POST /refresh` recognize technical accounts**: `/login` checks the username against
  `technical_account` before attempting a Keycloak password grant; `/refresh` peeks at the `iss` claim
  of the presented refresh token (`local_token_issuer.is_local_token`) and branches accordingly.
  Both paths return the same `TokenResponse` shape, regardless of source.
- **`gateway-service`'s own `TokenValidator` is likewise a `MultiIssuerTokenValidator`** (new
  `DMS_AUTH_SERVICE_BASE_URL` setting for the second JWKS source) — without this change, a freshly
  locally logged-in superuser would fail with 401 on every proxied call, even though `auth-service`
  correctly validates its own token.

**Fully verified live against the real running stack** (not just automated tests, Session
2, superuser): complete round trip through the real gateway — login before activation (401) → activation →
login through the gateway (200) → `GET /me` through the gateway (200, proves the gateway's own
multi-issuer switch) → a call against `document-service` with the same token through the gateway is
passed through (422 due to a missing required business field, not 401 — proves system-wide acceptance) →
`POST /refresh` through the gateway (200) → deactivation → subsequent refresh (401).

**Session 3 (domain admins) also verified live**: after rebuilding the `auth-service` image, the
`iss` claim of freshly issued `users-admin`/`config-admin` tokens showed `dms-auth-service-local` instead of
the Keycloak realm URL — both accounts are thereby actually local, no longer via Keycloak. `GET /users`
with a `users-admin` token through the gateway → 200, `GET /me` with a `config-admin` token through the gateway → 200
with `realm_roles: ["domain-admin-config"]`. The old Keycloak accounts for both usernames remain as
unused leftovers (`/login` finds the `TechnicalAccount` first and never reaches the
Keycloak fallback anymore) — no automated cleanup, see ADR 0065 "Consequences".

## SSO/Automatic Login (Ad-hoc Post-Roadmap Feature, see ADR 0062)

Optional, enabled installation-wide via `GET/PUT /sso-config` (singleton row, same pattern as `document-service`'s `ShareLinkConfig`). If SSO is active, `user-ui`'s `login/page.tsx` redirects to Keycloak's own login page BEFORE showing the password form (`GET /oidc/authorize`, the response contains only the URL, the client navigates there itself). If the machine holds a valid Kerberos ticket AND Kerberos is configured (see below), Keycloak's SPNEGO mechanism logs in automatically; otherwise Keycloak itself shows its hosted form — a pure fallback, no break. `POST /oidc/callback` exchanges the code server-side for tokens (`dms-api` is confidential, no PKCE needed, only `state` as CSRF/replay protection) and returns the same `TokenResponse` shape as `/login`.

**Kerberos/SPNEGO** (`_ensure_kerberos`, conditional on `kerberos_enabled`+`kerberos_realm`+`kerberos_server_principal`+`kerberos_keytab_path`): duplicates Keycloak's built-in `browser` flow (which already ships with a disabled-by-default `auth-spnego` execution) to `dms-browser-kerberos`, enables the SPNEGO execution (`requirement=ALTERNATIVE`), points the realm's `browserFlow` at it, and creates a Kerberos user federation component.

**`POST /logout`** is new — previously there was no server-side session teardown, "log out" only cleared local tokens. Calls Keycloak's `.../protocol/openid-connect/logout` with the refresh token, without which a SPNEGO-capable browser would immediately log itself back in automatically on the next visit.

**Not live-verifiable in this sandbox**: the actual automatic login via a real Kerberos ticket (no domain controller/KDC available) — a documented limitation agreed with the user. Fully verifiable and tested: bootstrap idempotency, clean skipping without Kerberos configuration, the complete redirect+callback flow via Keycloak's own form, and `/logout`.

## Events

**Publishes** (`stream="auth"`, since P6-S5): `auth.superuser.activated` (`{request_id, expires_at}`), `auth.superuser.deactivated` (`{reason}`, `"expired"`|`"manual"`). **Since P24-S2**: `auth.ad_group_role_mapping.created`/`.deleted` (`{id, ad_group_name, role_name}`, `actor=`calling principal) — audit trail for changes to the AD group→role mapping, see above.

**Consumes** (`durable="auth-service"`, since P6-S5, first consumer of this service): `permission.approval.approved`, filtered to `action_type="auth.superuser.activate"` — every other action type is ignored (belongs to another service, same principle as described in ADR 0022).

## Contacts (2.5/4.4/7.4, since P15-S4)

Directory for finding other staff — "local, always available" (2.5, literally), optionally cross-installation. Complete architecture rationale: [ADR 0054](../adr/0054-kontakte-directory-independent-second-federation-identity-per-installation.md).

- **Local search**: `admin_users.search_users` uses Keycloak's built-in `search` query parameter (case-insensitive across username/first/last name/email, server-side in Keycloak itself) — no own filter mechanism needed. Response deliberately without an `enabled` field (activation status is an administrative matter). **Corrected via live verification** (originally assumed to be "substring"): Keycloak's `search` matches only as a **prefix** per field, not anywhere within it — `q=admin` does not find `config-admin`, `q=config` does (see ADR 0054 "Open Points").
- **Federated search — own, second federation hub identity**: `auth-service` registers itself a second time with the same hub, independently of `workflow-service`'s already-existing federation participation (P6-S9/P13-S4) (own RSA-2048 key pair, own `installation_id`, display-name suffix `" (Contacts)"`) — `_ensure_federation_identity()` in `main.py`, identical pattern to `workflow_service.main._ensure_federation_identity`. Opt-in via `DMS_FEDERATION_HUB_BASE_URL`; additionally `DMS_FEDERATED_DIRECTORY_ENABLED` must be `true` for the federated endpoints to actually be active (two separate switches: hub registration vs. actual opt-in for search requests).
- **Capability marking**: registers with `supported_process_types=["dms.contact-directory.v1"]` — repurposes the already-existing, generic list field in `federation-hub-service`'s `Installation` model as a capability marker (`directory_federation.CONTACT_DIRECTORY_CAPABILITY`), no code change needed in federation-hub-service.
- **Direct installation-to-installation requests, not relayed via the hub**: `GET /users/directory/federated` queries every peer installation known via `GET /installations` that is not revoked and has opted in to contact search DIRECTLY via its `callback_base_url` (`directory_federation.search_all_peers`/`query_peer`), signed with its own private key (`X-Installation-Signature`, RSA-PSS/SHA-256, identical scheme to ADR 0039 — `federation_crypto.py`, duplicated as already twice before in this project, see ADR 0054). A single unreachable/rejecting peer does not block the others.
- **`POST /users/directory/federated-search-inbound`**: receives a signed request from a peer installation — verifies `X-Installation-Signature` against the public key of the REQUESTING installation stored at the hub (fetched live via `GET /installations`, no local peer key store), rejects unknown/revoked/not-registered-for-contact-search installations with `401`. Public route at the gateway (`gateway-service`'s `public_routes`, no `X-DMS-Principal`), analogous to `workflow-service`'s `federation/inbound`.
- **No end-to-end encryption of the payload** (unlike the handover scheme) — the hub is never in the request path anyway for direct calls, see ADR 0054 "Rationale".

## AD Group→Role Mapping (4.4, since P24-S2)

Configurable mapping of Keycloak/AD group memberships onto internal DMS roles — before this
session there was no translation layer at all for this, `/me` returned only Keycloak's raw
`realm_access.roles`. Complete architecture rationale/scope boundary: [ADR 0093](../adr/0093-ad-group-role-mapping-simple-1to1-scope-cut.md).

- **`ad_group_role_mapping`** (model `AdGroupRoleMapping`, `auth` schema): `id`, `ad_group_name`,
  `role_name`, `created_at`, `created_by` — **its own, lean table**, deliberately NOT
  `permission-service`'s `Group`/`GroupMembership`/`RoleAssignment` (Post-Roadmap Phase 22 Session 2,
  ADR 0088): those model ADMIN-CREATED groups with their own membership table to be kept in sync —
  a different, independent function from mapping EXTERNAL
  Keycloak/AD group claims here. `UniqueConstraint(ad_group_name, role_name)` only prevents exactly
  duplicate rows — an AD group name can map to multiple roles (multiple rows).
- **Deliberate scope cut relative to Concept 4.4**: only simple 1:1 mapping (one AD group → one
  role). Composite rules ("group X **and** attribute Y → role Z", "multiple groups → one
  shared role") are, per Concept 4.4, envisioned as the full target state, but explicitly NOT
  part of this session — see "Open Points" below and ADR 0093.
- **`groups` JWT claim**: Keycloak does not automatically include group memberships in the access
  token — `bootstrap._ensure_groups_mapper` (runs on every start, see "Realm/Client Bootstrap"
  above) adds an `oidc-group-membership-mapper` (`full.path=false`, i.e. only the bare group name,
  no Keycloak-internal path).
- **`ad_group_mapping.resolve_roles_for_groups(session, groups)`**: pure read function, evaluated fresh
  against the table on EVERY `GET /me` request (no caching) — a change/deletion of a
  mapping thus takes effect from the next call onward with no invalidation problem. `GET /me` merges the
  result into the same `realm_roles` field as Keycloak's directly assigned roles (deduplicated,
  `dict.fromkeys`) — deliberately no separate field, see ADR 0093 "Rationale".
- **`GET`/`POST`/`DELETE /ad-group-mappings`** (see API table above): CRUD for the mapping rows,
  gated on `admin.user_management` (same domain as `GET /users`/`POST /realm-roles` — a
  misconfigured mapping can silently grant users additional roles).
- **Audit**: `POST`/`DELETE` publish `auth.ad_group_role_mapping.created`/`.deleted`
  (`actor=`calling principal) via the existing event bus mechanism — `audit-service`
  already consumes the entire `auth.>` subject (since P6-S5), no new audit mechanism needed.
  `created_by`/`created_at` additionally stored directly on the row.

## Realm Role Management (14.1, since P17-S1)

Until P17-S1, every Keycloak realm role was created individually, hard-coded in the bootstrap
(`bootstrap._ensure_dms_admin_role` for `dms-admin`) — no generic way to create a NEW realm role
without changing code and redeploying the service. `GET`/`POST /realm-roles`
generalizes exactly the same primitive (`create_realm_role(..., skip_exists=True)`) to arbitrary
names, so a configuration package (`config-service`'s new `realm_roles` category, e.g. for
`dms-poststelle`, 2.5) can bring them along without inventing a new mechanism. **Deliberate
limitation, identical to the existing `dms-admin` pattern**: the endpoint only creates the role, it does
not assign it to anyone — assignment to specific users remains outside this service, via the
Keycloak Admin Console (see "Open Points"). Details/rationale see
[ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md).

## Self-Registration (Concept 3.2a, since P4-S1)

Registers itself with the registry at startup (`libs/dms-registry-client`: register, periodic heartbeat, deregister on shutdown) - basis for the API gateway's routing (`docs/services/gateway-service.md`). Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; without both values the service runs unchanged without discovery.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Tests

`uv run pytest services/auth-service/tests` (**105 tests**, of which 9 new since **P24-S2**,
`test_ad_group_mapping.py`: CRUD (`GET`/`POST`/`DELETE /ad-group-mappings`, without a bearer token → `401`,
with an authenticated but not `admin.user_management`-permitted user → `403`, unknown `id`
on delete → `404`) as well as the role resolution itself against real Keycloak groups (`keycloak_group`
fixture): a principal in a mapped group gets the mapped role in `GET /me`'s `realm_roles`,
a principal in two mapped groups gets both roles, a principal in a NOT mapped
group remains unchanged, a deletion of the mapping takes effect from the next `/me` call (no
caching). Runs entirely against real Postgres/Keycloak (real groups/memberships via
`KeycloakAdmin.create_group`/`group_user_add`), no mocks. Before that 96 tests, of which 5 new since **P17-S1**,
`test_realm_roles.py`: `GET /realm-roles` contains the already-bootstrapped `dms-admin`, excludes
Keycloak built-ins, `POST /realm-roles` without/with an unauthorized principal → `403`, creates a
new role idempotently — a second call with the same name does not fail, same
`authorized_principal` fixture pattern as `config-service`'s `tests/conftest.py`. Of these, 11 since
**P15-S4**, `test_directory.py`): local directory search (authentication required, prefix match per field, available to regular users not just domain admins), federation status default, `403` on the federated endpoints without activation, as well as a real self-registration against the running `federation-hub-service` (`federation_enabled` fixture, monkeypatches `settings` before a fresh `TestClient(app)`) including a real signature verification path (valid/invalid signature, unknown installation, own installation excluded from federated results) — runs against real Postgres/Keycloak/`federation-hub-service`, no mocks. **Bug found and fixed along the way**: `FederationHubClient.register()` originally did not transmit `supported_process_types` to the hub at all — the service's own capability marker (`dms.contact-directory.v1`) would thereby never actually have been visible in the address book, every incoming federated request (even a legitimate one) would have been rejected with `401`. Only made visible through the actual live self-loopback test, not through pure mocking. **Additional finding during live verification against the running gateway**: contrary to the original assumption, Keycloak's `search` parameter is not a substring match but a prefix match per field — documentation corrected accordingly (see above, ADR 0054 "Open Points"). Also observed: repeated `federation_enabled` test runs leave real, permanent registrations in the shared `federation-hub-service` address book (no cleanup possible without a configured `hub_operator_key`) — see ADR 0054 "Open Points".

## Open Points

- **AD group → internal role mapping — partially solved since P24-S2**: simple 1:1 mapping
  (`ad_group_role_mapping`, `GET`/`POST`/`DELETE /ad-group-mappings`, see "AD Group→Role Mapping"
  above, ADR 0093) is implemented. Still open, envisioned per Concept 4.4, but a deliberate
  scope cut for this session:
  - **Composite rules** (group X **and** attribute Y → role Z; multiple groups → one
    shared role via AND logic) — no generic rule DSL, only direct 1:1 name mapping.
  - **Configurable default behavior for unmapped groups** ("assign no role vs.
    a defined default role") — currently fixed to "no role", no setting for this.
  - **Explicit release/save before taking effect** ("no live editing with immediate
    broad impact without control") — every change takes effect immediately, no additional
    four-eyes/approval step like `permission.role_assignment.create` (ADR 0060).
  - **No AD synchronization interval/no user/group synchronization** — group memberships
    are read exclusively from the `groups` JWT claim at token-acquisition time, no
    periodic reconciliation.
  - **No JSON configuration export** (7.3) — `ad_group_role_mapping` rows are not part of
    `config-service`'s configuration packages, so cannot be transferred between installations.
  Role assignment/evaluation in the narrower sense remains the task of the Permission Service (4.1,
  P2-S2) — `auth-service` only supplies the role names, no permission check of its own.
- **Issuer hostname consistency — partially solved since the ad-hoc post-roadmap SSO feature**: the Auth Service addresses Keycloak internally via `DMS_KEYCLOAK_BASE_URL` (`http://keycloak:8080` inside the compose network); issued tokens accordingly carry `iss=http://keycloak:8080/realms/dms`. With the new browser-based redirect flow (`standardFlowEnabled`, since the SSO feature), exactly the consequence predicted here became real: `GET /oidc/authorize` returns a URL to which the browser navigates — with the internal `http://keycloak:8080` this would not have been resolvable for the browser. Fixed via a new, separate `keycloak_public_base_url` setting (`DMS_KEYCLOAK_PUBLIC_BASE_URL`, `http://localhost:8080` in the compose stack), used only by `_authorization_endpoint` (in `keycloak_client.py`) — token/logout endpoints remain on the internal URL, since they are called exclusively server-side from within `auth-service`. `iss` in the token itself remains the internal URL (Keycloak's own `frontendUrl` configuration would be the complete fix for this, deliberately not touched here, since `TokenValidator` already consistently checks against the same internal issuer).
- **SAML 2.0** (Concept 4.4, for legacy ADFS federations) not part of this session.
- **`/users` endpoints gated since P6-S5** (see above) — resolves the former open point for this service. Assigning `admin.user_management` to *additional* principals (e.g. real humans in addition to the technical `users-admin` account) runs via the now itself gated user/permission-management Admin UI page (`POST /role-assignments` against `permission-service`).
- **No role assignment API/UI for Keycloak realm roles** (since P5e-S2, only partially solved since P17-S1): `dms-admin`/`dms-poststelle` etc. are Keycloak realm roles, not a native `permission-service` construct (unlike the domain admin roles from P6-S5). Since P17-S1 there is at least a generic **creation** path (`POST /realm-roles`, e.g. from a configuration package) — but **assignment** to specific users remains exclusively via the Keycloak Admin Console, no API/UI for it in this project.
- **5 of the 7 domain admin roles from 4.6 without an associated technical account** (since P6-S5/S6): `domain-admin-storage`/`-license`/`-query-console`/`-deletion`/`-deletion-vs` exist only as a `Role` row in `permission-service`, without a Keycloak account and without any endpoint checking them — will follow with the respective domain's future retrofit session. `domain-admin-config` has been enforced since **P6-S6** (`config-admin` account, `workflow-service`'s process-definition endpoints).
- **No elevated audit priority during an active superuser session** (4.6, since P6-S5): `audit-service` consumes the break-glass lifecycle events (`auth.>`) at normal priority; third-party actions performed *while* activation is in effect in other services are not specially marked.
- **No rolling inactivity deactivation** (4.6, since P6-S5): a single absolute expiry timestamp instead of separate total-duration/10-minute-inactivity timers, see ADR 0023.
- ~~**Bug discovered at P6-S6, not fixed (P6-S5 code)**: the superuser account cannot log in interactively in the current live environment (`POST /login` returns `401`/"Account is not fully set up" directly from Keycloak). Cause: `firstName`/`lastName`/`email` missing on the Keycloak account...~~ — **disappeared without replacement since Phase 18 Session 2** ([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)): the superuser is no longer a Keycloak account, there is no more declarative-user-profile required-field problem that could cause this state.
- **`GET /users/lookup` is an existence oracle** (since P14-S6): any authenticated user can find out whether a particular username exists — deliberately left as is (internal management software, known user population), but a documented deviation from the previous state (user directory fully behind `admin.user_management`). Since P19-S3 (ADR 0068) gated via the "everyone" group instead of hard-coded open — an admin can revoke `users.lookup` from the "everyone" role to close the oracle, without a code change. See [ADR 0043](../adr/0043-teamspace-service-membership-and-permission-integration.md).
