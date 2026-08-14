# registry-service

**Responsibility:** Service discovery — registration, heartbeat, active routing table per service type (Concept 3.2a). Since P9-S2 additionally license brokering (3.2b/9.3): queries `license-service` and passes a computed license status (`licensed`/`demo`/`unlicensed`) per `service_type` to registering/heartbeating services, without performing any license check itself. Since P10-S2 additionally the **drain mechanism** (10.5/3.8): an instance can be marked as `draining`, remains reachable during this, but no longer receives new requests via the gateway. Since P13-S1 additionally the sole HTTP-queryable source of information for this installation's **installation identity** (3a).

**Concept Reference:** 3.2a, 3.2b, 3a, 9.3, 10.5
**Own Postgres schema:** `registry` (table `service_instance`)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/instances` | Register/update (upsert by `instance_id`) |
| `POST` | `/instances/{instance_id}/heartbeat` | Heartbeat, updates `last_heartbeat_at` |
| `DELETE` | `/instances/{instance_id}` | Deregister |
| `POST` | `/instances/{instance_id}/drain` | Drain mechanism (10.5/3.8, P10-S2): sets `status="draining"` — ungated; WHEN draining happens is decided by an external deploy tool/`scripts/rolling-update.sh`, not by the registry itself. |
| `POST` | `/instances/{instance_id}/activate` | Reversal of `/drain` (10.5, P10-S3): resets `status="active"` — the basis for a real rollback path, see `docs/operations/rolling-updates.md`. |
| `GET` | `/instances/{service_type}` | Only currently reachable instances of this type |
| `GET` | `/instances` | All instances incl. computed `healthy` flag |
| `GET` | `/license-status/{service_type}` | Computed license status (`licensed`/`demo`/`unlicensed`) for this service type — ungated, for internal poll clients (e.g. `workflow-service`, see below). |
| `GET` | `/metrics` | Own sensors in Prometheus format (10.1, P11-S1) — scraped by `monitoring-service`, not directly by Prometheus. |
| `GET` | `/installation` | Installation identity (3a, P13-S1): `{id, display_name}` from `DMS_INSTALLATION_ID`/`DMS_INSTALLATION_DISPLAY_NAME` (`dms_common.BaseServiceSettings`) — ungated, pure configuration values, no DB access. |
| `GET` | `/healthz` | Own health check |

## Data Model

`service_instance`: `instance_id` (PK), `service_type`, `version`, `capabilities` (JSON list), `sensors` (JSON list, since P11-S1 — purely passed-through sensor self-declaration, see below), `health_endpoint`, `address`, `registered_at`, `last_heartbeat_at`, `status` (`"active"`/`"draining"`, default `"active"`, since P10-S2 — added additively via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, no Alembic in this phase). `healthy` is not a stored field but computed on every query from `last_heartbeat_at` vs. `heartbeat_timeout_seconds` (default 15s, configurable via `DMS_HEARTBEAT_TIMEOUT_SECONDS`). `license_status` (in `InstanceOut`) is likewise not a stored field but appended to every response via `ComponentLicenseCache`.

## Drain Mechanism (10.5/3.8, P10-S2/S3)

- **State, no automatic trigger**: `POST /instances/{instance_id}/drain` sets `status="draining"` — the registry itself does not decide *when* draining happens. Since P10-S3 this is handled by `scripts/rolling-update.sh`, which reuses the same mechanism for update rollouts instead of building it anew (Concept 10.5: "the same drain mechanism... just for a different occasion").
- **Effect only in routing**: a `draining` instance remains visible in `GET /instances/{type}` (not deregistered, no kill) but disappears from the gateway's selection pool for **new** requests (`gateway_service.upstream.InstanceResolver.resolve()` filters on `status == "active"` in addition to `healthy`). Already-running requests are never affected — this literally matches 10.5 ("no longer accepts new tasks but completes running operations").
- **Ungated**, like every other registry endpoint — the registry has no role gate anywhere, and this would not be a consistency improvement here.
- A **new** registration (row does not yet exist) always starts with `status="active"`; a re-registration of the same `instance_id` (self-healing after `404`, not a real restart, see `dms-registry-client`) leaves an existing `status` unchanged — only heartbeat/register never automatically revert it, only `/drain`/`/activate` set it.
- **Rollback (10.5, P10-S3)**: `POST /instances/{instance_id}/activate` resets `status` back to `"active"` — without this reversal there would be no way to make an already-drained instance reachable for new requests again. Concept 10.5 explicitly requires that a rollback remains possible as long as the drain is not yet fully completed (i.e. the instance has not yet stopped). Used by `scripts/rolling-update.sh`'s manual rollback procedure, see `docs/operations/rolling-updates.md`.

## License Brokering (3.2b/9.3, P9-S2)

- **Only configured components are subject to licensing at all**: `settings.licensable_components` (default `{"workflow-service": "demo", "webdav-connector": "demo", "migration-service": "demo"}`, the latter two since P12-S1/P12-S2) assigns each separately licensable `service_type` a policy (`"demo"` or `"lock"`) that applies when no valid license is installed or the component is not included in the license's `licensed_components`. Every unlisted `service_type` is "core" and always gets `"licensed"` — Concept 9.1 explicitly names the CMIS connector/migration service/workflow automation as examples of separately licensable components. Concept 3.3 explicitly names connectors as an example, hence `webdav-connector` since P12-S1 follows the same `"demo"` pattern as `workflow-service` (see `docs/services/webdav-connector.md`); `migration-service` likewise since P12-S2 (see `docs/services/migration-service.md`).
- **`ComponentLicenseCache`** (`licensing.py`): a TTL cache (`license_status_cache_ttl_seconds`, default 60s) around the raw `license-service` status, plus invalidation by the new `license.>` NATS consumer (`consumer.py`, this service's first own consumer, durable `registry-service`) — reacts to status changes both event-driven and with a hard upper bound.
- `InstanceOut` (register/heartbeat/listing responses) additionally carries `license_status`. A dedicated `GET /license-status/{service_type}` lets already-running services re-query their own status without restarting (e.g. `workflow-service`'s `license_client.LicenseStatusClient`, see `docs/services/workflow-service.md`).
- Fail-open when `license-service` is unreachable (`"licensed"` for core, configured policy for licensable components remains at its last known value) — a license service outage should not disable the registry.

## Events

Published (stream `registry`, `dms-eventbus-client`, after commit):

- `registry.instance.registered` — `subject`=`instance_id`, `payload`={`service_type`, `version`}
- `registry.instance.deregistered` — `subject`=`instance_id`, `payload`={`service_type`}

No event per heartbeat. This stream is consumed by the Audit Service (`docs/services/audit-service.md`). Since P9-S2, `registry-service` itself consumes `license.>` (see above) — a pure cache-invalidation trigger, no payload parsing.

## Usage (since P4-S1)

Until P4-S1, only this API existed, with no caller at all. Since then,
seven backend services register themselves here (via the new shared
library `libs/dms-registry-client`: register at startup, periodic heartbeat,
deregister at shutdown) — see `docs/services/gateway-service.md`, which
uses `/instances/{service_type}` to route requests dynamically instead of
statically configuring backend addresses.

**Since P4-S3 also registers itself with itself** (`DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS` both point to its own address): without this, there would be no resolvable instance for `service_type=registry-service`, and the gateway could never resolve `/api/registry-service/...` (e.g. for the Admin UI's registry overview). The very first registration inevitably fails (its own Uvicorn server only accepts connections after lifespan startup completes) — the self-healing fix from `dms-registry-client` (re-registration on `404` at the next heartbeat, see P4-S1) kicks in here for what is arguably the most common use case of this mechanism.

## Sensors (Concept 10.1, P11-S1)

`registry-service` is itself one of the two sensor pilots (no full retrofit of all services, see the P11-S0 finding): declares two sensors at its own self-registration (`registry.instances.active_total`, `registry.service.heartbeat.miss` — both names taken literally from Concept 10.1's example list) and exposes them via its own `GET /metrics` (Prometheus format, `libs/dms-metrics-client`). **The actual sensor registry (catalog aggregation + activation configuration) deliberately does NOT live here**, but in the new `monitoring-service` (a P11-S1 architecture decision made after a clarifying question at the start of the session — Prometheus scrapes exclusively `monitoring-service`, which in turn queries `GET /instances` here to read each instance's declared `sensors` and scrapes their `/metrics` endpoints itself). `registry-service`'s footprint accordingly stays minimal: a passed-through `sensors` field, no new business logic, no new gate. See `docs/services/monitoring-service.md` and `docs/operations/monitoring.md` for details.

## Installation Identity (3a, P13-S1)

- `GET /installation` reads only `settings.installation_id`/`settings.installation_display_name` (`dms_common.BaseServiceSettings`, available to every service since P13-S1 via the same two environment variables `DMS_INSTALLATION_ID`/`DMS_INSTALLATION_DISPLAY_NAME`) — no own data model, no persistence here, purely a configuration lookup.
- Replaces the previously inconsistent practice where only `workflow-service` knew its own, separately configured `installation_display_name` (for federation registration, 7.4), and every other service — in particular `license-service` — had no installation identifier at all, even though Concept 3a explicitly requires this for license checking ("Each installation registers with the License Service... with its own installation ID"). Since P13-S1, `license-service` ties license checking to this field, see `docs/services/license-service.md` and [ADR 0032](../adr/0032-lizenzdatei-signaturverfahren.md) (addendum).
- Actually used since P13-S2 by `docs/services/fleet-management-service.md` (3a, "optional, separate building block") — this endpoint is where the fleet service queries multiple installations for their identity, without building its own discovery logic per installation. The call runs via the respective installation's gateway (`registry-service:installation` in `gateway_service.settings.public_routes`, [ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md)).
- **Deliberately separate from the federation hub identifier** (7.4): `workflow-service` still registers with the hub using its own, randomly generated, purely opt-in installation ID (`federation_client.py`), not the `installation_id` from this response — federation partners should not automatically learn an installation's internal fleet identity.

## Open Points

- Actively pinging the reported `health_endpoint` (instead of pure heartbeat push) as a possible later addition, not part of this session.
- **No cleanup of permanently unreachable instances** (observed since P4-S1: container restarts without a clean `DELETE /instances/{id}`, e.g. on `docker compose down` without prior deregistration, leave permanent `healthy=false` rows behind). Not critical for routing (`GET /instances/{service_type}` already filters them out), but they accumulate unbounded in the table — periodic cleanup (e.g. deletion after X days without a heartbeat) is not part of this session.
