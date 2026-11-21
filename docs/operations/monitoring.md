# Monitoring & Sensor Concept (Concept 10.1, P11-S1/S2)

Concept 10.1 requires a granularly configurable sensor concept: every service brings
named, grouped, cost-aware measurement points that can be enabled/disabled
individually or by group — such that a disabled sensor **does not
generate data at all**, not merely stays invisible in the export. This page describes how
this is actually implemented in the project (`libs/dms-metrics-client`, `registry-service`,
`monitoring-service`) and how a further service can join in.

## Architecture overview

```
Prometheus ──scrape──▶ monitoring-service ──scrape──▶ registry-service /metrics
                              │                    └──▶ document-service /metrics
                              │                    └──▶ (every other self-registering service)
                              │
                              └──query──▶ registry-service GET /instances
                                          (addresses + declared sensor catalogs)
```

Every scraped sample also gets an `instance` (opaque instance ID) and `service`
(`RegistryInstance.service_type`, e.g. `"document-service"`) label injected by
`monitoring_service.scraper.merge_metric_families` - `service` is what Grafana
dashboards actually group/filter by, `instance` disambiguates when multiple
replicas of the same service type are running.

**Architecture decision at session start** (open question): Prometheus does not scrape each
domain service directly, but exclusively `monitoring-service` (pull/proxy model). The
domain services each keep their own `/metrics` endpoint; `monitoring-service` knows all
active instances via `registry-service`'s already existing `GET /instances`, fetches their
`/metrics` endpoints live and in parallel, and merges them into one response (a real
federation pattern via `prometheus_client.parser`, see `docs/services/monitoring-service.md`).

The actual **sensor registry** (catalog + on/off configuration) lives in
`monitoring-service`, not in `registry-service` — this is the concept domain "monitoring", not
"discovery". `registry-service` merely passes through an additional `sensors` field on
self-registration.

## Defining a sensor (`libs/dms-metrics-client`)

```python
from dms_metrics_client import SensorRegistry, SensorSpec

SPEC = SensorSpec(
    name="mein_service.irgendwas.dauer",
    group="performance",
    cost="expensive",  # or "cheap" - Concept 10.1: every sensor knows its cost
    description="What exactly is being measured",
)

registry = SensorRegistry("mein-service", is_active=config_client.is_active)
sensor = registry.histogram(SPEC)  # or .counter()/.gauge()
```

- **`counter()`/`histogram()`** are called inline at the point of the event (`sensor.inc()`/`sensor.observe(value)`) — when the sensor is disabled, nothing happens, not even a `time.monotonic()` call, provided the caller first checks `sensor.is_active()` (see `document_service.main.create_document`).
- **`gauge()`** for "current state" values is updated periodically via `run_gauge_sampler_loop()` — the underlying (potentially expensive) computation only runs if the sensor is active.
- **Pull-on-scrape gauges (since P88-S2b):** `registry.on_scrape(gauge, fn)` (sync) or `registry.on_scrape_async(gauge, async_fn)` compute the value when `/metrics` is read, and only while the sensor is active. A sampler returns a number, a `{label_tuple: value}` dict for a labelled gauge (label sets missing from the dict are dropped), or `None` to keep the last value. A sampler that raises is logged and skipped; it never breaks the scrape. Async samplers need an async `GET /metrics` that calls `await refreshed_metrics_payload(registry)`.
- **`is_active`** comes either from `SensorConfigClient` (TTL poll against `monitoring-service`, for most services) or — if the service happens to be `monitoring-service` itself — directly from its own state.

## Connecting a service as a sensor source

Every self-registering service gets two generic sensors "for free" -
`http.requests` (2xx/4xx/5xx-labeled request counter) and
`http.request.duration_seconds` (response-time histogram) - covering request
count, avg/p95/p99 response time, and error/success count via PromQL, without
any bespoke instrumentation:

1. Add `dms-metrics-client` as a dependency (`pyproject.toml` + `[tool.uv.sources]`).
2. Add `monitoring_service_base_url: str = "http://localhost:8026"` to `Settings`.
3. **At module level**, immediately after `app = FastAPI(...)` (NOT inside
   `lifespan` - `bootstrap_http_sensors` registers ASGI middleware, and FastAPI
   raises `RuntimeError: Cannot add middleware after an application has
   started` if that happens from inside `lifespan`):
   ```python
   sensor_config_proxy, sensor_registry, _requests, _duration = bootstrap_http_sensors(
       app, settings.service_name
   )
   ```
