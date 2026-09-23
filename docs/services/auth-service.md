# auth-service

**Responsibility:** Thin OIDC broker in front of Keycloak — holds the client secret and admin access, callers only see login/refresh/token validation (Concept 4.4). No own IAM logic, no own user table.

**Concept Reference:** 4.4/2.5 (contacts, since P15-S4)/7.4 (federated contact search, since P15-S4)/14.1 (realm roles for configuration packages, since P17-S1)
**Own Postgres Schema:** `auth` (since P15-S4, `federation_identity` — a singleton row for the optional federated contact search; since the ad-hoc post-roadmap SSO feature additionally `sso_config`, also a singleton row; since Phase 18 additionally `local_signing_key` (singleton) and `technical_account`, see "Auth Decoupling from Keycloak" below; since **P24-S2** additionally `ad_group_role_mapping`, see "AD Group→Role Mapping" below; since
**Post-Roadmap Phase 39 Session 3** additionally `ad_group_role_composite_rule`,
`ad_group_role_composite_rule_group`, and the singleton `ad_group_mapping_default_role`, ADR 0153; since **Post-Roadmap Phase 41 Session 3** additionally `user_tracking_config`, `user_tracking_session`, and the singleton `user_tracking_retention_config`, see "Fine-Grained User Tracking" below, [ADR 0157](../adr/0157-fine-grained-user-tracking-privileged-accounts.md)). Until P15-S4 the service was fully stateless; Keycloak itself continues to manage its own data in its own schema `keycloak` (see `infra/postgres-init/001-schemas.sql`).

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/login` | `{username, password}` → password grant against Keycloak, returns access/refresh tokens. **Since P6-S6**: reads `X-DMS-Maintenance-Active` (injected by the gateway, 4.8) — if maintenance mode is active and `username` is not the superuser account, `503` instead of login. **Since Phase 18 Session 2/3**: recognizes technical accounts (`technical_account` table lookup) before the Keycloak path and authenticates them locally (bcrypt) — since Session 2 the superuser, since Session 3 additionally both domain admin accounts, see "Auth Decoupling from Keycloak" below |
| `POST` | `/refresh` | `{refresh_token}` → new tokens. **Since Phase 18 Session 2**: recognizes locally issued refresh tokens via the `iss` claim and issues a fresh pair without Keycloak involvement |
| `GET` | `/me` | Validate bearer token (JWKS, stateless, no round trip to Keycloak), return normalized identity. **Since P24-S2**: `realm_roles` additionally contains the roles derived from the `groups` JWT claim (AD group→role mapping, 4.4) alongside Keycloak's raw `realm_access.roles`, merged and deduplicated into the same list — see "AD Group→Role Mapping" below |
| `GET` | `/users` | List users (since P4-S3, basis for the Admin UI user management) — reads directly from Keycloak. **Gated since P6-S5**: requires the capability `admin.user_management` (domain "user/permission management", 4.6), otherwise `403` |
| `POST` | `/users` | Create user (`username`, `email`, `password`, `first_name`, `last_name`) — 409 for an already-taken username. Gated like `GET /users`. **Since Post-Roadmap Phase 42 Session 2**: also checks `license-service`'s `"users"` usage dimension first, `403` if exceeded — see "License-Limit Block on New Users" below |
| `DELETE` | `/users/{id}` | Delete user — 404 for an unknown `id`. Gated like `GET /users`. **Since P55-S2**: after the Keycloak deletion, also revokes every `permission-service` `RoleAssignment` for this principal and removes their `teamspace-service` memberships — both fail-soft (logged, not raised), see "Cross-Service Cleanup on User Deletion" below |
| `GET` | `/users/{id}` | **Since P19-S4** (ADR 0069): reverse identity resolution, counterpart to `GET /users/lookup` — returns only `{id, username}`, `404` for an unknown `id`. Same gate as `GET /users/lookup` (`users.lookup` via the "everyone" group). Must be registered after all static `/users/...` paths (registration order, see ADR 0069) |
| `GET` | `/me/preferences` | Theme and locale preference of the logged-in account (`{theme, locale}`, defaults `"auto"`/`"de"`) — since P4-S6, `locale` added in Phase 47 Session 1 |
| `PUT` | `/me/preferences` | Partial update: `{theme?, locale?}` (`theme` ∈ `light`/`dark`/`high-contrast`/`auto`, `locale` ∈ `de`/`en`, otherwise 422) — a field omitted from the body is left unchanged, see "Theme/Locale Preference" below |
| `GET` | `/superuser/status` | Break-glass status (4.6, since P6-S5): `{active, expires_at}`, since **P6-S6** additionally `principal_id` (since Phase 18 Session 2 the `TechnicalAccount.id`, previously the Keycloak `id`, for the Permission Service's not-shutdown lift check, 4.8) — 404 if the superuser account has not yet been created |
| `POST` | `/superuser/deactivate` | Early, voluntary deactivation (since P6-S5) — complements the automatic expiry enforcement via the poll loop |
| `GET` | `/users/lookup` | Exact name resolution (`?username=`) — returns only `{id, username}`, `404` for an unknown name. New in P14-S6, for `teamspace-service`'s invite-by-username (2.5): deliberately NOT gated behind `admin.user_management` like `GET /users` above — every person, not just domain admins, should be able to invite others to a team workspace. Since P19-S3 (ADR 0068) gated via the "everyone" group from permission-service (`users.lookup`, pre-seeded since P19-S2) instead of only `Depends(get_current_user)` — actual behavior is unchanged, but the permission is now admin-editable. See [ADR 0043](../adr/0043-teamspace-service-membership-and-permission-integration.md) |
| `GET` | `/users/count` | Internal call from `license-service` (9.1 "named accounts" model, since P9-S1) — ungated, since no service holds a real Keycloak bearer token for `Depends(get_current_user)` |
| `GET` | `/sessions/count` | Internal call from `license-service` (9.1 "concurrent users" model, since P9-S1) — `KeycloakAdmin.get_client_sessions_stats()`, ungated |
| `GET` | `/users/directory?q=` | Directory search (2.5/4.4, since P15-S4, Keycloak `search` parameter — prefix per field, no substring, see "Contacts" below) — no `admin.user_management` gate, but since P19-S3 (ADR 0068) checked via the "everyone" group from permission-service (`users.directory`) instead of merely requiring authentication |
| `GET` | `/users/service-directory` | **New in Phase 50 Session 2**: service-to-service counterpart to `GET /users`, `X-DMS-Principal`-gated via the new, narrow `service.user_lookup` capability (seeded role `service-user-lookup`) instead of `admin.user_management` — for callers that need to scan the full directory (recipient existence by email/username, signer resolution by username) where neither `GET /users/lookup` (exact username only) nor `GET /users/directory` (prefix search only) can answer reliably. Returns `DirectoryEntryOut` (no `enabled`), same shape as `GET /users/directory`. Replaces `notification-service`/`signature-service`'s previous `users-admin` login for this exact purpose — see "Service-to-Service Directory Lookup" below |
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
| `POST` | `/ad-group-mappings` | **Since P24-S2**: creates a new mapping (`{ad_group_name, role_name}`). Takes effect from the next `GET /me` resolution onward. Gated like `GET /ad-group-mappings`. **Since Post-Roadmap Phase 39 Session 3** ([ADR 0153](../adr/0153-ad-group-mapping-composite-rules-default-role-four-eyes-export.md)) also optionally gated via the generic four-eyes mechanism (`auth.ad_group_role_mapping.create`) — response `{status: "created"\|"pending_approval", mapping, approval_request_id}` (bare object before this session), `201`. Audited via `auth.ad_group_role_mapping.created` |
| `DELETE` | `/ad-group-mappings/{id}` | **Since P24-S2**: deletes a mapping, `404` for an unknown `id`. Audited via `auth.ad_group_role_mapping.deleted`. Gated like `GET /ad-group-mappings`. **Since Post-Roadmap Phase 39 Session 3** (ADR 0153) also optionally four-eyes-gated (`auth.ad_group_role_mapping.delete`) — response `{status: "deleted"\|"pending_approval", approval_request_id}`, `200` (was `204` before this session) |
| `GET`/`POST`/`DELETE` | `/ad-group-composite-rules`(`/{id}`) | **Since Post-Roadmap Phase 39 Session 3** (ADR 0153): AND-composite counterpart of the three endpoints above (`{role_name, ad_group_names}`, at least 2 groups or `422`) — same gate/four-eyes/audit pattern, action types `auth.ad_group_role_composite_rule.create`/`.delete` |
| `GET`/`PUT` | `/ad-group-mappings/default-role` | **Since Post-Roadmap Phase 39 Session 3** (ADR 0153): the configurable default role for genuinely unmapped groups (`{default_role_name, updated_at, updated_by}`) — gated on `admin.user_management`. `GET` returns the bare object, unchanged. `PUT`, since **Phase 53 Session 1** ([ADR 0171](../adr/0171-ad-group-mapping-default-role-four-eyes-and-display-name-fix.md)), joined the four-eyes mechanism the other mutations already had (`auth.ad_group_mapping.default_role_set`) — response `{status: "set"\|"pending_approval", config, approval_request_id}` (bare object before this session) |
| `GET`/`POST` | `/ad-group-mapping-config`(`/import`) | **Since Post-Roadmap Phase 39 Session 3** (ADR 0153): `config-service`'s export/import target for the whole AD-group-mapping bundle (mappings + composite rules + default role) — service-to-service-gated (`X-DMS-Principal`/`_require_service_user_management`) like `POST /realm-roles`, not the bearer-token endpoints above; import is idempotent per item and deliberately bypasses four-eyes, same precedent as `POST /realm-roles` |
| `GET`/`PUT` | `/user-tracking-config/{principal_id}` | **Since Post-Roadmap Phase 41 Session 3** (5.5, [ADR 0157](../adr/0157-fine-grained-user-tracking-privileged-accounts.md)): per-principal opt-in for fine-grained session tracking (`{principal_id, enabled, updated_by, updated_at}`) — `GET` synthesizes a default `enabled: false` shape instead of `404` for a principal with no row yet (the normal, expected state). Both gated on `admin.user_tracking` (role `domain-admin-user-tracking`). **Since Post-Roadmap Phase 74 Session 2**: `PUT` returns `UserTrackingConfigActionResult` (`{status: "applied"\|"pending_approval", config, approval_request_id}`) — deferred to four-eyes approval when configured for `action_type="auth.user_tracking_config.update"`, same pattern as the AD-group-mapping endpoints (ADR 0153) |
| `GET` | `/user-tracking-sessions?principal_id=` | **Since Post-Roadmap Phase 41 Session 3**: tracked login/refresh events (`{id, principal_id, username, event_type, auth_method, client_ip, user_agent, occurred_at}`) — gated on a SEPARATE, higher-risk capability, `admin.user_tracking_view` (role `domain-admin-user-tracking-view`), since viewing already-collected data exposes behavioral information the toggle above does not |
| `GET`/`PUT` | `/user-tracking-retention-config` | **Since Post-Roadmap Phase 41 Session 3**: configurable retention period for tracked sessions (`{retention_days, updated_at}`, concept default 7 days, `422` if `< 1`) — gated on `admin.user_tracking`, same as the toggle above (a config knob, not exposed data) |

## Realm/Client Bootstrap

On every start (`ensure_realm_and_client`, idempotent via `skip_exists=True`):
- Realm `dms`
- Confidential client `dms-api` with `directAccessGrantsEnabled=true`, `standardFlowEnabled=false` (no browser redirect flow in this session)
- Audience mapper, so that `aud` in the access token contains `dms-api` instead of just `account` (Keycloak default without a mapper)
- Declared user profile attributes `dms_theme` (since P4-S6) and `dms_locale` (since Phase 47 Session 1, see below) — without this declaration, Keycloak's declarative user profile silently drops the attribute on every `update_user` call
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

## Theme/Locale Preference (Concept 8, since P4-S6, `locale` since Phase 47 Session 1)

Cross-UI theming (light/dark/high-contrast/automatic, User UI and Admin UI) stores its preference on the user account across devices instead of only locally in the browser — rationale and pitfalls (declarative user profile trap) in [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). Summary: `dms_theme` is a declared Keycloak user attribute, read/written via the existing admin client (`admin_users.get_theme_preference`/`set_theme_preference`), exposed via `/me/preferences`.

**Since Phase 47 Session 1** ([ADR 0167](../adr/0167-locale-switcher-pattern-and-office-addin-host-locale.md)): UI display language (`de`/`en`) added the same way, as its own independent Keycloak attribute `dms_locale` (`admin_users.get_locale_preference`/`set_locale_preference`) — kept separate from `dms_theme` so either preference can be read/written without touching the other. `PUT /me/preferences` takes a dedicated `PreferencesUpdate` body with both fields optional/`None`-defaulted (not `ThemePreference`'s own defaults): only a field actually present in the request is written, so an existing caller that only ever sends `{"theme": ...}` (every app before this session) cannot accidentally reset `locale` back to its default, and vice versa.

That same ADR also fixed a real, pre-existing bug present since P4-S6: `set_theme_preference`/`set_locale_preference` called `admin.update_user(user_id, {"attributes": ...})`, which Keycloak's admin API treats as a full user-representation replacement, not a merge — silently wiping `firstName`/`lastName`/`email` on every preference change. Fixed by spreading the just-read full user representation (`{**raw, "attributes": attributes}`) before writing.

**Another real, pre-existing bug found live in Phase 49 Session 3, fixed in Phase 51 Session 2**: `GET`/`PUT /me/preferences` `500`d on every single call for a `TechnicalAccount`-authenticated caller (`users-admin`, `config-admin`, `superuser`, etc., decoupled from Keycloak since Phase 18 Session 3/ADR 0065) — `user["sub"]` for such a caller is the account's own local integer row id, not a Keycloak UUID, so the unconditional `KeycloakAdmin.get_user()` call 404d, surfacing as a cross-app `500` on every single admin-ui page load while logged in as one of these accounts (every page loads the theme/locale switcher). Confirmed cross-app and pre-existing (not caused by whatever session happened to notice it) for two full phases before finally getting a session. Fixed by checking `user["iss"] == local_token_issuer.LOCAL_ISSUER` (the same claim `POST /refresh` already uses to route between the local and Keycloak token paths) and short-circuiting to `ThemePreference`'s own defaults on `GET`, or an echo of the requested fields on `PUT`, instead of attempting a Keycloak call that can never succeed for this caller. Nothing is persisted server-side for a technical account either way — the frontend's `localStorage` cache still applies the choice immediately within the same browser (ADR 0009's already-established fire-and-forget design), the only loss is cross-device sync, which a shared technical account arguably shouldn't have anyway.

## Domain-Separated Admin Roles (4.6, since P6-S5)

Domain admin "roles" are deliberately **not Keycloak realm roles** (unlike `dms-admin`), but native `Role` rows in `permission-service` (see `docs/services/permission-service.md`) — `auth-service` only creates the associated **technical accounts** and assigns them the role via an HTTP call to `permission-service` (`permission_client.py`, `PermissionServiceClient.ensure_role_assignment`). Complete architecture rationale, see [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). Currently actually created: `users-admin` (domain "user/permission management") and `config-admin` (domain "workflow configuration", since P6-S6) — **as `TechnicalAccount` rows instead of Keycloak accounts since Phase 18 Session 3** ([ADR 0065](../adr/0065-domain-admin-migration-lokale-technische-konten.md)), see "Auth Decoupling from Keycloak" below. Role assignment continues to run best-effort at lifespan startup — if `permission-service` is not yet reachable (or the assignment on this installation requires four-eyes approval and has not yet been approved), it is skipped and retried on the next restart (no retry loop).

## Service-to-Service Directory Lookup (Phase 50 Session 2)

`notification-service`'s recipient-existence check (`POST /notifications`, P6-S6) and
`signature-service`'s signer-resolution check (same retrofit pattern) both need to scan the full user
directory — neither `GET /users/lookup` (exact username only) nor `GET /users/directory` (prefix search
only) can reliably answer "does any user have this email" or "resolve this username to a display name".
Both services previously solved this by authenticating as the `users-admin` technical account
(`POST /login` on every call) purely to reach the `admin.user_management`-gated `GET /users` — a real
excess-privilege exposure: `users-admin` can create/delete arbitrary users and rewrite the AD-group→role
mapping that governs privilege assignment installation-wide, not merely read a directory. A credential
leak or compromise of either service would have granted full user-management control, not just
directory-read.

Fixed via a new, narrow, service-only capability instead of reusing the broad one: `GET
/users/service-directory` (see the API table above), gated by `service.user_lookup` via
`_require_service_user_lookup` — same `X-DMS-Principal`-trusted mechanism as
`_require_service_user_management`/`POST /realm-roles` (see "Realm Role Management" below), but a
distinct, narrower capability, not a reuse of `admin.user_management`. The seeded role
`service-user-lookup` (`permission-service` `repository.py`, same non-"domain-admin-..." naming
convention as `archival-service-callback`) is auto-created on every fresh installation, but — like
`archival-service-callback` — its **assignment** to the `notification-service`/`signature-service`
principals is a one-time, per-installation operator step (`POST /role-assignments`), not automated by
any running service's own startup code; each service's own test suite grants it to its own fixed
identity via an autouse `conftest.py` fixture (mirroring `document-service/tests/conftest.py`'s
`_grant_disposal_callback_permission`), exercising the exact real caller identity used in production.

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

## Fine-Grained User Tracking (5.5, Post-Roadmap Phase 41 Session 3, [ADR 0157](../adr/0157-fine-grained-user-tracking-privileged-accounts.md))

Concept 5.5, verbatim: "vollständige Session-Metadaten (u. a. Client-IP, Geräte-/Browser-Fingerprint, ... Authentifizierungsmethode, Zeitpunkt/Dauer der Session, ggf. bekannte Netzwerk-/Standortinformationen)" for privileged accounts, default-active for the activated superuser (4.6), individually toggleable for others, with its own configurable retention (default 7 days) separate from the regular audit log (5.3).

- **Hooked into all three token-minting endpoints** (`POST /login`, `POST /refresh`, `POST /oidc/callback`) via one shared helper, `_maybe_track_session_event` — decodes the JUST-ISSUED access token (not any caller-supplied claims) so it works identically regardless of which branch produced the tokens (technical-account/Keycloak login, either refresh branch, SSO callback). Never allowed to propagate an exception — a tracking bug must not lock anyone out of logging in.
- **Default off, per-principal opt-in** (`UserTrackingConfig`, `PUT /user-tracking-config/{principal_id}`) — a principal with no row is simply not tracked. The activated superuser is the ONE exception, and deliberately NOT via a persisted flag: `_maybe_track_session_event` checks `superuser.get_status()`/`get_principal_id()` directly, tying the default to the live activation state itself (matching the concept's own wording literally — "default active *while activated*"), so activating/deactivating break-glass never needs to keep a separate tracking flag in sync.
- **Two separate capabilities** (`admin.user_tracking` for the toggle/retention config, `admin.user_tracking_view` for viewing collected session data) — same asymmetric-risk split this project already uses for `admin.attribute_pseudonymization`/`admin.attribute_reveal` (Post-Roadmap Phase 41 Session 2, ADR 0156): toggling REDUCES what's captured going forward, viewing EXPOSES already-captured behavioral data about a specific principal.
- **Captured fields, deliberately limited to what's server-side determinable without new infrastructure**: `client_ip` (new `X-DMS-Client-IP` header, forwarded by the gateway unconditionally since this session — previously `request.client.host` was computed there only for its own rate limiting, never passed downstream), `user_agent` (the standard header, passed through unchanged by the gateway's own `filter_headers`), `auth_method` (`technical_account`/`keycloak`/`sso`). **No GeoIP/network-location lookup, no client-side canvas/font fingerprinting** — both deliberately out of scope, see ADR 0157 "Rationale". **"Session duration" is not a stored, correlated login/logout pair** — no session-id concept exists anywhere in this service to correlate events by; approximated at display time as time-since-last-login instead.
- **Own, shorter retention** (`UserTrackingRetentionConfig`, `GET`/`PUT /user-tracking-retention-config`, concept default 7 days) — enforced by its own poll loop (`_tracking_retention_poll_loop`, `tracking_retention_poll_interval_seconds`, default 3600s, same idiom as `_superuser_poll_loop`), completely independent of the regular audit log's own retention rules (5.2/5.3).
- **Admin-UI page since Phase 52 Session 1** (`/user-tracking/`, `admin-ui`) — had been API/curl-only until this session. The one page so far whose `RequireCapability` wraps an ARRAY (`["admin.user_tracking", "admin.user_tracking_view"]`, OR semantics — a genuinely new need, since the backend gates its two sections on two independent capabilities and no other admin page had that shape) instead of a single capability string; each section then does its own finer-grained check so a view-only auditor sees sessions without the config form, and vice versa. No list-all endpoint exists for per-principal config, so that section is a lookup-by-id form rather than AdGroupMappings.tsx's "load everything" shape.

## License-Limit Block on New Users (Concept 9.3, Post-Roadmap Phase 42 Session 2)

`POST /users` checks, right after the existing `admin.user_management` gate, via a new, thin `license_client.py` (`LicenseLimitClient`, 30s TTL cache, fail-open "not exceeded" — the same shape as `document-service`'s original P9-S2 client, deliberately duplicated rather than shared) whether `license-service`'s `GET /license/status` reports the `"users"` dimension in `limits_exceeded` — if so, `403`. This is the one and only endpoint anywhere in this codebase that creates a new named account (no AD/Keycloak self-registration flow exists in the repo), so no other call site needed the check. Brings `"users"` to parity with `document-service`'s pre-existing `"documents"`/`"storage_gb"` blocking, see `docs/services/license-service.md`'s "Usage-Limit Blocking (9.3), All Three Dimensions".

**A real bug found only by live verification, not by the test suite**: `infra/docker-compose.yml`'s `auth-service` block had never needed an outbound call to `license-service` before this session and was missing `DMS_LICENSE_SERVICE_BASE_URL` entirely — the client's default (`http://localhost:8023`) is unreachable from inside the container, and its fail-open design meant the check silently always passed (a warning logged, no error surfaced) until the missing environment variable was added. Both services' pytest suites monkeypatch `LicenseLimitClient.is_exceeded` directly and never make a real network call, so this could only be caught by actually exercising the real Compose network.

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

