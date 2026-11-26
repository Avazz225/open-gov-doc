import logging
from datetime import UTC, datetime

from dms_metrics_client import COST_LEVELS, LEGACY_KEY_LEVELS, normalize_cost, resolve_active
from monitoring_service import log_levels
from monitoring_service.clients import RegistryInstance
from monitoring_service.models import GLOBAL_DEFAULT_KEY, SensorConfigEntry, TracingConfig
from monitoring_service.schemas import SensorConfigOut, SensorOut, TracingConfigOut
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_global_default_seeded(session: AsyncSession) -> None:
    entry = await session.get(SensorConfigEntry, GLOBAL_DEFAULT_KEY)
    if entry is None:
        session.add(SensorConfigEntry(key=GLOBAL_DEFAULT_KEY, enabled=True))
        await session.flush()


# Fallback ratio while no one has set one (P94-S1) - main.py sets it from the
# service's own `DMS_OTEL_SAMPLING_RATIO`.
default_tracing_ratio: float = 1.0


async def get_sensor_config(session: AsyncSession) -> SensorConfigOut:
    result = await session.execute(select(SensorConfigEntry))
    entries = {e.key: e.enabled for e in result.scalars().all()}
    global_default = entries.pop(GLOBAL_DEFAULT_KEY, True)
    tracing = await get_tracing_config(session)
    active_levels = await log_levels.list_active(session, datetime.now(UTC))
    return SensorConfigOut(
        global_default=global_default,
        overrides=entries,
        tracing_sampling_ratio=tracing.sampling_ratio,
        log_levels=log_levels.distributed(active_levels),
    )


async def get_tracing_config(session: AsyncSession) -> TracingConfigOut:
    row = await session.get(TracingConfig, 1)
    if row is None:
        return TracingConfigOut(sampling_ratio=default_tracing_ratio)
    return TracingConfigOut(
        sampling_ratio=row.sampling_ratio, updated_by=row.updated_by, updated_at=row.updated_at
    )


async def set_tracing_ratio(session: AsyncSession, ratio: float, actor: str) -> TracingConfigOut:
    row = await session.get(TracingConfig, 1)
    now = datetime.now(UTC)
    if row is None:
        session.add(TracingConfig(id=1, sampling_ratio=ratio, updated_by=actor, updated_at=now))
    else:
        row.sampling_ratio = ratio
        row.updated_by = actor
        row.updated_at = now
    await session.flush()
    return await get_tracing_config(session)


logger = logging.getLogger(__name__)


def is_sensor_active(
    config: SensorConfigOut,
    name: str,
    *,
    group: str | None = None,
    cost: int | str | None = None,
    default_enabled: bool = True,
) -> bool:
    return resolve_active(
        config.global_default,
        config.overrides,
        name,
        group=group,
        cost=cost,
        default_enabled=default_enabled,
    )[0]


def expand_override_key(key: str) -> list[str]:
    """Validates an override key and returns the key(s) to store (P88-S2/
    S2c, ADR 0243): a sensor name, `group:<group>` or `cost:<1-5>` as is; a
    legacy `cost:cheap`/`cost:expensive` (an old export, an old script)
    becomes its levels. Any other prefixed key is a typo that would
    silently match nothing, so it is refused."""
    if ":" not in key:
        return [key]
    prefix, _, value = key.partition(":")
    if not value:
        raise ValueError(f"Leerer Wert in {key!r}")
    if prefix == "group":
        return [key]
    if prefix == "cost":
        if value in LEGACY_KEY_LEVELS:
            return [f"cost:{level}" for level in LEGACY_KEY_LEVELS[value]]
        if value.isdigit() and int(value) in COST_LEVELS:
            return [key]
    raise ValueError(
        f"Unbekannter Schlüssel {key!r} - erlaubt: Sensorname, 'group:<Gruppe>', "
        "'cost:1' bis 'cost:5'"
    )


async def migrate_legacy_cost_keys(session: AsyncSession) -> list[str]:
    """Rewrites stored `cost:cheap`/`cost:expensive` overrides into their
    levels on startup (P88-S2c). A level that already has its own override
    keeps it. Returns the legacy keys that were migrated."""
    migrated = []
    for legacy, levels in LEGACY_KEY_LEVELS.items():
        entry = await session.get(SensorConfigEntry, f"cost:{legacy}")
        if entry is None:
            continue
        for level in levels:
            if await session.get(SensorConfigEntry, f"cost:{level}") is None:
                session.add(SensorConfigEntry(key=f"cost:{level}", enabled=entry.enabled))
        await session.delete(entry)
        migrated.append(entry.key)
    if migrated:
        await session.flush()
        logger.info("sensor_cost_keys_migrated", extra={"keys": migrated})
    return migrated


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
        cost = normalize_cost(sensor["cost"])
        default_enabled = bool(sensor.get("default_enabled", True))
        active, source = resolve_active(
            config.global_default,
            config.overrides,
            name,
            group=sensor["group"],
            cost=cost,
            default_enabled=default_enabled,
        )
        sensors.append(
            SensorOut(
                name=sensor["name"],
                group=sensor["group"],
                cost=cost,
                description=sensor["description"],
                service_types=sorted(service_types[name]),
                active=active,
                default_enabled=default_enabled,
                source=source,
            )
        )
    return sorted(sensors, key=lambda s: (s.group, s.name))
