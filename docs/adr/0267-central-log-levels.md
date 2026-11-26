# 0267 — Central, per-area log levels

**Status:** accepted (P97-S1 backend, P97-S2 CLI and admin UI)
**Context:** user request 2026-11-26.
- Log levels should be settable centrally and per function, and through the CLI.
- Until now each service had exactly one level, `DMS_LOG_LEVEL`, applied once at startup in
  `dms_common.logging.configure_logging`; a change meant a redeploy.

What already existed:
- every module logs through `logging.getLogger(__name__)`;
- 30 of 32 services poll `GET /sensor-config` from monitoring-service every 15 s
  (`dms_metrics_client.SensorConfigClient`);
- P94-S1's tracing ratio travels that way: stored centrally, audited, applied at runtime (ADR 0250).

## Decision 1 — "Per function" means per logger

Module loggers carry their area in their name: `workflow_service.repository`, `permission_service.main`,
`sqlalchemy.engine`, `httpx`. A level on a logger covers its children, as in Python's `logging`. There is
no second taxonomy of "functions" to maintain. Friendly area names can be added later where the catalog
shows they're needed.

**Scopes, from broad to specific:**

| Scope | Key | Effect |
|---|---|---|
| global | `global` | root level of every service |
| service | `service:<service>` | root level of one service |
| logger | `logger:<service\|*>:<logger>` | one logger in one service, or in all |

**Precedence:**
- the root level is the service's setting, then global, then the service's own `DMS_LOG_LEVEL`;
- for a logger, the service's own entry beats `*`.

## Decision 2 — Stored in monitoring-service, distributed with the sensor configuration

- **Storage:** table `log_level_setting`. `GET /log-levels` is ungated like `/sensor-config`;
  `PUT`/`DELETE /log-levels` need `admin.monitoring` (`domain-admin-monitoring`), the same gate as
  tracing and sensors.
- **Audit:** `monitoring.log_levels.changed` (with the previous state) and `monitoring.log_levels.expired`.
- **Distribution:** `GET /sensor-config` carries the settings as `log_levels`. No new channel: the
  `SensorConfigClient` hands them to a listener that `dms_common.logging` registers, as `otel` does for
  the tracing ratio. Not one of the 30 services changed.
- **Applying (`apply_log_levels`):** idempotent on every poll.
  - It remembers a logger's own level before the first central change and gives it back when the
    setting goes (httpx keeps its WARNING default).
  - It writes `log_levels_applied` once per actual change, straight to the handler, so a switch to
    ERROR is still visible. The first poll with nothing set stays silent.
- **Fail-open:** while monitoring-service is unreachable the last applied levels stay; an older
  monitoring-service without the key changes nothing.

**Replicas:** every replica polls on its own and applies the same settings, keyed by service name, not
by instance. That includes a replica that starts later. monitoring-service doesn't poll itself:
- a loop applies its own levels and handles expiry every 15 s;
- a change through its API applies at once in the replica that took it.

## Decision 3 — DEBUG always expires

DEBUG can write personal data, multiplies log volume, and a forgotten DEBUG is a classic problem.
- Without a duration it expires after 60 minutes (`DMS_LOG_LEVEL_DEBUG_DEFAULT_MINUTES`).
- It can't be set for longer than 1440 (`…_MAX_MINUTES`).
- Other levels stay until removed, unless given a duration.

Expired entries:
- drop out of the distributed form at once;
- are deleted by the loop with `DELETE … RETURNING`, so with several monitoring replicas each expiry is
  announced exactly once.

## Decision 4 — A logger catalog from the registrations

`RegistryRegistration` reports the process's logger names at start, under a new pass-through field
`loggers` in registry-service:
- the service's own package;
- the `dms_*` libraries;
- a few third-party loggers (`sqlalchemy*`, `httpx`, `httpcore`, `uvicorn*`, `nats`, `opentelemetry`).

monitoring-service merges all healthy instances per service. A `PUT` with a service name the registry
doesn't know is refused, since a typo would silently match nothing; when the registry can't answer the
check is skipped. Logger names aren't checked against the catalog: loggers can appear later (e.g.
`sqlalchemy.engine.Engine` on first use).

## Consequences

- **Live (P97-S1):**
  - `httpx` set to INFO for workflow-service with one minute: its request lines appeared within a
    poll, the level returned by itself after the expiry, and the lines stopped;
  - the audit trail holds the change under the admin and the expiry under
    `system:monitoring-log-levels`.
- **Rollout:** services built before P97 ignore `log_levels` and report no catalog; they pick both up
  with their next image.
- **Per-instance confirmation** (which replica runs which level) isn't reported back. The
  `log_levels_applied` line per instance (with `service_name`, in Loki) serves as the evidence. A
  heartbeat field could follow if needed.
- **P97-S2:**
  - `dms logging show|set|reset|loggers` (`--for 30m|2h|1h30m`);
  - the admin UI section „Log-Level" on the monitoring page;
  - the admin skill recipe `15-log-levels.md`.

- **P97-S3, live with three permission-service instances** (`--profile permission-replicas`, two extra
  replicas):
  - all three reported their logger catalog;
  - `dms logging set INFO --service permission-service --logger httpx --for 4m`: all three switched
    within 4 s, and workflow-service applied nothing;
  - monitoring-service stopped for a minute: each replica logged `sensor_config_refresh_failed` and kept
    its level;
  - after the restart and the expiry all three fell back within one poll, and the audit trail holds the
    expiry exactly once.