4. Inside `lifespan`, construct a **fresh** `SensorConfigClient` on every
   startup and bind it into the proxy (its `httpx.AsyncClient` can't outlive
   the event loop it was first used on - see `SensorConfigProxy`'s docstring
   in `dms_metrics_client.config_client`):
   ```python
   sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
   await sensor_config_client.start()
   sensor_config_proxy.bind(sensor_config_client)
   app.state.sensor_config_client = sensor_config_client
   app.state.sensor_registry = sensor_registry
   # ... teardown, after yield: sensor_config_proxy.unbind(); await sensor_config_client.stop()
   ```
5. Expose `GET /metrics` via `dms_metrics_client.metrics_payload(app.state.sensor_registry)`
   (or `await refreshed_metrics_payload(...)` in an async endpoint, when the service has async
   scrape samplers).
6. On self-registration, include `sensors=sensor_registry.declarations()` (since P88-S2b: exactly
   what the service registered, so the catalog can't drift). Older form: `http_sensor_declarations()` (merge
   with any bespoke sensor declarations the service also has, see
   `registry_service/metrics.py`'s `sensor_declarations()` for the pattern) -
   `monitoring-service` automatically reads them via `registry-service`'s
   `GET /instances`, no further step needed.

For a service's own **bespoke** sensors beyond the generic HTTP pair (e.g.
`document.upload.duration`): create a `metrics.py` module with the
`SensorSpec`s and register them on the SAME `sensor_registry` returned by
`bootstrap_http_sensors` (one `SensorRegistry`/`CollectorRegistry` per
service - see `registry_service/metrics.py`/`document_service/metrics.py`).

## Runtime sensors in every service (P88-S2b)

`bootstrap_http_sensors` also registers seven runtime sensors, all pull-on-scrape:
- `http.requests.in_flight`;
- `db.pool.in_use`/`db.pool.size`, summed over the engines `dms_db_base.build_engine` created in the
  process (`dms_db_base.live_engines`);
- `process.memory_bytes` (RSS from `/proc`), `process.cpu_seconds`, `process.open_fds`;
- `service.startup.duration_seconds`, which times the lifespan up to its `yield` by wrapping the
  router's lifespan context.

The first is in the group `performance`, the pool sensors in `capacity`, the rest in the new group
`runtime`.

## Catalog extension (P88-S2b)

After a comparison with an external reference system, the user approved packages A–D:

| Sensor | Service | Group / cost level (P88-S2c) | Notes |
|---|---|---|---|
| `license.expiration.days` | license | capacity / 1 | from the poll loop's usage snapshot |
| `license.usage.users.current`, `license.usage.documents.current` | license | capacity / 1 | same snapshot, no extra HTTP calls |
| `auth.sessions.active` | auth | capacity / 3 | Keycloak admin API, in a thread |
| `storage.target.healthy{target}` | storage | reliability / 3 | one `exists()` probe per target, 3 s timeout |
| `storage.target.used_percent{target}` | storage | capacity / 1 | local targets only (`shutil.disk_usage`) |
| `storage.replication.failed_permanent` | storage | reliability / 2 | stands in for a "purge backlog", which doesn't exist: deletes are immediate |
| `ocr.results.failed{state}`, `ocr.results.needs_review` | ocr | reliability / 3 | there is no OCR queue; `state` is `retrying` or `permanent` |
| `rendering.renditions.failed{kind,state}` | rendering | reliability / 3 | `kind` is `thumbnail`, `pdf_archive`, `substitute_text` |
| `rendering.renditions.produced{kind}` | rendering | performance / 1 | counter |
| `virus_scan.quarantined` | virus-scan | security / 2 | scanning is synchronous; open quarantine instead of a queue |
| `workflow.instances.running` | workflow | capacity / 2 | |
| `workflow.tasks.escalated` | workflow | reliability / 1 | counter. There is no failed-instance status and no task due date, so SLA escalations are the overdue measure |
| `archival.transfers.active{kind}`, `archival.transfers.failed{kind}` | archival | capacity, reliability / 3 | `kind` is `document` or `case`; failed means `failed_permanent` |
| `notification.deliveries.failed{channel,state}` | notification | reliability / 3 | |
| `document.locks.oldest_age_hours` | document | reliability / 2 | active locks only |
| `document.trash.overdue` | document | reliability / 2 | past the restore period and not yet purged; holds are not subtracted |
| `search.index.failures` | search | reliability / 1 | counter; failures were only logged and redelivered before |
| `search.query.duration_seconds` | search | performance / 4 | the database search only, without the permission filter |
| `auth.logins{method,result}` | auth | security / 1 | `method` is `technical_account`, `keycloak`, `sso` or `password` (refused before the method is known). `result` is `success`, `rejected` or `maintenance` |
| `permission.cache.lookups{result}`, `permission.cache.invalidations` | permission | security / 1 | counters |
| `permission.cache.rebuild.duration_seconds` | permission | performance / 4 | timed only while on |