**Publishes** (`stream="auth"`, since P6-S5): `auth.superuser.activated` (`{request_id, expires_at}`), `auth.superuser.deactivated` (`{reason}`, `"expired"`|`"manual"`). **Since P24-S2**: `auth.ad_group_role_mapping.created`/`.deleted` (`{id, ad_group_name, role_name}`, `actor=`calling principal) — audit trail for changes to the AD group→role mapping, see above. **Since Post-Roadmap Phase 39 Session 3** (ADR 0153): `auth.ad_group_role_composite_rule.created`/`.deleted` (`{id, role_name, ad_group_names, created_at, created_by}`) and `auth.ad_group_mapping.default_role_set` (`{default_role_name}`) — same subject/consumption mechanism, no new audit path needed.

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
- **Composite (AND) rules, added Post-Roadmap Phase 39 Session 3** ([ADR 0153](../adr/0153-ad-group-mapping-composite-rules-default-role-four-eyes-export.md)):
  `AdGroupRoleCompositeRule` (`id`, `role_name`, `created_at`, `created_by`) plus
  `AdGroupRoleCompositeRuleGroup` (`id`, `rule_id` FK, `ad_group_name`) - a rule grants `role_name`
  only if a principal belongs to EVERY linked group (AND logic), as opposed to the simple table's
  OR-across-rows semantics. Deliberately ADDITIVE, not a replacement - both mechanisms are unioned in
  `resolve_roles_for_groups`, so every pre-existing simple mapping keeps working unchanged. At least 2
  distinct groups per rule (`422` otherwise, a 1-group rule would duplicate the simple mechanism).
  `GET`/`POST`/`DELETE /ad-group-composite-rules` (see API table above), same gate/four-eyes pattern as
  the simple mapping endpoints.
