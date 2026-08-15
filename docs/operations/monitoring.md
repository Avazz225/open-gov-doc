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
5. Expose `GET /metrics` via `dms_metrics_client.metrics_payload(app.state.sensor_registry)`.
6. On self-registration, include `sensors=http_sensor_declarations()` (merge
   with any bespoke sensor declarations the service also has, see
   `registry_service/metrics.py`'s `sensor_declarations()` for the pattern) -
   `monitoring-service` automatically reads them via `registry-service`'s
   `GET /instances`, no further step needed.

For a service's own **bespoke** sensors beyond the generic HTTP pair (e.g.
`document.upload.duration`): create a `metrics.py` module with the
`SensorSpec`s and register them on the SAME `sensor_registry` returned by
`bootstrap_http_sensors` (one `SensorRegistry`/`CollectorRegistry` per
service - see `registry_service/metrics.py`/`document_service/metrics.py`).

## Deliberate simplifications of this build-out stage

- **`federation-hub-service`/`fleet-management-service` are deliberately excluded** from the full HTTP-sensor rollout - both are independently-operated, explicitly **not** internal services of a single installation (ADR 0028, see their own `Settings` docstrings), and neither self-registers with `registry-service` at all. Instrumenting them the same way as installation-internal services would blur that architectural boundary.
- **`monitoring-service` itself is not instrumented with the generic HTTP sensors** - it already self-registers and runs its own scrape-proxy `/metrics`; adding itself to its own scrape target list risks a self-referential scrape. Low value anyway (low-volume admin-API traffic).
- **TTL poll instead of NATS invalidation** for `SensorConfigClient` (default 15s) — simpler than the `ComponentLicenseCache` template (P9-S2), a deliberate scope decision given the overall size of this session.
- **Static Prometheus target** (`infra/prometheus.yml`, a single entry `monitoring-service:8000`) instead of scrape configuration automatically derived from the registry — Concept 10.1 names the latter as an option, not a requirement; with only one scrape target, automation does not yet add value.
- **No integration with 7.3** (configuration import/export) — the service only exists from P12-S3 on (the same backward-dependency pattern as the P10-S0 finding on 10.1). Until then, sensor configuration is persisted and audited independently in `monitoring-service`.
- **No admin UI control surface** for sensor on/off toggling — only the backend API (`GET /sensors`, `PUT /sensor-config/...`).
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