## Cost levels and default-off sensors (P88-S2c)

The cost is a level from 1 (very cheap) to 5 (very expensive) instead of cheap/expensive, at the user's
request. The rubric is the work per measurement plus the number of series a sensor produces:

| Level | Meaning | Examples |
|---|---|---|
| 1 | in-memory counters and values, no I/O | `auth.logins`, `process.*`, `http.requests.in_flight`, `license.*` |
| 2 | one indexed count, a cheap syscall, labelled counter with few series | `virus_scan.quarantined`, `http.requests` |
| 3 | grouped counts, histograms with moderate cardinality, one network probe | `ocr.results.failed`, `http.request.duration_seconds`, `storage.target.healthy` |
| 4 | timing histograms on hot paths | `document.upload.duration`, `search.query.duration_seconds` |
| 5 | high-cardinality breakdowns, active probes | package E below |

- **Override keys** are `cost:1` … `cost:5`. Legacy keys are mapped: `cost:cheap` means levels 1–3 and
  `cost:expensive` means 4–5.
  - monitoring-service rewrites stored legacy keys on startup and expands them on write (old exports,
    old scripts).
  - The resolution in `dms-metrics-client` honours a not-yet-migrated legacy key as a safety net.
  - A declared legacy cost reads as 2 (`cheap`) or 4 (`expensive`), so a mixed fleet during a rolling
    update keeps registering.
- **`SensorSpec.default_enabled=False`:** such a sensor stays off under "monitor everything" until its own,
  group or cost-level override turns it on; its catalog source is `default`.
- **admin-ui:** five dots with a green → light green → yellow → orange → red scale, always with the
  filled-dot count and the level in words. One switch per level.

**Package E**, all level 5 and off by default:

| Sensor | Service | Notes |
|---|---|---|
| `document.count.by_type{object_type_id}` | document | active documents per object type |
| `workflow.instances.by_definition{process_definition_id}` | workflow | running instances per definition |
| `rendering.probe.duration_seconds{result}` | rendering | renders a generated 64×64 image to PDF/A with the real renderer |
| `search.probe.duration_seconds{result}` | search | one full-text search against the real index |

The two probes run at most once per 300 s (`dms_metrics_client.throttled`) however often Prometheus
scrapes, and report `result="failed"` instead of raising. Alerting on sensors: since P88-S5, see `docs/services/monitoring-service.md` "Alerting" and ADR 0245.

## Deliberate simplifications of this build-out stage

- **`federation-hub-service`/`fleet-management-service` are deliberately excluded** from the full HTTP-sensor rollout - both are independently-operated, explicitly **not** internal services of a single installation (ADR 0028, see their own `Settings` docstrings), and neither self-registers with `registry-service` at all. Instrumenting them the same way as installation-internal services would blur that architectural boundary.
- **`monitoring-service` itself is not instrumented with the generic HTTP sensors** - it already self-registers and runs its own scrape-proxy `/metrics`; adding itself to its own scrape target list risks a self-referential scrape. Low value anyway (low-volume admin-API traffic).
- **TTL poll instead of NATS invalidation** for `SensorConfigClient` (default 15s) — simpler than the `ComponentLicenseCache` template (P9-S2), a deliberate scope decision given the overall size of this session.
- **Static Prometheus target** (`infra/prometheus.yml`, a single entry `monitoring-service:8000`) instead of scrape configuration automatically derived from the registry — Concept 10.1 names the latter as an option, not a requirement; with only one scrape target, automation does not yet add value.
- **No integration with 7.3** (configuration import/export) — the service only exists from P12-S3 on (the same backward-dependency pattern as the P10-S0 finding on 10.1). Until then, sensor configuration is persisted and audited independently in `monitoring-service`.
- ~~**No admin UI control surface** for sensor on/off toggling~~ — **closed in P88-S2**: admin-ui `/sensors/`, with group and cost-class switches (ADR 0243).
- **No OpenTelemetry tracing connected** — `libs/dms-common.configure_tracing()` already exists (prepared but not called by any service), but per Concept 10.1 is a standalone, complementary topic ("distributed tracing... complements the sensor metrics") and not part of this session.
- **The plugin orchestration service (P10-S1) does not switch to this sensor layer** — its `psutil` sampler remains in place. In the actually existing Docker Compose environment there is only one host anyway; a switch would provide no benefit ahead of real multi-node infrastructure (see P11-S0 finding).

