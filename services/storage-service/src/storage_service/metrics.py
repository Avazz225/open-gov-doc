from dms_metrics_client import GuardedGauge, SensorRegistry, SensorSpec, http_sensor_declarations

# Sensor concept (10.1, Phase 40 Session 4) - this service's first custom
# sensor (previously only the generic HTTP ones). Deliberately updated by
# `GET /metrics` itself (querying `repository.count_pending_copies_by_
# backend` fresh on every scrape) rather than a periodic background loop
# like every other sensor-using service's samplers - storage-service
# deliberately has ZERO in-process background tasks (ADR 0004: "explicit
# endpoint instead of implicit background task"), and a new
# `asyncio.create_task` sampler loop would have been the first one ever,
# cutting against that ADR's own reasoning even though read-only. Pull-
# on-scrape instead matches ADR 0004's philosophy exactly: `/metrics` is
# itself an explicit, externally-triggered call (the monitoring scraper
# hitting it on its own schedule), the same idiom as the external
# CronJob hitting `POST /replication/process-pending` on ITS own
# schedule (see docs/services/storage-service.md).
REPLICATION_BACKLOG = SensorSpec(
    name="storage.replication.backlog",
    group="reliability",
    cost="cheap",
    description=(
        "Anzahl object_copy-Zeilen mit Status 'pending'/'failed' ueber alle "
        "konfigurierten Ziele hinweg (Replikations-Rueckstand, 3.6)"
    ),
)


def sensor_declarations() -> list[dict]:
    return [REPLICATION_BACKLOG.as_dict(), *http_sensor_declarations()]


def build_sensor_registry(registry: SensorRegistry) -> GuardedGauge:
    return registry.gauge(REPLICATION_BACKLOG)