- **Configurable default role for genuinely unmapped groups, added Post-Roadmap Phase 39 Session 3**
  (ADR 0153): singleton `AdGroupMappingDefaultRole` (`id=1`, `default_role_name`, `updated_at`,
  `updated_by`, same pattern as `SsoConfig`). Applied only when the principal has at least one AD group
  claim AND neither the simple table nor any composite rule matched - deliberately NOT applied to a
  principal with no AD group claim at all, a narrower reading than "any unmapped principal" to avoid an
  unintended broad grant. `GET`/`PUT /ad-group-mappings/default-role`, gated on `admin.user_management`
  like the rest of this surface. `PUT` gained optional four-eyes since **Phase 53 Session 1**
  ([ADR 0171](../adr/0171-ad-group-mapping-default-role-four-eyes-and-display-name-fix.md)), reversing
  ADR 0153's own deliberate "single scalar setting, not a mapping row" scope cut on explicit request -
  same `_maybe_defer_to_approval` pattern, action type `auth.ad_group_mapping.default_role_set`.
- **Initiator display-name resolution, added Phase 53 Session 1** ([ADR 0171](../adr/0171-ad-group-mapping-default-role-four-eyes-and-display-name-fix.md)):
  a mapping/rule/default-role set via the four-eyes/consumer path now records the initiator's resolved
  Keycloak username as `created_by`/`updated_by`, not their raw `sub` - `consumer._resolve_display_name()`
  reuses `admin_users.find_user_by_id` (`GET /users/{user_id}`, ADR 0069), the same reverse-identity-
  resolution primitive already used for delegations/teamspace member lists, called server-side at
  execution time rather than extending the approval-request payload itself.
