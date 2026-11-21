# registry-service

**Responsibility:** Service discovery — registration, heartbeat, active routing table per service type (Concept 3.2a). Since P9-S2 additionally license brokering (3.2b/9.3): queries `license-service` and passes a computed license status (`licensed`/`demo`/`unlicensed`) per `service_type` to registering/heartbeating services, without performing any license check itself. Since P10-S2 additionally the **drain mechanism** (10.5/3.8): an instance can be marked as `draining`, remains reachable during this, but no longer receives new requests via the gateway. Since P13-S1 additionally the sole HTTP-queryable source of information for this installation's **installation identity** (3a).

**Concept Reference:** 3.2a, 3.2b, 3a, 9.3, 10.5
**Own Postgres schema:** `registry` (table `service_instance`)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/instances` | Register/update (upsert by `instance_id`). **Since Phase 59 Session 4** ([ADR 0181](../adr/0181-registry-service-instance-mutation-authorization.md)): `401`/`403` self-service gate, see "Authorization" below |
| `POST` | `/instances/{instance_id}/heartbeat` | Heartbeat, updates `last_heartbeat_at`. **Since Phase 59 Session 4**: `403` self-service gate |
| `DELETE` | `/instances/{instance_id}` | Deregister. **Since Phase 59 Session 4**: `403` self-service gate |
| `POST` | `/instances/{instance_id}/drain` | Drain mechanism (10.5/3.8, P10-S2): sets `status="draining"`. **Since Phase 59 Session 4**: gated via `registry_operator_key` (`Authorization: Bearer ...`) — WHEN draining happens is still decided by an external deploy tool/`scripts/rolling-update.sh`, not by the registry itself, now with an actual operator secret proving that caller is legitimate. |
| `POST` | `/instances/{instance_id}/activate` | Reversal of `/drain` (10.5, P10-S3): resets `status="active"` — the basis for a real rollback path, see `docs/operations/rolling-updates.md`. **Since Phase 59 Session 4**: same `registry_operator_key` gate as `/drain` |
| `GET` | `/instances/{service_type}` | Only currently reachable instances of this type |
| `GET` | `/instances` | All instances incl. computed `healthy` flag |
| `GET` | `/license-status/{service_type}` | Computed license status (`licensed`/`demo`/`unlicensed`) for this service type — ungated, for internal poll clients (e.g. `workflow-service`, see below). |
| `GET` | `/metrics` | Own sensors in Prometheus format (10.1, P11-S1) — scraped by `monitoring-service`, not directly by Prometheus. |
| `GET` | `/installation` | Installation identity (3a, P13-S1): `{id, display_name}` from `DMS_INSTALLATION_ID`/`DMS_INSTALLATION_DISPLAY_NAME` (`dms_common.BaseServiceSettings`) — ungated, pure configuration values, no DB access. |
| `GET` | `/installation/branding` | **Since P69-S2** ([ADR 0202](../adr/0202-p69s2-branding-config-and-role-dependent-dashboard.md)): installation-level branding (7.3/8) — `{product_name, accent_color, logo_url, updated_at}`, all three nullable. Deliberately ungated (must render on the login screen, before any token exists) — the gateway allows only `GET` on this exact path through unauthenticated, see "Gateway Routing" below. |
| `PUT` | `/installation/branding` | Update branding — requires `admin.object_config` (this service's first ever `PermissionServiceClient` check, reusing the existing capability rather than minting a new one). Reachable via `config-service`'s regular 7.3 export/import (`branding_config` category, singleton shape like `sensor_config`/`federation_config`). |
| `POST` | `/instances/{instance_id}/forget` | **Since P87-S2** ([ADR 0241](../adr/0241-service-instance-replacement-and-expected-replicas.md)): an admin forgets an entry by hand, e.g. a service decommissioned for good. Requires `admin.monitoring`. Publishes `registry.instance.forgotten` with `reason: admin` |
| `GET` | `/replicas/summary` | **Since P87-S3**: per-state counts plus the types in an alarm state, for fleet-management. Gated by `DMS_FLEET_AGENT_API_KEY` (`403` without a configured key), public at the gateway for `GET` only (ADR 0241 Decision 5) |
| `GET` | `/replicas` | **Since P87-S2**: per service type the expected range and its source (`admin`/`default`/`none`), `reachable`/`draining`/`unreachable` counts, and the committed alarm `state` with `since` (plus a `pending_state` still inside the debounce). Readable like `GET /instances` |
| `PUT` | `/replicas/{service_type}` | **Since P87-S2**: admin override `{min_replicas, max_replicas?}` (`max` ≥ `min`, `null` = no upper limit). Requires `admin.monitoring`. Publishes `registry.replicas.expectation_set` |
| `DELETE` | `/replicas/{service_type}` | **Since P87-S2**: drops the override; the Helm default (or none) applies again. Requires `admin.monitoring` |
| `GET` | `/healthz` | Own health check |

## Data Model

`service_instance`: `instance_id` (PK), `service_type`, `version`, `capabilities` (JSON list), `sensors` (JSON list, since P11-S1 — purely passed-through sensor self-declaration, see below), `health_endpoint`, `address`, `registered_at`, `last_heartbeat_at`, `status` (`"active"`/`"draining"`, default `"active"`, since P10-S2 — added additively via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, no Alembic in this phase). `healthy` is not a stored field but computed on every query from `last_heartbeat_at` vs. `heartbeat_timeout_seconds` (default 15s, configurable via `DMS_HEARTBEAT_TIMEOUT_SECONDS`). `license_status` (in `InstanceOut`) is likewise not a stored field but appended to every response via `ComponentLicenseCache`. Since P87-S2 also `expected_replicas` (`service_type` PK, `min_replicas`, `max_replicas` nullable, `updated_by`, `updated_at`) and `replica_alarm_state` (`service_type` PK, `state`, `since`, `pending_state`, `pending_since`).

## Drain Mechanism (10.5/3.8, P10-S2/S3)

- **State, no automatic trigger**: `POST /instances/{instance_id}/drain` sets `status="draining"` — the registry itself does not decide *when* draining happens. Since P10-S3 this is handled by `scripts/rolling-update.sh`, which reuses the same mechanism for update rollouts instead of building it anew (Concept 10.5: "the same drain mechanism... just for a different occasion").
- **Effect only in routing**: a `draining` instance remains visible in `GET /instances/{type}` (not deregistered, no kill) but disappears from the gateway's selection pool for **new** requests (`gateway_service.upstream.InstanceResolver.resolve()` filters on `status == "active"` in addition to `healthy`). Already-running requests are never affected — this literally matches 10.5 ("no longer accepts new tasks but completes running operations").
- **Since Phase 59 Session 4**: gated via `registry_operator_key`, see "Authorization" below — not "ungated like every other endpoint" anymore, since every mutating endpoint now has a real gate.
- A **new** registration (row does not yet exist) always starts with `status="active"`; a re-registration of the same `instance_id` (self-healing after `404`, not a real restart, see `dms-registry-client`) leaves an existing `status` unchanged — only heartbeat/register never automatically revert it, only `/drain`/`/activate` set it.
- **Rollback (10.5, P10-S3)**: `POST /instances/{instance_id}/activate` resets `status` back to `"active"` — without this reversal there would be no way to make an already-drained instance reachable for new requests again. Concept 10.5 explicitly requires that a rollback remains possible as long as the drain is not yet fully completed (i.e. the instance has not yet stopped). Used by `scripts/rolling-update.sh`'s manual rollback procedure, see `docs/operations/rolling-updates.md`.

## Instance Replacement and Expected Replicas (P87-S2, [ADR 0241](../adr/0241-service-instance-replacement-and-expected-replicas.md))

`replicas.py` holds the rules; `run_replica_tick` in `main.py` applies them every
`cleanup_poll_interval_seconds` (30 s). The tick replaces Phase 58's 7-day cleanup, which also deleted
types that vanished for good, against the user's rule.

**Forgetting (Decision 1).**
- **The rule:** per `service_type`, once at least one instance is reachable and `active` (R ≥ 1), only
  `max(0, min − R)` unreachable entries stay (the most recently seen). Every other entry that has been
  unreachable for longer than `forget_after_seconds` (60 s) is forgotten. No expectation counts as
  min = 1.
- **What it covers:** restart, rolling restart and scale-down all come out of this one rule. A crashed
  replica under an expectation stays visible.
- **R = 0** (a vanished type) keeps everything until an admin forgets it (`POST .../forget`).
- **Draining** instances are leaving on purpose: they count neither as reachable nor as dead.
- **Late heartbeats:** a forgotten instance that is still alive gets a 404 on its next heartbeat, which
  `dms-registry-client` answers by registering again.
- **The event:** each forget publishes `registry.instance.forgotten` (`reason: replaced`, actor
  `system:registry`). audit-service consumes `registry.>`, so the audit trail says why an entry went.

**Expected replicas (Decision 3).**
- **A range `min..max`.** An admin override (table `expected_replicas`) wins over the Helm default
  `DMS_EXPECTED_REPLICAS_DEFAULTS` (JSON, `{"document-service": {"min": 2, "max": 6}}`).
- **Helm** renders that default from each enabled, self-registering service: HPA `minReplicas..
  maxReplicas`, else `replicas..replicas`. It skips static frontends and services marked
  `registersWithRegistry: false`.
- **Compose** sets none, so the dev stack has no expectations and no alarms.
- A type that is expected but never registered counts as vanished.

**Alarms (Decision 4).**
- **States:** `ok`/`under`/`vanished`/`over`/`unmonitored`.
- **Debounce:** an observed state becomes committed once it has held for `replica_alarm_after_seconds`
  (120 s), so restarts and rolling updates don't alarm.
- **Persistence:** the state lives in `replica_alarm_state`. The commit is a conditional
  `UPDATE … WHERE state = <old>`, so with several registry replicas exactly one publishes.
- **Events, one per committed transition:**
  - `registry.replicas.under` (also for `vanished`, with `reachable: 0`);
  - `registry.replicas.over`;
  - `registry.replicas.restored` (leaving an alarm state).
  - Payload: `service_type`, `state`, `previous_state`, `reachable`, `min`, `max`.
- **Sensors,** labelled `service_type` and set after each tick:
  - `registry.replicas.reachable`;
  - `registry.replicas.expected_min` and `.expected_max` (only when set);
  - `registry.replicas.state` (−1 under/vanished, 0 ok, 1 over).
  - `dms-metrics-client` gauges take `labelnames` since this session.

## Authorization (Phase 59 Session 4, [ADR 0181](../adr/0181-registry-service-instance-mutation-authorization.md))

Previously every mutating instance endpoint had **zero authorization** — any authenticated caller reachable through the gateway could register a fake instance of an arbitrary `service_type` at an attacker-controlled `address` (a traffic-hijack/credential-harvesting vector via `gateway_service.upstream.InstanceResolver`'s unvalidated `random.choice()` pool), or drain/deregister any REAL instance (a fleet-wide denial-of-service). Two gate shapes, chosen by tracing every real caller first:

- **`POST /instances`, `POST /instances/{id}/heartbeat`, `DELETE /instances/{id}` — self-service gate**: the caller's own `X-DMS-Principal` must equal the `service_type` it is acting on (for register: the payload's `service_type`; for heartbeat/deregister: the target instance's own `service_type`, existence-checked first). `libs/dms-registry-client`'s `RegistryRegistration` now sends `X-DMS-Principal: <its own service_type>` as a default header on every request — **one shared-library change automatically covers all ~31 self-registering services**, no per-service source change needed. `401` with no header (register only — heartbeat/deregister collapse a missing header into the same `403` as a mismatched one, since existence is checked first and an empty header can never match a real `service_type` anyway), `403` on mismatch.
- **`POST /instances/{id}/drain`, `.../activate` — operator-key gate**: the real caller (`scripts/rolling-update.sh`, an external ops script/human operator, confirmed via grep — never a backend service or `admin-ui`) has no natural service identity of its own, so these reuse `federation-hub-service`'s exact `hub_operator_key` shape: `403` unless `Authorization: Bearer <registry_operator_key>` matches `DMS_REGISTRY_OPERATOR_KEY`, `None`/fully locked by default. `scripts/rolling-update.sh` reads the same value from `REGISTRY_OPERATOR_KEY` in the operator's own shell.

## License Brokering (3.2b/9.3, P9-S2)

- **Only configured components are subject to licensing at all**: `settings.licensable_components` (default `{"workflow-service": "demo", "webdav-connector": "demo", "migration-service": "demo"}`, the latter two since P12-S1/P12-S2) assigns each separately licensable `service_type` a policy (`"demo"` or `"lock"`) that applies when no valid license is installed or the component is not included in the license's `licensed_components`. Every unlisted `service_type` is "core" and always gets `"licensed"` — Concept 9.1 explicitly names the CMIS connector/migration service/workflow automation as examples of separately licensable components. Concept 3.3 explicitly names connectors as an example, hence `webdav-connector` since P12-S1 follows the same `"demo"` pattern as `workflow-service` (see `docs/services/webdav-connector.md`); `migration-service` likewise since P12-S2 (see `docs/services/migration-service.md`).
- **`ComponentLicenseCache`** (`licensing.py`): a TTL cache (`license_status_cache_ttl_seconds`, default 60s) around the raw `license-service` status, plus invalidation by the new `license.>` NATS consumer (`consumer.py`, this service's first own consumer, durable `registry-service`) — reacts to status changes both event-driven and with a hard upper bound.
- `InstanceOut` (register/heartbeat/listing responses) additionally carries `license_status`. A dedicated `GET /license-status/{service_type}` lets already-running services re-query their own status without restarting (e.g. `workflow-service`'s `license_client.LicenseStatusClient`, see `docs/services/workflow-service.md`).
- Fail-open when `license-service` is unreachable (`"licensed"` for core, configured policy for licensable components remains at its last known value) — a license service outage should not disable the registry.

## Events

Published (stream `registry`, `dms-eventbus-client`, after commit):

- `registry.instance.registered` — `subject`=`instance_id`, `payload`={`service_type`, `version`}
- `registry.instance.deregistered` — `subject`=`instance_id`, `payload`={`service_type`}
- `registry.instance.forgotten` (since P87-S2) — `subject`=`instance_id`, `payload`={`service_type`, `reason`: `replaced`|`admin`}
- `registry.replicas.under`/`.over`/`.restored` (since P87-S2) — `subject`=`service_type`, see above
- `registry.replicas.expectation_set`/`.expectation_reset` (since P87-S2) — `subject`=`service_type`, actor = the admin

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

## Installation-Level Branding (7.3/8, P69-S2, [ADR 0202](../adr/0202-p69s2-branding-config-and-role-dependent-dashboard.md))

- New `BrandingConfig` singleton row (`id=1`, same lazy-seed-on-first-access pattern as `workflow_service.models.FederationConfig`) — `product_name`/`accent_color`/`logo_url` all nullable, `None` meaning "use this build's static default," not an error state any consumer needs to special-case away.
- Co-located here (not a new service) because it describes "what is this installation," the same question `GET /installation` already answers — one machine-readable, the other now also human-facing.
- `GET` is ungated like `GET /installation`; `PUT` requires `admin.object_config` — this service's first ever `PermissionServiceClient` consumer (`dms-permission-client`), reusing the existing capability `workflow-service`'s federation config and BPMN upload already use, rather than minting a narrower one for a single write endpoint.
- **Gateway routing needed a new mechanism**: every previous `public_routes` entry in `gateway-service` was either strictly read-only on its own path or used only pre-auth on a path with no authenticated counterpart. `GET`/`PUT /installation/branding` share the exact same path with different authorization needs — the gateway's route matching gained an optional `"<METHOD> "` prefix so a public entry can be scoped to one HTTP method instead of every method on that path (`"GET registry-service:installation/branding"` in `settings.public_routes`/`maintenance_mode_allowed_routes`, matched by a new `method_route_key` alongside the existing bare `route_key`). See `docs/services/gateway-service.md`.
- Five of the six frontend apps (`user-ui`/`admin-ui`/`reviewer-ui`/`process-designer`/`migration-console`) fetch this once at load and apply `product_name`/`accent_color` on top of the static `libs/dms-ui` tokens; `office-addin` deliberately does not (see `docs/services/office-addin.md` "follow the host" reasoning). `libreoffice-addin` was never in scope (no browser DOM).

## Open Points

- Actively pinging the reported `health_endpoint` (instead of pure heartbeat push) as a possible later addition, not part of this session.
- ~~**Zero authorization on every mutating instance endpoint** — any authenticated caller could register a fake instance at an attacker-controlled address, or drain/deregister a real one.~~ — **closed in Phase 59 Session 4**, see "Authorization" above ([ADR 0181](../adr/0181-registry-service-instance-mutation-authorization.md)). 54/54 tests (+9).
- ~~**No cleanup of permanently unreachable instances** (observed since P4-S1: container restarts without a clean `DELETE /instances/{id}`, e.g. on `docker compose down` without prior deregistration, leave permanent `healthy=false` rows behind). Not critical for routing (`GET /instances/{service_type}` already filters them out), but they accumulate unbounded in the table — periodic cleanup (e.g. deletion after X days without a heartbeat) is not part of this session.~~ — **closed in Phase 58 Session 2** by a 7-day cleanup, which **P87-S2 replaced** with the per-type forgetting rule (see "Instance Replacement and Expected Replicas" above).
- ~~Admin UI for expectations, forgetting and alarm state, and notification-service's subscription to `registry.replicas.*`~~ — done in P87-S3 (see `docs/services/admin-ui.md` "Registry by Service Type" and `docs/services/notification-service.md` "Replica alarm consumers").
