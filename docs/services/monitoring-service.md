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
| `GET` | `/metrics` | Scrape proxy — the only target Prometheus actually queries. Since P88-S7 also `monitoring_alert_level{rule,sensor}`, see "Alarm levels for Grafana". |
| `GET` | `/sensors` | Aggregated sensor catalog (name/group/cost/description/service types/activation status), ungated. |
| `GET` | `/sensor-config` | Resolved activation configuration (`global_default`, `overrides`), ungated — poll target for `dms_metrics_client.SensorConfigClient`. |
| `PUT` | `/sensor-config/global` | Set the global baseline setting (`admin.monitoring` or activated superuser), audited. |
| `PUT` | `/sensor-config/{key}` | Set an override (`enabled: bool`) or clear it (`enabled: null`, the next less specific setting applies), audited. `key` is a sensor name, `group:<group>` or `cost:cheap|expensive` (since P88-S2; anything else with a prefix is `422`). |
| `GET` | `/alert-rules` | P88-S5 ([ADR 0245](../adr/0245-sensor-alerting-thresholds-downtime-and-notification.md)): every alert rule with its evaluated state (`level`, `value`, `since`, `notified_level`, `suppressed`), ungated |
| `POST`/`PUT`/`DELETE` | `/alert-rules[/{id}]` | Create/replace/delete a rule (`admin.monitoring`). `422` for no threshold, thresholds out of order, an unknown aggregation, repeat < 1. A change restarts streaks and rate but keeps the notified level |
| `GET` | `/alert-rules/prometheus` | The rules as a Prometheus rule file (`promtool check rules` clean), for installations running Alertmanager |
| `GET` | `/downtime-windows` | Windows with `active` now, ungated |
| `POST`/`PUT`/`DELETE` | `/downtime-windows[/{id}]` | `admin.monitoring`; `422` without any range/weekday/time, half a daily range, a bad `HH:MM`, or an end before the start |
| `GET` | `/tracing-config` | P94-S1 ([ADR 0250](../adr/0250-distributed-tracing.md)): the tracing sampling ratio (`sampling_ratio`, who/when, `tracing_enabled` for this service), ungated. The same ratio comes with `GET /sensor-config` as `tracing_sampling_ratio`, which every polling service applies to its sampler |
| `PUT` | `/tracing-config` | Set the ratio 0..1 (`admin.monitoring`), table `tracing_config`; without a row the service's own `DMS_OTEL_SAMPLING_RATIO` applies |
| `GET` | `/log-levels` | P97-S1 ([ADR 0267](../adr/0267-central-log-levels.md)): the active log-level settings, what applies per service (`root_level`/`root_source` `service`/`global`/`env`, logger levels with source `service`/`all`), the logger catalog per service (from the instances' registrations), and the DEBUG limits. Ungated like `/sensor-config`, which carries the same settings as `log_levels` |
| `PUT` | `/log-levels` | `admin.monitoring`. Body `{service?, logger?, level, expires_in_minutes? \| expires_at?}`: nothing = global, a service = its root level, a logger = that logger in that service (or `*`, the default without a service). `422` for an unknown level, an unregistered service (unchecked when the registry doesn't answer), `*` without a logger, a bad logger name, both expiry forms, or DEBUG longer than `DMS_LOG_LEVEL_DEBUG_MAX_MINUTES` (1440). DEBUG without a duration expires after `DMS_LOG_LEVEL_DEBUG_DEFAULT_MINUTES` (60) |
| `DELETE` | `/log-levels?service=&logger=` | `admin.monitoring`; removes one setting, `404` if there is none |
| `GET` | `/healthz` | Own health check |

## Events

Published (stream `monitoring`, `dms-eventbus-client`, after commit):

- `monitoring.sensor.config_changed` — `subject`=sensor name or `"__global__"`, `payload`={`enabled`, `actor`}
- `monitoring.tracing.config_changed` — `subject`=`"tracing"`, `payload`={`sampling_ratio`, `actor`} (P94-S1)
- `monitoring.log_levels.changed` — `subject`=scope, `payload`={`scope`, `level` (`null` on removal), `expires_at`, `previous`, `actor`} (P97-S1)
- `monitoring.log_levels.expired` — actor `system:monitoring-log-levels`, once per expired entry (P97-S1)
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

## Dead letters (P99-S1, [ADR 0270](../adr/0270-eventbus-dead-letters.md))

`eventbus_dead_letters{stream,consumer}`: events a consumer gave up on after all delivery attempts, read
from the NATS `DLQ` stream on every `/metrics` scrape and every alert evaluation.
- Always on and outside the sensor catalog, like `monitoring_alert_level`.
- An empty DLQ is a single `{stream="-",consumer="-"} 0` sample, so the default rule „Unzustellbare
  Ereignisse" (`packages/monitoring-defaults` 1.1.0, sum > 0 → warning) reads `ok` rather than
  `unknown`.
- **Since P99-S2:** `GET /dead-letters`, `GET /dead-letters/{seq}`, `POST /dead-letters/{seq}/replay`
  `{reason}`, `DELETE /dead-letters/{seq}?reason=`.
  - All need `admin.monitoring`.
  - Replay goes to exactly the consumer that failed, through its `replay.<stream>.<consumer>`
    channel.
  - Events `monitoring.dead_letter.replayed`/`.discarded`.
  - Tests: `test_dead_letters.py` (4).

## Alarm levels for Grafana (P88-S7, [ADR 0252](../adr/0252-alert-rules-as-config-category-default-package-and-grafana.md))

`GET /metrics` appends a gauge `monitoring_alert_level{rule,sensor}` per **enabled** rule: `-1` unknown
(also before the first evaluation), `0` ok, `1` warning, `2` error.
- It is read from `alert_state` on every scrape, not cached: only the replica holding the advisory lock
  evaluates, so another replica's memory would lag.
- Like `monitoring_scrape_failures_total` it is always on and outside the sensor catalog.
- Grafana's sensor dashboard shows it as the "Alarme" row. OG Doc stays the only engine; Grafana only
  draws the levels. The rules themselves are kept on the admin page or in a configuration package
  (`alert_rules` in config-service, default package `packages/monitoring-defaults`).

## Limitations of this stage

- **Static Prometheus target** (`infra/prometheus.yml`, one entry: `monitoring-service:8000`) instead of scrape configuration automatically derived from the registry (10.1 names this as an option, not a requirement) — with only one target, automation does not yet add value.
- **TTL poll instead of NATS invalidation** for `SensorConfigClient` (default 15s) — deliberately simpler than the `ComponentLicenseCache` precedent (P9-S2), since this session already covers the sensor concept + new service + scrape proxy + two pilots.
- ~~**No Admin UI control** for toggling sensors on/off~~ — **closed in P88-S2**: admin-ui `/sensors/` ("Monitoring-Sensoren"), see `docs/services/admin-ui.md`.
- **No CheckMK** — Concept 10.1 names it as a third integration target; the user explicitly decided against it at the P11-S2 plan approval (see `docs/operations/monitoring.md`).

## Grafana (P11-S2)

`GET /metrics` is the only source Grafana ever reaches — indirectly via Prometheus, which in turn scrapes exclusively this service. A default dashboard (`infra/grafana/dashboards/dms-sensor-overview.json`) visualizes all currently declared sensors, provisioned declaratively (no manual setup). See `docs/operations/monitoring.md` for details.

## Central log levels (P97-S1, [ADR 0267](../adr/0267-central-log-levels.md))

- **Table `log_level_setting`:** `scope` (PK: `global`, `service:<service>`,
  `logger:<service|*>:<logger>`), `level`, `expires_at`, `updated_by`, `updated_at`.
- **Distribution:** `GET /sensor-config` carries `log_levels`, with expired entries already left out:
  `{"global", "services": {…}, "loggers": [{service, logger, level}]}`.
  - Every service replica polls it every 15 s (`SensorConfigClient`) and applies it with
    `dms_common.logging.apply_log_levels`.
  - Precedence: the service's level over global; the service's own logger entry over `*`.
  - Removed or expired settings give back the logger's previous level (httpx: WARNING) and the root
    level falls back to `DMS_LOG_LEVEL`.
  - A change is written once as `log_levels_applied`, also when the new level would hide it.
- **The log-level loop** (`DMS_LOG_LEVEL_REFRESH_SECONDS`, 15 s): deletes expired rows with
  `DELETE … RETURNING`, so with several replicas each expiry is announced once, and applies this
  service's own levels; it doesn't poll itself. A change through the API is applied here right away.
- **Logger catalog:** `RegistryRegistration` reports the logger names at start (own package, `dms_*`,
  a few third-party loggers); `RegistryClient.service_loggers` merges all healthy instances per service.

## Tests

```bash
scripts/run-tests.sh monitoring-service
```

Log levels (`tests/test_log_levels.py`, 8):
- storage, resolution and distribution;
- DEBUG expiry and cap;
- expiry purged once;
- reset and `404`;
- validation and `403`;
- registry down;
- this service applies its own levels;
- scope round trip.
