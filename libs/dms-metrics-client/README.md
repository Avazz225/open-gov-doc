# dms-metrics-client

Sensor concept (Concept 10.1, P11-S1): configurable, cost-aware Prometheus sensors for DMS services.

A sensor is a `SensorSpec` (name/group/cost/description), registered via a
`SensorRegistry`, which builds `Guarded*` wrappers (`GuardedCounter`/`GuardedGauge`/`GuardedHistogram`)
around `prometheus_client` objects from it. If a sensor is disabled, collection is
**entirely** skipped (no `inc()`/`observe()`/`set()` on the underlying Prometheus object,
no expensive database query in the periodic sampler) — not just visibility in the export.

The activation status comes from an `is_active(name) -> bool` function that the
`SensorRegistry` receives at build time:

- For remote services: `SensorConfigClient` polls `monitoring-service`'s `GET /sensor-config`
  (TTL cache, default 15s, fails open to "everything active").
- `run_gauge_sampler_loop()` is a generic poll loop for "current state" gauges
  (e.g. "how many active documents are there right now") — invokes the expensive computation
  only for currently active sensors.
- `metrics_payload(registry)` returns raw bytes + content type in Prometheus exposition format
  (no FastAPI dependency in this lib — the service builds the `Response` itself).

See `docs/operations/monitoring.md` for the overall picture (scrape proxy via `monitoring-service`,
sensor registry, configuration management) and `services/registry-service`/`services/document-service`
for the two pilots that use this lib.
