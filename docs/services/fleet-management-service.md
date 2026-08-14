# fleet-management-service

**Responsibility:** Overarching, installation-independent management layer for multiple DMS installations (concept 3a "Central management layer, optional, separate building block"). Not an internal service of any single installation — an operator running/overseeing multiple installations deploys this service independently of them (same architectural pattern as `federation-hub-service`, ADR 0028).

Since P13-S2b, additionally **fleet update orchestration** (3a extension): named installation groups/waves, versioned update plans, staged rollouts using the concept's own five-valued failure decision.

**Concept Reference:** 3a
**Own Postgres Schema:** `fleet` (tables `managed_installation`, `installation_group`, `installation_group_member`, `update_plan`, `rollout`, `installation_run`)

## Architecture Decisions

See [ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md) for the full rationale. Summary:

- **Three capabilities, taken literally from 3a**: health/license overview (`GET /installations/status`), license assignment/renewal (`POST /installations/{id}/license`), central provisioning from a configuration template (`POST /installations/{id}/provision`).
- **Reaches every managed installation exclusively through its gateway** (`{gateway_base_url}/api/{service}/...`), never an internal container address — and exclusively `registry-service`/`license-service`/`config-service`, never `document-service`/`folder-service` (3a: "no access to document content of individual installations").
- **Authentication via a dedicated, installation-wide `DMS_FLEET_AGENT_API_KEY`** instead of RBAC — this service has no Keycloak principal in any managed installation. Same API-key-instead-of-RBAC approach as `federation-hub-service`/`migration-service`.
- **Pure pass-through, no template library**: `POST /installations/{id}/provision` accepts a raw 7.3 configuration document (e.g., the export of a reference installation) — a curated package library is concept §14/Phase 17, not part of this session.
- **`fleet_agent_api_key` stored in plaintext** (not hashed) — this service must present it on every outgoing call, never verify it itself (identical rationale to `migration-service`'s `PairedInstallation`, ADR 0034).

## Fleet Update Orchestration (3a extension, P13-S2b)

See [ADR 0038](../adr/0038-fleet-update-orchestration-external-gates-not-remote-control.md) for the full rationale. Summary:

- **Waves/groups**: `InstallationGroup` groups installations under a name; a `Rollout` applies an `UpdatePlan` to group members ∪ `include` − `exclude` and must be started explicitly (`POST /rollouts/{id}/start`) — no automatic chain reaction to the next wave, which is its own, separately created `Rollout`.
- **Declarative, versioned update plan**: `UpdatePlan.steps` is an ordered list of `{name, step_type, requires_approval}`. Two `step_type` values: `"verify"` (automatic, queries the target installation's `_fetch_status()`) and `"gate"` (confirmed via `POST .../mark-done` — stands in for any action outside this service: setting a scope lock/maintenance mode, performing the rolling update, taking a backup, final release).
- **Deliberate boundary**: `fleet-management-service` does **not** trigger `gate` steps itself via HTTP on the target installation (see ADR 0038 — `permission-service`'s scope-lock/maintenance-mode endpoints have no secure, RBAC-independent remote-control opening like `license-service`/`config-service`, and 10.4/10.5 are deliberately scripts, not services, anyway). The operator who performed the action confirms it via the fleet console.
- **Five-valued failure decision** (3a, literally) as `InstallationRun.status`: `retry_later`/`wait_external`/`manual_required`/`recoverable_failed`/`fatal_contract`, plus the two structural states `pending`/`completed`. `mark-done` lets the operator choose any of the four reportable decisions (`success` is the default).
- **Four-eyes principle (4.3) as a structural propose/approve flow**: a `requires_approval` step transitions to `manual_required` after `mark-done` (`proposed_by` stored); `approve` requires `actor != proposed_by`. No real two-identity cryptography (this service does not run its own user management), see ADR 0038.
- **`retry`** for `retry_later`/`recoverable_failed` (resets the same step back to the waiting state); **`acknowledge-fatal`** for `fatal_contract` (its own, explicit endpoint instead of `retry` — enforces a deliberate confirmation that the plan/configuration have been corrected).

### API (Orchestration)

| Method | Path | Description |
|---|---|---|
| `POST` | `/groups` | Create group/wave |
| `GET` | `/groups` | List, including member installation IDs |
| `POST` | `/groups/{id}/members` | Add installation to group |
| `DELETE` | `/groups/{id}/members/{installation_id}` | Remove from group |
| `DELETE` | `/groups/{id}` | Delete group |
| `POST` | `/plans` | Create update plan (`422` on unknown `step_type`/empty steps) |
| `GET` | `/plans`, `GET /plans/{id}` | List/detail |
| `POST` | `/rollouts` | Create wave (`422` on empty target set) — status `draft` |
| `GET` | `/rollouts`, `GET /rollouts/{id}` | List/detail, including all `InstallationRun`s |
| `POST` | `/rollouts/{id}/start` | Explicit start (`409` if not `draft`) |
| `POST` | `/rollouts/{id}/installations/{iid}/advance` | Only for `"verify"` steps — automatic check |
| `POST` | `/rollouts/{id}/installations/{iid}/mark-done` | Only for `"gate"` steps — report result |
| `POST` | `/rollouts/{id}/installations/{iid}/approve`, `.../reject` | Four-eyes decision for `manual_required` |
| `POST` | `/rollouts/{id}/installations/{iid}/retry` | For `retry_later`/`recoverable_failed` |
| `POST` | `/rollouts/{id}/installations/{iid}/acknowledge-fatal` | For `fatal_contract` |

## API (Fleet/License Basics, P13-S2)

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Own health check |
| `POST` | `/installations` | Register a managed installation — `fleet_agent_api_key` optional (otherwise generated), included in plaintext only in this response |
| `GET` | `/installations` | List of all managed installations, without keys |
| `DELETE` | `/installations/{id}` | Remove a managed installation |
| `POST` | `/installations/{id}/rotate-key` | Key rotation (since **Post-Roadmap Phase 21 Session 1**, [ADR 0084](../adr/0084-fleet-license-key-rotation.md)) — replaces `fleet_agent_api_key` immediately, optional body value (otherwise generated), included in plaintext only in this response; updates ONLY this side, see "Open Points" |
| `GET` | `/installations/{id}/status` | Live status retrieval for an installation (identity + license status via its gateway) — `reachable=false` instead of an exception on network/auth errors |
| `GET` | `/installations/status` | Status retrieval for all managed installations, in parallel (`asyncio.gather`) |
| `POST` | `/installations/{id}/license` | Forward a license token to the target installation (`POST .../license` via its gateway) |
| `POST` | `/installations/{id}/provision` | Forward a configuration document to the target installation (`POST .../config/fleet-import` via its gateway — **since P17-S1**, previously `.../config/import`, see "Counterpart on the Managed Installation" below) |

## Data Model

`fleet.managed_installation`: `id` (PK, UUID), `display_name`, `gateway_base_url`, `fleet_agent_api_key` (plaintext), `created_at`, `updated_at`.

Since P13-S2b, additionally: `installation_group` (`id`, `name` unique, `created_at`), `installation_group_member` (`group_id`+`installation_id`, composite PK), `update_plan` (`id`, `name`, `version`, `steps` JSON, `created_at`), `rollout` (`id`, `plan_id`, `name`, `group_id` nullable, `include`/`exclude` JSON lists, `status`, `created_at`, `started_at`, `started_by`), `installation_run` (`id`, `rollout_id`, `installation_id`, `current_step_index`, `status`, `last_outcome`, `error_message`, `proposed_by`, `started_at`, `updated_at`, `completed_at`).

## Counterpart on the Managed Installation

No new role/no new service on the target side — three existing endpoints extended or reused:

- `registry-service` `GET /installation` (P13-S1, unchanged, ungated) — returns `{id, display_name}`.
- `license-service` `GET /license/status` (P9-S2, unchanged, ungated) as well as `POST /license` (new: additionally accepts `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>` instead of RBAC, see its `_is_fleet_agent()`).
- `config-service` `POST /config/fleet-import` (same bypass as `license-service`, see its `_is_fleet_agent()`) — **since P17-S1 its own, dedicated path instead of shared with `POST /config/import`** (RBAC callers). Reason: paths in `gateway-service.settings.public_routes` NEVER get a validated bearer token/`X-DMS-Principal` from the gateway, regardless of whether the caller sends one — as long as both access paths shared the same path, `config-service`'s import gate's RBAC branch was effectively unreachable for real, logged-in admins (only discovered at the first admin-UI hookup of `config-service`, P17-S1 — see [ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md)). `agent_client.py`'s `provision_config()` calls the new path accordingly.
- `gateway-service.settings.public_routes` (four entries, so these paths are reachable through the gateway without a Keycloak token — the actual securing of the two write paths remains with the target services themselves).

## Self-Registration

None — this service does not belong to any single installation, so no `dms-registry-client` call (`DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS` remain unused), no event bus producer/consumer.

## Tests

`services/fleet-management-service/tests/` — 30 tests (previously 26, +4 since **Post-Roadmap Phase 21 Session 1**, [ADR 0084](../adr/0084-fleet-license-key-rotation.md): default generation of a new key, operator-supplied value, actual use of the new value on an outgoing call, `404` for an unknown installation), before that 26 (since P13-S2b, previously 13), in-process `TestClient` against an ASGI stub of the target installation (`httpx.ASGITransport`, same pattern as `federation-hub-service`): installation CRUD including key-shown-only-once, status retrieval (reachable/unreachable), aggregation across multiple installations, license push, provisioning, 404 cases, as well as (P13-S2b) group membership, plan validation, rollout resolution (group/include/exclude, empty target set), a complete rollout happy path across all step types including four-eyes, `recoverable_failed`→`retry`, `fatal_contract`→`acknowledge-fatal`, `verify` for an unreachable installation → `retry_later`, rejection of an approval. The P13-S2 capabilities additionally verified live against the running Docker Compose stack (self-loopback).

## Open Points

- ~~No key rotation~~ — **partially resolved in Post-Roadmap Phase 21 Session 1** ([ADR 0084](../adr/0084-fleet-license-key-rotation.md)): `POST /installations/{id}/rotate-key` replaces the stored value immediately. **Remaining, deliberately documented limitation**: this service only PRESENTS the key, never verifies it itself — the target installation still checks against its own value, statically configured via `DMS_FLEET_AGENT_API_KEY`. A compromised key therefore still requires a manual change on the installation side (env var + restart); the rotation endpoint only makes this service's own side more secure/easier to operate, but does not replace the second, manual step.
- No template library for `POST .../provision` — pure pass-through. Since P17-S1 the forwarded document can carry an optional `manifest` (concept §14.1, `config-service`), but the first concrete, curated package (eGov, 14.2) is not assigned until P17-S2/S3.
- No proactive notification on license expiry/exceedance at the fleet level — purely pull-based (`GET .../status`), no push/webhook from the managed installation back to this service.
- **`gate` steps are not remote-controlled, only confirmed** (see ADR 0038) — `fleet-management-service` does not verify that a scope lock/backup/rolling update actually took place before `mark-done` marks the step complete. A tighter, action-/installation-specific remote control remains a possible future extension, to be designed in detail.
- **Four-eyes principle without its own user management** (ADR 0038) — `approve` only enforces `actor != proposed_by` as a plain-text comparison, not a real, cryptographically anchored two-identity check.
- No sensor/Prometheus export for rollout progress (3a names "observable via existing monitoring" as a goal) — currently only queryable via `GET /rollouts/{id}`, no `/metrics` integration.
