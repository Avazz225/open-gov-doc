from collections.abc import Awaitable, Callable

from dms_metrics_client import GuardedGauge, SensorRegistry, SensorSpec, http_sensor_declarations
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_service import repository

# registry-service was one of the two original sensor pilots (10.1,
# P11-S1, see P11-S0 finding) - both bespoke sensor names below are
# deliberately taken verbatim from concept 10.1's own example list, where
# available ("registry.service.heartbeat.miss"). Now additionally covered
# by the generic http.* sensors from the full rollout (see `main.py`'s
# module-level `bootstrap_http_sensors` call, which builds the
# `SensorRegistry` this module's sensors are added to).
ACTIVE_INSTANCES = SensorSpec(
    name="registry.instances.active_total",
    group="capacity",
    cost="cheap",
    description="Anzahl aktuell aktiver, gesunder Service-Instanzen",
)
HEARTBEAT_MISS = SensorSpec(
    name="registry.service.heartbeat.miss",
    group="reliability",
    cost="cheap",
    description=(
        "Anzahl registrierter Instanzen, deren letzter Heartbeat aktuell "
        "überfällig ist (Live-Zählung, kein monotoner Event-Counter)"
    ),
)


# Expected replicas (P87-S2, ADR 0241 Decision 4) - one value per
# `service_type` label, set by the replica loop in `main.py` after each
# evaluation (not by the generic sampler loop, which knows no labels).
REPLICAS_REACHABLE = SensorSpec(
    name="registry.replicas.reachable",
    group="capacity",
    cost="cheap",
    description="Erreichbare, aktive Instanzen je Service-Typ",
)
REPLICAS_EXPECTED_MIN = SensorSpec(
    name="registry.replicas.expected_min",
    group="capacity",
    cost="cheap",
    description="Erwartete Mindestanzahl Instanzen je Service-Typ (nur wenn gesetzt)",
)
REPLICAS_EXPECTED_MAX = SensorSpec(
    name="registry.replicas.expected_max",
    group="capacity",
    cost="cheap",
    description="Erwartete Höchstanzahl Instanzen je Service-Typ (nur wenn gesetzt)",
)
REPLICAS_STATE = SensorSpec(
    name="registry.replicas.state",
    group="reliability",
    cost="cheap",
    description="Replika-Zustand je Service-Typ: -1 zu wenig/verschwunden, 0 ok, 1 zu viele",
)
REPLICA_SPECS = (REPLICAS_REACHABLE, REPLICAS_EXPECTED_MIN, REPLICAS_EXPECTED_MAX, REPLICAS_STATE)
_STATE_VALUES = {"under": -1.0, "vanished": -1.0, "ok": 0.0, "over": 1.0}


def sensor_declarations() -> list[dict]:
    return [
        ACTIVE_INSTANCES.as_dict(),
        HEARTBEAT_MISS.as_dict(),
        *(spec.as_dict() for spec in REPLICA_SPECS),
        *http_sensor_declarations(),
    ]


def build_replica_gauges(registry: SensorRegistry) -> dict[str, GuardedGauge]:
    return {spec.name: registry.gauge(spec, labelnames=("service_type",)) for spec in REPLICA_SPECS}


def observe_replicas(gauges: dict[str, GuardedGauge], rows: list) -> None:
    """`rows` are `ReplicaTypeOut`s. Types without an expectation report
    only their reachable count; min/max/state stay absent for them."""
    for gauge in gauges.values():
        gauge.clear()
    for row in rows:
        labels = {"service_type": row.service_type}
        gauges[REPLICAS_REACHABLE.name].set(row.reachable, **labels)
        if row.expected_min is not None:
            gauges[REPLICAS_EXPECTED_MIN.name].set(row.expected_min, **labels)
        if row.expected_max is not None:
            gauges[REPLICAS_EXPECTED_MAX.name].set(row.expected_max, **labels)
        if row.state in _STATE_VALUES:
            gauges[REPLICAS_STATE.name].set(_STATE_VALUES[row.state], **labels)


def build_sensor_registry(registry: SensorRegistry) -> tuple[GuardedGauge, GuardedGauge]:
    active_gauge = registry.gauge(ACTIVE_INSTANCES)
    heartbeat_miss_gauge = registry.gauge(HEARTBEAT_MISS)
    return active_gauge, heartbeat_miss_gauge


def build_samplers(
    active_gauge: GuardedGauge,
    heartbeat_miss_gauge: GuardedGauge,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    heartbeat_timeout_seconds: float,
) -> dict[str, tuple[GuardedGauge, Callable[[], Awaitable[float]]]]:
    async def count_active() -> float:
        async with session_factory() as session:
            instances = await repository.list_all(
                session, heartbeat_timeout_seconds=heartbeat_timeout_seconds
            )
        return float(sum(1 for i in instances if i.healthy and i.status == "active"))

    async def count_heartbeat_miss() -> float:
        async with session_factory() as session:
            instances = await repository.list_all(
                session, heartbeat_timeout_seconds=heartbeat_timeout_seconds
            )
        return float(sum(1 for i in instances if not i.healthy))

    return {
        ACTIVE_INSTANCES.name: (active_gauge, count_active),
        HEARTBEAT_MISS.name: (heartbeat_miss_gauge, count_heartbeat_miss),
    }
