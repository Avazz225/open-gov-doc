from monitoring_service.clients import RegistryInstance
from monitoring_service.models import GLOBAL_DEFAULT_KEY, SensorConfigEntry
from monitoring_service.schemas import SensorConfigOut, SensorOut
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_global_default_seeded(session: AsyncSession) -> None:
    entry = await session.get(SensorConfigEntry, GLOBAL_DEFAULT_KEY)
    if entry is None:
        session.add(SensorConfigEntry(key=GLOBAL_DEFAULT_KEY, enabled=True))
        await session.flush()


async def get_sensor_config(session: AsyncSession) -> SensorConfigOut:
    result = await session.execute(select(SensorConfigEntry))
    entries = {e.key: e.enabled for e in result.scalars().all()}
    global_default = entries.pop(GLOBAL_DEFAULT_KEY, True)
    return SensorConfigOut(global_default=global_default, overrides=entries)


def is_sensor_active(config: SensorConfigOut, name: str) -> bool:
    return config.overrides.get(name, config.global_default)


async def set_global_default(session: AsyncSession, enabled: bool) -> SensorConfigOut:
    entry = await session.get(SensorConfigEntry, GLOBAL_DEFAULT_KEY)
    if entry is None:
        session.add(SensorConfigEntry(key=GLOBAL_DEFAULT_KEY, enabled=enabled))
    else:
        entry.enabled = enabled
    await session.flush()
    return await get_sensor_config(session)


async def set_sensor_override(
    session: AsyncSession, name: str, enabled: bool | None
) -> SensorConfigOut:
    entry = await session.get(SensorConfigEntry, name)
    if enabled is None:
        if entry is not None:
            await session.delete(entry)
    elif entry is None:
        session.add(SensorConfigEntry(key=name, enabled=enabled))
    else:
        entry.enabled = enabled
    await session.flush()
    return await get_sensor_config(session)


async def list_sensors(session: AsyncSession, instances: list[RegistryInstance]) -> list[SensorOut]:
    """Aggregates the sensor catalog from the self-declarations of all
    currently active instances (10.1) - deduplicated by sensor name; in
    case of conflicting declarations (unlikely, since the same sensor
    usually comes from the same service type), the most recently seen
    declaration wins, a documented simplification."""
    config = await get_sensor_config(session)
    catalog: dict[str, dict] = {}
    service_types: dict[str, set[str]] = {}
    for instance in instances:
        for sensor in instance.sensors:
            name = sensor["name"]
            catalog[name] = sensor
            service_types.setdefault(name, set()).add(instance.service_type)
    return [
        SensorOut(
            name=sensor["name"],
            group=sensor["group"],
            cost=sensor["cost"],
            description=sensor["description"],
            service_types=sorted(service_types[name]),
            active=is_sensor_active(config, name),
        )
        for name, sensor in catalog.items()
    ]
