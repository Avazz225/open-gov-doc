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
- **Configuration**: `sensor_config` table with a special key `__global__` (baseline setting "monitor everything"/"nothing," default `true`) and any number of overrides.
  - **Since P88-S2** ([ADR 0243](../adr/0243-sensor-group-and-cost-overrides-and-admin-page.md)), an override key is a sensor name, `group:<group>` or `cost:cheap|expensive`.
  - **Resolution:** sensor, then group, then cost class, then the global baseline (`dms_metrics_client.resolve_active`).
  - **Applied in each service.** Each service's `Guarded*` sensors apply this resolution themselves, so a group or cost switch stops generation, not just display.
  - **The catalog** reports each sensor's `source` (`sensor`/`group`/`cost`/`global`) and comes sorted by group and name.
  - **Validation:** unknown prefixed keys are `422`.
- **Connected to 7.3 (configuration export) since P12-S3**: `config-service` exports/imports `sensor_config` as a regular category (`GET /sensor-config` and `PUT /sensor-config/global`+`PUT /sensor-config/{sensor_name}`), see [`docs/services/config-service.md`](config-service.md). This resolves the finding deliberately deferred at P11-S0/P11-S1 — until then, the configuration was persisted and audited standalone, without a connection to an export/import mechanism.
- **First gated write operation outside the previous registry domain**: `PUT /sensor-config/global`/`PUT /sensor-config/{sensor_name}` require `admin.monitoring` (new domain-admin role `domain-admin-monitoring`, `permission-service`) or the activated superuser — Concept 10.1 explicitly names disabling security-relevant sensors itself as a security-relevant operation.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/metrics` | Scrape proxy — the only target Prometheus actually queries. |
| `GET` | `/sensors` | Aggregated sensor catalog (name/group/cost/description/service types/activation status), ungated. |
| `GET` | `/sensor-config` | Resolved activation configuration (`global_default`, `overrides`), ungated — poll target for `dms_metrics_client.SensorConfigClient`. |
| `PUT` | `/sensor-config/global` | Set the global baseline setting (`admin.monitoring` or activated superuser), audited. |
| `PUT` | `/sensor-config/{key}` | Set an override (`enabled: bool`) or clear it (`enabled: null`, the next less specific setting applies), audited. `key` is a sensor name, `group:<group>` or `cost:cheap|expensive` (since P88-S2; anything else with a prefix is `422`). |
| `GET` | `/alert-rules` | P88-S5 ([ADR 0245](../adr/0245-sensor-alerting-thresholds-downtime-and-notification.md)): every alert rule with its evaluated state (`level`, `value`, `since`, `notified_level`, `suppressed`), ungated |
| `POST`/`PUT`/`DELETE` | `/alert-rules[/{id}]` | Create/replace/delete a rule (`admin.monitoring`). `422` for no threshold, thresholds out of order, an unknown aggregation, repeat < 1. A change restarts streaks and rate but keeps the notified level |
| `GET` | `/alert-rules/prometheus` | The rules as a Prometheus rule file (`promtool check rules` clean), for installations running Alertmanager |
| `GET` | `/downtime-windows` | Windows with `active` now, ungated |
| `POST`/`PUT`/`DELETE` | `/downtime-windows[/{id}]` | `admin.monitoring`; `422` without any range/weekday/time, half a daily range, a bad `HH:MM`, or an end before the start |
| `GET` | `/healthz` | Own health check |

## Events

Published (stream `monitoring`, `dms-eventbus-client`, after commit):

- `monitoring.sensor.config_changed` — `subject`=sensor name or `"__global__"`, `payload`={`enabled`, `actor`}
- Since P88-S5 (actor = the admin):
  - `monitoring.alert_rule.created`/`.updated`/`.deleted`;
  - `monitoring.downtime_window.saved`/`.deleted`.
- Since P88-S5 (actor `system:monitoring-alerting`):
  - `monitoring.alert.state_changed` on every level change;
  - `monitoring.alert.raised`/`.resolved` when a notification is due (consumed by notification-service).

Consumed by `audit-service` (`monitoring.>`).

## Pilot sensors (no full retrofit, P11-S0 finding)

Beyond the original two pilots (`registry-service`/`document-service`), three more services gained
targeted, high-value custom sensors in Phase 40 Session 4 — still deliberately NOT a blanket
retrofit across all ~25 services, just the concrete cases that came up:

- `registry.instances.active_total` (gauge, `capacity`, `cheap`)
- `registry.service.heartbeat.miss` (gauge, `reliability`, `cheap` — Concept 10.1 example name)
- `document.upload.duration` (histogram, `performance`, `expensive` — Concept 10.1 example name)
- `document.count.active_total` (gauge, `capacity`, `cheap`)
- `federation_hub.retry_cache.forward_pending`/`.result_pending` (gauges, `capacity`, `cheap`,
  Phase 40 Session 3/4) — live depth of the two in-process handover-retry payload caches, directly
  against ADR 0147's memory-pressure risk. This service is not registered with `registry-service`
  (see `docs/services/federation-hub-service.md`), so `monitoring-service`'s own registry-driven
  scrape-proxy can never discover it — `infra/prometheus.yml` therefore scrapes it directly as a
  second, separate static target instead (same idiom as `cadvisor`), bypassing this service's
  aggregation entirely for these two sensors.
- `storage.replication.backlog` (gauge, `reliability`, `cheap`, Phase 40 Session 4) — sum of
  `object_copy` rows in `pending`/`failed` across all configured targets. **Updated pull-on-scrape**
  (computed fresh inside `GET /metrics` itself), not via the periodic-push idiom every other sensor
  here uses — `storage-service` deliberately has no in-process background loops at all (ADR 0004),
  see `docs/services/storage-service.md` "Sensors" for the full rationale.
- `plugin_orchestration.node.cpu_usage_percent`/`.available_ram_mb` (gauges, `capacity`, `cheap`,
  Phase 40 Session 4) — migrates that service's existing `psutil`-sampled node data onto this
  infrastructure, additive to (not a replacement of) the `cluster_node` DB row `placement.py`'s
  scheduling algorithm still reads directly (a `GuardedGauge` has no getter).

**Since P88-S2b** every service also reports seven runtime sensors, and twelve services gained their own backlog, failure, licence and security sensors. The full catalog is in [`docs/operations/monitoring.md`](../operations/monitoring.md) "Catalog extension".

Further services get their sensors only once actually needed, in later sessions (`libs/dms-metrics-client` is already in place for this, see `docs/operations/monitoring.md`).

## Alerting (P88-S5, [ADR 0245](../adr/0245-sensor-alerting-thresholds-downtime-and-notification.md))

**Rules.** A rule names a sensor, an optional label filter (a subset of a sample's labels, including
`service`/`instance`), an aggregation and up to four thresholds (`min_error ≤ min_warning ≤ max_warning
≤ max_error`) with separate `warning_repeat`/`error_repeat`.
- **Aggregations:** `max`/`min`/`sum`/`avg` for gauges.
- **`rate_per_minute`** for counters (`_total`) and histograms (`_count`), from the persisted previous
  total; a counter reset counts the new total.

**Evaluation.** `run_alert_tick` runs every `alert_evaluation_interval_seconds` (60) in the replica that
gets `pg_try_advisory_xact_lock`. It scrapes and merges exactly like `GET /metrics`; `alerting.py` holds
the pure logic, `alert_store.py` the persistence.
- **Levels:** `ok`/`warning`/`error`, or `unknown` without samples (sensor off, service down, filter
  matches nothing).
- **Streaks:** an error breach counts towards both, so an error not yet repeated enough shows as
  warning, and a value back in range is `ok` at once.

**Notification** follows the level: a mail is due when an alarm level differs from `notified_level`,
or on `ok` after a notified alarm, and no active downtime window covers the rule.
- A change during a window is therefore mailed when the window ends, if it still holds.
- Initial `ok` and `unknown` are never mailed.

**Downtime windows** suppress notification only, not evaluation. Each window has an optional absolute
range, optional weekdays plus a daily `HH:MM` range in `timezone` (Europe/Berlin; spanning midnight
allowed), and `rule_ids` (null = all).

**Live:**
- **Raise:** a rule `storage.target.used_percent{target=local}` with warning over 30 (35.2 % used) went
  to `warning` on its first tick and mailed `ops-admin@dms.local` "Warnung: …".
- **Resolve:** raising the threshold to 50 mailed "Wieder in Ordnung: …".
- **Audit and export:** both appeared in the audit trail, and the exported rule file passes
  `promtool check rules`.

**UI (P88-S6):** admin-ui's sensors page, see `docs/services/admin-ui.md` "Sensor Alerts". For its rule editor, `POST /alert-rules/preview` evaluates a draft against a fresh scrape (value, would-be level, matching series without `instance`; `reason` `no_samples`/`rate_needs_history`), ungated like `GET /metrics`.

## Limitations of this stage

- **Static Prometheus target** (`infra/prometheus.yml`, one entry: `monitoring-service:8000`) instead of scrape configuration automatically derived from the registry (10.1 names this as an option, not a requirement) — with only one target, automation does not yet add value.
- **TTL poll instead of NATS invalidation** for `SensorConfigClient` (default 15s) — deliberately simpler than the `ComponentLicenseCache` precedent (P9-S2), since this session already covers the sensor concept + new service + scrape proxy + two pilots.
- ~~**No Admin UI control** for toggling sensors on/off~~ — **closed in P88-S2**: admin-ui `/sensors/` ("Monitoring-Sensoren"), see `docs/services/admin-ui.md`.
- **No CheckMK** — Concept 10.1 names it as a third integration target; the user explicitly decided against it at the P11-S2 plan approval (see `docs/operations/monitoring.md`).

## Grafana (P11-S2)

`GET /metrics` is the only source Grafana ever reaches — indirectly via Prometheus, which in turn scrapes exclusively this service. A default dashboard (`infra/grafana/dashboards/dms-sensor-overview.json`) visualizes all currently declared sensors, provisioned declaratively (no manual setup). See `docs/operations/monitoring.md` for details.

## Tests

```bash
uv run pytest services/monitoring-service/tests
```