- **Admin UI, added Phase 50 Session 5** — this whole surface was API-only until this session despite
  having full backend CRUD since P24-S2/ADR 0153. New `admin-ui` page (`/ad-group-mappings/`, `apps/
  admin-ui/src/components/AdGroupMappings.tsx`), following the established `RequireAuth`→
  `RequireCapability`→`AdminShell` pattern (ADR 0148, `capability="admin.user_management"`, matching
  this surface's own gate exactly). Per-row CRUD (fetch list, add-row form posting immediately, per-row
  delete calling `DELETE` immediately) rather than the batched-single-`PUT` style `RetentionSettings.tsx`
  uses, since these ARE individually addressable rows with their own ids - mirrors `UserManagement.tsx`'s
  "Role Assignments" section, including its `pending_approval` handling (all four mutating calls here
  can optionally be four-eyes-gated per installation, same envelope shape). Three sections: simple
  mappings, composite AND-rules (comma-separated AD-group-name input, deduplicated/trimmed client-side,
  client-side ≥2-distinct-groups validation ahead of the backend's own `422`), and the default-role
  setting (a plain save, no delete, matching it being a singleton not a list).
- **Optional four-eyes on all four mutating mapping/rule endpoints, added Post-Roadmap Phase 39
  Session 3** (ADR 0153): mirrors `permission-service`'s own OPTIONAL, per-action-type-configurable
  pattern (ADR 0130/0151), not break-glass's mandatory one. `PermissionServiceClient` gained
  `requires_approval()`/`request_approval()` - `auth-service`'s first REMOTE check of
  `permission-service`'s approval-config (the config lives in a different service's database, unlike
  `permission-service`'s own gated endpoints). `consumer.py` gained matching execution branches for
  `auth.ad_group_role_mapping.create`/`.delete`/`auth.ad_group_role_composite_rule.create`/`.delete`.
  Response shapes changed from bare objects/`204` to wrapped envelopes (`status`/resource/
  `approval_request_id`, `200` on delete) regardless of whether approval is configured - verified zero
  real callers affected (no frontend anywhere in this project calls this API, confirmed by exhaustive
  grep ahead of this session).
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

~~None yet — follows in Phase 11.~~ Stale — this service registers `bootstrap_http_sensors`/`sensor_registry` like every other service, same standard HTTP request/duration sensor rollout, found and corrected as a drive-by fix while writing this session's own docs (P55-S2).

## Cross-Service Cleanup on User Deletion (P55-S2)

`DELETE /users/{id}` previously only called Keycloak's `delete_user` — it never notified or called `permission-service` (to revoke that principal's `RoleAssignment` rows) or `teamspace-service` (to remove their `TeamspaceMember` rows), leaving stale grants/references accumulate indefinitely for every deleted user (breaking, among other things, `GET /users/lookup` resolution for admin/UI display of a reference to an account that can never authenticate again). Unlike `permission-service`'s own `delete_group`, which explicitly documents and justifies leaving `RoleAssignment` rows behind as harmless (a deleted group's rows simply match no principal anymore), a deleted USER has no equivalent precedent — this is real, unaddressed data-hygiene debt, the same "resource deletion cleans up its dependents in another service" shape already fixed several times elsewhere in this project (e.g. ADR 0169's pseudonymization-vault cleanup, P55-S1's teamspace orphan cleanup).

