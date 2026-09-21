# license-service

**Responsibility:** License management/checking (9.1/9.2/9.3) — manages a signed license file (JWT/RS256, [ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md)), continuously checks (not only at startup) current usage against four dimensions, and publishes status changes as events. `registry-service` consumes these events and derives from them a license status per component (P9-S2, see `docs/services/registry-service.md`); `document-service` queries `GET /license/status` directly to block new creations/new versions once the document-count or storage limit is exceeded, and (**since Post-Roadmap Phase 42 Session 2**) `auth-service` does the same to block new user accounts once the user limit is exceeded — all three dimensions now have real, symmetric enforcement (see "Usage-limit blocking" below).

**Concept Reference:** 9.1, 9.2, 9.3
**Own Postgres Schema:** `license` (table `installed_license`, singleton row — genuinely own state, no duplication of foreign data).

## Architecture Decisions

- **Signature scheme: JWT/RS256, statically embedded public key** ([ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md)) — reuse of `python-jose[cryptography]`, already present in every service container via `libs/dms-auth-client`'s `TokenValidator` (Keycloak JWT verification). No JWKS fetch at this stage. **Since Post-Roadmap Phase 21 Session 1** ([ADR 0084](../adr/0084-fleet-license-key-rotation.md)), `license_verifier.decode()` supports an optional second candidate key (`settings.license_previous_public_key_pem`) — the basis for a transition period during a licensor key change, see below.
- **Only an invalid signature causes the upload to be rejected (`400`)** — a license with a valid signature but already expired is still stored and shown as invalid/expired via `GET /license/status`. This reflects the real situation ("this is the currently installed license, it is just expired") rather than a special case at upload time.
- **Four concept-9.1 dimensions as JWT claims**: `user_model` (`"concurrent"|"named"`), `max_users`, `storage_limit_gb`, `document_limit`, `licensed_components` — each `null`-valued = "unlimited" (concept 9.1, literally).
- **Installation binding since P13-S1** (3a, [ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md) addendum): optional `installation_id` claim, checked against `settings.installation_id` (`dms_common.BaseServiceSettings`, `DMS_INSTALLATION_ID` — one value for the whole installation). If the claim is absent, nothing is checked (backward compatibility with older license files); if it is set and differs, the license is considered invalid (`invalid_reason="Lizenz wurde fuer eine andere Installation ausgestellt"`), even with a valid signature/validity period. Prevents an unmodified copy of a license file issued for a different installation.
- **Usage-data sources — direct service-to-service calls, no detour via reporting-service**: `storage-service`'s `GET /storage/usage` (sum of `total_size_bytes`), `document-service`'s new `GET /documents/count-active-total` (installation-wide, no folder filter — unlike the existing, folder-filtered `POST /documents/count-active`, P7-S1b), `auth-service`'s new `GET /sessions/count`/`GET /users/count` (only the relevant call is made, depending on the `user_model` claim). All three target services remain the source of truth for their own data (service isolation). **P67-S2** ([ADR 0198](../adr/0198-cli-migration-license-plugin-orchestration-deletion-register-commands.md)): `clients.StorageClient.total_bytes()` was found, via a real `500` in the CLI's own live verification, to have never sent an `X-DMS-Principal` header at all — `storage-service`'s `GET /storage/usage` has required a trusted-caller identity since P66-S1, and this service was simply never added to that allowlist. Fixed: `license-service` is now in `storage-service`'s trusted-caller allowlist and this client sends its identity on every call.
- **`auth-service`'s `GET /users` is unsuitable for internal calls** — gated by `Depends(get_current_user)` (a real Keycloak bearer token), which no service holds. The two new endpoints `GET /sessions/count`/`GET /users/count` are therefore deliberately ungated (internal call, same rationale as e.g. `permission-service`'s `/role-assignments`). `GET /sessions/count` uses `KeycloakAdmin.get_client_sessions_stats()` (a ready-made admin API method, no new session tracking).
- **Poll loop instead of push** (9.2: "checks continuously, not only at startup") — the same idiom as `document-service`'s `_retention_poll_loop`/`workflow-service`'s SLA timer (ADR 0020), interval 3600s. An error in one tick does not abort the loop.
- **Edge detection instead of event spam** — `InstalledLicense.last_status_snapshot` (JSON) records which states (invalid/expiring soon/exceeded per dimension) were already reported at the last tick; events only fire on an actual state change, not on every tick. A reinstall resets the snapshot.
- **Three events, 1:1 matching the status-change types named in 9.2**: `license.limit_exceeded` (`dimension`/`current`/`limit`), `license.expiring_soon` (`days_remaining`, threshold 30 days), `license.invalid` (`reason`). Additionally `license.installed` on upload. `audit-service`'s subject list gained `"license.>"`.
- **`notification-service` consumes all three edge events** (concept 9.2 literally names it as the consumer) — a fixed `settings.license_admin_email` address, no recipient-resolution mechanism, a 1:1 copy of `_handle_maintenance_mode_activated`. Since all three subjects share the new `"license"` stream, each needed its own durable name (`notification-service-license-*`) — the same durable name for multiple filter subjects on the same stream fails with "consumer is already bound to a subscription", the same limitation already encountered earlier with `workflow.federation.inbound_received`.
- **The `admin.license` gate activates, for the first time, the long-pre-seeded domain-admin role `domain-admin-license`** — `POST /license` requires it (or an activated superuser), a 1:1 gate pattern from `query-service`. `GET /license/status` remains ungated (queried by `registry-service` in P9-S2, later by the admin UI, and since **Phase 45 Session 1** also by `reporting-service`'s license-utilization report, all without a principal header).
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

`services/license-service/tests/` — 37 tests, unchanged since **Post-Roadmap Phase 42 Session 2**
(the new blocking checks live entirely in `document-service`/`auth-service`'s own test suites, see
`docs/services/document-service.md`/`docs/services/auth-service.md` — this service's own `GET
/license/status` response already carried all three dimension names since P9-S1, no new endpoint or
test needed here). Before that, 37 tests (previously 32, +5 since **Post-Roadmap Phase 21 Session 1**,
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
- ~~The "application components" dimension (`licensed_components`) has been enforced since P9-S2, but only for `workflow-service` — the only licensable component that actually exists today (CMIS connector/migration service arrive only in Phase 12).~~ — **closed at P12-S1/P12-S2**: `registry-service`'s `licensable_components` now also covers `webdav-connector` (P12-S1, the CMIS-adjacent connector) and `migration-service` (P12-S2), same `"demo"`/`"lock"` pattern as `workflow-service`, see `docs/services/registry-service.md`.
- ~~Usage-limit blocking (9.3) has so far only been implemented for the document count~~ — **closed in Post-Roadmap Phase 42 Session 2**: `storage_gb` and `users` now block new creations exactly like `documents` already did, see "Usage-Limit Blocking (9.3), All Three Dimensions" below.

## Usage-Limit Blocking (9.3), All Three Dimensions (Post-Roadmap Phase 42 Session 2)

All three usage dimensions (`documents`/`storage_gb`/`users`) now actively prevent new creations once exceeded, not just the document count as before this session — each caller queries this service's own already-existing `GET /license/status` (no new endpoint needed, `limits_exceeded` already listed all three dimension names since P9-S1) via a small, deliberately duplicated-per-service `LicenseLimitClient` (same TTL-cache-and-fail-open shape in both callers, matching `document_service.license_client`'s original P9-S2 implementation — same rationale as this project's other per-service crypto/client duplication: cheap to keep in sync, avoids a shared-library dependency for ~40 lines of code).

- **`storage_gb`** (`document-service`): checked once, centrally, inside the shared `_persist_new_document()` helper — every caller that adds a brand-new object to storage (`POST /documents`, `POST /documents/from-quarantine-release`, and the redaction endpoint, all three funnel through this one helper) is covered by a single check rather than three duplicated ones. Additionally checked in `POST /documents/{id}/versions` (`checkin_version`) — a new version is deliberately excluded from the `documents` dimension (no new document row, see below) but genuinely adds new bytes to storage, so it must still be blocked once `storage_gb` is exceeded.
- **`users`** (`auth-service`): checked in `POST /users`, the one and only endpoint anywhere in this codebase that creates a new named account (no AD/Keycloak self-registration flow exists in the repo) — new `auth_service.license_client.LicenseLimitClient`, wired into `app.state` exactly like `document-service`'s.
- **`documents`** (unchanged): still only `POST /documents`/`POST /documents/from-quarantine-release` — existing documents/versioning/restoration remain deliberately unaffected (concept 9.3 literally: "does not block retroactively"), exactly as before this session.
- **A real bug found and fixed only by live verification, not by the test suite**: `infra/docker-compose.yml`'s `auth-service` block was missing `DMS_LICENSE_SERVICE_BASE_URL` entirely (present on `document-service`/`registry-service` since P9-S1/P9-S2, simply never added when `auth-service` needed its first outbound call to `license-service`) — the client silently fell back to its default `http://localhost:8023`, unreachable from inside the container, and `LicenseLimitClient.is_exceeded()`'s fail-open design meant every check simply logged a warning and let the request through with no visible error. Pytest never caught this because both services' tests monkeypatch `LicenseLimitClient.is_exceeded` directly (never a real network call), and the missing env var only manifests against the real Compose network. Found by actually calling `POST /users` through the real gateway against a real installed test license with `users` exceeded and observing an unexpected `201`; fixed by adding the missing environment variable.
