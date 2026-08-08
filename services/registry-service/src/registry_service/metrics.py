from collections.abc import Awaitable, Callable

from dms_metrics_client import GuardedGauge, SensorConfigClient, SensorRegistry, SensorSpec
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from registry_service import repository

# registry-service ist einer der zwei Sensor-Piloten (10.1, P11-S1, siehe
# P11-S0-Befund: kein Vollretrofit aller Services). Beide Sensor-Namen sind
# bewusst wörtlich aus Konzept 10.1s eigener Beispielliste übernommen, wo
# vorhanden ("registry.service.heartbeat.miss").
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
    return [ACTIVE_INSTANCES.as_dict(), HEARTBEAT_MISS.as_dict()]


def build_sensor_registry(
    config_client: SensorConfigClient,
) -> tuple[SensorRegistry, GuardedGauge, GuardedGauge]:
    registry = SensorRegistry("registry-service", is_active=config_client.is_active)
    active_gauge = registry.gauge(ACTIVE_INSTANCES)
    heartbeat_miss_gauge = registry.gauge(HEARTBEAT_MISS)
    return registry, active_gauge, heartbeat_miss_gauge


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
