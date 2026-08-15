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


def sensor_declarations() -> list[dict]:
    return [ACTIVE_INSTANCES.as_dict(), HEARTBEAT_MISS.as_dict(), *http_sensor_declarations()]


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
