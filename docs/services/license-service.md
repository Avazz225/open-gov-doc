# license-service

**Responsibility:** License management/checking (9.1/9.2/9.3) — manages a signed license file (JWT/RS256, [ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md)), continuously checks (not only at startup) current usage against four dimensions, and publishes status changes as events. `registry-service` consumes these events and derives from them a license status per component (P9-S2, see `docs/services/registry-service.md`); `document-service` queries `GET /license/status` directly to block new creations once the document limit is exceeded.

**Concept Reference:** 9.1, 9.2, 9.3
**Own Postgres Schema:** `license` (table `installed_license`, singleton row — genuinely own state, no duplication of foreign data).

## Architecture Decisions

- **Signature scheme: JWT/RS256, statically embedded public key** ([ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md)) — reuse of `python-jose[cryptography]`, already present in every service container via `libs/dms-auth-client`'s `TokenValidator` (Keycloak JWT verification). No JWKS fetch at this stage. **Since Post-Roadmap Phase 21 Session 1** ([ADR 0084](../adr/0084-fleet-license-key-rotation.md)), `license_verifier.decode()` supports an optional second candidate key (`settings.license_previous_public_key_pem`) — the basis for a transition period during a licensor key change, see below.
- **Only an invalid signature causes the upload to be rejected (`400`)** — a license with a valid signature but already expired is still stored and shown as invalid/expired via `GET /license/status`. This reflects the real situation ("this is the currently installed license, it is just expired") rather than a special case at upload time.
- **Four concept-9.1 dimensions as JWT claims**: `user_model` (`"concurrent"|"named"`), `max_users`, `storage_limit_gb`, `document_limit`, `licensed_components` — each `null`-valued = "unlimited" (concept 9.1, literally).
- **Installation binding since P13-S1** (3a, [ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md) addendum): optional `installation_id` claim, checked against `settings.installation_id` (`dms_common.BaseServiceSettings`, `DMS_INSTALLATION_ID` — one value for the whole installation). If the claim is absent, nothing is checked (backward compatibility with older license files); if it is set and differs, the license is considered invalid (`invalid_reason="Lizenz wurde fuer eine andere Installation ausgestellt"`), even with a valid signature/validity period. Prevents an unmodified copy of a license file issued for a different installation.
- **Usage-data sources — direct service-to-service calls, no detour via reporting-service**: `storage-service`'s `GET /storage/usage` (sum of `total_size_bytes`), `document-service`'s new `GET /documents/count-active-total` (installation-wide, no folder filter — unlike the existing, folder-filtered `POST /documents/count-active`, P7-S1b), `auth-service`'s new `GET /sessions/count`/`GET /users/count` (only the relevant call is made, depending on the `user_model` claim). All three target services remain the source of truth for their own data (service isolation).
- **`auth-service`'s `GET /users` is unsuitable for internal calls** — gated by `Depends(get_current_user)` (a real Keycloak bearer token), which no service holds. The two new endpoints `GET /sessions/count`/`GET /users/count` are therefore deliberately ungated (internal call, same rationale as e.g. `permission-service`'s `/role-assignments`). `GET /sessions/count` uses `KeycloakAdmin.get_client_sessions_stats()` (a ready-made admin API method, no new session tracking).
- **Poll loop instead of push** (9.2: "checks continuously, not only at startup") — the same idiom as `document-service`'s `_retention_poll_loop`/`workflow-service`'s SLA timer (ADR 0020), interval 3600s. An error in one tick does not abort the loop.
- **Edge detection instead of event spam** — `InstalledLicense.last_status_snapshot` (JSON) records which states (invalid/expiring soon/exceeded per dimension) were already reported at the last tick; events only fire on an actual state change, not on every tick. A reinstall resets the snapshot.
- **Three events, 1:1 matching the status-change types named in 9.2**: `license.limit_exceeded` (`dimension`/`current`/`limit`), `license.expiring_soon` (`days_remaining`, threshold 30 days), `license.invalid` (`reason`). Additionally `license.installed` on upload. `audit-service`'s subject list gained `"license.>"`.
- **`notification-service` consumes all three edge events** (concept 9.2 literally names it as the consumer) — a fixed `settings.license_admin_email` address, no recipient-resolution mechanism, a 1:1 copy of `_handle_maintenance_mode_activated`. Since all three subjects share the new `"license"` stream, each needed its own durable name (`notification-service-license-*`) — the same durable name for multiple filter subjects on the same stream fails with "consumer is already bound to a subscription", the same limitation already encountered earlier with `workflow.federation.inbound_received`.
- **The `admin.license` gate activates, for the first time, the long-pre-seeded domain-admin role `domain-admin-license`** — `POST /license` requires it (or an activated superuser), a 1:1 gate pattern from `query-service`. `GET /license/status` remains ungated (queried by `registry-service` in P9-S2 and later by the admin UI without a principal header).
- **No license-issuing tool in this repo** (ADR 0032) — the private key exists exclusively outside the system, with the licensor. The test fixture key (`tests/fixtures/dev_private_key.pem`) is explicitly a throwaway development key, not part of any issuing tool.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/license` `{license_token}` | Install a signed license file — `400` on invalid signature, otherwise `201` even for an expired license. Requires `admin.license`, an activated superuser, or since P13-S2 a valid `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>` (fleet-management-service, not a principal of this installation, see [ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md)). |
| `GET` | `/license/status` | Current license status + usage per dimension (`installed`/`valid`/`invalid_reason`/`issued_at`/`expires_at`/`days_remaining`/`user_model`/`users`/`storage_gb`/`documents`/`licensed_components`/`limits_exceeded`). Ungated. |

## Data Model

`license.installed_license` — singleton (`id=1`): `raw_token`, `installed_at`, `installed_by`, `issued_at`, `expires_at`, `last_status_snapshot` (JSON, edge detection).

## Events

Publishes (stream `license`): `license.installed`, `license.limit_exceeded`, `license.expiring_soon`, `license.invalid`.
Consumes: none (no own NATS consumer — producer only, like `query-service` before P8-S2).

## Self-Registration

Like every other service, via `dms-registry-client` (3.2a) — independent of the license-mediation function planned for the registry itself in P9-2.

## Tests

`services/license-service/tests/` — 37 tests (previously 32, +5 since **Post-Roadmap Phase 21 Session 1**,
[ADR 0084](../adr/0084-fleet-license-key-rotation.md), all in `test_license_verifier.py`: fallback to
the previous key during a transition period, preference for the current key with no
fallback needed, failure when neither the current nor the previous key matches, unchanged
behavior with no previous key configured), before that 31 (since P13-S1, previously 25):
`test_license_verifier.py` (signature check, including an expired-but-signature-valid token),
`test_usage.py` (dimension threshold logic including "unlimited", since P13-S1 additionally
installation binding: missing claim/matching/mismatching), `test_poll_loop.py`
(edge detection, since P13-S1 additionally an installation-mismatch event), `test_api.py` (upload gate,
status endpoint including "no license installed", since P13-S1 additionally end-to-end
installation binding).

## Open Points

- ~~No key rotation~~ — **partially resolved in Post-Roadmap Phase 21 Session 1** ([ADR 0084](../adr/0084-fleet-license-key-rotation.md)): `license_previous_public_key_pem` allows a transition period in which both the new and the previous public verification key are accepted. **No JWKS** deliberately remains the case (ADR 0032) — a compromised PRIVATE key resides with the licensor, not in this service, and still requires a new public key issued there (the operator then enters it via the two settings, no new `license-service` release needed).
- ~~Installation ID not enforced~~ — closed since P13-S1, see "Installation Binding" above.
- The "application components" dimension (`licensed_components`) has been enforced since P9-S2, but only for `workflow-service` — the only licensable component that actually exists today (CMIS connector/migration service arrive only in Phase 12).
- Usage-limit blocking (9.3) has so far only been implemented for the document count (`document-service`'s `POST /documents`) — storage/user limits currently do not prevent new creations, only the status display/events capture them.