## Grafana (Concept 10.1, P11-S2)

Concept 10.1: "the system itself does not provide its own Grafana-replacement UI, but
provides sensible default dashboard definitions as exportable JSON templates (a starting
point, not mandatory use)." Implemented as:

- **`grafana` compose service** (`infra/docker-compose.yml`), default login `admin`/`admin`
  (pure dev convenience, like other default credentials in this stack).
- **Declarative provisioning** — no manual setup in the UI needed:
  `infra/grafana/provisioning/datasources/prometheus.yml` wires up the already running
  `prometheus` instance with a fixed `uid: prometheus`; `infra/grafana/provisioning/dashboards/dashboards.yml`
  automatically loads every JSON file from `infra/grafana/dashboards/` on startup.
- **`infra/grafana/dashboards/dms-sensor-overview.json`** — the exportable template
  required by the concept: a dashboard with the original pilot sensors
  (`registry.instances.active_total`, `registry.service.heartbeat.miss`,
  `document.upload.duration`, `document.count.active_total`) plus the always-active
  `monitoring_scrape_failures_total` counter and the `up{job="monitoring-service"}` target status.
  - **"Alarme" row at the top (P88-S7, ADR 0252):** a state timeline per alert rule plus counts of rules
    in error, in warning and without a value, from `monitoring_alert_level` (-1 unknown, 0 ok,
    1 warning, 2 error). The alarms are not defined in Grafana: OG Doc evaluates them (ADR 0245) and
    Grafana only draws the result, so the admin page, the mails and Grafana always agree. Installations
    that want Prometheus/Alertmanager to alert instead take `GET /alert-rules/prometheus` (opt-in).
  - **Default rules:** `packages/monitoring-defaults/config.json` (19 rules and a disabled example
    downtime window). It is imported deliberately, like any configuration package, and not seeded on
    start.
- **`infra/grafana/dashboards/dms-service-http-overview.json`** — full-rollout
  dashboard for the generic `http.requests`/`http.request.duration_seconds`
  sensors: a `$service` template variable (populated from
  `label_values(http_requests_total, service)`) drives per-service detail
  panels (request rate by route, avg/p95/p99 response time, error/success
  rate), plus an all-services overview table for spotting which service needs
  attention first without switching the dropdown.
- **`infra/grafana/dashboards/dms-resource-usage.json`** — per-container
  CPU/memory/network from cadvisor (`loadtest/`, service-sizing analysis) -
  a different data source (cadvisor, not the sensor concept) but complements
  the two dashboards above: correlate a service's request volume with its
  actual resource cost.

## CheckMK — deliberately not part of this build-out stage (P11-S2)

Concept 10.1 names CheckMK as a third possible integration target (alongside Prometheus/Grafana),
via its standard Prometheus special agent or a dedicated check plugin. **Open question at
session start**: the user explicitly decided against a CheckMK integration in this
session ("I want Grafana, not CheckMK") — neither as a running container nor as pure
documentation. This is a deliberate scope decision made by the user, not a
technical necessity: research prior to the decision showed that an actual
`checkmk/check-mk-raw` container would be runnable without much manual setup (official
Docker image, automatic site creation), but the fully automated configuration of the
Prometheus special agent rule via the REST API would likely have required a one-time manual
GUI step, due to a lack of independently documented raw format
for this ruleset type. Remains an open point for a possible later session, should
actual demand for a classic IT monitoring landscape (alerting/escalation/SLA reporting)
arise — not silently marked as done.

## Changing sensor configuration

```bash
# Global base setting (default: everything active)
curl -X PUT http://localhost:8026/sensor-config/global \
  -H "x-dms-principal: <principal>" -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Enable an individual sensor independent of the base setting
curl -X PUT http://localhost:8026/sensor-config/document.upload.duration \
  -H "x-dms-principal: <principal>" -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Delete the override again (falls back to the base setting)
curl -X PUT http://localhost:8026/sensor-config/document.upload.duration \
  -H "x-dms-principal: <principal>" -H "Content-Type: application/json" \
  -d '{"enabled": null}'
```

Requires the domain admin role `domain-admin-monitoring` (`admin.monitoring`) or the
activated superuser. Every change is audited as `monitoring.sensor.config_changed`.
