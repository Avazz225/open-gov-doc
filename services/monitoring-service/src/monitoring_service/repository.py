from dms_metrics_client import resolve_active
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


def is_sensor_active(
    config: SensorConfigOut, name: str, *, group: str | None = None, cost: str | None = None
) -> bool:
    return resolve_active(config.global_default, config.overrides, name, group=group, cost=cost)[0]


VALID_COSTS = frozenset({"cheap", "expensive"})


def validate_override_key(key: str) -> None:
    """P88-S2 (ADR 0243): a sensor name, `group:<group>` or `cost:<cheap|
    expensive>`. Any other prefixed key is a typo that would silently
    match nothing, so it is refused."""
    if ":" not in key:
        return
    prefix, _, value = key.partition(":")
    if not value:
        raise ValueError(f"Leerer Wert in {key!r}")
    if prefix == "group":
        return
    if prefix == "cost" and value in VALID_COSTS:
        return
    raise ValueError(
        f"Unbekannter Schlüssel {key!r} - erlaubt: Sensorname, 'group:<Gruppe>', "
        "'cost:cheap', 'cost:expensive'"
    )


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
    sensors = []
    for name, sensor in catalog.items():
        active, source = resolve_active(
            config.global_default,
            config.overrides,
            name,
            group=sensor["group"],
            cost=sensor["cost"],
        )
        sensors.append(
            SensorOut(
                name=sensor["name"],
                group=sensor["group"],
                cost=sensor["cost"],
                description=sensor["description"],
                service_types=sorted(service_types[name]),
                active=active,
                source=source,
            )
        )
    return sorted(sensors, key=lambda s: (s.group, s.name))
