# monitoring-service

**Responsibility:** Sensor registry (catalog + activation configuration) and Prometheus scrape proxy (Concept 10.1). New service since P11-S1, arising from a clarifying question at session start: instead of Prometheus scraping every domain service directly, Prometheus scrapes exclusively `monitoring-service` — which in turn queries the `/metrics` endpoints of all currently active, healthy instances live and merges them (pull/proxy model).

**Concept reference:** 10.1
**Own Postgres schema:** `monitoring` (table `sensor_config`)

## Architecture

- **Scrape proxy, not a scrape target list**: `GET /metrics` queries `registry-service`'s existing `GET /instances` (no new registry endpoint needed — the `sensors` field on `InstanceOut` is sufficient), then queries the `/metrics` endpoints of all active, healthy instances in parallel (`asyncio.gather`) and merges the results.
- **A real federation pattern, not text concatenation**: `scraper.merge_metric_families` parses each scraped response with `prometheus_client.parser.text_string_to_metric_families`, groups by metric name, and tags each sample with an `instance` label. Simply concatenating raw exposition texts from multiple instances would violate the exposition format's grouping rule for metrics with the same name (HELP/TYPE must be contiguous for a given metric name).
- **Live, not cached**: every `GET /metrics` call scrapes fresh — no background poll loop needed; the scrape happens on demand, exactly when Prometheus actually queries.
- **A failing target does not block the others**: `scraper.scrape_targets` collects errors instead of aborting and increments the always-active, non-configurable counter `monitoring_scrape_failures_total` (own `CollectorRegistry`, separate from the scraped domain-service metrics — a self-reference here would be unnecessary complexity without real benefit, see "Sensor catalog" below).

## Sensor registry (10.1)

- **Catalog**: `GET /sensors` aggregates the self-declarations of all currently active instances (`instance.sensors`, see `registry-service`) by sensor name, incl. `service_types` list and resolved `active` status. In case of conflicting declarations (unlikely, since a sensor typically comes from the same service type), the most recently seen declaration wins — a documented simplification.
- **Configuration**: `sensor_config` table with a special key `__global__` (baseline setting "monitor everything"/"nothing," default `true`) and any number of sensor-specific overrides. Resolution: an override present → its value, otherwise the global baseline setting.
- **Connected to 7.3 (configuration export) since P12-S3**: `config-service` exports/imports `sensor_config` as a regular category (`GET /sensor-config` and `PUT /sensor-config/global`+`PUT /sensor-config/{sensor_name}`), see [`docs/services/config-service.md`](config-service.md). This resolves the finding deliberately deferred at P11-S0/P11-S1 — until then, the configuration was persisted and audited standalone, without a connection to an export/import mechanism.
- **First gated write operation outside the previous registry domain**: `PUT /sensor-config/global`/`PUT /sensor-config/{sensor_name}` require `admin.monitoring` (new domain-admin role `domain-admin-monitoring`, `permission-service`) or the activated superuser — Concept 10.1 explicitly names disabling security-relevant sensors itself as a security-relevant operation.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/metrics` | Scrape proxy — the only target Prometheus actually queries. |
| `GET` | `/sensors` | Aggregated sensor catalog (name/group/cost/description/service types/activation status), ungated. |
| `GET` | `/sensor-config` | Resolved activation configuration (`global_default`, `overrides`), ungated — poll target for `dms_metrics_client.SensorConfigClient`. |
| `PUT` | `/sensor-config/global` | Set the global baseline setting (`admin.monitoring` or activated superuser), audited. |
| `PUT` | `/sensor-config/{sensor_name}` | Set a sensor-specific override (`enabled: bool`) or clear it (`enabled: null`, falls back to the baseline setting), audited. |
| `GET` | `/healthz` | Own health check |

## Events

Published (stream `monitoring`, `dms-eventbus-client`, after commit):

- `monitoring.sensor.config_changed` — `subject`=sensor name or `"__global__"`, `payload`={`enabled`, `actor`}

Consumed by `audit-service` (`monitoring.>`).

## Pilot sensors (no full retrofit, P11-S0 finding)

Only `registry-service` and `document-service` currently declare sensors:

- `registry.instances.active_total` (gauge, `capacity`, `cheap`)
- `registry.service.heartbeat.miss` (gauge, `reliability`, `cheap` — Concept 10.1 example name)
- `document.upload.duration` (histogram, `performance`, `expensive` — Concept 10.1 example name)
- `document.count.active_total` (gauge, `capacity`, `cheap`)

Further services get their sensors only once actually needed, in later sessions (`libs/dms-metrics-client` is already in place for this, see `docs/operations/monitoring.md`).

## Limitations of this stage

- **Static Prometheus target** (`infra/prometheus.yml`, one entry: `monitoring-service:8000`) instead of scrape configuration automatically derived from the registry (10.1 names this as an option, not a requirement) — with only one target, automation does not yet add value.
- **TTL poll instead of NATS invalidation** for `SensorConfigClient` (default 15s) — deliberately simpler than the `ComponentLicenseCache` precedent (P9-S2), since this session already covers the sensor concept + new service + scrape proxy + two pilots.
- **No Admin UI control** for toggling sensors on/off — backend API only, the same pattern as other sessions that deliver backend before frontend.
- **No CheckMK** — Concept 10.1 names it as a third integration target; the user explicitly decided against it at the P11-S2 plan approval (see `docs/operations/monitoring.md`).

## Grafana (P11-S2)

`GET /metrics` is the only source Grafana ever reaches — indirectly via Prometheus, which in turn scrapes exclusively this service. A default dashboard (`infra/grafana/dashboards/dms-sensor-overview.json`) visualizes all currently declared sensors, provisioned declaratively (no manual setup). See `docs/operations/monitoring.md` for details.

## Tests

```bash
uv run pytest services/monitoring-service/tests
```