Both new cleanup calls run **after** the Keycloak deletion (the security-relevant part) and are **fail-soft** — a `permission-service`/`teamspace-service` outage doesn't turn a completed user deletion into a confusing partial-failure `500`:

- `PermissionServiceClient.revoke_all_role_assignments(principal_id)` — lists then deletes every `RoleAssignment` for the principal via `GET`/`DELETE /role-assignments`.
- `TeamspaceClient.delete_memberships(principal_id)` (new `teamspace_client.py`) — calls `teamspace-service`'s new `DELETE /principals/{id}/teamspace-memberships` (see `docs/services/teamspace-service.md`), a system-to-system cleanup endpoint gated to only accept `X-DMS-Principal: auth-service`.

No new ADR — a mechanical extension of an already-established cleanup-on-delete pattern, not a new architecture decision.

## Tests

`uv run pytest services/auth-service/tests` (**149 tests since P55-S2** — +2:
`test_delete_user_revokes_role_assignments`/`test_delete_user_removes_teamspace_memberships` in
`test_admin_users.py`, both real round trips against the real, live-running `permission-service`/
`teamspace-service` (the latter invites a second, fixed cleanup principal as manager before the
deleted user's own membership is removed, so the test doesn't leave a permanently unmanageable
teamspace behind — same cleanup discipline `teamspace-service/tests/test_api.py`'s own
`_cleanup_teamspace_folders` fixture already established for a different reason). Before P55-S2, **136
tests**, of which 5 new since **Post-Roadmap Phase 42 Session 2** — new `test_license_limit.py`:
`LicenseLimitClient.is_exceeded` unit tests (exceeded/not
exceeded/fails open, mirroring `document-service`'s identically named test module), `POST /users`
blocked `403` when the `"users"` license dimension is exceeded, allowed `201` when it is not. A local
`_default_no_license_limit_exceeded` override fixture (same name, same shadowing trick as
`document-service`'s) lets these tests observe the client's real behavior instead of the global
autouse patch every other test relies on. Before that 131 tests, of which 11 new since **Post-Roadmap
Phase 41 Session 3** ([ADR 0157](../adr/0157-fine-grained-user-tracking-privileged-accounts.md)),
`test_user_tracking.py`: config get/put without permission → `403`, get defaults to `enabled: false`
for an unconfigured principal, put/get roundtrip, a login is NOT recorded while disabled, a login IS
recorded once enabled including the new `X-DMS-Client-IP`/`User-Agent` headers, a refresh is recorded,
listing sessions requires the separate view capability (not just the toggle one), retention config
defaults to 7 days and is editable, `422` for `retention_days < 1`, and the activated superuser is
tracked by default with NO `UserTrackingConfig` row at all (`superuser.activate()` called directly,
bypassing the full four-eyes flow, to isolate the tracking behavior itself). Before that 120 tests,
15 new since **Post-Roadmap Phase
39 Session 3** (ADR 0153): composite-rule create/list/delete (`422` for a single-group rule),
AND-only-with-both-groups resolution against real Keycloak groups (re-logging in after each membership
change, since the `groups` claim is baked into the token at login time, not re-evaluated live), default-
role get/set/reset, default-role resolution (granted when groups are unmapped, withheld with no groups
at all, overridden by an actual match), and four-eyes `pending_approval` deferral for both mapping and
composite-rule creation (`test_ad_group_mapping.py`) plus the matching consumer-execution tests
(`test_consumer.py`). Before that 105 tests, of which 9 new since **P24-S2**,
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

- ~~**AD group → internal role mapping — partially solved since P24-S2**: simple 1:1 mapping
  is implemented. Still open, envisioned per Concept 4.4: composite rules, configurable default for
  unmapped groups, four-eyes before taking effect, no config export~~ — **three of the four closed in
  Post-Roadmap Phase 39 Session 3** ([ADR 0153](../adr/0153-ad-group-mapping-composite-rules-default-role-four-eyes-export.md)):
  composite (AND) rules (new, additive `AdGroupRoleCompositeRule`/`AdGroupRoleCompositeRuleGroup`
  tables, `GET`/`POST`/`DELETE /ad-group-composite-rules`), a configurable default role for genuinely
  unmapped groups (new singleton `AdGroupMappingDefaultRole`, `GET`/`PUT /ad-group-mappings/
  default-role`), and optional per-action-type four-eyes on all four mutating mapping/rule endpoints
  (`auth.ad_group_role_mapping.create`/`.delete`/`auth.ad_group_role_composite_rule.create`/`.delete`,
  mirroring the same OPTIONAL pattern as `permission.role.create`/`.update`, ADR 0130/0151 - NOT
  break-glass's mandatory pattern). Config export/import also closed - see "Configuration Packages"
  below. Still genuinely open: **no AD synchronization interval/no user/group synchronization** — group
  memberships are read exclusively from the `groups` JWT claim at token-acquisition time, no periodic
  reconciliation (not part of ADR 0153's scope; concept 4.4 itself does not call for one). ~~**No admin-UI
  CRUD surface** for any of this — remains API/curl-only, same as before this session (deliberately not
  built, ADR 0153 "Rationale": no such UI existed before, and building one was beyond the four named
  deliverables).~~ — **resolved in Phase 50 Session 5**, see "Admin UI, added Phase 50 Session 5" above.
  ~~The default-role setting itself has no four-eyes protection, unlike the per-row CRUD — see ADR 0153
  "Consequences".~~ — **resolved in Phase 53 Session 1** ([ADR 0171](../adr/0171-ad-group-mapping-default-role-four-eyes-and-display-name-fix.md)).
  Role assignment/evaluation in the narrower sense remains the task of the
  Permission Service (4.1, P2-S2) — `auth-service` only supplies the role names, no permission check of
  its own.
- **Issuer hostname consistency — partially solved since the ad-hoc post-roadmap SSO feature**: the Auth Service addresses Keycloak internally via `DMS_KEYCLOAK_BASE_URL` (`http://keycloak:8080` inside the compose network); issued tokens accordingly carry `iss=http://keycloak:8080/realms/dms`. With the new browser-based redirect flow (`standardFlowEnabled`, since the SSO feature), exactly the consequence predicted here became real: `GET /oidc/authorize` returns a URL to which the browser navigates — with the internal `http://keycloak:8080` this would not have been resolvable for the browser. Fixed via a new, separate `keycloak_public_base_url` setting (`DMS_KEYCLOAK_PUBLIC_BASE_URL`, `http://localhost:8080` in the compose stack), used only by `_authorization_endpoint` (in `keycloak_client.py`) — token/logout endpoints remain on the internal URL, since they are called exclusively server-side from within `auth-service`. `iss` in the token itself remains the internal URL (Keycloak's own `frontendUrl` configuration would be the complete fix for this, deliberately not touched here, since `TokenValidator` already consistently checks against the same internal issuer).
- **SAML 2.0** (Concept 4.4, for legacy ADFS federations) not part of this session.
- **`/users` endpoints gated since P6-S5** (see above) — resolves the former open point for this service. Assigning `admin.user_management` to *additional* principals (e.g. real humans in addition to the technical `users-admin` account) runs via the now itself gated user/permission-management Admin UI page (`POST /role-assignments` against `permission-service`).
- **No role assignment API/UI for Keycloak realm roles** (since P5e-S2, only partially solved since P17-S1): `dms-admin`/`dms-poststelle` etc. are Keycloak realm roles, not a native `permission-service` construct (unlike the domain admin roles from P6-S5). Since P17-S1 there is at least a generic **creation** path (`POST /realm-roles`, e.g. from a configuration package) — but **assignment** to specific users remains exclusively via the Keycloak Admin Console, no API/UI for it in this project.
- ~~**5 of the 7 domain admin roles from 4.6 without an associated technical account** (since P6-S5/S6): `domain-admin-storage`/`-license`/`-query-console`/`-deletion`/`-deletion-vs` exist only as a `Role` row in `permission-service`, without a Keycloak account and without any endpoint checking them — will follow with the respective domain's future retrofit session.~~ — **corrected in Post-Roadmap Phase 39 Session 1** ([ADR 0150](../adr/0150-domain-admin-deletion-capability-migration.md)): this bullet was stale. None of the 5 has (or needs) a dedicated technical account — all are enforced via a direct `has_permission`/role-assignment lookup against whatever principal holds the role, no account required. 4 of the 5 already had real enforcement before this session: `domain-admin-license` since **P9-S1** (`license-service`'s `POST /license`), `domain-admin-query-console` since **P8-S1** (`query-service`'s query-console gate), `domain-admin-deletion-vs` since **Phase 32 Session 4** ([ADR 0133](../adr/0133-document-service-classified-deletion-capability.md), classified-documents trash/purge), and `domain-admin-storage` since **Phase 38 Session 3** ([ADR 0148](../adr/0148-admin-ui-authorization-full-alignment.md), storage-service's operational/guard config). Only `domain-admin-deletion` was genuinely still unused — closed by this session (regular, non-classified trash/purge admin on `document-service`/`folder-service`). `domain-admin-config` has been enforced since **P6-S6** (`config-admin` account, `workflow-service`'s process-definition endpoints).
- **No elevated audit priority during an active superuser session** (4.6, since P6-S5): `audit-service` consumes the break-glass lifecycle events (`auth.>`) at normal priority; third-party actions performed *while* activation is in effect in other services are not specially marked.
- **No rolling inactivity deactivation** (4.6, since P6-S5): a single absolute expiry timestamp instead of separate total-duration/10-minute-inactivity timers, see ADR 0023.
- ~~**Bug discovered at P6-S6, not fixed (P6-S5 code)**: the superuser account cannot log in interactively in the current live environment (`POST /login` returns `401`/"Account is not fully set up" directly from Keycloak). Cause: `firstName`/`lastName`/`email` missing on the Keycloak account...~~ — **disappeared without replacement since Phase 18 Session 2** ([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)): the superuser is no longer a Keycloak account, there is no more declarative-user-profile required-field problem that could cause this state.
- **`GET /users/lookup` is an existence oracle** (since P14-S6): any authenticated user can find out whether a particular username exists — deliberately left as is (internal management software, known user population), but a documented deviation from the previous state (user directory fully behind `admin.user_management`). Since P19-S3 (ADR 0068) gated via the "everyone" group instead of hard-coded open — an admin can revoke `users.lookup` from the "everyone" role to close the oracle, without a code change. See [ADR 0043](../adr/0043-teamspace-service-membership-and-permission-integration.md).
- **Fine-grained user tracking (5.5, Post-Roadmap Phase 41 Session 3, [ADR 0157](../adr/0157-fine-grained-user-tracking-privileged-accounts.md)) has no GeoIP/network-location lookup** — only the raw `client_ip` is captured, the concept's own "Netzwerk-/Standortinformationen" field is deliberately left unimplemented (no existing dependency in this project for it, and the concept itself hedges it as optional). **No client-side device/browser fingerprinting** either — only the server-side `User-Agent` header, real canvas/font-based fingerprinting would need new frontend instrumentation with no precedent anywhere in this project. **No stored, correlated session duration** — approximated as time-since-last-login at display time, since no session-id concept exists to correlate a login with its later logout/expiry. ~~**No four-eyes on the toggle action** — the concept explicitly names this as optional ("kann optional... unterliegen"), not built this session; still genuinely open as of Phase 65+'s gap-analysis round (`put_user_tracking_config` has no `requires_approval` wiring, unlike the AD-group-mapping endpoints — small fix if ever pursued, same pattern as ADR 0171).~~ — **closed in Post-Roadmap Phase 74 Session 2**: `put_user_tracking_config` now defers to the same `_maybe_defer_to_approval`/`action_type="auth.user_tracking_config.update"` mechanism as the AD-group-mapping endpoints (ADR 0153), wrapped in a new `UserTrackingConfigActionResult` envelope (`{status, config, approval_request_id}`); `admin-ui`'s `UserTracking.tsx` shows the same pending-approval hint as `AdGroupMappings.tsx`. Live-verified end-to-end against the real running stack (gate off → applied; gate on → `pending_approval` with unchanged config; approved → config updated). ~~**No admin-UI page** — API/curl-only, same deliberate "backend before frontend" precedent already established for AD-group-mapping administration (see above) and consistent with this session's own scope; live verification instead ran through the real gateway with a real login, proving the actual capture pipeline end-to-end.~~ — **closed at Post-Roadmap Phase 52 Session 1**, see the note above (`/user-tracking/` page). Stale bullet — the closure was documented elsewhere in this same file but this Open Points bullet was never struck through; found and fixed during Phase 65+'s gap-analysis round.
