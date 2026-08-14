# monitoring-service

Sensor registry, sensor configuration, and Prometheus scrape proxy (Concept 10.1, P11-S1).
Details in [`docs/services/monitoring-service.md`](../../docs/services/monitoring-service.md)
and [`docs/operations/monitoring.md`](../../docs/operations/monitoring.md).

**Architecture (pull/proxy, user decision at session start)**: Prometheus scrapes
exclusively this service. `monitoring-service` asks `registry-service` for all
currently active, healthy instances, calls their own `/metrics` endpoints live and
in parallel, and merges them into a single response (a genuine federation pattern via
`prometheus_client.parser`, not text concatenation).

**Limitations of this stage** (see P11-S0 finding): pilot on two domain services
(`registry-service`, `document-service`), not a full retrofit. Sensor configuration is
persisted independently (7.3/configuration export doesn't exist until P12-S3).

## Endpoints

- `GET /metrics` — scrape target for Prometheus, merged live.
- `GET /sensors` — aggregated sensor catalog (name/group/cost/description/service types/activation status), ungated.
- `GET /sensor-config` — resolved activation configuration, ungated (poll target for `dms_metrics_client.SensorConfigClient`).
- `PUT /sensor-config/global` — set the global base setting (`admin.monitoring` or activated superuser).
- `PUT /sensor-config/{sensor_name}` — set/delete a sensor-specific override (`admin.monitoring` or activated superuser).

## Events

- `monitoring.sensor.config_changed` — on every configuration change, consumed by `audit-service` (`monitoring.>`).

## Tests

```bash
uv run pytest services/monitoring-service/tests
```
